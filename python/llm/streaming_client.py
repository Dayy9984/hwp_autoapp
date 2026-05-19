"""OpenAI 스트리밍 클라이언트 - 실시간 편집을 위한 텍스트 스트리밍

v7.3: OpenAI Responses API로 전환
- client.responses.create() 사용 (Chat Completions API 대체)

- SSE 이벤트 기반 스트리밍 처리
- search_rag function tool 지원 유지
"""

import sys
import json
import re
import html as html_module
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Generator, Callable, Dict, Any, List, Tuple
from dataclasses import dataclass
from openai import OpenAI

from utils.logger import debug, log_llm_interaction, get_log_dir
from .system_prompt_v7_11 import build_system_prompt_v7_11
from .edit_tools_schema import build_v710_edit_tools, build_v711_batch_tools, build_v711_analysis_tools
from .edit_tools_parser import parse_v710_tool_call, parse_execute_edits



# ============================================================================
# RAG Context (v7.2: OpenAI file_search 기반)
# ============================================================================

_rag_context: Dict[str, Any] = {
    "project_id": None,
    "chat_id": None,
    "user_data_path": None,
    "openai_api_key": None,
}


def _pick_most_common(counter: Dict[str, int], default: str) -> str:
    if not counter:
        return default
    return max(counter.items(), key=lambda item: item[1])[0]


def _extract_list_style_hints(html: str) -> Dict[str, str]:
    """문서 HTML에서 리스트 스타일 힌트를 추출."""
    defaults = {"bullet": "-", "number": "1.", "subitem_indent": "\\t"}
    if not html:
        return defaults

    normalized = re.sub(r"<br\\s*/?>", "\n", html, flags=re.IGNORECASE)
    normalized = re.sub(r"</p\\s*>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    normalized = html_module.unescape(normalized)

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]

    bullet_counts: Dict[str, int] = {}
    number_counts: Dict[str, int] = {}

    bullet_pattern = re.compile(r"^([\-•·○□■◆▪▫▶➤])\s+")
    number_dot_pattern = re.compile(r"^(\d+)\.")
    number_paren_pattern = re.compile(r"^(\d+)\)")
    number_wrapped_pattern = re.compile(r"^\((\d+)\)")

    for line in lines:
        bullet_match = bullet_pattern.match(line)
        if bullet_match:
            token = bullet_match.group(1)
            bullet_counts[token] = bullet_counts.get(token, 0) + 1
            continue

        if number_dot_pattern.match(line):
            number_counts["1."] = number_counts.get("1.", 0) + 1
            continue

        if number_paren_pattern.match(line):
            number_counts["1)"] = number_counts.get("1)", 0) + 1
            continue

        if number_wrapped_pattern.match(line):
            number_counts["(1)"] = number_counts.get("(1)", 0) + 1

    return {
        "bullet": _pick_most_common(bullet_counts, defaults["bullet"]),
        "number": _pick_most_common(number_counts, defaults["number"]),
        "subitem_indent": defaults["subitem_indent"],
    }


def set_rag_context(
    project_id: str = None,
    chat_id: str = None,
    user_data_path: str = None,
    openai_api_key: str = None,
    codex_mode: bool = False,
):
    """RAG 검색에 필요한 컨텍스트 설정"""
    global _rag_context
    _rag_context = {
        "project_id": project_id,
        "chat_id": chat_id,
        "user_data_path": user_data_path,
        "openai_api_key": openai_api_key,
        "codex_mode": codex_mode,
    }


_CONTEXT_TAGS = [
    "AVAILABLE_FILES",
    "PREVIOUS_CONVERSATION",
    "MATCHED_TEMPLATE_PAIR",
    "REJECTION_CONTEXT",
    "REJECTED_EDITS_LOG",
    "SELECTION",
    "LIST_STYLE_HINT",
    "REFERENCE_DOCUMENTS",
    "CHAT_FILES",
    "PROJECT_FILES",
]

def _extract_context_tags(prompt: str) -> Tuple[str, str]:
    """Electron에서 prompt에 삽입한 XML context 태그를 분리.

    Returns (context_sections, user_request_text):
      - context_sections: 추출된 태그 블록들 (줄바꿈 구분)
      - user_request_text: 태그를 제거한 순수 사용자 요청
    """
    remaining = prompt
    extracted: List[str] = []

    for tag in _CONTEXT_TAGS:
        pattern = re.compile(
            rf"<{tag}>\s*(.*?)\s*</{tag}>",
            re.DOTALL,
        )
        match = pattern.search(remaining)
        if match:
            extracted.append(match.group(0).strip())
            remaining = remaining[:match.start()] + remaining[match.end():]

    # 선택영역 레거시 포맷: [선택영역: 표|텍스트]\n...\n---
    legacy_sel = re.search(
        r"\[선택영역:\s*(표|텍스트)\]\n(.*?)\n---\n*",
        remaining,
        re.DOTALL,
    )
    if legacy_sel:
        sel_type = legacy_sel.group(1)
        sel_text = legacy_sel.group(2).strip()
        extracted.append(f"<SELECTION>\n[선택영역: {sel_type}]\n{sel_text}\n</SELECTION>")
        remaining = remaining[:legacy_sel.start()] + remaining[legacy_sel.end():]

    context_sections = "\n\n".join(extracted)
    user_request_text = remaining.strip()
    return context_sections, user_request_text


def _build_search_rag_tool(codex_mode: bool = False) -> List[Dict[str, Any]]:
    """search_rag function tool 정의 (v7.3.2: 배치 검색 지원).

    codex_mode=True면 grep(키워드 정확 매칭) 안내,
    codex_mode=False면 임베딩(의미 검색) 안내로 description을 분기한다.
    """
    if codex_mode:
        # Codex 모드: 로컬 grep — 정확한 키워드/정규식 매칭
        description = (
            "업로드된 참조 파일에서 정보를 검색합니다 (grep 방식 — 텍스트 정확 매칭).\n"
            "임베딩이 아니므로 **단일 단어/명사/숫자/고유명사**로 짧고 구체적으로 검색하세요.\n"
            "필요시 정규식 `대표자|대표\\s*자` 같은 alternation도 사용 가능합니다.\n"
            "\n"
            "올바른 예: ['업체명', '대표자', '사업자등록번호', '매출액', '2025년']\n"
            "잘못된 예: ['업체명 대표자 사업자등록번호'] ← 공백 묶음 (전체가 한 문자열로 검색됨)\n"
            "잘못된 예: ['회사 기본 정보를 알려주세요'] ← 자연어 문장 (매칭 실패)\n"
            "\n"
            "결과가 0건이면 동의어/유사어로 reformulate하여 재호출. "
            "file_name 필터가 너무 좁으면 해제. "
            "최소 2회 재시도 후에도 빈 결과일 때만 사용자에게 안내."
        )
        queries_desc = (
            "검색 키워드 배열. **각 원소는 단일 단어 또는 짧은 정규식**으로. "
            "예: ['업체명', '대표자', '매출액', '2025년']. "
            "여러 단어를 공백으로 묶으면 정확한 문자열만 매칭되어 결과가 거의 안 나옵니다."
        )
    else:
        # API 모드: OpenAI Vector Store (임베딩 의미 검색) — 자연어 구문 OK
        description = (
            "업로드된 참조 파일에서 정보를 검색합니다 (임베딩 의미 검색). "
            "자연어 구문으로 검색하면 유사한 의미의 단락을 자동으로 찾아줍니다. "
            "참조 파일이 있으면 추론과 같은 응답에서 동시에 호출하세요. "
            "검색 결과는 시스템이 자동으로 전달합니다. "
            "**중요**: 결과가 0건이거나 빈약하면 단일 호출로 결론짓지 말 것. "
            "동의어/유사어로 쿼리를 reformulate하여 search_rag를 재호출하라. "
            "file_name 필터가 너무 좁으면 해제하고 재시도. "
            "최소 2회 재시도 후에도 빈 결과일 때만 사용자에게 안내."
        )
        queries_desc = "검색 키워드 배열. 예: ['팀원 정보', '사업 목표', '추진 전략']"

    return [{
        "type": "function",
        "name": "search_rag",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "검색할 파일명 (선택). AVAILABLE_FILES에서 정확히 복사. 예: '2026년_IBK_창공_모집.pdf'"
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10,
                    "description": queries_desc
                }
            },
            "required": ["queries"]
        }
    }]


def _build_read_file_tool() -> List[Dict[str, Any]]:
    """read_file function tool 정의 (Codex 모드 분석 단계용).

    search_rag 결과에서 잘림 표시가 있을 때 이어 읽기에 사용.
    """
    return [{
        "type": "function",
        "name": "read_file",
        "description": (
            "참조 파일의 특정 구간을 읽습니다. "
            "search_rag 결과의 스니펫이 잘려있을 때(truncated_after=true) "
            "end_char 값을 offset으로 사용하여 이어 읽습니다. "
            "충분한 참조 데이터 확보를 위해 적극적으로 사용하세요."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "읽을 파일명. search_rag 결과의 source 필드 또는 AVAILABLE_FILES에서 정확히 복사."
                },
                "offset": {
                    "type": "integer",
                    "description": "읽기 시작 위치 (문자 단위, 0-based). search_rag 결과의 end_char 값 사용 권장."
                },
                "length": {
                    "type": "integer",
                    "description": "읽을 문자 수 (최대 2000, 기본 1000)."
                }
            },
            "required": ["file_name", "offset"]
        }
    }]


def _execute_read_file(
    file_name: str,
    offset: int,
    length: int = 1000,
    on_file_search: Optional[Callable[[str, str], None]] = None,
) -> str:
    """read_file tool 실행 — LocalFileReaderService.read_chunk 호출."""
    project_id = _rag_context.get("project_id")
    chat_id = _rag_context.get("chat_id")
    user_data_path = _rag_context.get("user_data_path")
    if not project_id:
        return json.dumps({"success": False, "error": "project_id not set"})
    try:
        from services.local_file_reader_service import LocalFileReaderService
        from services.config import config

        reader = LocalFileReaderService(user_data_path or config.user_data_path)

        if on_file_search:
            on_file_search("search", f"파일 읽기: {file_name} (offset={offset}, length={length})")

        result = reader.read_chunk(file_name, project_id, chat_id, offset, length)

        if on_file_search:
            if result.get("success"):
                on_file_search("complete", f"읽기 완료: {file_name} {result.get('returned_chars', 0)}자")
            else:
                on_file_search("complete", f"읽기 실패: {result.get('error', 'unknown')}")

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        debug(f"[READ_FILE] error: {e}")
        return json.dumps({"success": False, "error": str(e)})


_EXPAND_TARGET = 1500  # 스니펫 자동 확장 목표 (편측, 총 ~3000자)


def _execute_local_search(
    queries: List[str],
    project_id: str,
    chat_id: Optional[str],
    file_name: Optional[str],
    user_data_path: Optional[str],
    on_file_search: Optional[Callable[[str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> str:
    """Codex 모드용 로컬 파일 검색 + 자동 확장.

    grep → read 패턴: search로 위치를 찾고, read_chunk로 주변을 넉넉하게
    자동 확장하여 LLM에게 충분한 컨텍스트를 제공합니다.
    """
    try:
        from services.local_file_reader_service import LocalFileReaderService
        from services.config import config

        effective_path = user_data_path or config.user_data_path
        debug(f"[LOCAL_SEARCH] user_data_path={user_data_path}, effective={effective_path}")
        reader = LocalFileReaderService(effective_path)
        _dbg_files = reader.get_available_files(project_id, chat_id)
        debug(f"[LOCAL_SEARCH] project_id={project_id}, chat_id={chat_id}, available_files={len(_dbg_files)}")
        for _fi in _dbg_files[:3]:
            import os as _os
            _exists = _os.path.exists(_fi.get('abs_path', ''))
            debug(f"[LOCAL_SEARCH]   {_fi.get('display_name')}: exists={_exists}, path={_fi.get('abs_path','')[:80]}")

        if on_file_search:
            kw = ", ".join(queries[:5])
            if len(queries) > 5:
                kw += f" 외 {len(queries) - 5}개"
            on_file_search("search", f"자료 검색 중: [{kw}]")

        if cancel_check and cancel_check():
            return json.dumps({"success": False, "error": "cancelled", "results": []})

        result = reader.search(queries, project_id, chat_id, file_name, on_progress=on_file_search)
        debug(f"[LOCAL_SEARCH] search result: success={result.get('success')}, results={len(result.get('results',[]))}, message={result.get('message','')}")

        if cancel_check and cancel_check():
            return json.dumps({"success": False, "error": "cancelled", "results": []})

        # ── grep→read 자동 확장: 스니펫이 잘린 경우 read_chunk로 주변 확장 ──
        raw_results = result.get("results", [])
        if raw_results:
            expanded = _auto_expand_snippets(reader, raw_results, project_id, chat_id, on_file_search)
            result["results"] = expanded

        if on_file_search:
            count = len(result.get("results", []))
            if count:
                total_chars = sum(len(r.get("content", "")) for r in result["results"])
                files_hit = len({r.get("source", "") for r in result["results"]})
                on_file_search("complete", f"검색 완료: {files_hit}개 파일에서 {count}곳 발견 (총 {total_chars:,}자)")
            else:
                on_file_search("complete", "검색 완료: 매치 없음")

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        debug(f"[LOCAL_SEARCH] error: {e}")
        return json.dumps({"error": str(e), "results": []})


def _auto_expand_snippets(
    reader,
    results: List[Dict[str, Any]],
    project_id: str,
    chat_id: Optional[str],
    on_file_search: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """search 결과 스니펫을 read_chunk로 자동 확장.

    각 스니펫의 start_char/end_char를 기준으로 앞뒤를 넓혀 읽어
    ~3000자 수준의 풍부한 컨텍스트를 확보합니다.
    겹치는 구간은 병합하여 중복 제거합니다.
    """
    # 파일별로 그룹핑
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        src = r.get("source", "")
        by_file.setdefault(src, []).append(r)

    expanded: List[Dict[str, Any]] = []

    for source, file_results in by_file.items():
        total_chars = file_results[0].get("total_chars", 0)
        if total_chars == 0:
            expanded.extend(file_results)
            continue

        # 각 매치의 확장 범위 계산 → 겹치면 병합
        ranges: List[List[int]] = []  # [[start, end], ...]
        for r in file_results:
            s = r.get("start_char", 0)
            e = r.get("end_char", s + 600)
            # 앞뒤로 _EXPAND_TARGET만큼 확장
            exp_s = max(0, s - _EXPAND_TARGET)
            exp_e = min(total_chars, e + _EXPAND_TARGET)
            ranges.append([exp_s, exp_e])

        # 정렬 + 병합
        ranges.sort()
        merged: List[List[int]] = [ranges[0]]
        for s, e in ranges[1:]:
            if s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])

        # 병합된 범위를 read_chunk 반복 호출로 전체 읽기
        for rng_start, rng_end in merged:
            length = rng_end - rng_start
            if length <= 0:
                continue

            # 범위가 크면 여러 번 read_chunk → 연결
            parts: List[str] = []
            cursor = rng_start
            read_ok = True
            while cursor < rng_end:
                chunk_len = min(2000, rng_end - cursor)
                chunk_result = reader.read_chunk(
                    source, project_id, chat_id,
                    offset=cursor, length=chunk_len,
                )
                if not chunk_result.get("success"):
                    read_ok = False
                    break
                parts.append(chunk_result.get("content", ""))
                cursor = chunk_result.get("end_char", cursor + chunk_len)

            if not read_ok:
                # 읽기 실패 시 원본 스니펫 유지
                for r in file_results:
                    rs = r.get("start_char", 0)
                    re_ = r.get("end_char", 0)
                    if rs >= rng_start and re_ <= rng_end:
                        expanded.append(r)
                continue

            chunk_text = "".join(parts)
            actual_start = rng_start
            actual_end = min(rng_start + len(chunk_text), total_chars)

            if on_file_search:
                on_file_search("search", f"확장 읽기: {source} [{actual_start:,}~{actual_end:,}자] ({len(chunk_text):,}자)")

            # 이 범위에 속하는 원본 쿼리들 수집
            queries_in_range = []
            for r in file_results:
                rs = r.get("start_char", 0)
                re_ = r.get("end_char", 0)
                if rs >= rng_start and re_ <= rng_end:
                    q = r.get("query", "")
                    if q and q not in queries_in_range:
                        queries_in_range.append(q)

            # 잘림 마커
            prefix = f"[...앞쪽 {actual_start:,}자 생략...]" if actual_start > 0 else ""
            chars_after = total_chars - actual_end
            suffix = (
                f"[...뒤쪽 {chars_after:,}자 생략 — "
                f"read_file(file_name=\"{source}\", offset={actual_end})로 이어 읽기...]"
            ) if chars_after > 300 else ""
            content = f"{prefix}\n{chunk_text}\n{suffix}".strip() if (prefix or suffix) else chunk_text

            expanded.append({
                "query": ", ".join(queries_in_range),
                "scope": file_results[0].get("scope", ""),
                "content": content,
                "source": source,
                "start_char": actual_start,
                "end_char": actual_end,
                "total_chars": total_chars,
                "truncated_before": actual_start > 0,
                "truncated_after": chars_after > 300,
            })

    return expanded


def _execute_search_rag(
    queries: List[str],
    file_name: Optional[str] = None,
    on_file_search: Optional[Callable[[str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> str:
    """search_rag function tool 실행 (v7.3.2: 배치 검색 지원)

    Args:
        queries: 검색 키워드 배열 (1-5개)
        file_name: 검색할 파일명 (선택)
        on_file_search: 검색 진행 콜백

    Returns:
        JSON 문자열: {"success": bool, "results": [{query, scope, content, source}, ...]}
    """
    project_id = _rag_context.get("project_id")
    chat_id = _rag_context.get("chat_id")
    user_data_path = _rag_context.get("user_data_path")
    openai_api_key = _rag_context.get("openai_api_key")
    codex_mode = _rag_context.get("codex_mode", False)

    if not project_id:
        return json.dumps({"error": "project_id not set", "results": []})
    if not codex_mode and not openai_api_key:
        return json.dumps({"error": "OPENAI_API_KEY_REQUIRED", "results": []})

    # queries 유효성 검사
    if not queries or not isinstance(queries, list):
        return json.dumps({"error": "queries must be a non-empty array", "results": []})

    # 최대 10개로 제한 (중복 제거 + 정규화)
    seen = set()
    queries_clean = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries_clean.append(q)
    queries = queries_clean[:10]

    try:
        from services.config import config
        if user_data_path and not config.initialized:
            config.set_user_data_path(user_data_path)

        # Codex 모드: Vector Store 사용 불가 → 로컬 파일 직접 검색
        if codex_mode:
            return _execute_local_search(queries, project_id, chat_id, file_name, user_data_path, on_file_search, cancel_check)

        from services.file_search_service import FileSearchService

        file_search_service = FileSearchService(openai_api_key)
        all_results = []
        cancelled = False

        def is_cancelled() -> bool:
            return bool(cancel_check and cancel_check())

        # Progress 콜백: 배치 검색 시작 알림 (키워드 포함)
        if on_file_search:
            keywords_display = ", ".join(queries[:5])  # 최대 5개까지 표시
            if len(queries) > 5:
                keywords_display += f" 외 {len(queries) - 5}개"
            on_file_search("search", f"자료 검색 중: [{keywords_display}]")


        # 병렬 검색 실행 (쿼리당 독립 검색)
        indexed_queries = list(enumerate(queries))
        results_by_index: Dict[int, List[Dict[str, Any]]] = {idx: [] for idx, _ in indexed_queries}

        def run_query(query_text: str) -> List[Dict[str, Any]]:
            if is_cancelled():
                return []
            try:
                result = file_search_service.query_multi_scope(
                    query_text=query_text,
                    project_id=project_id,
                    chat_id=chat_id,
                    file_name=file_name,
                    top_k=15,  # limit results per query for batch search
                    mode="naive",
                    progress_callback=None  # no per-query progress callback
                )
                if result.get("success") and result.get("results"):
                    return [
                        {
                            "query": query_text,
                            "scope": r.get("scope"),
                            "content": r.get("content", "")[:3000],  # truncate content for batch
                            "source": r.get("source"),
                        }
                        for r in result["results"]
                    ]
            except Exception as e:
                debug(f"[RAG] per-query error: {e}")
            return []
        max_workers = max(1, len(indexed_queries))
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(run_query, query_text): idx for idx, query_text in indexed_queries}

        try:
            for future in as_completed(futures):
                if is_cancelled():
                    cancelled = True
                    break
                idx = futures[future]
                query_text = indexed_queries[idx][1]
                query_results: List[Dict[str, Any]] = []
                try:
                    query_results = future.result() or []
                    if query_results:
                        results_by_index[idx].extend(query_results)
                except Exception as e:
                    debug(f"[RAG] per-query error: {e}")

                if on_file_search and not is_cancelled():
                    if query_results:
                        message = f"자료 검색 완료: [{query_text}] ({len(query_results)}개)"
                    else:
                        message = f"자료 검색 완료: [{query_text}] (결과 없음)"
                    on_file_search("complete", message)
        finally:
            if cancelled:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        for idx, _ in indexed_queries:
            all_results.extend(results_by_index[idx])

        if cancelled:
            debug("[RAG] 배치 검색 취소됨")
            return json.dumps({"success": False, "error": "cancelled", "results": []})

        # Progress 콜백: 검색 완료 알림 (결과 수 포함)
        if on_file_search:
            on_file_search("complete", f"자료 검색 완료: {len(all_results)}개 참고자료 발견")


        if all_results:
            return json.dumps({"success": True, "results": all_results}, ensure_ascii=False)
        else:
            return json.dumps({"success": True, "results": [], "message": "검색 결과가 없습니다"})

    except Exception as e:
        debug(f"[RAG] search_rag 실행 오류: {type(e).__name__}: {e}")
        import traceback
        debug(f"[RAG] traceback: {traceback.format_exc()}")
        return json.dumps({"error": str(e), "results": []})


# ============================================================================
# 시스템 프롬프트 (스트리밍 편집 패턴)
# ============================================================================

SYSTEM_PROMPT_STREAMING = (
    "[DEPRECATED] Legacy streaming prompt placeholder. "
    "Runtime prompt is build_system_prompt_v7_11() (Enriched CVD + Tool Calling only)."
)

# Legacy compatibility aliases (unused in v7.10 runtime path)
SYSTEM_PROMPT_CVD = SYSTEM_PROMPT_STREAMING
SYSTEM_PROMPT_CVD_COMPACT = SYSTEM_PROMPT_STREAMING




# HTML 태그 검증 및 이스케이프
_FORBIDDEN_HTML_TAGS = re.compile(
    r'</?(?:tr|td|th|table|tbody|thead|tfoot|span|p|div|html|body|head|script|style)[^>]*>',
    re.IGNORECASE
)
_FORBIDDEN_HTML_TAGS_STRIP = re.compile(
    r'</?(?:tr|td|th|table|tbody|thead|tfoot|span|p|div|html|body|head|script|style|list|textbox|footnote|image)[^>]*>',
    re.IGNORECASE
)
_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_P_TAG = re.compile(r"</?p[^>]*>", re.IGNORECASE)
_MD_FENCE_LINE = re.compile(r"```[^\n`]*\n?")
_MD_FENCE_TOKEN = re.compile(r"```")
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_HEADING_PREFIX = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_MD_BLOCKQUOTE_PREFIX = re.compile(r"(?m)^\s*>\s?")
_MD_LIST_PREFIX = re.compile(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_MD_EMPHASIS_PATTERNS = (
    re.compile(r"(?<!\w)\*\*\*([^\n]+?)\*\*\*"),
    re.compile(r"(?<!\w)\*\*([^\n]+?)\*\*"),
    re.compile(r"(?<!\w)__([^\n]+?)__"),
    re.compile(r"(?<!\w)\*([^\n*]+?)\*"),
    re.compile(r"(?<!\w)_([^\n_]+?)_"),
    re.compile(r"(?<!\w)~~([^\n]+?)~~"),
)


def _sanitize_content(content: str) -> str:
    """
    셀 콘텐츠에서 금지된 HTML 태그를 이스케이프 처리

    허용되는 태그: <br/>, <br>, <BR/>
    금지되는 태그: <tr>, <td>, <table>, <span>, <p>, <div> 등

    Args:
        content: 원본 콘텐츠

    Returns:
        이스케이프된 콘텐츠
    """
    if not content:
        return content

    # 금지된 태그 검출
    if _FORBIDDEN_HTML_TAGS.search(content):
        debug(f"[SANITIZE] 금지된 HTML 태그 발견, 이스케이프 처리: {content[:50]}...")
        # < 를 &lt; 로, > 를 &gt; 로 변환 (단, <br/> 태그는 유지)
        # 먼저 <br/> 태그를 임시 플레이스홀더로 치환
        temp_content = content
        br_placeholder = "\x00BR_PLACEHOLDER\x00"
        for br_pattern in ["<br/>", "<BR/>", "<br>", "<BR>", "<br />", "<BR />"]:
            temp_content = temp_content.replace(br_pattern, br_placeholder)

        # 나머지 < > 를 이스케이프
        temp_content = temp_content.replace("<", "&lt;").replace(">", "&gt;")

        # 플레이스홀더를 <br/>로 복원
        content = temp_content.replace(br_placeholder, "<br/>")

    return content


def _normalize_delta_content(content: str) -> str:
    """op 블록 기반 콘텐츠에서 태그/마크업을 제거하고 정규화."""
    if not content:
        return content

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _BR_TAG.sub("\n", content)
    content = _P_TAG.sub("\n", content)
    if _FORBIDDEN_HTML_TAGS_STRIP.search(content):
        content = _FORBIDDEN_HTML_TAGS_STRIP.sub("", content)
    return _sanitize_content(content)


def _normalize_message_text(content: Optional[str]) -> str:
    """메시지/사고 텍스트에서 Markdown 문법을 제거해 일반 텍스트로 정규화."""
    if content is None:
        return ""

    text = str(content)
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_TAG.sub("\n", text)
    text = _P_TAG.sub("\n", text)
    if _FORBIDDEN_HTML_TAGS_STRIP.search(text):
        text = _FORBIDDEN_HTML_TAGS_STRIP.sub("", text)

    text = _MD_FENCE_LINE.sub("", text)
    text = _MD_FENCE_TOKEN.sub("", text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADING_PREFIX.sub("", text)
    text = _MD_BLOCKQUOTE_PREFIX.sub("", text)
    text = _MD_LIST_PREFIX.sub("", text)

    # 중첩된 강조 기호(**, __, ~~ 등)를 순차적으로 제거
    for _ in range(3):
        previous = text
        for pattern in _MD_EMPHASIS_PATTERNS:
            text = pattern.sub(r"\1", text)
        if text == previous:
            break

    # 짝이 맞지 않는 잔여 마크업 토큰 정리
    text = text.replace("**", "").replace("__", "").replace("~~", "")

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return _sanitize_content(text)




def _truncate_text(value: str, max_chars: int) -> str:
    if not value or len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...(truncated)"


@dataclass
class StreamingCommand:
    """스트리밍 중 파싱된 단일 명령"""
    action: str  # "thinking" | "edit_document" | "append_table_row" | "message"
    id: Optional[int | str] = None  # int(셀 전체) 또는 str(셀 내부 문단, 예: "19-3")
    content: Optional[str] = None
    rows: Optional[list] = None
    message: Optional[str] = None
    metadata: Optional[dict] = None  # Delta 메타데이터 (operation, find, position 등)


@dataclass
class StreamingResult:
    """스트리밍 완료 후 최종 결과"""
    success: bool
    commands_executed: int
    messages: list[str]
    token_usage: dict  # 항상 dict 보장 (최소 {"input": 0, "output": 0, "total": 0})
    status: Optional[str] = "unknown"
    error: Optional[str] = None


class OpenAIStreamingClient:
    """
    OpenAI 스트리밍 클라이언트

    텍스트 생성을 스트리밍으로 받아서 줄 단위로 명령을 파싱하고,
    각 명령이 완성될 때마다 콜백을 호출합니다.

    취소 기능:
    - cancel() 메서드로 진행 중인 스트리밍을 중단할 수 있음
    - 타임아웃 시 자동으로 중단되도록 외부에서 호출 가능
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-5.1",
        reasoning_effort: str = "low",
        timeout: float = 120.0,  # 타임아웃 (초)
        codex_mode: bool = False,
        codex_account_id: str = "",
    ):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY_REQUIRED")

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.codex_mode = codex_mode
        self.codex_account_id = codex_account_id

        # 타임아웃 설정된 HTTP 클라이언트
        self._http_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,      # 연결 타임아웃
                read=timeout,      # 읽기 타임아웃 (스트리밍 응답)
                write=30.0,        # 쓰기 타임아웃
                pool=10.0          # 풀 타임아웃
            )
        )
        self.client = self._build_client()
        self._cancelled = False  # 취소 플래그
        self._active_streams: List[Any] = []

    def _build_client(self) -> OpenAI:
        if self.codex_mode:
            return OpenAI(
                api_key=self.api_key,
                base_url="https://chatgpt.com/backend-api/codex",
                default_headers={
                    "ChatGPT-Account-Id": self.codex_account_id,
                    "OpenAI-Beta": "responses=v1",
                    "OpenAI-Originator": "codex",
                },
                http_client=self._http_client,
            )
        return OpenAI(api_key=self.api_key, http_client=self._http_client)

    def _apply_codex_overrides(self, params: dict) -> dict:
        if not getattr(self, 'codex_mode', False):
            return params
        params.pop("max_output_tokens", None)
        params.pop("previous_response_id", None)
        params["store"] = False
        inp = params.get("input")
        if isinstance(inp, str):
            params["input"] = [{"type": "message", "role": "user", "content": inp}]
        return params

    def set_api_key(self, api_key: str, codex_mode: bool = False, codex_account_id: str = "") -> None:
        next_key = (api_key or "").strip()
        if not next_key:
            raise ValueError("OPENAI_API_KEY_REQUIRED")
        mode_changed = (codex_mode != self.codex_mode or codex_account_id != self.codex_account_id)
        if next_key == self.api_key and not mode_changed:
            return
        self.api_key = next_key
        self.codex_mode = codex_mode
        self.codex_account_id = codex_account_id
        self.client = self._build_client()

    def cancel(self):
        """진행 중인 스트리밍 취소"""
        self._cancelled = True
        if self._active_streams:
            for stream in list(self._active_streams):
                self._close_stream(stream)
            self._active_streams.clear()
        debug("[STREAMING] 취소 요청됨")

    def _register_stream(self, stream: Any) -> None:
        self._active_streams.append(stream)

    def _unregister_stream(self, stream: Any) -> None:
        self._active_streams = [s for s in self._active_streams if s is not stream]

    def _close_stream(self, stream: Any) -> None:
        closer = getattr(stream, "close", None)
        if callable(closer):
            try:
                closer()
                return
            except Exception as e:
                debug(f"[STREAMING] 스트림 종료 실패: {e}")
        response = getattr(stream, "response", None)
        closer = getattr(response, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as e:
                debug(f"[STREAMING] 스트림 응답 종료 실패: {e}")

    def reset_cancel(self):
        """취소 플래그 초기화 (새 요청 전 호출)"""
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        """취소 상태 확인"""
        return self._cancelled

    def generate_commands_streaming(
        self,
        html: str,
        prompt: str,
        on_command: Callable[[StreamingCommand], None],
        on_progress: Optional[Callable[[str], None]] = None,
        on_file_search: Optional[Callable[[str, str], None]] = None,
        on_tool_validation_error: Optional[Callable[[str, str], None]] = None,
        compact_mode: bool = True,
        use_delta: bool = False,
        use_html: bool = False,
        enable_file_search: bool = False,
        prompt_custom_rules: Optional[str] = None,
        prompt_custom_enabled: bool = False,
        prompt_full_override: Optional[str] = None
    ) -> StreamingResult:
        """
        스트리밍으로 편집 명령 생성 및 실시간 콜백

        Args:
            html: 문서 CVD JSONL (compact_mode=True면 압축 JSONL) 또는 HTML (use_html=True)
            prompt: 사용자 요청
            on_command: 명령이 파싱될 때마다 호출되는 콜백
            on_progress: 진행 상황 텍스트 콜백 (선택)
            compact_mode: 압축 모드 사용 여부 (기본값: True, CVD 모드에서만 사용)
            use_delta: Delta 형식 사용 여부 (기본값: False, CVD 모드)
            use_html: HTML 모드 사용 여부 (기본값: False, DocumentView HTML 방식)

        Returns:
            StreamingResult: 최종 결과
        """
        # 취소 플래그 초기화
        self.reset_cancel()
        # v7.10 단일 체계: Graph + Tool Calling만 허용
        if not use_delta:
            debug("[STREAMING] legacy mode requested; forcing use_delta=True (v7.10 graph/tool-only)")
            use_delta = True
        if use_html:
            debug("[STREAMING] use_html requested; ignored in v7.10 graph/tool-only mode")
            use_html = False
        self._compact_mode = compact_mode  # 압축 모드 저장 (ID 변환용)
        self._use_html = use_html
        self._use_delta = use_delta

        messages_collected = []
        commands_count = 0
        edit_commands_count = 0
        full_response = ""
        output_text_delta_seen = False
        output_text_done_seen = False
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        token_usage = {"input": 0, "output": 0, "total": 0}
        response_id = None
        status = "unknown"
        buffer = ""

        try:
            # Build user message and choose system prompt
            custom_rules = prompt_custom_rules if prompt_custom_enabled else None
            full_override = prompt_full_override if prompt_full_override and prompt_full_override.strip() else None
            effective_prompt = prompt
            # 강제 선행 RAG는 제거한다.
            # 문서 기반 키워드 검색은 모델의 search_rag tool-calling만 사용한다.

            # Delta mode (tool-calling) - v7.11 prompt
            # Electron에서 추가한 <AVAILABLE_FILES>, <PREVIOUS_CONVERSATION> 등
            # context 태그를 <USER_REQUEST> 밖으로 분리하여 시스템 프롬프트의
            # 태그 계층 구조에 맞춘다.
            context_sections, user_request_text = _extract_context_tags(effective_prompt)

            user_message_parts = [f"<ENRICHED_CVD>\n{html}\n</ENRICHED_CVD>"]
            if context_sections:
                user_message_parts.append(context_sections)
            user_message_parts.append(f"<USER_REQUEST>\n{user_request_text}\n</USER_REQUEST>")
            user_message = "\n\n".join(user_message_parts)

            # 파일 컨텍스트 감지: AVAILABLE_FILES/CHAT_FILES/PROJECT_FILES 중
            # 내용이 비어있지 않은 태그가 하나라도 있으면 True
            # (Electron이 프로젝트 파일 + 채팅 업로드 파일을 통합해 AVAILABLE_FILES 태그로 전달)
            _has_file_context = False
            for _file_tag in ("AVAILABLE_FILES", "CHAT_FILES", "PROJECT_FILES"):
                _m = re.search(
                    rf"<{_file_tag}>\s*(.*?)\s*</{_file_tag}>",
                    context_sections,
                    re.DOTALL,
                )
                if _m and _m.group(1).strip():
                    _has_file_context = True
                    break

            # RAG 모드: file_search 활성화 + 프로젝트 + 실제 파일 존재 모두 충족 시에만
            # 파일 0개인데 phase="analysis" 진입하면 search_rag 도구 노출만 되고
            # execute_edits는 노출 안 되어 thinking 단독 호출 후 종료되는 버그 방지
            _has_rag = bool(
                enable_file_search
                and _rag_context.get("project_id")
                and _has_file_context
            )

            if _has_rag:
                system_prompt = build_system_prompt_v7_11(
                    phase="analysis",
                    full_override=full_override,
                    # custom_rules 미포함: 검색 단계에 문체 규칙 불필요
                )
            else:
                system_prompt = build_system_prompt_v7_11(
                    phase="full",
                    custom_rules=custom_rules,
                    full_override=full_override,
                    has_file_context=_has_file_context,
                )
            mode_str = "Delta"
            prompt_version = "v7_11"
            debug(f"LLM 스트리밍 호출 ({mode_str}): model={self.model}, content_len={len(html)}")

            # HWPML 파싱 결과 (HTML) 로깅
            hwpml_log_file = get_log_dir() + f"/hwpml_parsing_{timestamp}.html"

            with open(hwpml_log_file, 'w', encoding='utf-8') as f:
                f.write("<!-- HWPML 파싱 결과 (LLM에게 전달된 HTML) -->\n")
                f.write(f"<!-- 생성 시간: {datetime.now().isoformat()} -->\n")
                f.write(f"<!-- 모드: {mode_str} -->\n")
                f.write(f"<!-- 길이: {len(html)} chars -->\n\n")
                f.write(html)

            debug(f"HWPML 파싱 HTML 저장: {hwpml_log_file}")

            # LLM 호출 전 프롬프트 정보 로깅
            prompt_log_file = get_log_dir() + f"/prompt_{timestamp}.json"
            with open(prompt_log_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "mode": mode_str,
                    "system_prompt_version": prompt_version,
                    "model": self.model,
                    "html_length": len(html),
                    "prompt_length": len(effective_prompt),
                    "prompt": effective_prompt,
                    "original_prompt_length": len(prompt),
                    "system_prompt_length": len(system_prompt),
                }, f, ensure_ascii=False, indent=2)
            debug(f"프롬프트 정보 저장: {prompt_log_file}")

            # v7.3: Responses API로 전환
            api_params = {
                "model": self.model,
                "instructions": system_prompt,
                "input": user_message,
                "stream": True,
                "max_output_tokens": 32768,
            }

            if use_delta:
                batch_tools = build_v711_batch_tools()
                if _has_rag:
                    # analysis phase: search_rag + thinking 만 (execute_edits 없음)
                    api_params["tools"] = _build_search_rag_tool(codex_mode=self.codex_mode) + build_v711_analysis_tools()
                    api_params["tool_choice"] = "required"
                    debug(f"[RAG/analysis] tools=[search_rag, thinking], tool_choice=required")
                else:
                    api_params["tools"] = batch_tools
                    api_params["tool_choice"] = "required"
                    debug("[TOOL/full] v7.11 batch tools, tool_choice=required")
            elif enable_file_search and _rag_context.get("project_id"):
                api_params["tools"] = _build_search_rag_tool(codex_mode=self.codex_mode)
                api_params["tool_choice"] = "auto"
                debug("[RAG] search_rag tool enabled")
            
            # 스트리밍 API 호출
            api_params = self._apply_codex_overrides(api_params)
            stream = self.client.responses.create(**api_params)

            # editing phase 프롬프트: RAG 2차 호출용. 루프 전 한 번만 빌드.
            editing_prompt = None
            if _has_rag:
                editing_prompt = build_system_prompt_v7_11(
                    phase="editing",
                    custom_rules=custom_rules,
                    full_override=full_override,
                )
            
            # Function call 상태 추적 (call_id 기반 인자 누적)
            tool_calls_state: Dict[int, Dict[str, Any]] = {}
            thinking_emitted = False
            deferred_message_cmd: Optional[StreamingCommand] = None
            _first_round_tool_calls: List[Dict[str, Any]] = []  # 1차 응답의 tool call 누적 (follow-up input용)
            _pending_initial_rag: Optional[tuple] = None  # 1차 호출에서 search_rag 감지 시 저장 (deferred RAG loop)
            _analysis_pending_tool_outputs: List[Dict[str, Any]] = []  # previous_response_id 사용 시 이전 round tool outputs

            # 버퍼: 줄이 완성될 때까지 모음
            buffer = ""

            def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
                if obj is None:
                    return default
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)

            def _extract_text_from_message_item(item: Any) -> str:
                if not item:
                    return ""
                texts: List[str] = []
                content = _safe_get(item, "content", []) or []
                for part in content:
                    part_type = _safe_get(part, "type")
                    if part_type in ("output_text", "text"):
                        text_val = _safe_get(part, "text") or _safe_get(part, "content")
                        if text_val:
                            texts.append(text_val)
                if texts:
                    return "".join(texts)
                direct_text = _safe_get(item, "text")
                return direct_text or ""

            def _handle_output_item_done(item: Any) -> None:
                nonlocal output_text_done_seen
                if not item:
                    return
                item_type = _safe_get(item, "type")
                if output_text_delta_seen or output_text_done_seen:
                    return
                if item_type == "message":
                    text = _extract_text_from_message_item(item)
                elif item_type == "output_text":
                    text = _safe_get(item, "text") or ""
                else:
                    text = ""
                if text:
                    _handle_output_delta(text)
                    output_text_done_seen = True

            def _handle_output_delta(content: str):
                nonlocal full_response
                if not content:
                    return

                full_response += content  # 원본은 그대로 저장 (로깅용)

                if on_progress:
                    on_progress(content)
                # v7.10 Tool Calling 모드에서는 출력 텍스트를 명령으로 파싱하지 않는다.
                # 편집 명령은 response.function_call 계열 이벤트에서만 처리한다.
                return

            def _iter_stream_events(active_stream: Any):
                self._register_stream(active_stream)
                try:
                    try:
                        for stream_event in active_stream:
                            yield stream_event
                    except Exception as e:
                        if self._cancelled:
                            return
                        raise
                finally:
                    self._unregister_stream(active_stream)
                    self._close_stream(active_stream)

            def _safe_json_loads(raw: str) -> Dict[str, Any]:
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    stripped = raw.strip()
                    decoder = json.JSONDecoder()
                    parsed, end = decoder.raw_decode(stripped)
                    if stripped[end:].strip():
                        debug("[RAG] Tool call args had trailing data; truncating extra payload")
                    return parsed

            def _deliver_command(cmd: StreamingCommand) -> None:
                nonlocal commands_count, edit_commands_count, messages_collected
                if cmd.action == "message":
                    text = _normalize_message_text(cmd.message or cmd.content or "")
                    if not text:
                        text = "요청을 처리했습니다."
                    cmd.message = text
                    if not cmd.content:
                        cmd.content = text
                    messages_collected.append(text)
                elif cmd.action == "thinking":
                    commands_count += 1
                else:
                    commands_count += 1
                    edit_commands_count += 1
                on_command(cmd)

            def _emit_auto_thinking(reason: str = "auto_injected_missing_first_thinking") -> None:
                nonlocal thinking_emitted
                if thinking_emitted:
                    return
                thinking_emitted = True
                if on_tool_validation_error:
                    try:
                        on_tool_validation_error("thinking", reason)
                    except Exception:
                        pass
                _deliver_command(
                    StreamingCommand(
                        action="thinking",
                        content="문서를 분석하고 편집 계획을 수립합니다.",
                        message="문서를 분석하고 편집 계획을 수립합니다.",
                        metadata={"operation": "thinking", "auto_injected": True},
                    )
                )

            def _emit_single_payload(payload: Dict[str, Any], source_tool: str) -> None:
                """Emit a single parsed payload as a StreamingCommand."""
                nonlocal thinking_emitted, deferred_message_cmd

                message_value = payload.get("message")
                action_value = payload.get("action")
                if action_value in {"message", "thinking"} and message_value is not None:
                    message_value = _normalize_message_text(message_value)

                cmd = StreamingCommand(
                    action=action_value,
                    id=payload.get("id"),
                    content=payload.get("content"),
                    rows=payload.get("rows"),
                    message=message_value,
                    metadata=payload.get("metadata"),
                )
                if cmd.action == "thinking":
                    if thinking_emitted:
                        if on_tool_validation_error:
                            try:
                                on_tool_validation_error(source_tool, "duplicate_thinking_ignored")
                            except Exception:
                                pass
                        return
                    thinking_emitted = True
                    _deliver_command(cmd)
                    return

                if cmd.action == "message":
                    deferred_message_cmd = cmd
                    return

                if not thinking_emitted:
                    _emit_auto_thinking()

                _deliver_command(cmd)

            def _emit_tool_command(func_name: Optional[str], func_args: Any) -> None:
                nonlocal thinking_emitted, deferred_message_cmd
                if not func_name:
                    return

                # 디버그: 모든 tool call 로깅
                args_preview = str(func_args)[:300] if func_args else "(empty)"
                debug(f"[TOOL] call received: name={func_name}, args_preview={args_preview}")

                # v7.11 batch tool: execute_edits
                if func_name == "execute_edits":
                    # execute_edits 전체 내용을 별도 파일에 저장 (디버깅용)
                    try:
                        from datetime import datetime as _dt
                        _ops_log_file = get_log_dir() + f"/execute_edits_{timestamp}.json"
                        with open(_ops_log_file, 'w', encoding='utf-8') as _opf:
                            _raw = func_args if isinstance(func_args, str) else json.dumps(func_args, ensure_ascii=False)
                            _opf.write(_raw)
                        debug(f"[TOOL] execute_edits 전체 저장: {_ops_log_file}")
                    except Exception:
                        pass
                    parsed_list = parse_execute_edits(func_args)
                    for payload, parse_error in parsed_list:
                        if payload is None:
                            if on_tool_validation_error:
                                try:
                                    on_tool_validation_error(func_name, parse_error or "validation_failed")
                                except Exception:
                                    pass
                            debug(f"[TOOL] ignored batch op: reason={parse_error}")
                            continue
                        _emit_single_payload(payload, func_name)
                    return

                payload, parse_error = parse_v710_tool_call(func_name, func_args)
                if payload is None:
                    if on_tool_validation_error:
                        try:
                            on_tool_validation_error(func_name, parse_error or "validation_failed")
                        except Exception:
                            pass
                    debug(f"[TOOL] ignored tool call: name={func_name}, reason={parse_error}")
                    return

                _emit_single_payload(payload, func_name)

            def _resolve_tool_call_payload(call_id: Optional[str], func_name: Optional[str], func_args: Any) -> tuple[Optional[str], Any]:
                """response.output_item.done 시점에 누적된 인자/이름을 보정한다."""
                if call_id is None:
                    return func_name, func_args
                matched_state: Optional[Dict[str, Any]] = None
                for state in tool_calls_state.values():
                    if state.get("id") == call_id:
                        matched_state = state
                        break
                if matched_state is None:
                    return func_name, func_args

                resolved_name = func_name or matched_state.get("name")
                streamed_args = matched_state.get("args") or ""
                done_args_text = str(func_args or "")
                if not done_args_text.strip():
                    resolved_args = streamed_args
                elif streamed_args and len(streamed_args) > len(done_args_text):
                    resolved_args = streamed_args
                else:
                    resolved_args = func_args
                return resolved_name, resolved_args

            for event in _iter_stream_events(stream):
                # 취소 체크 - 요청이 취소되면 즉시 중단
                if self._cancelled:
                    debug(f"[STREAMING] 취소됨 - 스트리밍 중단 (명령 {commands_count}개 완료)")
                    return StreamingResult(
                        success=False,
                        commands_executed=commands_count,
                        messages=messages_collected,
                        token_usage=token_usage,
                        status="cancelled",
                        error="요청이 취소되었습니다"
                    )

                event_type = getattr(event, 'type', None)

                # 응답 완료 이벤트 (Responses API: response.completed)
                if event_type == "response.completed":
                    status = "completed"
                    debug(f"[STREAMING] response.completed 수신")

                    if hasattr(event, 'response'):
                        response_id = getattr(event.response, 'id', response_id)

                        if hasattr(event.response, 'usage'):
                            usage = event.response.usage
                            token_usage = {
                                "input": getattr(usage, 'input_tokens', 0),
                                "output": getattr(usage, 'output_tokens', 0),
                                "total": getattr(usage, 'total_tokens', 0)
                            }
                            debug(f"[STREAMING] usage: in={token_usage['input']}, out={token_usage['output']}, total={token_usage['total']}")
                        else:
                            debug(f"[STREAMING] response.completed에 usage 없음")
                    continue

                # 응답 실패/불완전 종료 (token_usage 추출 시도)
                if event_type in ("response.failed", "response.incomplete"):
                    status = event_type.split(".")[1]  # "failed" or "incomplete"
                    debug(f"[STREAMING] {event_type} 수신")

                    if hasattr(event, 'response'):
                        response_id = getattr(event.response, 'id', response_id)

                        if hasattr(event.response, 'usage'):
                            usage = event.response.usage
                            token_usage = {
                                "input": getattr(usage, 'input_tokens', 0),
                                "output": getattr(usage, 'output_tokens', 0),
                                "total": getattr(usage, 'total_tokens', 0)
                            }
                            debug(f"[STREAMING] {event_type} usage: total={token_usage['total']}")
                    continue
                
                # 함수 호출 인자 델타
                if event_type == "response.function_call_arguments.delta":
                    call_id = getattr(event, 'call_id', None) or getattr(event, 'item_id', None)
                    # v4.1.8: API 문서에 따라 delta.arguments 파싱
                    delta_obj = getattr(event, 'delta', None)
                    args_delta = ''
                    if delta_obj:
                        args_delta = getattr(delta_obj, 'arguments', '')

                    # tool_calls_state에서 해당 call_id 찾기
                    tool_index = None
                    for idx, state in tool_calls_state.items():
                        if state.get("id") == call_id:
                            tool_index = idx
                            break

                    if tool_index is None:
                        tool_index = len(tool_calls_state)
                        tool_calls_state[tool_index] = {"id": call_id, "name": None, "args": ""}

                    if args_delta:
                        tool_calls_state[tool_index]["args"] += args_delta
                    continue
                
                # 함수 호출 시작 (이름 획득)
                if event_type == "response.output_item.added":
                    item = getattr(event, 'item', None)
                    if item and getattr(item, 'type', None) == "function_call":
                        call_id = getattr(item, 'call_id', None) or getattr(item, 'id', None)
                        func_name = getattr(item, 'name', None)
                        
                        tool_index = len(tool_calls_state)
                        tool_calls_state[tool_index] = {"id": call_id, "name": func_name, "args": ""}
                    continue
                
                # 함수 호출 완료
                if event_type == "response.output_item.done":
                    item = getattr(event, 'item', None)
                    if item and getattr(item, 'type', None) == "function_call":
                        # 함수 호출 처리
                        call_id = getattr(item, 'call_id', None) or getattr(item, 'id', None)
                        func_name = getattr(item, 'name', None)
                        func_args = getattr(item, 'arguments', '{}')
                        func_name, func_args = _resolve_tool_call_payload(call_id, func_name, func_args)
                        debug(f"[TOOL] output_item.done: name={func_name}, args_len={len(str(func_args))}")

                        if func_name == "search_rag":
                            # 1차 search_rag 감지: outer stream 완료 후 analysis loop + editing call 실행
                            _pending_initial_rag = (call_id, func_args)
                        elif use_delta and func_name:
                            # thinking은 _first_round_tool_calls에 포함하지 않음
                            # (empty output이 follow-up에서 LLM을 혼란시키는 문제 방지)
                            if func_name != "thinking":
                                _first_round_tool_calls.append(
                                    {"type": "function_call", "call_id": call_id,
                                     "name": func_name, "arguments": func_args}
                                )
                                _first_round_tool_calls.append(
                                    {"type": "function_call_output", "call_id": call_id,
                                     "output": ""}
                                )
                            elif _has_rag and not getattr(self, 'codex_mode', False):
                                # analysis path: thinking call_id 추적 (previous_response_id 사용 시 output 제공용)
                                _analysis_pending_tool_outputs.append(
                                    {"type": "function_call_output", "call_id": call_id, "output": ""}
                                )
                            _emit_tool_command(func_name, func_args)
                    else:
                        _handle_output_item_done(item)
                    continue
                
                # 텍스트 출력 델타
                if event_type == "response.output_text.delta":
                    # v4.1.8: API 문서에 따라 delta.text 파싱
                    delta_obj = getattr(event, 'delta', None)
                    if delta_obj:
                        content = _safe_get(delta_obj, 'text') or _safe_get(delta_obj, 'output_text') or ''
                        if content:
                            output_text_delta_seen = True
                            _handle_output_delta(content)
                    continue

                if event_type == "response.output_text.done":
                    if output_text_delta_seen or output_text_done_seen:
                        continue
                    text = _safe_get(event, 'text')
                    if not text:
                        delta_obj = getattr(event, 'delta', None)
                        if delta_obj:
                            text = _safe_get(delta_obj, 'text') or _safe_get(delta_obj, 'output_text')
                    if text:
                        _handle_output_delta(text)
                        output_text_done_seen = True

            # 버퍼에 남은 내용 처리
            if buffer.strip():
                # v7.10 단일 모드에서는 버퍼 명령 파싱을 수행하지 않는다.
                buffer = ""

            # ===================================================================
            # RAG 분석/편집 완전 분리 루프
            # 1차 호출에서 search_rag 감지 → analysis loop (검색만) → editing call (편집만)
            # previous_response_id: CVD 재전송 없이 이전 응답 컨텍스트 재사용
            # ===================================================================
            if _pending_initial_rag is not None and _has_rag and use_delta and not self._cancelled:
                try:
                    _is_codex = getattr(self, 'codex_mode', False)
                    _MAX_RAG_ROUNDS = 5 if _is_codex else 3
                    # Codex: previous_response_id 미지원 → 항상 fallback 사용
                    _last_analysis_resp_id: Any = None if _is_codex else response_id
                    # fallback conversation (previous_response_id 미지원 시 사용)
                    _rag_conv_fb: List[Dict[str, Any]] = [
                        {"type": "message", "role": "user", "content": user_message}
                    ]

                    # 분석 도구 목록: Codex는 read_file 추가 + description은 grep 안내
                    _analysis_tool_defs = _build_search_rag_tool(codex_mode=_is_codex) + build_v711_analysis_tools()
                    if _is_codex:
                        _analysis_tool_defs += _build_read_file_tool()

                    # pending tool calls: [(name, call_id, args), ...]
                    _pending_tool_calls: List[Tuple[str, str, str]] = [
                        ("search_rag", _pending_initial_rag[0], _pending_initial_rag[1])
                    ]

                    # ── analysis loop ─────────────────────────────────────────
                    for _rag_round in range(_MAX_RAG_ROUNDS):
                        if not _pending_tool_calls:
                            break

                        # ── 대기 중인 도구 실행 ──────────────────────────────
                        _round_results: List[Tuple[str, str, str, str]] = []  # (name, cid, args, result)
                        for _tc_name, _tc_cid, _tc_args in _pending_tool_calls:
                            if _tc_name == "search_rag":
                                _parsed = _safe_json_loads(_tc_args)
                                _queries = _parsed.get("queries", [])
                                if not _queries and _parsed.get("query"):
                                    _queries = [_parsed.get("query")]
                                _file = _parsed.get("file_name")
                                debug(f"[RAG/analysis] round {_rag_round + 1}/{_MAX_RAG_ROUNDS}: queries={_queries}, file={_file}")
                                _result = _execute_search_rag(
                                    _queries, _file, on_file_search,
                                    cancel_check=lambda: self._cancelled,
                                )
                            elif _tc_name == "read_file":
                                _parsed = _safe_json_loads(_tc_args)
                                debug(f"[RAG/analysis] round {_rag_round + 1}/{_MAX_RAG_ROUNDS}: read_file={_parsed.get('file_name')}, offset={_parsed.get('offset')}")
                                _result = _execute_read_file(
                                    file_name=_parsed.get("file_name", ""),
                                    offset=_parsed.get("offset", 0),
                                    length=_parsed.get("length", 1000),
                                    on_file_search=on_file_search,
                                )
                            else:
                                continue
                            if self._cancelled:
                                debug("[STREAMING] 취소됨 - RAG 분석 중단")
                                return StreamingResult(
                                    success=False,
                                    commands_executed=commands_count,
                                    messages=messages_collected,
                                    token_usage=token_usage,
                                    status="cancelled",
                                    error="요청이 취소되었습니다",
                                )
                            _round_results.append((_tc_name, _tc_cid, _tc_args, _result))

                        _pending_tool_calls = []

                        _analysis_instr = (
                            "추가 검색이 필요하면 search_rag를 재호출하세요. "
                            + ("스니펫이 잘린 경우 read_file로 이어 읽으세요. " if _is_codex else "")
                            + "충분한 정보를 얻었으면 thinking만 호출하여 분석을 완료하세요."
                        )

                        # ── fallback conversation 갱신 ─────────────────────
                        if _is_codex:
                            # Codex: 모든 도구 결과를 user message로 전달
                            _combined = "\n\n".join(
                                f"<{'SEARCH_RESULT' if n == 'search_rag' else 'READ_RESULT'}>\n{r}\n</{'SEARCH_RESULT' if n == 'search_rag' else 'READ_RESULT'}>"
                                for n, _, _, r in _round_results
                            )
                            _rag_conv_fb.append({"type": "message", "role": "user", "content": f"{_combined}\n\n{_analysis_instr}"})
                        else:
                            for _tc_name, _tc_cid, _tc_args, _tc_result in _round_results:
                                _rag_conv_fb.append({"type": "function_call", "call_id": _tc_cid, "name": _tc_name, "arguments": _tc_args})
                                _rag_conv_fb.append({"type": "function_call_output", "call_id": _tc_cid, "output": _tc_result})
                            _rag_conv_fb.append({"type": "message", "role": "user", "content": _analysis_instr})

                        # ── API 파라미터 구성 ──────────────────────────────
                        if _last_analysis_resp_id:
                            _a_params: Dict[str, Any] = {
                                "model": self.model,
                                "instructions": system_prompt,
                                "previous_response_id": _last_analysis_resp_id,
                                "input": [
                                    *_analysis_pending_tool_outputs,
                                    *[{"type": "function_call_output", "call_id": c, "output": r}
                                      for _, c, _, r in _round_results],
                                    {"type": "message", "role": "user", "content": _analysis_instr},
                                ],
                                "stream": True,
                                "max_output_tokens": 16384,
                                "tools": _analysis_tool_defs,
                                "tool_choice": "auto",
                            }
                        else:
                            _a_params = {
                                "model": self.model,
                                "instructions": system_prompt,
                                "input": list(_rag_conv_fb),
                                "stream": True,
                                "max_output_tokens": 16384,
                                "tools": _analysis_tool_defs,
                                "tool_choice": "auto",
                            }

                        _analysis_pending_tool_outputs = []
                        _analysis_next_resp_id = None

                        try:
                            self._apply_codex_overrides(_a_params)
                            _a_fu = self.client.responses.create(**_a_params)
                        except Exception as _ae:
                            debug(f"[RAG/analysis] API error (prev_resp_id?): {_ae}")
                            _a_params.pop("previous_response_id", None)
                            _a_params["input"] = list(_rag_conv_fb)
                            self._apply_codex_overrides(_a_params)
                            _a_fu = self.client.responses.create(**_a_params)

                        # ── 분석 응답 스트리밍 처리 ────────────────────────
                        for _af in _iter_stream_events(_a_fu):
                            if self._cancelled:
                                break
                            _aft = getattr(_af, 'type', None)

                            if _aft == "response.completed":
                                if hasattr(_af, 'response'):
                                    _analysis_next_resp_id = getattr(_af.response, 'id', None)
                                    if hasattr(_af.response, 'usage'):
                                        _au = _af.response.usage
                                        token_usage = {
                                            "input": getattr(_au, 'input_tokens', 0),
                                            "output": getattr(_au, 'output_tokens', 0),
                                            "total": getattr(_au, 'total_tokens', 0),
                                        }
                                continue

                            if _aft == "response.function_call_arguments.delta":
                                _af_cid = getattr(_af, 'call_id', None) or getattr(_af, 'item_id', None)
                                _af_dlt = getattr(_af, 'delta', None)
                                _af_adelta = getattr(_af_dlt, 'arguments', '') if _af_dlt else ''
                                _af_tidx = None
                                for _ti, _ts in tool_calls_state.items():
                                    if _ts.get("id") == _af_cid:
                                        _af_tidx = _ti
                                        break
                                if _af_tidx is None:
                                    _af_tidx = len(tool_calls_state)
                                    tool_calls_state[_af_tidx] = {"id": _af_cid, "name": None, "args": ""}
                                if _af_adelta:
                                    tool_calls_state[_af_tidx]["args"] += _af_adelta
                                continue

                            if _aft == "response.output_item.added":
                                _af_item = getattr(_af, 'item', None)
                                if _af_item and getattr(_af_item, 'type', None) == "function_call":
                                    _af_cid = getattr(_af_item, 'call_id', None) or getattr(_af_item, 'id', None)
                                    _af_name = getattr(_af_item, 'name', None)
                                    _af_tidx = len(tool_calls_state)
                                    tool_calls_state[_af_tidx] = {"id": _af_cid, "name": _af_name, "args": ""}
                                continue

                            if _aft == "response.output_item.done":
                                _af_item = getattr(_af, 'item', None)
                                if _af_item and getattr(_af_item, 'type', None) == "function_call":
                                    _af_cid = getattr(_af_item, 'call_id', None) or getattr(_af_item, 'id', None)
                                    _af_name = getattr(_af_item, 'name', None)
                                    _af_args = getattr(_af_item, 'arguments', '{}')
                                    _af_name, _af_args = _resolve_tool_call_payload(_af_cid, _af_name, _af_args)
                                    if _af_name in ("search_rag", "read_file"):
                                        _pending_tool_calls.append((_af_name, _af_cid, _af_args))
                                    elif _af_name == "thinking":
                                        # analysis thinking: 사용자에게 노출 안 함
                                        if not _is_codex:
                                            _analysis_pending_tool_outputs.append(
                                                {"type": "function_call_output", "call_id": _af_cid, "output": ""}
                                            )
                                            _rag_conv_fb.append({"type": "function_call", "call_id": _af_cid, "name": "thinking", "arguments": _af_args})
                                            _rag_conv_fb.append({"type": "function_call_output", "call_id": _af_cid, "output": ""})

                        # Codex: previous_response_id 미지원 → 절대 갱신 안 함
                        if not _is_codex:
                            _last_analysis_resp_id = _analysis_next_resp_id or _last_analysis_resp_id

                        if not _pending_tool_calls or self._cancelled:
                            debug(f"[RAG/analysis] round {_rag_round + 1} 후 루프 종료: {'cancelled' if self._cancelled else 'thinking_only (분석 완료 신호)'}")
                            break
                        debug(f"[RAG/analysis] next round: {_rag_round + 2}")

                    # max rounds 소진 후 pending_tool_calls가 남아있으면 previous_response_id 사용 불가
                    if _pending_tool_calls:
                        _last_analysis_resp_id = None

                    # ── editing call (단일 호출, search_rag 없음) ──────────────
                    if not self._cancelled:
                        _edit_instr = (
                            "위 검색 결과를 바탕으로 편집을 진행하세요. "
                            "execute_edits의 operations 배열과 message 필드를 반드시 포함해야 합니다."
                        )
                        _rag_conv_fb.append({"type": "message", "role": "user", "content": _edit_instr})

                        if _last_analysis_resp_id:
                            _e_params: Dict[str, Any] = {
                                "model": self.model,
                                "instructions": editing_prompt,
                                "previous_response_id": _last_analysis_resp_id,
                                "input": [
                                    *_analysis_pending_tool_outputs,
                                    {"type": "message", "role": "user", "content": _edit_instr},
                                ],
                                "stream": True,
                                "max_output_tokens": 32768,
                                "tools": batch_tools,
                                "tool_choice": "required",
                            }
                        else:
                            _e_params = {
                                "model": self.model,
                                "instructions": editing_prompt,
                                "input": list(_rag_conv_fb),
                                "stream": True,
                                "max_output_tokens": 32768,
                                "tools": batch_tools,
                                "tool_choice": "required",
                            }

                        try:
                            self._apply_codex_overrides(_e_params)
                            _e_fu = self.client.responses.create(**_e_params)
                        except Exception as _ee:
                            debug(f"[RAG/editing] API error (prev_resp_id?): {_ee}")
                            _e_params.pop("previous_response_id", None)
                            _e_params["input"] = list(_rag_conv_fb)
                            self._apply_codex_overrides(_e_params)
                            _e_fu = self.client.responses.create(**_e_params)

                        for _ef in _iter_stream_events(_e_fu):
                            if self._cancelled:
                                break
                            _eft = getattr(_ef, 'type', None)

                            if _eft == "response.function_call_arguments.delta":
                                _ef_cid = getattr(_ef, 'call_id', None) or getattr(_ef, 'item_id', None)
                                _ef_dlt = getattr(_ef, 'delta', None)
                                _ef_adelta = getattr(_ef_dlt, 'arguments', '') if _ef_dlt else ''
                                _ef_tidx = None
                                for _ti, _ts in tool_calls_state.items():
                                    if _ts.get("id") == _ef_cid:
                                        _ef_tidx = _ti
                                        break
                                if _ef_tidx is None:
                                    _ef_tidx = len(tool_calls_state)
                                    tool_calls_state[_ef_tidx] = {"id": _ef_cid, "name": None, "args": ""}
                                if _ef_adelta:
                                    tool_calls_state[_ef_tidx]["args"] += _ef_adelta
                                continue

                            if _eft == "response.output_item.added":
                                _ef_item = getattr(_ef, 'item', None)
                                if _ef_item and getattr(_ef_item, 'type', None) == "function_call":
                                    _ef_cid = getattr(_ef_item, 'call_id', None) or getattr(_ef_item, 'id', None)
                                    _ef_name = getattr(_ef_item, 'name', None)
                                    _ef_tidx = len(tool_calls_state)
                                    tool_calls_state[_ef_tidx] = {"id": _ef_cid, "name": _ef_name, "args": ""}
                                continue

                            if _eft == "response.output_item.done":
                                _ef_item = getattr(_ef, 'item', None)
                                if _ef_item and getattr(_ef_item, 'type', None) == "function_call":
                                    _ef_cid = getattr(_ef_item, 'call_id', None) or getattr(_ef_item, 'id', None)
                                    _ef_name = getattr(_ef_item, 'name', None)
                                    _ef_args = getattr(_ef_item, 'arguments', '{}')
                                    _ef_name, _ef_args = _resolve_tool_call_payload(_ef_cid, _ef_name, _ef_args)
                                    if _ef_name:
                                        _emit_tool_command(_ef_name, _ef_args)
                                else:
                                    _handle_output_item_done(_ef_item)

                            elif _eft == "response.output_text.delta":
                                _ef_dlt_obj = getattr(_ef, 'delta', None)
                                if _ef_dlt_obj:
                                    _ef_txt = _safe_get(_ef_dlt_obj, 'text') or _safe_get(_ef_dlt_obj, 'output_text') or ''
                                    if _ef_txt:
                                        output_text_delta_seen = True
                                        _handle_output_delta(_ef_txt)

                            elif _eft == "response.output_text.done":
                                if output_text_delta_seen or output_text_done_seen:
                                    continue
                                _ef_txt = _safe_get(_ef, 'text')
                                if not _ef_txt:
                                    _ef_dlt_obj = getattr(_ef, 'delta', None)
                                    if _ef_dlt_obj:
                                        _ef_txt = _safe_get(_ef_dlt_obj, 'text') or _safe_get(_ef_dlt_obj, 'output_text')
                                if _ef_txt:
                                    _handle_output_delta(_ef_txt)
                                    output_text_done_seen = True

                            elif _eft == "response.completed":
                                if hasattr(_ef, 'response') and hasattr(_ef.response, 'usage'):
                                    _eu = _ef.response.usage
                                    token_usage = {
                                        "input": getattr(_eu, 'input_tokens', 0),
                                        "output": getattr(_eu, 'output_tokens', 0),
                                        "total": getattr(_eu, 'total_tokens', 0),
                                    }

                except Exception as e:
                    debug(f"[RAG] analysis/editing pipeline error: {e}")

            if deferred_message_cmd is not None and not thinking_emitted:
                _emit_auto_thinking("auto_injected_missing_first_thinking_before_final_message")

            if deferred_message_cmd is not None:
                raw_msg = deferred_message_cmd.message or deferred_message_cmd.content
                normalized_final_message = _normalize_message_text(raw_msg)
                if normalized_final_message:
                    deferred_message_cmd.message = normalized_final_message
                else:
                    debug(f"[STREAMING] deferred message normalized to empty, raw={repr(raw_msg)[:200]}")
                    deferred_message_cmd = None

            if deferred_message_cmd is None and use_delta and not self._cancelled:
                debug("[STREAMING] message tool 미호출 — LLM이 message를 생성하지 않음")

            if not thinking_emitted:
                _emit_auto_thinking("auto_injected_missing_first_thinking_before_final_message")

            if deferred_message_cmd is not None:
                _deliver_command(deferred_message_cmd)

            debug(
                f"LLM 스트리밍 완료: {commands_count}개 명령(편집 {edit_commands_count}개), {len(messages_collected)}개 메시지"
            )

            # LLM 응답 (full_response) 로깅
            llm_response_log_file = get_log_dir() + f"/llm_response_{timestamp}.txt"
            with open(llm_response_log_file, 'w', encoding='utf-8') as f:
                f.write("=== LLM 응답 (Full Response) ===\n")
                f.write(f"생성 시간: {datetime.now().isoformat()}\n")
                f.write(f"모드: {mode_str}\n")
                f.write(f"명령 개수: {commands_count}\n")
                f.write(f"편집 명령 개수: {edit_commands_count}\n")
                f.write(f"메시지 개수: {len(messages_collected)}\n")
                f.write(f"응답 길이: {len(full_response)} chars\n\n")
                f.write(full_response)
            debug(f"LLM 응답 저장: {llm_response_log_file}")

            # Fallback: token_usage가 비어있으면 response_id로 재조회
            if token_usage["total"] == 0 and response_id:
                debug(f"[STREAMING] token_usage 비어있음, response_id로 재조회: {response_id}")
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=self.api_key)
                    retrieved = client.responses.retrieve(response_id)

                    if hasattr(retrieved, 'usage') and retrieved.usage:
                        u = retrieved.usage
                        token_usage = {
                            "input": getattr(u, 'input_tokens', 0),
                            "output": getattr(u, 'output_tokens', 0),
                            "total": getattr(u, 'total_tokens', 0)
                        }
                        debug(f"[STREAMING] 재조회 성공: {token_usage}")
                    else:
                        debug(f"[STREAMING] 재조회했으나 usage 없음")
                except Exception as e:
                    debug(f"[STREAMING] 재조회 실패: {e}")

            # 토큰 사용량 및 비용 로깅
            debug(f"[STREAMING] token_usage: {token_usage}, model: {self.model}")
            if token_usage and token_usage["total"] > 0:
                from utils.logger import log_token_usage
                usage_info = log_token_usage(
                    token_usage=token_usage,
                    prompt=prompt,
                    context_info=f"{mode_str} 모드, HTML 길이: {len(html)} chars",
                    model=self.model
                )
                debug(f"[STREAMING] usage_info: {usage_info}")
                # cost와 model 정보를 token_usage에 추가 (Frontend 사용량 추적용)
                if usage_info and "request_cost_usd" in usage_info:
                    token_usage["cost"] = usage_info["request_cost_usd"]
                    token_usage["model"] = usage_info.get("model", self.model)
                    debug(f"[STREAMING] cost 추가: ${token_usage['cost']}, model: {token_usage['model']}")
            else:
                debug(f"[STREAMING] token_usage 비어있음 - log_token_usage 호출 안 함")

            # 로깅
            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw={
                    "streaming": True,
                    "full_response": full_response[:500] if full_response else "",
                    "commands_count": commands_count,
                    "messages_count": len(messages_collected)
                },
                commands=[],  # 스트리밍에서는 개별 로깅
                success=True
            )

            return StreamingResult(
                success=(status == "completed"),
                commands_executed=commands_count,
                messages=messages_collected,
                token_usage=token_usage,
                status=status
            )

        except httpx.TimeoutException as e:
            error_msg = f"LLM 응답 타임아웃 ({self.timeout}초 초과)"
            debug(error_msg)

            # 에러 응답 로깅
            error_log_file = get_log_dir() + f"/error_{timestamp}.txt"
            with open(error_log_file, 'w', encoding='utf-8') as f:
                f.write("=== LLM 에러 응답 ===\n")
                f.write(f"에러 시간: {datetime.now().isoformat()}\n")
                f.write(f"에러 타입: TimeoutException\n")
                f.write(f"에러 메시지: {error_msg}\n")
                f.write(f"타임아웃: {self.timeout}초\n\n")
                f.write(str(e))
            debug(f"에러 로그 저장: {error_log_file}")

            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw={"error": "timeout", "timeout_seconds": self.timeout},
                commands=[],
                success=False,
                error=error_msg
            )

            return StreamingResult(
                success=False,
                commands_executed=commands_count,
                messages=messages_collected,
                token_usage=token_usage,
                status="timeout",
                error=error_msg
            )

        except httpx.ConnectError as e:
            error_msg = f"LLM 서버 연결 실패: {str(e)}"
            debug(error_msg)

            # 에러 응답 로깅
            error_log_file = get_log_dir() + f"/error_{timestamp}.txt"
            with open(error_log_file, 'w', encoding='utf-8') as f:
                f.write("=== LLM 에러 응답 ===\n")
                f.write(f"에러 시간: {datetime.now().isoformat()}\n")
                f.write(f"에러 타입: ConnectError\n")
                f.write(f"에러 메시지: {error_msg}\n\n")
                f.write(str(e))
            debug(f"에러 로그 저장: {error_log_file}")

            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw={"error": "connection_failed"},
                commands=[],
                success=False,
                error=error_msg
            )

            return StreamingResult(
                success=False,
                commands_executed=commands_count,
                messages=messages_collected,
                token_usage=token_usage,
                status="connection_failed",
                error=error_msg
            )

        except Exception as e:
            error_msg = f"LLM 스트리밍 실패: {str(e)}"
            debug(error_msg)

            # 에러 응답 로깅
            error_log_file = get_log_dir() + f"/error_{timestamp}.txt"
            with open(error_log_file, 'w', encoding='utf-8') as f:
                f.write("=== LLM 에러 응답 ===\n")
                f.write(f"에러 시간: {datetime.now().isoformat()}\n")
                f.write(f"에러 타입: {type(e).__name__}\n")
                f.write(f"에러 메시지: {error_msg}\n\n")
                f.write(str(e))
                import traceback
                f.write("\n\n=== 트레이스백 ===\n")
                f.write(traceback.format_exc())
            debug(f"에러 로그 저장: {error_log_file}")

            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw={"error": str(e)},
                commands=[],
                success=False,
                error=error_msg
            )

            return StreamingResult(
                success=False,
                commands_executed=commands_count,
                messages=messages_collected,
                token_usage=token_usage,
                status="error",
                error=error_msg
            )

# 싱글톤 인스턴스
_streaming_client: Optional[OpenAIStreamingClient] = None


def get_streaming_client(
    api_key: Optional[str] = None,
    codex_mode: bool = False,
    codex_account_id: str = "",
) -> OpenAIStreamingClient:
    """스트리밍 클라이언트 싱글톤 반환"""
    global _streaming_client
    if _streaming_client is None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY_REQUIRED")
        _streaming_client = OpenAIStreamingClient(
            api_key=api_key,
            codex_mode=codex_mode,
            codex_account_id=codex_account_id,
        )
    elif api_key:
        _streaming_client.set_api_key(api_key, codex_mode=codex_mode, codex_account_id=codex_account_id)
    return _streaming_client

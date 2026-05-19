# -*- coding: utf-8 -*-
"""
Local File Reader Service

OpenAI Vector Stores 대신 로컬 파일을 직접 읽어 RAG에 활용.
- LlamaIndex SimpleDirectoryReader: PDF/DOCX/XLSX/PPTX/TXT/MD
- pyhwpx: HWP/HWPX (COM 기반)
- Grep+Read 패턴: search() → start_char/end_char 반환, read_chunk()로 추가 구간 읽기
"""

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

DB_FILENAME = "inserty.db"
LEGACY_DB_FILENAME = "mallo.db"
MAX_CHARS_PER_FILE = 80_000      # 필요 시 파일 전문 주입 상한 (~25K tokens)
CONTEXT_WINDOW = 300             # 키워드 주변 ± 문자 수
MAX_MATCHES_PER_QUERY = 5        # 쿼리당 최대 매치 수
MAX_TOTAL_MATCHES = 10           # 전체 최대 매치 수
MAX_READ_LENGTH = 2_000          # read_chunk 최대 읽기 문자 수
DEFAULT_READ_LENGTH = 1_000      # read_chunk 기본 읽기 문자 수

# 지원 확장자
HWP_EXTS = {".hwp", ".hwpx"}
LLAMA_EXTS = {".pdf", ".docx", ".xlsx", ".xls", ".xlsm", ".pptx", ".txt", ".md"}


class LocalFileReaderService:
    """로컬 파일 직접 읽기 기반 RAG 서비스 (LlamaIndex SDR + pyhwpx)."""

    def __init__(self, user_data_path: str):
        self.user_data_path = user_data_path

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_db_path(self) -> Optional[str]:
        db_path = os.path.join(self.user_data_path, DB_FILENAME)
        if os.path.exists(db_path):
            return db_path
        legacy = os.path.join(self.user_data_path, LEGACY_DB_FILENAME)
        if os.path.exists(legacy):
            return legacy
        return db_path  # 없어도 경로 반환 (에러는 connect 시 발생)

    def _connect_db(self) -> sqlite3.Connection:
        db_path = self._get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _save_text_length(self, file_id: str, text_length: int) -> None:
        """추출된 텍스트 길이를 DB files.text_length에 저장."""
        try:
            conn = self._connect_db()
            conn.execute(
                "UPDATE files SET text_length = ? WHERE id = ? AND text_length IS NULL",
                (text_length, file_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _build_abs_path(self, scope: str, project_id: Optional[str],
                        chat_id: Optional[str], rel_path: str) -> str:
        """DB rel_path → 절대경로 변환.

        Project: {userData}/projects/{project_id}/{rel_path}
        Chat:    {userData}/chat_files/{chat_id}/{rel_path}
        """
        if scope == "chat" and chat_id:
            return os.path.join(self.user_data_path, "chat_files", chat_id, rel_path)
        if project_id:
            return os.path.join(self.user_data_path, "projects", project_id, rel_path)
        return rel_path

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    def get_available_files(self, project_id: str,
                            chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """DB files 테이블에서 접근 가능한 파일 목록 반환.

        동일 파일명이 여러 개 존재할 경우 display_name으로 자동 구분:
        - 유일한 이름 → display_name = file_name (기존과 동일)
        - 스코프 달라 중복 → "[project] report.pdf" / "[chat] report.pdf"
        - 같은 스코프에서 중복 → "[project] report.pdf" / "[project] report.pdf (2)"

        Returns:
            [{"file_id", "file_name", "display_name", "scope", "abs_path", "extension"}, ...]
        """
        try:
            conn = self._connect_db()
            rows: list
            if chat_id:
                rows = conn.execute(
                    """SELECT id, scope, project_id, chat_id, name, rel_path, extension
                       FROM files
                       WHERE (scope='project' AND project_id=?)
                          OR (scope='chat' AND chat_id=?)
                       ORDER BY added_at""",
                    (project_id, chat_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, scope, project_id, chat_id, name, rel_path, extension
                       FROM files
                       WHERE scope='project' AND project_id=?
                       ORDER BY added_at""",
                    (project_id,),
                ).fetchall()
            conn.close()
        except Exception:
            return []

        # 1패스: abs_path 존재 확인 후 후보 목록 구성
        candidates = []
        for row in rows:
            abs_path = self._build_abs_path(
                row["scope"], row["project_id"], row["chat_id"], row["rel_path"]
            )
            if not os.path.exists(abs_path):
                continue
            ext = row["extension"] or os.path.splitext(row["name"])[1].lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            candidates.append({
                "file_id": row["id"],
                "file_name": row["name"],
                "scope": row["scope"],
                "abs_path": abs_path,
                "extension": ext.lower(),
            })

        # 2패스: 중복 파일명에 display_name 부여
        name_counts = Counter(c["file_name"] for c in candidates)
        scope_name_counter: Dict[tuple, int] = defaultdict(int)

        result = []
        for c in candidates:
            fname = c["file_name"]
            if name_counts[fname] == 1:
                display_name = fname
            else:
                key = (c["scope"], fname)
                scope_name_counter[key] += 1
                idx = scope_name_counter[key]
                display_name = (
                    f"[{c['scope']}] {fname}"
                    if idx == 1
                    else f"[{c['scope']}] {fname} ({idx})"
                )
            result.append({**c, "display_name": display_name})

        return result

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def read_file_text(self, abs_path: str) -> str:
        """파일을 plain text로 변환 (최대 MAX_CHARS_PER_FILE 문자).

        - HWP/HWPX: 업로드 시 메인 프로세스에서 미리 추출한 .extracted.txt 캐시 우선 사용.
          캐시 없으면 LlamaIndex(HWPX) 또는 subprocess pyhwpx(HWP) fallback.
        - 기타: LlamaIndex SimpleDirectoryReader
        """
        import sys
        ext = os.path.splitext(abs_path)[1].lower()
        try:
            # HWP/HWPX: 캐시 우선 (업로드 시 메인 프로세스에서 추출해둔 텍스트)
            if ext in HWP_EXTS:
                cached = self._read_extracted_cache(abs_path)
                if cached:
                    print(f"[LocalFileReader] cache hit: {len(cached)} chars for {os.path.basename(abs_path)}", file=sys.stderr)
                    return cached[:MAX_CHARS_PER_FILE]
                print(f"[LocalFileReader] cache miss for {os.path.basename(abs_path)}, trying fallback", file=sys.stderr)
                # 캐시 없으면 fallback
                if ext == ".hwpx":
                    text = self._read_hwpx_llamaindex(abs_path)
                else:
                    text = self._read_hwp_subprocess(abs_path)
            else:
                text = self._read_via_llamaindex(abs_path)
            print(f"[LocalFileReader] extracted {len(text)} chars for {os.path.basename(abs_path)}", file=sys.stderr)
            return text[:MAX_CHARS_PER_FILE]
        except Exception as exc:
            print(f"[LocalFileReader] ERROR reading {os.path.basename(abs_path)}: {exc}", file=sys.stderr)
            return f"[파일 읽기 오류: {exc}]"

    @staticmethod
    def _read_extracted_cache(abs_path: str) -> Optional[str]:
        """업로드 시 미리 추출된 .extracted.txt 캐시 읽기 (파일 잠금 대비 재시도)."""
        import time
        cache_path = abs_path + ".extracted.txt"
        if not os.path.exists(cache_path):
            return None
        for attempt in range(3):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return f.read()
            except (PermissionError, OSError):
                if attempt < 2:
                    time.sleep(0.3)
            except Exception:
                break
        return None

    def _read_hwpx_llamaindex(self, abs_path: str) -> str:
        """HWPX 파일을 LlamaIndex HWPReader로 추출 (COM 불필요)."""
        try:
            from llama_index.core import SimpleDirectoryReader
            docs = SimpleDirectoryReader(input_files=[abs_path]).load_data()
            return "\n\n".join(doc.text for doc in docs if doc.text)
        except Exception:
            # LlamaIndex가 HWPX를 지원하지 않으면 서브프로세스 pyhwpx로 폴백
            return self._read_hwp_subprocess(abs_path)

    def _read_hwp_subprocess(self, abs_path: str) -> str:
        """HWP/HWPX 텍스트를 서브프로세스 pyhwpx로 추출 (45초 타임아웃).

        에이전트 프로세스에서 pyhwpx.Hwp(visible=False)를 직접 호출하면
        COM 초기화 문제로 행(hang)이 발생하므로 별도 서브프로세스에서 실행.

        GetTextFile은 한글 인코딩이 깨지므로 SaveAs → CP949 파일 읽기 방식 사용.
        추출 성공 시 .extracted.txt 캐시도 생성하여 다음 호출부터 빠르게 읽음.
        """
        import subprocess
        import sys
        import tempfile
        tmp_txt = tempfile.mktemp(suffix=".txt")
        script = (
            "import sys, pyhwpx, os\n"
            "hwp = pyhwpx.Hwp(visible=False)\n"
            "try:\n"
            f"    hwp.Open({repr(abs_path)})\n"
            f"    hwp.SaveAs({repr(tmp_txt)}, 'TEXT')\n"
            "finally:\n"
            "    try: hwp.Quit()\n"
            "    except: pass\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                timeout=45,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            if result.returncode != 0 or not os.path.exists(tmp_txt):
                return ""
            # SaveAs TEXT는 CP949 인코딩으로 저장됨
            text = ""
            for enc in ("cp949", "utf-8", "euc-kr", "utf-16"):
                try:
                    with open(tmp_txt, "r", encoding=enc) as f:
                        text = f.read()
                    if any(0xAC00 <= ord(c) <= 0xD7A3 for c in text[:500]):
                        break  # 한글 정상 감지
                except Exception:
                    continue
            # 캐시 생성: 다음 호출부터 빠르게 읽기
            if text:
                cache_path = abs_path + ".extracted.txt"
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception:
                    pass
            return text
        except subprocess.TimeoutExpired:
            return "[HWP 파일 읽기 시간 초과 (45초) - 파일을 PDF 또는 DOCX로 변환 후 재업로드 권장]"
        except Exception as exc:
            return f"[HWP 파일 읽기 오류: {exc}]"
        finally:
            try:
                os.unlink(tmp_txt)
            except Exception:
                pass

    def _read_via_llamaindex(self, abs_path: str) -> str:
        """LlamaIndex SimpleDirectoryReader 기반 텍스트 추출."""
        try:
            from llama_index.core import SimpleDirectoryReader
        except ImportError:
            return self._read_hwp_subprocess(abs_path)
        docs = SimpleDirectoryReader(input_files=[abs_path]).load_data()
        return "\n\n".join(doc.text for doc in docs if doc.text)

    # ------------------------------------------------------------------
    # Keyword search (file_name 미지정)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_compact_text_with_index(text: str) -> tuple[str, List[int]]:
        compact_chars: List[str] = []
        index_map: List[int] = []
        for idx, ch in enumerate(text or ""):
            if ch.isspace():
                continue
            compact_chars.append(ch)
            index_map.append(idx)
        return "".join(compact_chars), index_map

    @staticmethod
    def _compact_query(query: str) -> str:
        return "".join(ch for ch in (query or "") if not ch.isspace())

    def _extract_matches_compact(
        self,
        query: str,
        text: str,
        file_info: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        compact_query = self._compact_query(query)
        if not compact_query:
            return []
        compact_text, index_map = self._build_compact_text_with_index(text)
        if not compact_text or not index_map:
            return []

        matches: List[Dict[str, Any]] = []
        total_len = len(text)
        start_at = 0
        while len(matches) < MAX_MATCHES_PER_QUERY:
            found_at = compact_text.find(compact_query, start_at)
            if found_at == -1:
                break
            found_end = found_at + len(compact_query) - 1
            raw_start = index_map[found_at]
            raw_end = index_map[found_end] + 1
            start = max(0, raw_start - CONTEXT_WINDOW)
            end = min(total_len, raw_end + CONTEXT_WINDOW)
            snippet = text[start:end].strip()
            prefix = f"[...앞쪽 {start:,}자 생략...]" if start > 0 else ""
            chars_after = total_len - end
            suffix = (
                f"[...뒤쪽 {chars_after:,}자 생략 — "
                f"read_file(offset={end})로 이어 읽기...]"
            ) if chars_after > 300 else ""
            content = f"{prefix}\n{snippet}\n{suffix}".strip() if (prefix or suffix) else snippet
            matches.append({
                "query": query,
                "scope": file_info["scope"],
                "content": content,
                "source": file_info["display_name"],
                "start_char": start,
                "end_char": end,
                "total_chars": total_len,
                "truncated_before": start > 0,
                "truncated_after": chars_after > 300,
            })
            start_at = found_at + max(1, len(compact_query))
        return matches

    @staticmethod
    def _split_query_keywords(query: str) -> List[str]:
        """복합 쿼리를 개별 키워드로 분리.

        '창업아이템명 및 산출물' → ['창업아이템명', '산출물']
        '1단계 정부지원사업비 집행계획' → ['1단계 정부지원사업비 집행계획'] (분리 불필요)
        """
        # 접속사/구분자로 분리
        import re as _re
        parts = _re.split(r'\s+(?:및|와|과|또는|그리고|,)\s+|\s*[,/·]\s*', query)
        keywords = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]
        return keywords if keywords else [query]

    @staticmethod
    def _compile_query(query: str) -> "re.Pattern":
        """쿼리를 정규식 패턴으로 컴파일.

        AI가 정규식 의도로 보낸 경우(예: '대표자|대표\\s*자') 그대로 사용.
        컴파일 실패 시 literal(re.escape) 폴백 — 기존 동작 보장.
        """
        try:
            return re.compile(query, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(query), re.IGNORECASE)

    def _collect_pattern_matches(
        self,
        pattern: "re.Pattern",
        query: str,
        text: str,
        file_info: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> None:
        """주어진 패턴으로 finditer 매칭하여 matches 리스트에 컨텍스트 스니펫 누적."""
        total_len = len(text)
        for m in pattern.finditer(text):
            if len(matches) >= MAX_MATCHES_PER_QUERY:
                return
            start = max(0, m.start() - CONTEXT_WINDOW)
            end = min(total_len, m.end() + CONTEXT_WINDOW)
            snippet = text[start:end].strip()
            prefix = f"[...앞쪽 {start:,}자 생략...]" if start > 0 else ""
            chars_after = total_len - end
            suffix = (
                f"[...뒤쪽 {chars_after:,}자 생략 — "
                f"read_file(offset={end})로 이어 읽기...]"
            ) if chars_after > 300 else ""
            content = f"{prefix}\n{snippet}\n{suffix}".strip() if (prefix or suffix) else snippet
            matches.append({
                "query": query,
                "scope": file_info["scope"],
                "content": content,
                "source": file_info["display_name"],
                "start_char": start,
                "end_char": end,
                "total_chars": total_len,
                "truncated_before": start > 0,
                "truncated_after": chars_after > 300,
            })

    def _extract_matches(self, query: str, text: str,
                         file_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """텍스트에서 쿼리 키워드가 포함된 단락(±CONTEXT_WINDOW 문자) 추출.

        검색 전략 (Claude grep 방식):
        1) 쿼리를 정규식으로 직접 컴파일 시도 (alternation `|`, `\\s*` 등 지원)
        2) 키워드 분리 후 OR 결합한 단일 패턴으로 한 번에 매칭
        3) 그래도 0건이면 compact 검색(공백 무시 매칭)으로 폴백

        스니펫이 잘린 경우 앞/뒤 잘림 마커를 삽입하여
        LLM이 read_file로 이어 읽도록 유도한다.
        """
        matches: List[Dict[str, Any]] = []

        # 1) 전체 쿼리를 정규식으로 시도 (정규식 의도 또는 단일 literal)
        primary = self._compile_query(query)
        self._collect_pattern_matches(primary, query, text, file_info, matches)
        if matches:
            return matches

        # 2) 키워드 분리 후 OR 결합 (Claude의 `(a|b|c)` 패턴)
        keywords = self._split_query_keywords(query)
        if len(keywords) > 1:
            alt = "|".join(re.escape(kw) for kw in keywords)
            try:
                or_pattern = re.compile(alt, re.IGNORECASE)
            except re.error:
                or_pattern = None
            if or_pattern is not None:
                self._collect_pattern_matches(or_pattern, query, text, file_info, matches)
            if matches:
                return matches

        # 3) compact 검색 폴백 (공백 제거 후 매칭 — 표/줄바꿈으로 단어가 쪼개진 경우)
        return self._extract_matches_compact(query, text, file_info)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        queries: List[str],
        project_id: str,
        chat_id: Optional[str] = None,
        file_name: Optional[str] = None,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """search_file 엔트리포인트.

        file_name 지정 → 해당 파일 안에서만 키워드 검색, 단락 수준 스니펫 반환
        file_name 미지정 → 전체 파일에서 키워드 검색, 단락 수준 스니펫 반환

        반환 형식:
        {
            "success": bool,
            "results": [
                {
                    "query": str,
                    "scope": str,
                    "content": str,
                    "source": str,
                    "start_char": int,
                    "end_char": int
                }, ...
            ]
        }
        """
        files = self.get_available_files(project_id, chat_id)
        if not files:
            return {"success": True, "results": [], "message": "참조 파일이 없습니다"}

        # ── file_name 지정: 해당 파일 안에서 키워드 검색 ──────────────────
        if file_name:
            # display_name으로 먼저 찾고, 없으면 file_name으로 fallback
            target = next(
                (f for f in files if f["display_name"] == file_name), None
            ) or next(
                (f for f in files if f["file_name"] == file_name), None
            )
            if not target:
                return {
                    "success": False,
                    "results": [],
                    "error": f"파일을 찾을 수 없습니다: {file_name}",
                }
            if on_progress:
                kw_display = ", ".join(queries[:3]) + ("..." if len(queries) > 3 else "")
                on_progress("search", f"파일 검색 중: {target['display_name']} [{kw_display}]")
            full_text = self.read_file_text(target["abs_path"])
            total_len = len(full_text)
            self._save_text_length(target["file_id"], total_len)
            all_results: List[Dict[str, Any]] = []
            for query in queries:
                if len(all_results) >= MAX_TOTAL_MATCHES:
                    break
                matches = self._extract_matches(query, full_text, target)
                if matches and on_progress:
                    locs = ", ".join(f"{m['start_char']:,}~{m['end_char']:,}자" for m in matches[:3])
                    on_progress("search", f"{target['display_name']}: '{query}' {len(matches)}곳 → {locs}")
                all_results.extend(matches)
            if all_results:
                snippet_chars = sum(len(r["content"]) for r in all_results)
                result_dict: Dict[str, Any] = {
                    "success": True,
                    "results": all_results,
                    "total_chars": total_len,
                    "snippet_chars": snippet_chars,
                    "coverage_pct": min(100, int(snippet_chars / total_len * 100)) if total_len > 0 else 100,
                }
                return result_dict
            return {
                "success": True,
                "results": [],
                "message": f"검색 결과가 없습니다: {target['display_name']}",
                "total_chars": total_len,
            }

        # ── file_name 미지정: 병렬 읽기 + 키워드 검색 ──────────────────
        total_files = len(files)
        if on_progress:
            kw_display = ", ".join(queries[:3]) + ("..." if len(queries) > 3 else "")
            on_progress("search", f"파일 {total_files}개 키워드 검색: [{kw_display}]")

        def read_one(fi: Dict[str, Any]) -> tuple:
            text = self.read_file_text(fi["abs_path"])
            self._save_text_length(fi["file_id"], len(text))
            return fi, text

        # display_name을 키로 사용 → 동일 파일명도 충돌 없이 구분
        file_texts: Dict[str, tuple] = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=min(4, len(files))) as pool:
            futures = {pool.submit(read_one, fi): fi for fi in files}
            for fut in as_completed(futures):
                fi, text = fut.result()
                file_texts[fi["display_name"]] = (fi, text)
                completed += 1
                if on_progress:
                    on_progress("search", f"({completed}/{total_files}) {fi['display_name']} — {len(text):,}자 로드")

        all_results: List[Dict[str, Any]] = []
        for query in queries:
            if len(all_results) >= MAX_TOTAL_MATCHES:
                break
            for _dname, (fi, text) in file_texts.items():
                if len(all_results) >= MAX_TOTAL_MATCHES:
                    break
                matches = self._extract_matches(query, text, fi)
                if matches and on_progress:
                    locs = ", ".join(f"{m['start_char']:,}~{m['end_char']:,}자" for m in matches[:3])
                    on_progress("search", f"{fi['display_name']}: '{query}' {len(matches)}곳 → {locs}")
                all_results.extend(matches)

        total_chars = sum(len(t) for _, t in file_texts.values())
        if all_results:
            snippet_chars = sum(len(r["content"]) for r in all_results)
            coverage_pct = min(100, int(snippet_chars / total_chars * 100)) if total_chars > 0 else 100
            result_dict: Dict[str, Any] = {
                "success": True,
                "results": all_results,
                "total_chars": total_chars,
                "snippet_chars": snippet_chars,
                "coverage_pct": coverage_pct,
            }
            if coverage_pct < 30:
                result_dict["steering"] = (
                    f"현재 확인 범위: 전체 {total_chars:,}자 중 {snippet_chars:,}자 ({coverage_pct}%). "
                    f"검색 결과 스니펫만으로는 문서 전체를 파악하기 어렵습니다. "
                    f"추가 키워드로 search_file을 호출하거나, "
                    f"read_file(offset=...)로 주변 내용을 이어 읽어 "
                    f"충분한 참조 데이터를 확보하세요."
                )
            return result_dict
        return {"success": True, "results": [], "message": "검색 결과가 없습니다", "total_chars": total_chars}

    def read_chunk(
        self,
        file_name: str,
        project_id: str,
        chat_id: Optional[str] = None,
        offset: int = 0,
        length: int = DEFAULT_READ_LENGTH,
    ) -> Dict[str, Any]:
        """파일의 특정 구간을 읽어 반환 (read_file 도구용).

        Args:
            file_name: AVAILABLE_FILES에서의 정확한 파일명
            project_id: 프로젝트 ID
            chat_id: 채팅 ID (선택)
            offset: 읽기 시작 문자 위치 (0-based)
            length: 읽을 문자 수 (최대 MAX_READ_LENGTH)

        Returns:
            {"success": bool, "source", "offset", "length", "total_chars", "content"}
        """
        files = self.get_available_files(project_id, chat_id)
        # display_name으로 먼저 찾고, 없으면 file_name으로 fallback
        target = next(
            (f for f in files if f["display_name"] == file_name), None
        ) or next(
            (f for f in files if f["file_name"] == file_name), None
        )
        if not target:
            return {"success": False, "error": f"파일을 찾을 수 없습니다: {file_name}"}

        full_text = self.read_file_text(target["abs_path"])
        self._save_text_length(target["file_id"], len(full_text))
        length = min(max(1, length), MAX_READ_LENGTH)
        file_len = len(full_text)
        if offset >= file_len:
            return {
                "success": False,
                "error": (
                    f"offset={offset}이 파일 끝({file_len}자)을 초과합니다. "
                    f"total_chars({file_len})를 offset으로 사용하면 안 됩니다. "
                    f"후반부 읽기: offset={file_len // 2}, 전체 읽기: offset=0"
                ),
                "total_chars": file_len,
            }
        chunk = full_text[offset: offset + length]
        return {
            "success": True,
            "source": target["display_name"],
            "offset": offset,
            "start_char": offset,
            "end_char": offset + len(chunk),
            "length": len(chunk),
            "returned_chars": len(chunk),
            "total_chars": file_len,
            "remaining_chars_after": max(0, file_len - (offset + len(chunk))),
            "is_near_eof": (offset + len(chunk)) >= file_len,
            "content": chunk,
        }

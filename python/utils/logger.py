"""
로깅 유틸리티

모든 로그는 stderr 또는 파일로 출력 (stdout은 JSON-RPC 전용)
"""

import sys
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
try:
    import pandas as pd
except ImportError:
    pd = None

# 로그 디렉토리 (%APPDATA%/Inserty AI/logs - Program Files 쓰기 권한 문제 방지)
def _get_log_dir() -> Path:
    """쓰기 가능한 로그 디렉토리 반환"""
    appdata = os.environ.get('APPDATA')
    if appdata:
        log_dir = Path(appdata) / "Inserty AI" / "logs"
    else:
        log_dir = Path.home() / ".inserty" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 최종 fallback: 임시 디렉토리
        import tempfile
        log_dir = Path(tempfile.gettempdir()) / "inserty_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

LOG_DIR = _get_log_dir()

# 현재 세션 로그 파일
_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file: Optional[Path] = None
_llm_log_file: Optional[Path] = None
_token_log_file: Optional[Path] = None

# 통합 토큰 사용량 엑셀 파일
_excel_usage_file = LOG_DIR / "token_usage_history.xlsx"
_ENABLE_EXCEL_USAGE_LOG = os.getenv("ENABLE_EXCEL_USAGE_LOG", "0").strip().lower() in {"1", "true", "yes", "y"}

# 세션 누적 토큰 카운터 (LLM - GPT-5.1)
_session_tokens = {
    "input": 0,
    "output": 0,
    "total": 0,
    "requests": 0
}

# 세션 누적 토큰 카운터 (RAG - Embedding + Knowledge Graph LLM)
_session_rag_tokens = {
    "embedding_tokens": 0,  # text-embedding-3-small
    "kg_llm_input": 0,      # Knowledge Graph 생성용 LLM input
    "kg_llm_output": 0,     # Knowledge Graph 생성용 LLM output
    "total": 0,
    "embedding_calls": 0,
    "kg_llm_calls": 0
}

# 세션 누적 File Search 사용량
_session_file_search = {
    "tool_calls": 0,
    "storage_bytes": 0,
    "storage_files": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
}


def _get_log_file() -> Path:
    global _log_file
    if _log_file is None:
        _log_file = LOG_DIR / f"debug_{_session_id}.log"
    return _log_file


def _get_llm_log_file() -> Path:
    global _llm_log_file
    if _llm_log_file is None:
        _llm_log_file = LOG_DIR / f"llm_{_session_id}.json"
        # 초기화
        with open(_llm_log_file, 'w', encoding='utf-8') as f:
            f.write("[]")
    return _llm_log_file


def debug(message: str):
    """디버그 로그 출력 (stderr + 파일)"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_line = f"[{timestamp}] {message}"

    # stderr로 출력 (stdout은 JSON-RPC 전용)
    print(log_line, file=sys.stderr)

    # 파일에도 저장
    try:
        with open(_get_log_file(), 'a', encoding='utf-8') as f:
            f.write(log_line + "\n")
    except:
        pass


def log_terminal_output(message: str, level: str = "INFO"):
    """
    터미널 출력을 별도 파일로 저장

    Args:
        message: 로그 메시지
        level: 로그 레벨 (INFO, ERROR, WARN, DEBUG)
    """
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_line = f"[{timestamp}] [{level}] {message}"

    # stderr로 출력
    print(log_line, file=sys.stderr)

    # 별도 터미널 로그 파일에도 저장
    try:
        terminal_log_file = LOG_DIR / f"terminal_{_session_id}.log"
        with open(terminal_log_file, 'a', encoding='utf-8') as f:
            f.write(log_line + "\n")
    except:
        pass


def log_llm_interaction(
    prompt: str,
    html: str,
    response_raw: dict,
    commands: list,
    success: bool,
    error: Optional[str] = None,
    mappings: Optional[dict] = None
):
    """LLM 상호작용 로깅 (파일)"""
    try:
        log_file = _get_llm_log_file()

        # 기존 로그 읽기
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = json.load(f)

        # 새 로그 추가
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt,
            "html_length": len(html),
            "html_preview": html[:500] + "..." if len(html) > 500 else html,
            "response_raw": response_raw,
            "commands": commands,
            "success": success,
            "error": error,
        }

        # 디버깅용: 전체 HTML과 매핑 정보 별도 파일로 저장
        if mappings:
            debug_file = LOG_DIR / f"debug_html_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(debug_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "html": html,
                    "mappings": {str(k): {
                        "type": v.type.value if hasattr(v.type, 'value') else str(v.type),
                        "table_index": v.table_index,
                        "row": v.row,
                        "col": v.col,
                        "para_index": v.para_index,
                    } for k, v in mappings.items()}
                }, f, ensure_ascii=False, indent=2)
            debug(f"디버그 HTML 저장: {debug_file}")

        logs.append(log_entry)

        # 저장
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

        debug(f"LLM 로그 저장: {log_file}")

    except Exception as e:
        debug(f"LLM 로그 저장 실패: {e}")


def get_log_dir() -> str:
    """로그 디렉토리 경로 반환"""
    return str(LOG_DIR)


def _get_token_log_file() -> Path:
    """토큰 로그 파일 경로 반환"""
    global _token_log_file
    if _token_log_file is None:
        _token_log_file = LOG_DIR / f"tokens_{_session_id}.log"
    return _token_log_file


# 모델별 단가 (USD per 1M tokens)
MODEL_PRICING = {
    "gpt-5.1": {
        "input": 2.50,
        "output": 20.00
    },
    "gpt-5.2": {
        "input": 3.00,
        "output": 24.00
    },
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60
    }
}

def log_token_usage(
    token_usage: Optional[dict],
    prompt: str = "",
    context_info: str = "",
    model: str = "gpt-5.1"
) -> dict:
    """
    토큰 사용량을 터미널과 로그 파일에 기록

    Args:
        token_usage: {"input": int, "output": int, "total": int} 또는 None
        prompt: 사용자 프롬프트 (로그용)
        context_info: 추가 컨텍스트 정보 (예: JSONL 길이)
        model: 사용 모델명 (비용 계산용)

    Returns:
        세션 누적 토큰 정보 + request_cost_usd + model
    """
    global _session_tokens

    if token_usage is None:
        debug("[TOKEN] 토큰 사용량 정보 없음")
        return _session_tokens

    # 현재 요청 토큰
    input_tokens = token_usage.get("input", 0)
    output_tokens = token_usage.get("output", 0)
    total_tokens = token_usage.get("total", 0)

    # 세션 누적
    _session_tokens["input"] += input_tokens
    _session_tokens["output"] += output_tokens
    _session_tokens["total"] += total_tokens
    _session_tokens["requests"] += 1

    # 모델별 단가 가져오기
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["gpt-5.1"])

    # 비용 계산
    cost_input = (input_tokens / 1_000_000) * pricing["input"]
    cost_output = (output_tokens / 1_000_000) * pricing["output"]
    cost_total = cost_input + cost_output

    session_cost_input = (_session_tokens["input"] / 1_000_000) * pricing["input"]
    session_cost_output = (_session_tokens["output"] / 1_000_000) * pricing["output"]
    session_cost_total = session_cost_input + session_cost_output

    # 터미널 출력 (stderr)
    timestamp = datetime.now().strftime("%H:%M:%S")
    terminal_msg = (
        f"[{timestamp}] [TOKEN] [{model}] "
        f"이번: {input_tokens:,} in + {output_tokens:,} out = {total_tokens:,} "
        f"(${cost_total:.6f}) | "
        f"누적: {_session_tokens['total']:,} tokens "
        f"(${session_cost_total:.6f}, {_session_tokens['requests']}회)"
    )
    print(terminal_msg, file=sys.stderr)

    # 로그 파일 저장
    try:
        log_file = _get_token_log_file()
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "request": {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
                "cost_usd": round(cost_total, 6)
            },
            "session": {
                "input": _session_tokens["input"],
                "output": _session_tokens["output"],
                "total": _session_tokens["total"],
                "requests": _session_tokens["requests"],
                "cost_usd": round(session_cost_total, 6)
            },
            "prompt_preview": prompt[:100] + "..." if len(prompt) > 100 else prompt,
            "context_info": context_info
        }

        # 파일에 추가 (한 줄씩 JSON)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    except Exception as e:
        debug(f"[TOKEN] 로그 파일 저장 실패: {e}")

    # 세션 누적 토큰과 현재 요청 비용, 모델 반환
    return {
        **_session_tokens,
        "request_cost_usd": round(cost_total, 6),
        "model": model
    }


def log_rag_token_usage(
    embedding_tokens: int = 0,
    kg_llm_input: int = 0,
    kg_llm_output: int = 0,
    operation: str = "",
    context_info: str = ""
) -> dict:
    """
    RAG 토큰 사용량을 터미널과 로그 파일에 기록

    Args:
        embedding_tokens: Embedding API 사용 토큰 (text-embedding-3-small)
        kg_llm_input: Knowledge Graph 생성용 LLM input 토큰 (gpt-4o-mini)
        kg_llm_output: Knowledge Graph 생성용 LLM output 토큰 (gpt-4o-mini)
        operation: 작업 종류 (indexing, query, multi-scope-query 등)
        context_info: 추가 컨텍스트 정보

    Returns:
        세션 누적 RAG 토큰 정보
    """
    global _session_rag_tokens

    # 세션 누적
    _session_rag_tokens["embedding_tokens"] += embedding_tokens
    _session_rag_tokens["kg_llm_input"] += kg_llm_input
    _session_rag_tokens["kg_llm_output"] += kg_llm_output
    _session_rag_tokens["total"] += embedding_tokens + kg_llm_input + kg_llm_output

    if embedding_tokens > 0:
        _session_rag_tokens["embedding_calls"] += 1
    if kg_llm_input > 0 or kg_llm_output > 0:
        _session_rag_tokens["kg_llm_calls"] += 1

    # 비용 계산
    # Embedding: text-embedding-3-small = $0.02/1M tokens
    # KG LLM: gpt-4o-mini = $0.15/1M input, $0.60/1M output
    COST_EMBEDDING_PER_1M = 0.02
    COST_KG_INPUT_PER_1M = 0.15
    COST_KG_OUTPUT_PER_1M = 0.60

    cost_embedding = (embedding_tokens / 1_000_000) * COST_EMBEDDING_PER_1M
    cost_kg_input = (kg_llm_input / 1_000_000) * COST_KG_INPUT_PER_1M
    cost_kg_output = (kg_llm_output / 1_000_000) * COST_KG_OUTPUT_PER_1M
    cost_total = cost_embedding + cost_kg_input + cost_kg_output

    session_cost_embedding = (_session_rag_tokens["embedding_tokens"] / 1_000_000) * COST_EMBEDDING_PER_1M
    session_cost_kg_input = (_session_rag_tokens["kg_llm_input"] / 1_000_000) * COST_KG_INPUT_PER_1M
    session_cost_kg_output = (_session_rag_tokens["kg_llm_output"] / 1_000_000) * COST_KG_OUTPUT_PER_1M
    session_cost_total = session_cost_embedding + session_cost_kg_input + session_cost_kg_output

    # 터미널 출력 (stderr)
    timestamp = datetime.now().strftime("%H:%M:%S")
    terminal_msg = (
        f"[{timestamp}] [RAG] {operation} - "
        f"Embed: {embedding_tokens:,} (${cost_embedding:.6f}) | "
        f"KG-LLM: {kg_llm_input:,}+{kg_llm_output:,} (${cost_kg_input + cost_kg_output:.6f}) | "
        f"누적: {_session_rag_tokens['total']:,} tokens "
        f"(${session_cost_total:.4f})"
    )
    print(terminal_msg, file=sys.stderr)

    # 로그 파일 저장
    try:
        log_file = _get_token_log_file()
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "RAG",
            "operation": operation,
            "request": {
                "embedding_tokens": embedding_tokens,
                "kg_llm_input": kg_llm_input,
                "kg_llm_output": kg_llm_output,
                "total": embedding_tokens + kg_llm_input + kg_llm_output,
                "cost_usd": round(cost_total, 6)
            },
            "session": {
                "embedding_tokens": _session_rag_tokens["embedding_tokens"],
                "kg_llm_input": _session_rag_tokens["kg_llm_input"],
                "kg_llm_output": _session_rag_tokens["kg_llm_output"],
                "total": _session_rag_tokens["total"],
                "embedding_calls": _session_rag_tokens["embedding_calls"],
                "kg_llm_calls": _session_rag_tokens["kg_llm_calls"],
                "cost_usd": round(session_cost_total, 6)
            },
            "context_info": context_info
        }

        # 파일에 추가 (한 줄씩 JSON)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    except Exception as e:
        debug(f"[RAG] 로그 파일 저장 실패: {e}")

    return _session_rag_tokens


def log_file_search_tool_usage(
    tool_calls: int = 1,
    operation: str = "file_search",
    context_info: str = "",
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None
) -> dict:
    """
    File Search tool call 비용 로그

    요금: $2.50 / 1,000 calls
    """
    global _session_file_search

    if tool_calls <= 0:
        return _session_file_search

    _session_file_search["tool_calls"] += tool_calls
    if input_tokens is not None:
        _session_file_search["input_tokens"] += input_tokens
    if output_tokens is not None:
        _session_file_search["output_tokens"] += output_tokens
    if total_tokens is not None:
        _session_file_search["total_tokens"] += total_tokens

    COST_PER_1K_CALLS = 2.50
    cost_now = (tool_calls / 1_000) * COST_PER_1K_CALLS
    cost_total = (_session_file_search["tool_calls"] / 1_000) * COST_PER_1K_CALLS

    timestamp = datetime.now().strftime("%H:%M:%S")
    terminal_msg = (
        f"[{timestamp}] [FILE_SEARCH] {operation} - "
        f"Calls: {tool_calls} (${cost_now:.6f}) | "
        f"누적: {_session_file_search['tool_calls']} calls (${cost_total:.6f})"
    )
    print(terminal_msg, file=sys.stderr)

    try:
        log_file = _get_token_log_file()
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "FILE_SEARCH",
            "operation": operation,
            "request": {
                "tool_calls": tool_calls,
                "cost_usd": round(cost_now, 6),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens
            },
            "session": {
                "tool_calls": _session_file_search["tool_calls"],
                "cost_usd": round(cost_total, 6),
                "input_tokens": _session_file_search["input_tokens"],
                "output_tokens": _session_file_search["output_tokens"],
                "total_tokens": _session_file_search["total_tokens"]
            },
            "context_info": context_info
        }
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        debug(f"[FILE_SEARCH] 로그 파일 저장 실패: {e}")

    # 엑셀 로그
    save_to_excel_usage(
        api_type="FILE_SEARCH",
        operation=operation,
        model="file_search_tool",
        input_tokens=input_tokens if input_tokens is not None else tool_calls,
        output_tokens=output_tokens or 0,
        cost_usd=cost_now,
        context_info=context_info
    )

    return _session_file_search


def log_file_search_storage(
    usage_bytes: int,
    operation: str = "file_upload",
    context_info: str = ""
) -> dict:
    """
    File Search 스토리지 비용 로그

    요금: $0.10 / GB / day (첫 1GB 무료)
    """
    global _session_file_search

    if usage_bytes <= 0:
        return _session_file_search

    _session_file_search["storage_bytes"] += usage_bytes
    _session_file_search["storage_files"] += 1

    COST_PER_GB_DAY = 0.10
    FREE_BYTES = 1_073_741_824  # 1GB

    total_bytes = _session_file_search["storage_bytes"]
    billable_bytes = max(total_bytes - FREE_BYTES, 0)
    cost_total_per_day = (billable_bytes / 1_073_741_824) * COST_PER_GB_DAY
    cost_now_per_day = (usage_bytes / 1_073_741_824) * COST_PER_GB_DAY

    timestamp = datetime.now().strftime("%H:%M:%S")
    terminal_msg = (
        f"[{timestamp}] [FILE_SEARCH] storage - "
        f"+{usage_bytes:,} bytes (${cost_now_per_day:.6f}/day) | "
        f"누적: {total_bytes:,} bytes (${cost_total_per_day:.6f}/day, "
        f"{_session_file_search['storage_files']} files)"
    )
    print(terminal_msg, file=sys.stderr)

    try:
        log_file = _get_token_log_file()
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "FILE_SEARCH_STORAGE",
            "operation": operation,
            "request": {
                "usage_bytes": usage_bytes,
                "cost_usd_per_day": round(cost_now_per_day, 6)
            },
            "session": {
                "usage_bytes": total_bytes,
                "files": _session_file_search["storage_files"],
                "cost_usd_per_day": round(cost_total_per_day, 6)
            },
            "context_info": context_info
        }
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        debug(f"[FILE_SEARCH] 스토리지 로그 저장 실패: {e}")

    # 엑셀 로그 (일일 비용 기준)
    save_to_excel_usage(
        api_type="FILE_SEARCH_STORAGE",
        operation=operation,
        model="file_search_storage",
        input_tokens=usage_bytes,
        output_tokens=0,
        cost_usd=cost_now_per_day,
        context_info=context_info
    )

    return _session_file_search


def save_to_excel_usage(
    api_type: str,  # "LLM" or "RAG-Embedding" or "RAG-KG"
    operation: str,  # "chat:send", "rag:query", "rag:indexChatFile" 등
    model: str,  # "gpt-5.1", "text-embedding-3-small", "gpt-4o-mini"
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    context_info: str = ""
):
    """
    API 사용량을 엑셀 파일에 누적 저장 (LLM + RAG 통합 관리)

    Args:
        api_type: API 타입 (LLM, RAG-Embedding, RAG-KG)
        operation: 작업명
        model: 사용 모델명
        input_tokens: 입력 토큰 수
        output_tokens: 출력 토큰 수 (Embedding은 0)
        cost_usd: 비용 (USD)
        context_info: 추가 정보
    """
    if not _ENABLE_EXCEL_USAGE_LOG or pd is None:
        return
    try:
        # 기존 데이터 로드 또는 새 DataFrame 생성
        if _excel_usage_file.exists():
            try:
                df = pd.read_excel(_excel_usage_file)
            except Exception as read_err:
                debug(f"[EXCEL] 기존 로그 읽기 실패: {read_err} - 새 파일로 초기화")
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{_excel_usage_file.stem}.corrupt.{timestamp}.xlsx"
                    backup_path = _excel_usage_file.with_name(backup_name)
                    _excel_usage_file.rename(backup_path)
                except Exception as backup_err:
                    debug(f"[EXCEL] 손상 로그 백업 실패: {backup_err}")
                df = pd.DataFrame(columns=[
                    "timestamp", "session_id", "api_type", "operation", "model",
                    "input_tokens", "output_tokens", "total_tokens",
                    "cost_usd", "cost_krw", "context_info"
                ])
        else:
            df = pd.DataFrame(columns=[
                "timestamp", "session_id", "api_type", "operation", "model",
                "input_tokens", "output_tokens", "total_tokens",
                "cost_usd", "cost_krw", "context_info"
            ])

        # 새 레코드 추가
        new_record = {
            "timestamp": datetime.now().isoformat(),
            "session_id": _session_id,
            "api_type": api_type,
            "operation": operation,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": round(cost_usd, 6),
            "cost_krw": round(cost_usd * 1400, 2),  # USD → KRW 환율 1400원
            "context_info": context_info
        }

        df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)

        # 엑셀 저장
        df.to_excel(_excel_usage_file, index=False)

    except Exception as e:
        debug(f"[EXCEL] 엑셀 저장 실패: {e}")


def get_session_token_stats() -> dict:
    """현재 세션의 토큰 통계 반환 (LLM + RAG)"""
    global _session_tokens, _session_rag_tokens, _session_file_search
    return {
        "llm": _session_tokens,
        "rag": _session_rag_tokens,
        "file_search": _session_file_search
    }


def list_log_files() -> dict:
    """
    현재 세션에서 생성된 로그 파일 목록 반환

    Returns:
        dict: 로그 파일 유형별 경로
    """
    log_files = {
        "debug": str(_get_log_file()),
        "llm": str(_get_llm_log_file()),
        "tokens": str(_get_token_log_file()),
        "terminal": str(LOG_DIR / f"terminal_{_session_id}.log"),
        "log_dir": str(LOG_DIR)
    }
    return log_files


def save_session_summary():
    """
    세션 요약 정보 저장 (세션 종료 시 호출)
    """
    try:
        summary_file = LOG_DIR / f"session_summary_{_session_id}.json"

        summary_data = {
            "session_id": _session_id,
            "timestamp": datetime.now().isoformat(),
            "token_stats": _session_tokens,
            "log_files": list_log_files()
        }

        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)

        debug(f"세션 요약 저장: {summary_file}")

    except Exception as e:
        debug(f"세션 요약 저장 실패: {e}")

    # 비용 계산 (GPT-5.1 기준)
    COST_INPUT_PER_1M = 1.25
    COST_OUTPUT_PER_1M = 10.00

    cost_input = (_session_tokens["input"] / 1_000_000) * COST_INPUT_PER_1M
    cost_output = (_session_tokens["output"] / 1_000_000) * COST_OUTPUT_PER_1M
    cost_total = cost_input + cost_output

    return {
        **_session_tokens,
        "cost_usd": round(cost_total, 6),
        "avg_tokens_per_request": (
            _session_tokens["total"] // _session_tokens["requests"]
            if _session_tokens["requests"] > 0 else 0
        )
    }


def reset_session_tokens():
    """세션 토큰 카운터 초기화"""
    global _session_tokens
    _session_tokens = {
        "input": 0,
        "output": 0,
        "total": 0,
        "requests": 0
    }
    debug("[TOKEN] 세션 토큰 카운터 초기화됨")

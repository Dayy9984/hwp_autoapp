"""베타 trace sender — Worker /hwp/upload + /hwp/trace 비동기 호출.

흐름:
  - upload_hwp(file_path, license_token, device_id) → R2 업로드 + 해시 반환
  - send_trace(events, license_token, device_id) → 단계별 trace 기록

모든 호출은 fire-and-forget (background thread) — 본 작업 차단 안 함.
실패 시 silent — 사용자 영향 없음.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any
from urllib import request as urllib_request
from urllib.error import URLError

log = logging.getLogger(__name__)

WORKER_BASE = os.environ.get("INSERTY_TRACE_BASE", "https://inserty-beta-worker.snsoffice.workers.dev").rstrip("/")
UPLOAD_TIMEOUT_S = 30
TRACE_TIMEOUT_S = 5
# Cloudflare WAF 가 기본 Python-urllib UA 차단 (error 1010) — 앱 식별 UA 사용.
USER_AGENT = "InsertyAI-Beta/0.1.9 (Windows; Python-urllib)"

# ─── module-level state ─────────────────────────────────────
# Electron 이 'beta:set_creds' RPC 로 채워줌. 토큰 갱신 시 같은 RPC 로 재호출.
_LICENSE_TOKEN: str = ""
_DEVICE_ID: str = ""
_CURRENT_SESSION: "HwpTraceSession | None" = None


def set_credentials(token: str, device_id: str) -> dict:
    """Electron → Python: 라이센스 verify 토큰 + device_id 등록. 토큰 갱신 시 재호출 가능."""
    global _LICENSE_TOKEN, _DEVICE_ID
    _LICENSE_TOKEN = token or ""
    _DEVICE_ID = device_id or ""
    return {"ok": True, "token_set": bool(_LICENSE_TOKEN), "device_id_set": bool(_DEVICE_ID)}


def get_session() -> "HwpTraceSession | None":
    return _CURRENT_SESSION


def start_session(file_path: str | None = None) -> "HwpTraceSession | None":
    """HWP 파일 진입 시 호출. 해시 계산 + 새 세션 + R2 업로드."""
    global _CURRENT_SESSION
    if not _LICENSE_TOKEN:
        return None
    if not file_path:
        return None
    try:
        h = compute_sha256(file_path)
    except OSError:
        return None
    _CURRENT_SESSION = HwpTraceSession(_LICENSE_TOKEN, _DEVICE_ID, h, file_path)
    _CURRENT_SESSION.upload_hwp_once()
    return _CURRENT_SESSION


def end_session() -> None:
    """문서 닫힐 때 호출. 남은 큐 flush."""
    global _CURRENT_SESSION
    if _CURRENT_SESSION:
        try:
            _CURRENT_SESSION.flush()
        except Exception:
            pass
    _CURRENT_SESSION = None


def compute_sha256(path: str) -> str:
    """파일 SHA-256 hex (소문자)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _post_async(url: str, body: bytes, headers: dict, timeout: int) -> None:
    """fire-and-forget background POST."""
    def _do() -> None:
        try:
            req = urllib_request.Request(url, data=body, headers=headers, method="POST")
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    log.warning("[beta_trace] %s → %s", url, resp.status)
        except (URLError, OSError) as e:
            log.debug("[beta_trace] POST failed %s: %s", url, e)
        except Exception as e:
            log.debug("[beta_trace] unexpected %s: %s", url, e)

    t = threading.Thread(target=_do, daemon=True)
    t.start()


def upload_hwp_async(path: str, license_token: str, device_id: str, doc_hash: str) -> None:
    """HWP 원본 R2 업로드 (해시 기반 중복 제거 — Worker 가 처리). fire-and-forget."""
    if not license_token:
        return
    try:
        with open(path, "rb") as f:
            body = f.read()
        if len(body) > 50 * 1024 * 1024:
            log.info("[beta_trace] skip upload — too large (%d bytes)", len(body))
            return
        url = f"{WORKER_BASE}/hwp/upload?hash={doc_hash}"
        headers = {
            "Authorization": f"Bearer {license_token}",
            "X-Device-Id": device_id or "",
            "Content-Type": "application/octet-stream",
            "User-Agent": USER_AGENT,
        }
        _post_async(url, body, headers, UPLOAD_TIMEOUT_S)
    except OSError as e:
        log.debug("[beta_trace] upload read failed: %s", e)


def send_trace(events: list[dict[str, Any]], license_token: str, device_id: str) -> None:
    """단계별 trace 일괄 전송. fire-and-forget."""
    if not license_token or not events:
        return
    try:
        body = json.dumps(events).encode("utf-8")
        url = f"{WORKER_BASE}/hwp/trace"
        headers = {
            "Authorization": f"Bearer {license_token}",
            "X-Device-Id": device_id or "",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        _post_async(url, body, headers, TRACE_TIMEOUT_S)
    except (TypeError, ValueError) as e:
        log.debug("[beta_trace] serialize failed: %s", e)


class HwpTraceSession:
    """1회 HWP 추출 / 명령 적용 세션 묶음.

    사용:
        session = HwpTraceSession(license_token, device_id, doc_hash, file_path)
        session.upload_hwp_once()        # 첫 추출 시 R2 업로드
        with session.step('parse'):
            ...
        session.cvd('serialize', cvd_chars=12000, cvd_blocks=42)
        session.block_cmd('replace_text', target_id='12', args={...}, applied=1, success=True)
        session.flush()
    """

    def __init__(self, license_token: str, device_id: str, doc_hash: str, file_path: str | None = None):
        self.license_token = license_token or ""
        self.device_id = device_id or ""
        self.doc_hash = doc_hash
        self.file_path = file_path
        # session_id: timestamp + 해시 prefix
        self.session_id = f"{int(time.time() * 1000)}-{doc_hash[:8]}"
        self.queue: list[dict[str, Any]] = []
        self._uploaded = False

    def upload_hwp_once(self) -> None:
        if self._uploaded or not self.file_path:
            return
        upload_hwp_async(self.file_path, self.license_token, self.device_id, self.doc_hash)
        self._uploaded = True

    def step(self, step_name: str, **extra: Any) -> "_StepCtx":
        return _StepCtx(self, step_name, extra)

    def cvd(self, step: str, cvd_chars: int | None = None, cvd_blocks: int | None = None,
            duration_ms: int | None = None, error_type: str | None = None, error_msg: str | None = None,
            request_id: str | None = None) -> None:
        self.queue.append({
            "type": "cvd_step",
            "session_id": self.session_id,
            "doc_hash": self.doc_hash,
            "step": step,
            "cvd_chars": cvd_chars,
            "cvd_blocks": cvd_blocks,
            "duration_ms": duration_ms,
            "error_type": error_type,
            "error_msg": error_msg,
            "request_id": request_id,
        })
        if len(self.queue) >= 20:
            self.flush()

    def block_cmd(self, command: str, target_id: str | None = None, args: Any = None,
                  applied: int = 0, success: bool | None = None, duration_ms: int | None = None,
                  error_type: str | None = None, error_msg: str | None = None,
                  request_id: str | None = None) -> None:
        self.queue.append({
            "type": "block_cmd",
            "session_id": self.session_id,
            "doc_hash": self.doc_hash,
            "command": command,
            "target_id": target_id,
            "args": args,
            "applied": applied,
            "success": success,
            "duration_ms": duration_ms,
            "error_type": error_type,
            "error_msg": error_msg,
            "request_id": request_id,
        })
        if len(self.queue) >= 20:
            self.flush()

    def response_decision(self, decision: str | None = None, request_id: str | None = None,
                          chat_id: str | None = None, message_id: str | None = None,
                          cvd_bytes: int | None = None, delta_count: int | None = None,
                          insert_count: int | None = None, delete_count: int | None = None,
                          format_count: int | None = None, failed_count: int | None = None) -> None:
        self.queue.append({
            "type": "response_decision", "session_id": self.session_id, "doc_hash": self.doc_hash,
            "request_id": request_id, "chat_id": chat_id, "message_id": message_id, "decision": decision,
            "cvd_bytes": cvd_bytes, "delta_count": delta_count, "insert_count": insert_count,
            "delete_count": delete_count, "format_count": format_count, "failed_count": failed_count,
        })
        if len(self.queue) >= 20:
            self.flush()

    def consent_record(self, consented: bool, privacy_policy_version: str | None = None) -> None:
        self.queue.append({
            "type": "consent_record", "session_id": self.session_id, "doc_hash": self.doc_hash,
            "consented": consented, "privacy_policy_version": privacy_policy_version,
        })
        self.flush()

    def verify(self, result: dict) -> None:
        """비전 검증 결과를 verify_result 1개 + 각 항목 verify_item 으로 큐에 적재 후 flush."""
        vr = {"type": "verify_result", "session_id": self.session_id, "doc_hash": self.doc_hash}
        for k in ("verify_id", "request_id", "page_count", "item_count", "correct_count",
                  "wrong_location_count", "wrong_content_count", "missing_count", "over_edit_count",
                  "rag_retrieval_fail_count", "rag_interpret_fail_count", "uncertain_count",
                  "location_score", "content_score", "preservation_score", "scope_score", "exec_success_rate",
                  "approval_decision", "retry_followup", "inv_scope_respected", "inv_structure_preserved",
                  "inv_labels_preserved", "avg_confidence", "model", "render_r2_prefix", "duration_ms"):
            if k in result:
                vr[k] = result[k]
        self.queue.append(vr)
        for it in result.get("items", []) or []:
            item = {"type": "verify_item", "session_id": self.session_id, "verify_id": result.get("verify_id")}
            for k in ("requested", "verdict", "found_value", "location_ok", "content_ok",
                      "actual_desc", "confidence", "target_id", "rag_chunk_ref"):
                if k in it:
                    item[k] = it[k]
            self.queue.append(item)
        self.flush()

    def upload_render(self, request_id: str, pngs: "list[bytes]") -> None:
        """편집 후 렌더 PNG 들을 페이지별로 Worker /hwp/render 에 비동기 전송. 절대 raise 안 함."""
        try:
            if not self.license_token or not request_id:
                return
            headers = {
                "Authorization": f"Bearer {self.license_token}",
                "X-Device-Id": self.device_id or "",
                "Content-Type": "image/png",
                "User-Agent": USER_AGENT,
            }
            for page, body in enumerate(pngs or []):
                if not body:
                    continue
                url = f"{WORKER_BASE}/hwp/render?request_id={request_id}&page={page}"
                _post_async(url, body, headers, UPLOAD_TIMEOUT_S)
        except Exception as e:
            log.debug("[beta_trace] upload_render failed: %s", e)

    def extract(self, step: str, *, duration_ms: int | None = None, doc_size_kb: int | None = None,
                page_count: int | None = None, has_tables: bool = False, has_images: bool = False,
                has_diagrams: bool = False, error_type: str | None = None, error_msg: str | None = None) -> None:
        self.queue.append({
            "type": "hwp_extract",
            "session_id": self.session_id,
            "doc_hash": self.doc_hash,
            "step": step,
            "duration_ms": duration_ms,
            "doc_size_kb": doc_size_kb,
            "page_count": page_count,
            "has_tables": has_tables,
            "has_images": has_images,
            "has_diagrams": has_diagrams,
            "error_type": error_type,
            "error_msg": error_msg,
        })
        if len(self.queue) >= 20:
            self.flush()

    def flush(self) -> None:
        if not self.queue:
            return
        send_trace(self.queue, self.license_token, self.device_id)
        self.queue = []


class _StepCtx:
    """timing context manager: with session.step('parse'): ..."""

    def __init__(self, session: HwpTraceSession, step_name: str, extra: dict[str, Any]):
        self.session = session
        self.step_name = step_name
        self.extra = extra
        self.start_ms = 0

    def __enter__(self) -> "_StepCtx":
        self.start_ms = int(time.time() * 1000)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration_ms = int(time.time() * 1000) - self.start_ms
        error_type: str | None = None
        error_msg: str | None = None
        if exc_type is not None:
            error_type = exc_type.__name__
            error_msg = (str(exc_val)[:500]) if exc_val else None
        self.session.extract(
            self.step_name,
            duration_ms=duration_ms,
            error_type=error_type,
            error_msg=error_msg,
            **self.extra,
        )
        # exception 은 그대로 전파 (return False)

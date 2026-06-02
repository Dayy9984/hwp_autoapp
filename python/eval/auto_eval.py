# -*- coding: utf-8 -*-
"""Headless auto-evaluation runner for the HWP auto-writing verify loop.

NO GUI. NO human clicks. Runs a corpus of template forms through the
edit -> verify pipeline unattended and scores against ground truth.

Modes
-----
MODE A (default, deterministic, NO API key, NO LLM)
    The primary regression engine. For each case:
      1. open the template HWP in an isolated headless HWP instance,
      2. prepare_context (populate segment registry / allowed ids),
      3. for each diff change, execute_delta the *known* filledValue into the
         diff-targeted cell (replace_cell_content) — zero LLM,
      4. re-extract the post-edit cell texts,
      5. scorer.score_against_diff -> per-cell verdict + accuracy.
    This answers "did the known value land in the right cell?" with no model.

MODE B (needs an OpenAI key)
    After applying the same deterministic edits, render the doc to PNGs and run
    the vision verifier (vision_verifier.verify_vision) using the case
    instruction as user_intent. Records the vision verdict alongside MODE A.

Output
------
    results/eval-<tag>.jsonl   — one JSON line appended per case, per run.
    results/summary.json       — aggregate, updated each run (accumulated).

Exit code is non-zero only when overall accuracy < --min-accuracy
(default 0.0, i.e. report-only).

Usage
-----
    python -m eval.auto_eval --corpus <root_or_manifest_or_db> --mode A
    python -m eval.auto_eval --corpus <...> --mode B --openai-key sk-...
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _force_utf8_io() -> None:
    """Force stdout/stderr to UTF-8 so the core LLM pipeline's debug prints never
    crash on non-Latin / emoji content.

    On Windows the default console codec is cp949, which cannot encode emoji
    (e.g. 📌 ``\\U0001f4cc``). The core ``streaming_client`` logs the LLM's
    ``message`` (often emoji-laden) to stderr via ``print``; under cp949 that
    raises ``UnicodeEncodeError``, which the streaming loop's generic
    ``except Exception`` catches and converts into ``status='error'`` —
    silently failing an otherwise-successful edit stream. Reconfiguring to
    UTF-8 here (eval-side only, additive) removes that failure mode without
    touching any core edit logic.
    """
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_force_utf8_io()

# Package root (python/) on sys.path so existing modules import cleanly.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from eval import corpus as corpus_mod  # noqa: E402
from eval import scorer as scorer_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Headless HWP plumbing (MODE A / MODE B). Imported lazily so the module can be
# imported (and unit-tested) on machines without HWP / pywin32.
# ---------------------------------------------------------------------------

def _open_headless_processor(template_hwp_path: str):
    """Open ``template_hwp_path`` in an isolated, invisible HWP and bind a
    DocumentProcessor to it.

    Returns ``(processor, connector, hwp, temp_copy_path)``. Raises on failure
    (the caller wraps per-case so a bad case does not abort the whole run).

    This mirrors services.cvd_service.CVDService.extract_single_cvd: a fresh
    ``Hwp(new=True, visible=False)`` instance (real pyhwpx object with
    open/save_as/quit/init_scan), wrapped in the project's HwpConnector and
    driven through the *real* prepare_context / execute_delta code paths.
    """
    import pythoncom  # noqa: F401  (ensure COM available in this thread)
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass

    from pyhwpx import Hwp
    from engine.connection.document_connector import HwpConnector
    from engine.connection.security_module import activate_security_module
    from engine.connection.hwp_file_opener import open_hwp_file_with_fallback
    from engine.state.session_state import (
        store_runtime_connector,
        store_runtime_target_process_id,
        store_runtime_target_window_handle,
        reset_session_state,
    )
    import hwp_com_process as hcp

    if not os.path.isfile(template_hwp_path):
        raise FileNotFoundError(f"Template not found: {template_hwp_path}")

    # Fresh, invisible, isolated instance.
    hwp = Hwp(new=True, visible=False)

    # Best-effort security module registration (pop-up suppression).
    try:
        activate_security_module(hwp, log_callback=None)
    except Exception as e:  # pragma: no cover - environment dependent
        print(f"[auto_eval] security module register warning: {e}", file=sys.stderr)

    opened_ok, _actual_path, temp_copy_path = open_hwp_file_with_fallback(
        hwp, template_hwp_path
    )
    if not opened_ok:
        raise RuntimeError(f"HWP open failed: {template_hwp_path}")

    # Build a connector and inject the live pyhwpx hwp directly. pyhwpx exposes
    # init_scan/get_pos so connector.check_alive() passes without rebinding.
    connector = HwpConnector(visible=False, new=False)
    connector._hwp = hwp  # noqa: SLF001 - intentional headless injection

    # Reset stale session state, then register THIS connector so the
    # DocumentProcessor's _ensure_connector reuses it (no SafeHwp rebind).
    try:
        reset_session_state()
    except Exception:
        pass
    store_runtime_target_process_id(None)
    store_runtime_target_window_handle(None)
    store_runtime_connector(connector)

    processor = hcp.DocumentProcessor()
    processor._connector = connector  # noqa: SLF001
    processor._current_file = template_hwp_path  # noqa: SLF001

    return processor, connector, hwp, temp_copy_path


def _extract_applied_cells(processor) -> Dict[int, str]:
    """Re-extract post-edit cell texts as ``{cell_id: text}``.

    Reuses the processor's extract_cvd (CVDExtractor) + diff_service.CVDParser,
    so no CVD parsing or extraction logic is reimplemented here.
    """
    from services.diff_service import CVDParser

    res = processor.extract_cvd()
    if not isinstance(res, dict) or not res.get("success"):
        raise RuntimeError(f"extract_cvd failed: {res}")
    cvd_text = res.get("cvd") or ""

    parser = CVDParser()
    blocks = parser.parse(cvd_text)

    cells: Dict[int, str] = {}
    for block in blocks:
        # Both td cells and their paragraph children are keyed by id so a diff
        # targeting either resolves. td text aggregates its paragraphs.
        if block.get("type") in ("td", "paragraph", "textbox"):
            cells[int(block["id"])] = block.get("text") or ""
    return cells


def _apply_diff_edits(
    processor,
    changes: List[Dict[str, Any]],
    context_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Replay each diff change's known filledValue into its target cell.

    Returns the op summary list (for MODE B's vision op_list and telemetry).
    """
    ops: List[Dict[str, Any]] = []
    for change in changes:
        target_id = change.get("filledTdId")
        if target_id is None:
            target_id = change.get("templateTdId")
        if target_id is None:
            continue
        filled_value = change.get("filledValue") or ""
        delta = {
            "id": int(target_id),
            "content": filled_value,
            "metadata": {"operation": "replace_cell_content"},
        }
        if context_id:
            delta["context_id"] = context_id
        try:
            result = processor.execute_delta(delta)
        except Exception as e:  # pragma: no cover - COM runtime
            result = {"success": False, "error": str(e)}
        ops.append({
            "id": int(target_id),
            "op": "replace_cell_content",
            "content": filled_value[:200],
            "result": result,
        })
    return ops


class _PdfSaveAdapter:
    """Give any HWP handle a ``save_as(path, format="PDF")`` for render_doc_to_pngs.

    After prepare_context, ``connector.hwp`` may be a ``HwpRawWrapper`` (no
    ``save_as``) rather than the pyhwpx ``Hwp`` we injected — the renderer then
    fails with ``'HwpRawWrapper' object has no attribute 'save_as'``. This
    adapter reproduces pyhwpx's PDF export via the raw COM ``HAction`` /
    ``HParameterSet`` (``FileSaveAs_S`` → fallback ``FileSaveAsPdf``), so render
    works regardless of which handle survives the rebind.
    """

    def __init__(self, com: Any):
        # ``com`` must expose HAction / HParameterSet (the raw HWP COM object).
        self._com = com

    def save_as(self, path: str, format: str = "PDF", arg: str = "") -> bool:  # noqa: A002
        com = self._com
        pset = com.HParameterSet.HFileOpenSave
        com.HAction.GetDefault("FileSaveAs_S", pset.HSet)
        pset.filename = path
        pset.Format = "PDF"
        pset.Attributes = 0
        if com.HAction.Execute("FileSaveAs_S", pset.HSet):
            return True
        pset = com.HParameterSet.HFileOpenSave
        com.HAction.GetDefault("FileSaveAsPdf", pset.HSet)
        com.HParameterSet.HFileOpenSave.filename = path
        com.HParameterSet.HFileOpenSave.Format = "PDF"
        com.HParameterSet.HFileOpenSave.Attributes = 16384
        return bool(com.HAction.Execute("FileSaveAsPdf", pset.HSet))


def _save_capable_handles(processor, orig_hwp):
    """Return ordered candidate handles for ``render_doc_to_pngs`` to save_as.

    The edits are applied through ``processor._connector.hwp`` (after the
    connector rebind that is the LIVE handle bound to the edited document and
    guaranteed COM-connected), so try it FIRST. ``orig_hwp`` (the originally
    injected pyhwpx Hwp) can become "object not connected to server" once the
    rebind swaps the connector's binding, so it is only a fallback. Each handle
    is wrapped so it exposes ``save_as`` (native, or a ``_PdfSaveAdapter`` over
    the raw COM via HAction). ``_run_vision`` tries them in order.
    """
    candidates: List[Any] = []
    seen: set = set()

    def _add(obj):
        if obj is None or id(obj) in seen:
            return
        seen.add(id(obj))
        if hasattr(obj, "save_as"):
            candidates.append(obj)
            return
        raw = getattr(obj, "_raw", None) or getattr(obj, "hwp", None) or obj
        if raw is not None and hasattr(raw, "HAction") and hasattr(raw, "HParameterSet"):
            candidates.append(_PdfSaveAdapter(raw))

    # 1) live connector handle (did the edits). 2) its raw COM. 3) orig_hwp.
    conn_handle = getattr(getattr(processor, "_connector", None), "hwp", None)
    _add(conn_handle)
    _add(orig_hwp)
    return candidates


def _run_vision(processor, instruction: Optional[str], ops: List[Dict[str, Any]],
                model: str, orig_hwp: Any = None) -> Dict[str, Any]:
    """MODE B: render the edited doc and run the vision verifier.

    The PDF render runs on the MAIN thread: HWP COM is STA and the document was
    created in this apartment, so a worker thread either fails CoInitialize or
    "object not connected to server" on cross-apartment marshaling. A single
    save_as PDF call does not exhibit the multi-op apply hang, so no watchdog is
    needed here.
    """
    from services.hwp_renderer import render_doc_to_pngs
    from llm.vision_verifier import verify_vision

    handles = _save_capable_handles(processor, orig_hwp)
    if not handles:
        return {"items": [], "error": "no_render_handle", "page_count": 0}
    pngs: List[bytes] = []
    last_err = "render_failed"
    for h in handles:
        try:
            result = render_doc_to_pngs(h)
        except Exception as e:  # pragma: no cover - COM runtime
            last_err = f"render_exception:{e}"
            continue
        if isinstance(result, list) and result:
            pngs = result
            break
        last_err = "render_failed"
    if not pngs:
        return {"items": [], "error": last_err, "page_count": 0}
    verdict = verify_vision(
        images=pngs,
        user_intent=instruction or "",
        op_list=[{"id": o["id"], "op": o["op"], "content": o["content"]} for o in ops],
        structure_summary=None,
        model=model,
    )
    verdict["page_count"] = len(pngs)
    return verdict


def _set_openai_key_for_vision(api_key: str) -> None:
    """Inject the OpenAI key into the context vision_verifier reads from."""
    try:
        from llm import streaming_client as sc
        ctx = getattr(sc, "_rag_context", None)
        if not isinstance(ctx, dict):
            sc._rag_context = {}
            ctx = sc._rag_context
        ctx["openai_api_key"] = api_key
        ctx.setdefault("codex_mode", False)
    except Exception as e:  # pragma: no cover
        print(f"[auto_eval] could not set OpenAI key: {e}", file=sys.stderr)


def _set_codex_for_vision(token: str, account_id: str) -> None:
    """Inject codex OAuth creds so vision_verifier._build_default_client builds the
    SAME codex backend client (chatgpt.com/backend-api/codex) the app uses.

    NEVER logs the token.
    """
    try:
        from llm import streaming_client as sc
        ctx = getattr(sc, "_rag_context", None)
        if not isinstance(ctx, dict):
            sc._rag_context = {}
            ctx = sc._rag_context
        ctx["openai_api_key"] = token        # codex OAuth access_token
        ctx["codex_mode"] = True
        ctx["codex_account_id"] = account_id
    except Exception as e:  # pragma: no cover
        print(f"[auto_eval] could not set codex creds for vision: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# MODE full — codex LLM generates edits, apply via execute_delta (in-process
# replica of agent_process._stream_llm command->execute_delta wiring).
# ---------------------------------------------------------------------------

# Edit actions the app's main process actually forwards to execute_delta
# (electron/main/index.ts allowedDeltaActions + append_table_row). thinking /
# message are non-edit and skipped, mirroring the app exactly.
_APPLY_ACTIONS = {
    "edit_document", "format_text", "insert_note", "insert_source_ref",
    "write_text", "line_break", "append_table_row",
}


def _stream_codex_edits(
    processor,
    cvd_html: str,
    instruction: str,
    context_id: Optional[str],
    token: str,
    account_id: str,
    model: str,
    apply_budget_s: float = 240.0,
    max_apply_ops: int = 120,
) -> Dict[str, Any]:
    """Drive the codex LLM to generate edit commands, then apply each via
    execute_delta. Replicates agent_process._stream_llm's command wiring
    in-process (no stdio, no license gate, no telemetry — eval only).

    The streaming phase only *collects* commands (no COM in the callback) so a
    slow LLM/COM interaction can never block the stream. Application happens
    afterward on the main (STA) thread, bounded by an overall wall-clock budget
    and an op cap — so a pathologically slow batch stops applying and still
    records a verdict instead of hanging the whole run.

    Returns a summary dict: {commands, applied, edited, ops, messages, errors,
    stream_success, stream_error, token_usage, apply_timed_out}.
    """
    from llm.streaming_client import get_streaming_client, StreamingCommand

    client = get_streaming_client(token, codex_mode=True, codex_account_id=account_id)
    if model:
        client.model = model

    collected: List["StreamingCommand"] = []
    messages: List[str] = []
    counters = {"commands": 0, "applied": 0, "edited": 0, "errors": 0}

    def on_command(cmd: "StreamingCommand") -> None:
        # Collect only — NEVER touch COM here (keeps the stream thread free).
        action = cmd.action or ""
        if action in ("thinking", "message"):
            if action == "message":
                txt = cmd.message or cmd.content
                if txt:
                    messages.append(txt)
            return
        if action not in _APPLY_ACTIONS:
            return
        collected.append(cmd)

    stream_success = False
    stream_error = None
    token_usage: Dict[str, Any] = {}
    try:
        res = client.generate_commands_streaming(
            html=cvd_html,
            prompt=instruction,
            on_command=on_command,
            use_delta=True,
            use_html=False,
            compact_mode=True,
            enable_file_search=False,
        )
        stream_success = bool(getattr(res, "success", False))
        token_usage = getattr(res, "token_usage", {}) or {}
        if not stream_success:
            stream_error = getattr(res, "error", None) or getattr(res, "status", None)
    except Exception as e:
        stream_error = str(e)
        print(f"[auto_eval] codex stream failed: {e}", file=sys.stderr)

    # --- apply phase (main/STA thread): bounded by wall-clock budget + op cap ---
    import time as _time
    ops: List[Dict[str, Any]] = []
    apply_timed_out = False
    counters["commands"] = len(collected)
    _t_start = _time.time()
    for idx, cmd in enumerate(collected):
        # Stop issuing COM calls once the budget or op cap is exceeded; record
        # the remaining commands as skipped so the verdict is still complete.
        over_budget = (_time.time() - _t_start) > apply_budget_s
        over_cap = idx >= max_apply_ops
        if over_budget or over_cap:
            apply_timed_out = apply_timed_out or over_budget
            reason = "apply_budget_exceeded" if over_budget else "apply_op_cap_exceeded"
            ops.append({
                "id": cmd.id,
                "op": (cmd.metadata or {}).get("operation") or cmd.action,
                "action": cmd.action,
                "content": (cmd.content or "")[:200],
                "result": {"success": False, "error": reason},
            })
            counters["errors"] += 1
            continue
        action = cmd.action or ""
        delta: Dict[str, Any] = {
            "action": action,
            "id": cmd.id,
            "content": cmd.content,
            "message": cmd.message,
            "metadata": dict(cmd.metadata or {}),
            "rows": cmd.rows,
        }
        if context_id:
            delta["context_id"] = context_id
        try:
            result = processor.execute_delta(delta)
        except Exception as e:  # pragma: no cover - COM runtime
            result = {"success": False, "error": str(e)}
        if isinstance(result, dict):
            if result.get("success"):
                counters["applied"] += 1
                if result.get("edited"):
                    counters["edited"] += 1
            else:
                counters["errors"] += 1
        ops.append({
            "id": cmd.id,
            "op": (cmd.metadata or {}).get("operation") or action,
            "action": action,
            "content": (cmd.content or "")[:200],
            "result": result,
        })

    return {
        "commands": counters["commands"],
        "applied": counters["applied"],
        "edited": counters["edited"],
        "apply_errors": counters["errors"],
        "ops": ops,
        "messages": messages,
        "stream_success": stream_success,
        "stream_error": stream_error,
        "token_usage": token_usage,
        "apply_timed_out": apply_timed_out,
    }


def _run_case_full(
    case: Dict[str, Any],
    record: Dict[str, Any],
    vision_model: str,
    codex_token: Optional[str],
    codex_account_id: Optional[str],
    edit_model: str,
) -> Dict[str, Any]:
    """MODE full: codex edit-gen -> apply -> render -> codex vision verify.

    Fully guarded; never raises. No ground truth needed (R2 forms).
    """
    name = case.get("name", "unknown")
    instruction = case.get("instruction") or corpus_mod.DEFAULT_R2_INSTRUCTION

    if not codex_token:
        record["error"] = "no_codex_credentials"
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        return record

    processor = connector = hwp = temp_copy = None
    try:
        processor, connector, hwp, temp_copy = _open_headless_processor(
            case["template_hwp_path"]
        )

        prep = processor.prepare_context(prompt=instruction)
        if not isinstance(prep, dict) or not prep.get("success"):
            raise RuntimeError(f"prepare_context failed: {prep}")
        context_id = prep.get("context_id")
        # The app feeds document_graph_json to the LLM (electron/main/index.ts
        # docContent = document_graph_json). Fall back to cvd/html for older builds.
        cvd_html = (
            prep.get("document_graph_json")
            or prep.get("cvd")
            or prep.get("html")
            or ""
        )
        if isinstance(cvd_html, (dict, list)):
            import json as _json
            cvd_html = _json.dumps(cvd_html, ensure_ascii=False)
        record["cvd_chars"] = len(cvd_html)
        record["cvd_source"] = (
            "document_graph_json" if prep.get("document_graph_json")
            else ("cvd" if prep.get("cvd") else ("html" if prep.get("html") else "none"))
        )
        record["allowed_elements"] = len(prep.get("allowed_elements") or [])
        # The final prompt from prepare_context already wraps reference material.
        effective_instruction = prep.get("prompt") or instruction
        if not cvd_html:
            raise RuntimeError("prepare_context returned empty CVD payload "
                               "(document_graph_json/cvd/html all empty)")

        # --- codex LLM generates edits, applied in-process via execute_delta ---
        llm = _stream_codex_edits(
            processor, cvd_html, effective_instruction, context_id,
            codex_token, codex_account_id or "", edit_model,
        )
        record["llm"] = {
            "stream_success": llm["stream_success"],
            "stream_error": llm["stream_error"],
            "commands": llm["commands"],
            "applied": llm["applied"],
            "edited": llm["edited"],
            "apply_errors": llm["apply_errors"],
            "apply_timed_out": llm.get("apply_timed_out", False),
            "messages": llm["messages"][:5],
            "token_usage": llm["token_usage"],
            "ops": [
                {"id": o["id"], "action": o["action"], "op": o["op"],
                 "content": o["content"],
                 "ok": bool(isinstance(o["result"], dict) and o["result"].get("success")),
                 "edited": bool(isinstance(o["result"], dict) and o["result"].get("edited")),
                 "error": (o["result"].get("error") if isinstance(o["result"], dict) else None)}
                for o in llm["ops"]
            ],
        }
        record["applied_ops"] = llm["applied"]

        # --- render + codex vision verify (same codex client) ---
        # Vision uses the plain user intent as the answer key (not the
        # reference-wrapped prompt), per vision_verifier spec. Pass the original
        # pyhwpx hwp so render has a save_as-capable handle even after the
        # connector rebind swaps connector.hwp to a HwpRawWrapper.
        record["vision"] = _run_vision(
            processor, instruction, llm["ops"], vision_model, orig_hwp=hwp
        )
    except Exception as e:
        record["error"] = str(e)
        record["trace"] = traceback.format_exc()
        print(f"[auto_eval] case '{name}' (full) failed: {e}", file=sys.stderr)
    finally:
        _teardown_case(hwp, connector, temp_copy)

    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    return record


def _teardown_case(hwp, connector, temp_copy) -> None:
    """Quit the case's HWP instance and FULLY reset global session state.

    Critical for batch isolation: ``_ensure_connector`` consults the global
    runtime connector. If we only ``hwp.quit()`` and leave the (now dead)
    connector in global state, the *next* case's ``prepare_context`` finds it,
    runs ``validate_and_reconnect()`` and rebinds via SafeHwp to a stale
    instance (observed: ``PID=None, HWND=None``), which makes that case's
    execute_delta calls hang. Clearing the global connector + target ids here
    forces the next case to use its own freshly-injected headless connector.
    """
    try:
        if connector is not None:
            try:
                connector.disconnect()
            except Exception:
                pass
    finally:
        if hwp is not None:
            try:
                hwp.quit()
            except Exception:
                pass
        try:
            from engine.state.session_state import (
                store_runtime_connector,
                store_runtime_target_process_id,
                store_runtime_target_window_handle,
                reset_session_state,
            )
            store_runtime_connector(None)
            store_runtime_target_process_id(None)
            store_runtime_target_window_handle(None)
            reset_session_state()
        except Exception:
            pass
        if temp_copy:
            try:
                from engine.connection.hwp_file_opener import cleanup_temp_open_copy
                cleanup_temp_open_copy(temp_copy)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Per-case execution (each fully guarded — a bad case never aborts the run)
# ---------------------------------------------------------------------------

def run_case(
    case: Dict[str, Any],
    mode: str,
    vision_model: str,
    codex_token: Optional[str] = None,
    codex_account_id: Optional[str] = None,
    edit_model: str = "gpt-5.1",
) -> Dict[str, Any]:
    """Run a single case through the pipeline. Never raises.

    mode A    — deterministic diff replay + score (needs diff.json).
    mode B    — A + vision verify.
    mode full — codex LLM generates edits (no ground truth required) -> apply
                -> render -> codex vision verify. The unattended codex path.
    """
    name = case.get("name", "unknown")
    started = datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "name": name,
        "mode": mode,
        "source": case.get("source"),
        "template_hwp_path": case.get("template_hwp_path"),
        "r2_key": case.get("r2_key"),
        "instruction": case.get("instruction"),
        "started_at": started,
        "error": None,
        "score": None,
        "vision": None,
        "llm": None,
    }

    if mode == "full":
        return _run_case_full(
            case, record, vision_model, codex_token, codex_account_id, edit_model
        )

    # ---- MODE A / B (ground-truth replay) ----
    # Resolve ground-truth changes (diff.json preferred).
    changes = corpus_mod.load_diff_changes(case.get("diff_json_path"))
    if not changes:
        # No diff -> nothing to replay/score. Record as error (skipped).
        record["error"] = "no_diff_changes"
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        return record

    processor = connector = hwp = temp_copy = None
    try:
        processor, connector, hwp, temp_copy = _open_headless_processor(
            case["template_hwp_path"]
        )

        prep = processor.prepare_context(prompt=case.get("instruction") or "")
        if not isinstance(prep, dict) or not prep.get("success"):
            raise RuntimeError(f"prepare_context failed: {prep}")
        context_id = prep.get("context_id")

        ops = _apply_diff_edits(processor, changes, context_id)
        record["applied_ops"] = len(ops)

        applied_cells = _extract_applied_cells(processor)
        record["score"] = scorer_mod.score_against_diff(applied_cells, changes)

        if mode == "B":
            record["vision"] = _run_vision(
                processor, case.get("instruction"), ops, vision_model
            )
    except Exception as e:
        record["error"] = str(e)
        record["trace"] = traceback.format_exc()
        print(f"[auto_eval] case '{name}' failed: {e}", file=sys.stderr)
    finally:
        # Always tear down the isolated HWP instance + temp copy.
        if hwp is not None:
            try:
                hwp.quit()
            except Exception:
                pass
        if temp_copy:
            try:
                from engine.connection.hwp_file_opener import cleanup_temp_open_copy
                cleanup_temp_open_copy(temp_copy)
            except Exception:
                pass

    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    return record


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_summary(path: str, summary: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def _load_prior_summary(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"runs": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("runs", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"runs": []}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_table(case_results: List[Dict[str, Any]], agg: Dict[str, Any]) -> None:
    print("\n=== Auto-Eval Results ===")
    header = f"{'CASE':<32} {'RESULT':<8} {'ACC':>6}  cells(ok/wrong/miss)"
    print(header)
    print("-" * len(header))
    for case in case_results:
        score = case.get("score")
        if case.get("error") or not isinstance(score, dict):
            result = "ERROR"
            acc = "  -  "
            cells = case.get("error") or "n/a"
        else:
            acc_val = score.get("accuracy", 0.0)
            result = "PASS" if acc_val >= 1.0 else "FAIL"
            acc = f"{acc_val * 100:5.1f}%"
            cells = (
                f"{score.get('correct_count', 0)}/"
                f"{score.get('wrong_count', 0)}/"
                f"{score.get('missing_count', 0)}"
            )
        name = (case.get("name") or "")[:32]
        print(f"{name:<32} {result:<8} {acc:>6}  {cells}")
    print("-" * len(header))
    print(
        f"cases={agg['case_count']} pass={agg['pass_count']} "
        f"fail={agg['fail_count']} error={agg['error_count']} | "
        f"cells ok/wrong/miss = "
        f"{agg['correct_count']}/{agg['wrong_count']}/{agg['missing_count']} | "
        f"overall_accuracy={agg['overall_accuracy'] * 100:.1f}%"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.auto_eval",
        description="Headless auto-eval harness for the HWP verify loop.",
    )
    p.add_argument("--corpus", default=None,
                   help="Manifest .json, SQLite .db, or directory to scan for template_pairs. "
                        "Optional when --r2-keys / --r2-latest is given.")
    p.add_argument("--mode", choices=["A", "B", "full"], default="A",
                   help="A = deterministic ground-truth replay (default, no key). "
                        "B = + vision verification (needs --openai-key). "
                        "full = codex LLM edit-gen -> apply -> render -> codex vision "
                        "verify (no API key; uses ~/.codex/auth.json).")
    p.add_argument("--r2-keys", default=None,
                   help="Comma-separated R2 keys (e.g. 'hwp/<hash>.hwp,...') to pull "
                        "from the inserty-ai bucket as cases.")
    p.add_argument("--r2-latest", type=int, default=0,
                   help="Pull the latest N HWP forms from D1 hwp_uploads (R2 source).")
    p.add_argument("--r2-instruction", default=None,
                   help="Instruction for R2 cases (default: fill blank cells).")
    p.add_argument("--edit-model", default="gpt-5.5",
                   help="Codex edit-generation model for MODE full. The codex "
                        "ChatGPT backend only supports gpt-5.5 (default gpt-5.5).")
    p.add_argument("--out", default="results",
                   help="Output directory for eval-<tag>.jsonl and summary.json.")
    p.add_argument("--tag", default="run",
                   help="Run tag (used in eval-<tag>.jsonl). Explicit to keep runs deterministic.")
    p.add_argument("--min-accuracy", type=float, default=0.0,
                   help="Exit non-zero if overall accuracy < this (default 0.0 = report-only).")
    p.add_argument("--openai-key", default=None,
                   help="OpenAI API key for MODE B vision verification.")
    p.add_argument("--vision-model", default="gpt-5.1",
                   help="Vision model name for MODE B (default gpt-5.1).")
    p.add_argument("--user-data-path", default=None,
                   help="Electron userData dir (only for DB corpus rel-path resolution).")
    p.add_argument("--project-id", default=None,
                   help="Restrict DB corpus to a single project id.")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap number of cases (0 = all).")
    return p


def _print_full_table(case_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """MODE full reporter — no ground truth; summarize codex edits + vision."""
    print("\n=== Auto-Eval Results (MODE full: codex edit -> vision verify) ===")
    header = (f"{'CASE':<20} {'STREAM':<7} {'CMDS':>4} {'APPL':>4} {'EDIT':>4} "
              f"{'VISION':<10} {'V-ITEMS':<22}")
    print(header)
    print("-" * len(header))
    n_cases = len(case_results)
    n_err = n_vision_ok = n_vision_err = 0
    total_applied = total_cmds = 0
    for case in case_results:
        name = (case.get("name") or "")[:20]
        llm = case.get("llm") or {}
        vis = case.get("vision") or {}
        if case.get("error"):
            n_err += 1
            print(f"{name:<20} {'ERR':<7} {'-':>4} {'-':>4} {'-':>4} "
                  f"{'-':<10} {str(case.get('error'))[:22]}")
            continue
        stream = "ok" if llm.get("stream_success") else "fail"
        cmds = int(llm.get("commands", 0) or 0)
        appl = int(llm.get("applied", 0) or 0)
        edit = int(llm.get("edited", 0) or 0)
        total_cmds += cmds
        total_applied += appl
        if vis.get("error"):
            n_vision_err += 1
            vstat = "ERR"
            vitems = str(vis.get("error"))[:22]
        else:
            n_vision_ok += 1
            vstat = "ok"
            ov = vis.get("overall") or {}
            vitems = (f"ok={ov.get('correct',0)} wl={ov.get('wrong_location',0)} "
                      f"wc={ov.get('wrong_content',0)} miss={ov.get('missing',0)}")[:22]
        print(f"{name:<20} {stream:<7} {cmds:>4} {appl:>4} {edit:>4} "
              f"{vstat:<10} {vitems:<22}")
    print("-" * len(header))
    print(f"cases={n_cases} error={n_err} | total cmds={total_cmds} applied={total_applied} | "
          f"vision ok={n_vision_ok} err={n_vision_err}")
    return {
        "case_count": n_cases, "error_count": n_err,
        "total_commands": total_cmds, "total_applied": total_applied,
        "vision_ok": n_vision_ok, "vision_error": n_vision_err,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    codex_token = codex_account_id = None

    if args.mode == "B":
        key = args.openai_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            print("[auto_eval] MODE B requires --openai-key (or OPENAI_API_KEY env).",
                  file=sys.stderr)
            return 2
        _set_openai_key_for_vision(key)

    if args.mode == "full":
        # codex OAuth creds from ~/.codex/auth.json (NEVER printed).
        from eval.codex_auth import get_codex_credentials
        codex_token, codex_account_id = get_codex_credentials()
        if not codex_token:
            print("[auto_eval] MODE full requires codex login "
                  "(~/.codex/auth.json with tokens.access_token). "
                  "Run `codex login`.", file=sys.stderr)
            return 2
        # Same codex client for the vision verifier (no separate key).
        _set_codex_for_vision(codex_token, codex_account_id or "")
        # The codex ChatGPT backend only supports gpt-5.5. If the vision model
        # is still at its non-codex default, switch it so MODE B vision works.
        if args.vision_model == "gpt-5.1":
            args.vision_model = "gpt-5.5"
        print(f"[auto_eval] codex creds loaded (account_id "
              f"{'set' if codex_account_id else 'empty'}, token len ok); "
              f"edit_model={args.edit_model} vision_model={args.vision_model}")

    # ---- Corpus: R2 source (real user HWP) or local corpus ----
    cases: List[Dict[str, Any]] = []
    if args.r2_keys or args.r2_latest:
        keys = None
        if args.r2_keys:
            keys = [k.strip() for k in args.r2_keys.split(",") if k.strip()]
        print(f"[auto_eval] pulling forms from R2 "
              f"({'keys=' + str(len(keys)) if keys else 'latest=' + str(args.r2_latest)})...")
        cases = corpus_mod.load_from_r2(
            keys=keys,
            latest=args.r2_latest,
            instruction=args.r2_instruction,
        )
    elif args.corpus:
        cases = corpus_mod.load_corpus(
            args.corpus,
            user_data_path=args.user_data_path,
            project_id=args.project_id,
        )

    if args.limit and args.limit > 0:
        cases = cases[: args.limit]

    if not cases:
        print(
            f"[auto_eval] No corpus found "
            f"(corpus={args.corpus!r}, r2_keys={args.r2_keys!r}, r2_latest={args.r2_latest}).\n"
            "  Provide one of: --r2-latest N, --r2-keys k1,k2, or --corpus <dir/.json/.db>.\n",
            file=sys.stderr,
        )
        return 0

    print(f"[auto_eval] mode={args.mode} cases={len(cases)} tag={args.tag}")

    case_results: List[Dict[str, Any]] = []
    jsonl_path = os.path.join(args.out, f"eval-{args.tag}.jsonl")
    run_started = datetime.now(timezone.utc).isoformat()

    for case in cases:
        record = run_case(
            case, args.mode, args.vision_model,
            codex_token=codex_token, codex_account_id=codex_account_id,
            edit_model=args.edit_model,
        )
        case_results.append(record)
        _append_jsonl(jsonl_path, record)

    if args.mode == "full":
        agg = _print_full_table(case_results)
    else:
        agg = scorer_mod.aggregate_results(case_results)
        _print_table(case_results, agg)

    # Accumulate run summaries.
    summary_path = os.path.join(args.out, "summary.json")
    summary = _load_prior_summary(summary_path)
    summary["runs"].append({
        "tag": args.tag,
        "mode": args.mode,
        "corpus": args.corpus,
        "started_at": run_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "aggregate": agg,
    })
    summary["latest"] = summary["runs"][-1]
    _write_summary(summary_path, summary)

    print(f"[auto_eval] wrote {jsonl_path}")
    print(f"[auto_eval] updated {summary_path}")

    # MODE full has no ground-truth accuracy gate — report-only.
    overall_acc = agg.get("overall_accuracy")
    if overall_acc is not None and overall_acc < args.min_accuracy:
        print(
            f"[auto_eval] overall accuracy {overall_acc:.3f} "
            f"< min {args.min_accuracy:.3f} -> FAIL",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

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


def _run_vision(processor, instruction: Optional[str], ops: List[Dict[str, Any]],
                model: str) -> Dict[str, Any]:
    """MODE B: render the edited doc and run the vision verifier."""
    from services.hwp_renderer import render_doc_to_pngs
    from llm.vision_verifier import verify_vision

    connector = processor._connector  # noqa: SLF001
    hwp = connector.hwp
    pngs = render_doc_to_pngs(hwp)
    if not pngs:
        return {"items": [], "error": "render_failed", "page_count": 0}
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


# ---------------------------------------------------------------------------
# Per-case execution (each fully guarded — a bad case never aborts the run)
# ---------------------------------------------------------------------------

def run_case(case: Dict[str, Any], mode: str, vision_model: str) -> Dict[str, Any]:
    """Run a single case through the pipeline. Never raises."""
    name = case.get("name", "unknown")
    started = datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "name": name,
        "mode": mode,
        "source": case.get("source"),
        "template_hwp_path": case.get("template_hwp_path"),
        "started_at": started,
        "error": None,
        "score": None,
        "vision": None,
    }

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
    p.add_argument("--corpus", required=True,
                   help="Manifest .json, SQLite .db, or directory to scan for template_pairs.")
    p.add_argument("--mode", choices=["A", "B"], default="A",
                   help="A = deterministic ground-truth replay (default, no key). "
                        "B = + vision verification (needs --openai-key).")
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


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.mode == "B":
        key = args.openai_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            print("[auto_eval] MODE B requires --openai-key (or OPENAI_API_KEY env).",
                  file=sys.stderr)
            return 2
        _set_openai_key_for_vision(key)

    cases = corpus_mod.load_corpus(
        args.corpus,
        user_data_path=args.user_data_path,
        project_id=args.project_id,
    )
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]

    if not cases:
        print(
            f"[auto_eval] No corpus found at: {args.corpus}\n"
            "  Expected one of:\n"
            "    - a directory containing template_pairs/<pairId>/ "
            "(each with a template.hwp + diff.json or filled.cvd.md)\n"
            "    - a manifest .json listing cases\n"
            "    - the app SQLite .db (with --user-data-path)\n",
            file=sys.stderr,
        )
        return 0

    print(f"[auto_eval] mode={args.mode} cases={len(cases)} tag={args.tag}")

    case_results: List[Dict[str, Any]] = []
    jsonl_path = os.path.join(args.out, f"eval-{args.tag}.jsonl")
    run_started = datetime.now(timezone.utc).isoformat()

    for case in cases:
        record = run_case(case, args.mode, args.vision_model)
        case_results.append(record)
        _append_jsonl(jsonl_path, record)

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

    if agg["overall_accuracy"] < args.min_accuracy:
        print(
            f"[auto_eval] overall accuracy {agg['overall_accuracy']:.3f} "
            f"< min {args.min_accuracy:.3f} -> FAIL",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

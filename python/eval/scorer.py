# -*- coding: utf-8 -*-
"""Deterministic ground-truth scoring for the headless auto-eval harness.

Pure functions only — NO HWP, NO COM, NO LLM, NO I/O. Fully unit-testable.

The scorer compares the *post-edit cell texts* (extracted from the document
after replaying the known filled values) against the diff.json ``changes``
(the ground truth: which cell should hold which value). It answers the core
MODE A question: "did the known value land in the right cell?"

Verdicts (per cell):
    correct      — normalized actual text == normalized expected text.
    wrong_value  — cell has content, but it differs from the expected value.
    missing      — cell is empty/absent though a non-empty value was expected.
    error        — the change has no resolvable target cell id (malformed diff).
"""

import re
from typing import Any, Dict, List, Optional

_WS_RE = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    """Normalize text for deterministic comparison.

    - ``None`` -> ``""``.
    - Non-strings are coerced via ``str()``.
    - All runs of whitespace (incl. newlines/tabs) collapse to a single space.
    - Surrounding whitespace is stripped.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _WS_RE.sub(" ", value).strip()


def _build_applied_lookup(applied_cells: Dict[Any, Any]) -> Dict[str, Any]:
    """Index applied cells by stringified id so int/str keys both resolve."""
    lookup: Dict[str, Any] = {}
    for key, val in (applied_cells or {}).items():
        lookup[str(key)] = val
    return lookup


def _resolve_target_id(change: Dict[str, Any]) -> Optional[int]:
    """Pick the ground-truth target cell id for a diff change.

    Prefer ``filledTdId`` (the cell in the filled/answer doc); fall back to
    ``templateTdId``. Position-based diffs keep these aligned because template
    and filled share identical table structure.
    """
    for key in ("filledTdId", "templateTdId"):
        raw = change.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def score_against_diff(
    applied_cells: Dict[Any, Any],
    diff_changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Score post-edit cell texts against diff.json ground-truth changes.

    Args:
        applied_cells: ``{cell_id: text}`` extracted AFTER edits. ``cell_id``
            may be int or str; text may be ``None``.
        diff_changes: the ``changes`` list from diff.json. Each entry carries
            ``templateTdId`` / ``filledTdId`` (target cell) and ``filledValue``
            (the expected value). ``changeType == "deleted"`` expects empty.

    Returns:
        {
          "cells": [
             {"cell_id": int|None, "verdict": str,
              "expected": str, "actual": str, "change_type": str}, ...
          ],
          "correct_count": int,
          "wrong_count": int,
          "missing_count": int,
          "error_count": int,
          "total": int,            # number of scored changes
          "accuracy": float,       # correct / total (1.0 when total == 0)
        }
    """
    lookup = _build_applied_lookup(applied_cells)

    cells: List[Dict[str, Any]] = []
    correct = wrong = missing = error = 0

    for change in diff_changes or []:
        target_id = _resolve_target_id(change)
        change_type = change.get("changeType") or "modified"
        expected = normalize_text(change.get("filledValue"))

        if target_id is None:
            # Malformed change: no cell id to land a value in.
            error += 1
            cells.append({
                "cell_id": None,
                "verdict": "error",
                "expected": expected,
                "actual": "",
                "change_type": change_type,
            })
            continue

        actual_raw = lookup.get(str(target_id))
        actual = normalize_text(actual_raw)

        if actual == expected:
            verdict = "correct"
            correct += 1
        elif actual == "":
            # Expected a (non-empty) value but cell is blank/absent.
            verdict = "missing"
            missing += 1
        else:
            verdict = "wrong_value"
            wrong += 1

        cells.append({
            "cell_id": target_id,
            "verdict": verdict,
            "expected": expected,
            "actual": actual,
            "change_type": change_type,
        })

    total = len(cells)
    accuracy = (correct / total) if total else 1.0

    return {
        "cells": cells,
        "correct_count": correct,
        "wrong_count": wrong,
        "missing_count": missing,
        "error_count": error,
        "total": total,
        "accuracy": accuracy,
    }


def aggregate_results(
    case_results: List[Dict[str, Any]],
    pass_threshold: float = 1.0,
) -> Dict[str, Any]:
    """Aggregate per-case scores into an overall summary.

    Args:
        case_results: list of case dicts. Each should contain ``name`` and
            either ``score`` (a dict from :func:`score_against_diff`) or
            ``error`` (a truthy message marking a failed/crashed case).
        pass_threshold: per-case accuracy required to count as a pass
            (default 1.0 — every targeted cell must be correct).

    Returns:
        {
          "case_count": int,
          "pass_count": int, "fail_count": int, "error_count": int,
          "correct_count": int, "wrong_count": int, "missing_count": int,
          "total_cells": int,
          "overall_accuracy": float,  # correct_cells / total_cells
        }
    """
    case_count = len(case_results or [])
    passed = failed = errored = 0
    correct = wrong = missing = total_cells = 0

    for case in case_results or []:
        score = case.get("score")
        has_error = bool(case.get("error")) or not isinstance(score, dict)

        if has_error:
            errored += 1
            failed += 1
            continue

        correct += int(score.get("correct_count", 0))
        wrong += int(score.get("wrong_count", 0))
        missing += int(score.get("missing_count", 0))
        total_cells += int(score.get("total", 0))

        accuracy = float(score.get("accuracy", 0.0))
        if accuracy >= pass_threshold:
            passed += 1
        else:
            failed += 1

    overall_accuracy = (correct / total_cells) if total_cells else 0.0

    return {
        "case_count": case_count,
        "pass_count": passed,
        "fail_count": failed,
        "error_count": errored,
        "correct_count": correct,
        "wrong_count": wrong,
        "missing_count": missing,
        "total_cells": total_cells,
        "overall_accuracy": overall_accuracy,
    }

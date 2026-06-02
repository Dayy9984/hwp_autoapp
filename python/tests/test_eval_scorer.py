"""Unit tests for eval/scorer.py — pure, HWP-free, deterministic.

These tests define the contract for the ground-truth replay scorer used by
the headless auto-eval harness (MODE A). No HWP / COM / LLM dependency.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval.scorer import (  # noqa: E402
    normalize_text,
    score_against_diff,
    aggregate_results,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_none_becomes_empty(self):
        assert normalize_text(None) == ""

    def test_strips_surrounding_whitespace(self):
        assert normalize_text("  hello  ") == "hello"

    def test_collapses_internal_whitespace(self):
        assert normalize_text("a\t b\n c") == "a b c"

    def test_collapses_newlines_between_paragraphs(self):
        assert normalize_text("line1\n\n\nline2") == "line1 line2"

    def test_non_string_is_coerced(self):
        assert normalize_text(123) == "123"

    def test_already_clean_unchanged(self):
        assert normalize_text("2024년 1월") == "2024년 1월"


# ---------------------------------------------------------------------------
# score_against_diff — per-cell verdicts
# ---------------------------------------------------------------------------

def _change(template_id, filled_id, filled_value, template_value="", change_type="modified"):
    return {
        "templateTdId": template_id,
        "filledTdId": filled_id,
        "templateValue": template_value,
        "filledValue": filled_value,
        "changeType": change_type,
    }


class TestScoreAgainstDiff:
    def test_all_correct(self):
        diff = [_change(10, 10, "홍길동"), _change(11, 11, "2024-01-01")]
        applied = {10: "홍길동", 11: "2024-01-01"}
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 2
        assert result["wrong_count"] == 0
        assert result["missing_count"] == 0
        assert result["total"] == 2
        assert result["accuracy"] == 1.0
        verdicts = {c["cell_id"]: c["verdict"] for c in result["cells"]}
        assert verdicts == {10: "correct", 11: "correct"}

    def test_wrong_value(self):
        diff = [_change(10, 10, "홍길동")]
        applied = {10: "김철수"}
        result = score_against_diff(applied, diff)

        assert result["wrong_count"] == 1
        assert result["correct_count"] == 0
        assert result["missing_count"] == 0
        assert result["accuracy"] == 0.0
        cell = result["cells"][0]
        assert cell["verdict"] == "wrong_value"
        assert cell["expected"] == "홍길동"
        assert cell["actual"] == "김철수"

    def test_missing_when_cell_absent(self):
        diff = [_change(10, 10, "홍길동")]
        applied = {}  # cell never written
        result = score_against_diff(applied, diff)

        assert result["missing_count"] == 1
        assert result["correct_count"] == 0
        assert result["wrong_count"] == 0
        assert result["cells"][0]["verdict"] == "missing"

    def test_missing_when_cell_empty(self):
        diff = [_change(10, 10, "홍길동")]
        applied = {10: "   "}  # written but blank
        result = score_against_diff(applied, diff)

        assert result["missing_count"] == 1
        assert result["cells"][0]["verdict"] == "missing"

    def test_whitespace_normalized_before_compare(self):
        diff = [_change(10, 10, "홍길동  주임")]
        applied = {10: "홍길동 주임"}  # collapsed spacing
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 1
        assert result["cells"][0]["verdict"] == "correct"

    def test_string_cell_ids_match_int_diff_ids(self):
        # applied_cells keys may arrive as strings (from JSON/CVD parse)
        diff = [_change(10, 10, "값A")]
        applied = {"10": "값A"}
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 1

    def test_falls_back_to_template_td_id(self):
        # filledTdId missing/None → use templateTdId as target
        diff = [_change(10, None, "값B")]
        applied = {10: "값B"}
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 1
        assert result["cells"][0]["cell_id"] == 10

    def test_mixed_verdicts_accuracy(self):
        diff = [
            _change(1, 1, "a"),   # correct
            _change(2, 2, "b"),   # wrong
            _change(3, 3, "c"),   # missing
            _change(4, 4, "d"),   # correct
        ]
        applied = {1: "a", 2: "X", 4: "d"}
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 2
        assert result["wrong_count"] == 1
        assert result["missing_count"] == 1
        assert result["total"] == 4
        assert result["accuracy"] == 0.5

    def test_deleted_change_expects_empty(self):
        # changeType 'deleted' → target should be empty after edit
        diff = [_change(10, 10, "", template_value="old", change_type="deleted")]
        applied = {10: ""}
        result = score_against_diff(applied, diff)

        assert result["correct_count"] == 1
        assert result["cells"][0]["verdict"] == "correct"

    def test_deleted_change_still_filled_is_wrong(self):
        diff = [_change(10, 10, "", template_value="old", change_type="deleted")]
        applied = {10: "still here"}
        result = score_against_diff(applied, diff)

        # expected empty but value present → not correct
        assert result["correct_count"] == 0
        assert result["wrong_count"] == 1

    def test_empty_diff_is_perfect_and_zero_total(self):
        result = score_against_diff({}, [])
        assert result["total"] == 0
        assert result["accuracy"] == 1.0
        assert result["correct_count"] == 0

    def test_change_without_any_target_id_is_skipped_as_error(self):
        diff = [{"filledValue": "x"}]  # no templateTdId / filledTdId
        result = score_against_diff({}, diff)
        # unresolvable target recorded but not counted as correct
        assert result["correct_count"] == 0
        assert result["total"] == 1
        assert result["cells"][0]["verdict"] in ("missing", "error")


# ---------------------------------------------------------------------------
# aggregate_results — across cases
# ---------------------------------------------------------------------------

class TestAggregateResults:
    def _case(self, name, correct, wrong, missing, error=None):
        total = correct + wrong + missing
        return {
            "name": name,
            "error": error,
            "score": {
                "correct_count": correct,
                "wrong_count": wrong,
                "missing_count": missing,
                "total": total,
                "accuracy": (correct / total) if total else 1.0,
            },
        }

    def test_empty_cases(self):
        agg = aggregate_results([])
        assert agg["case_count"] == 0
        assert agg["overall_accuracy"] == 0.0
        assert agg["pass_count"] == 0

    def test_aggregates_cell_counts(self):
        cases = [
            self._case("a", 2, 0, 0),  # 2/2 pass
            self._case("b", 1, 1, 0),  # 1/2
            self._case("c", 0, 0, 2),  # 0/2
        ]
        agg = aggregate_results(cases)

        assert agg["case_count"] == 3
        assert agg["correct_count"] == 3
        assert agg["wrong_count"] == 1
        assert agg["missing_count"] == 2
        assert agg["total_cells"] == 6
        # overall = correct / total cells = 3/6
        assert agg["overall_accuracy"] == 0.5

    def test_pass_count_uses_full_accuracy(self):
        cases = [
            self._case("a", 2, 0, 0),  # accuracy 1.0 → pass
            self._case("b", 1, 1, 0),  # accuracy 0.5 → fail
        ]
        agg = aggregate_results(cases)
        assert agg["pass_count"] == 1
        assert agg["fail_count"] == 1

    def test_error_cases_counted_as_failures(self):
        cases = [
            self._case("a", 2, 0, 0),
            {"name": "b", "error": "HWP open failed", "score": None},
        ]
        agg = aggregate_results(cases)
        assert agg["error_count"] == 1
        assert agg["pass_count"] == 1
        assert agg["fail_count"] == 1
        assert agg["case_count"] == 2

    def test_custom_pass_threshold(self):
        cases = [
            self._case("a", 1, 1, 0),  # 0.5
            self._case("b", 3, 1, 0),  # 0.75
        ]
        agg = aggregate_results(cases, pass_threshold=0.7)
        assert agg["pass_count"] == 1
        assert agg["fail_count"] == 1

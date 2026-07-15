import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
MANUAL_DIR = os.path.abspath(os.path.dirname(__file__))
if MANUAL_DIR not in sys.path:
    sys.path.insert(0, MANUAL_DIR)


from compare_real_doc_hdml_hwpml_graph import run_real_doc_compare


DEFAULT_FILE = r"C:\Users\dlgkr\Downloads\[신청서] 2026년 부니콘 씨드 육성사업(부산 예비창업패키지) (1) (2).hwp"


def _metric(report: Dict[str, Any], path: List[str], default: float = 0.0) -> float:
    current: Any = report
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    try:
        return float(current)
    except Exception:
        return default


def _evaluate_report(
    report: Dict[str, Any],
    *,
    min_structure: float,
    min_overall_v2: float,
    min_hwpml_signature: float,
    min_hwpml_style: float,
) -> List[str]:
    failures: List[str] = []
    if not report.get("success"):
        failures.append(f"compare failed: {report.get('error')}")
        return failures

    structure_score = _metric(report, ["comparison", "scores_v2", "structure_score_v2"])
    overall_score_v2 = _metric(report, ["comparison", "scores_v2", "overall_score_v2"])
    hwpml_signature = _metric(report, ["hwpml_graph_comparison", "ratios", "signature_coverage"])
    hwpml_style = _metric(report, ["hwpml_graph_comparison", "overall_style_score"])

    if structure_score < min_structure:
        failures.append(f"structure_score_v2 {structure_score:.6f} < {min_structure:.6f}")
    if overall_score_v2 < min_overall_v2:
        failures.append(f"overall_score_v2 {overall_score_v2:.6f} < {min_overall_v2:.6f}")
    if hwpml_signature < min_hwpml_signature:
        failures.append(f"hwpml signature_coverage {hwpml_signature:.6f} < {min_hwpml_signature:.6f}")
    if hwpml_style < min_hwpml_style:
        failures.append(f"hwpml overall_style_score {hwpml_style:.6f} < {min_hwpml_style:.6f}")

    warnings = list((((report.get("graph") or {}).get("parse_warnings")) or []))
    if warnings:
        failures.append(f"graph parse_warnings present: {', '.join(str(w) for w in warnings)}")

    hdml_td = int(((report.get("hdml") or {}).get("td_count")) or 0)
    graph_td = int(((report.get("graph") or {}).get("td_count")) or 0)
    if hdml_td != graph_td:
        failures.append(f"hdml td_count({hdml_td}) != graph td_count({graph_td})")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Bunicon (pages 2~6) completeness regression gate for runtime/hdml registry sources."
    )
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--start-page", type=int, default=2)
    parser.add_argument("--end-page", type=int, default=6)
    parser.add_argument("--sources", default="runtime,hdml", help="comma-separated: runtime,hdml")
    parser.add_argument("--min-structure", type=float, default=0.99)
    parser.add_argument("--min-overall-v2", type=float, default=0.90)
    parser.add_argument("--min-hwpml-signature", type=float, default=0.98)
    parser.add_argument("--min-hwpml-style", type=float, default=0.85)
    parser.add_argument("--max-runtime-drop-vs-hdml", type=float, default=0.02)
    parser.add_argument("--out-dir", default=os.path.abspath(os.path.join(ROOT, "..", "tmp")))
    args = parser.parse_args()

    requested_sources = [s.strip().lower() for s in str(args.sources or "").split(",") if s.strip()]
    sources = [s for s in requested_sources if s in {"runtime", "hdml"}]
    if not sources:
        sources = ["runtime", "hdml"]

    os.makedirs(args.out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    reports: Dict[str, Dict[str, Any]] = {}
    failures_by_source: Dict[str, List[str]] = {}
    report_paths: Dict[str, str] = {}

    for source in sources:
        report = run_real_doc_compare(
            file_path=args.file,
            start_page=int(args.start_page),
            end_page=int(args.end_page),
            registry_source=source,
        )
        reports[source] = report

        report_path = os.path.join(
            args.out_dir,
            f"bunicon_2_6_compare_{source}_{timestamp}.json",
        )
        report_paths[source] = report_path
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        failures_by_source[source] = _evaluate_report(
            report,
            min_structure=float(args.min_structure),
            min_overall_v2=float(args.min_overall_v2),
            min_hwpml_signature=float(args.min_hwpml_signature),
            min_hwpml_style=float(args.min_hwpml_style),
        )

    cross_failures: List[str] = []
    if "runtime" in reports and "hdml" in reports:
        runtime_score = _metric(reports["runtime"], ["comparison", "scores_v2", "overall_score_v2"])
        hdml_score = _metric(reports["hdml"], ["comparison", "scores_v2", "overall_score_v2"])
        if (hdml_score - runtime_score) > float(args.max_runtime_drop_vs_hdml):
            cross_failures.append(
                f"runtime overall_score_v2 dropped too much vs hdml: runtime={runtime_score:.6f}, hdml={hdml_score:.6f}"
            )

    all_failures: List[str] = []
    for source in sources:
        for failure in failures_by_source.get(source, []):
            all_failures.append(f"[{source}] {failure}")
    all_failures.extend(cross_failures)

    summary = {
        "success": len(all_failures) == 0,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": args.file,
        "pages": {"start": int(args.start_page), "end": int(args.end_page)},
        "sources": sources,
        "report_paths": report_paths,
        "thresholds": {
            "min_structure": float(args.min_structure),
            "min_overall_v2": float(args.min_overall_v2),
            "min_hwpml_signature": float(args.min_hwpml_signature),
            "min_hwpml_style": float(args.min_hwpml_style),
            "max_runtime_drop_vs_hdml": float(args.max_runtime_drop_vs_hdml),
        },
        "failures": all_failures,
        "metrics": {
            source: {
                "structure_score_v2": _metric(report, ["comparison", "scores_v2", "structure_score_v2"]),
                "overall_score_v2": _metric(report, ["comparison", "scores_v2", "overall_score_v2"]),
                "hwpml_signature_coverage": _metric(report, ["hwpml_graph_comparison", "ratios", "signature_coverage"]),
                "hwpml_overall_style_score": _metric(report, ["hwpml_graph_comparison", "overall_style_score"]),
                "hdml_td_count": int(((report.get("hdml") or {}).get("td_count")) or 0),
                "graph_td_count": int(((report.get("graph") or {}).get("td_count")) or 0),
            }
            for source, report in reports.items()
        },
    }

    summary_path = os.path.join(args.out_dir, f"bunicon_2_6_quality_gate_{timestamp}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

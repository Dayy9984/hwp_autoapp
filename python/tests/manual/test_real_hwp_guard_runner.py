import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


from hwp_com_process import DocumentProcessor
from processing.extraction.cvd_extractor import CVDExtractor
from processing.structure.segment_registry import SegmentRegistry
from engine.connection.security_module import activate_security_module


def _make_processor() -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor._target_uid_to_id = {}
    processor._id_to_target_uid = {}
    return processor


def run_real_doc_guard_test(file_path: str, start_page: int, end_page: int) -> Dict[str, Any]:
    try:
        from pyhwpx import Hwp
    except Exception as exc:
        return {
            "success": False,
            "error": f"pyhwpx import failed: {exc}",
        }

    hwp = None
    try:
        if not os.path.exists(file_path):
            return {"success": False, "error": f"file not found: {file_path}"}

        hwp = Hwp(new=True, visible=False)
        sec_ok, module_id, _dll_path = activate_security_module(hwp)
        if not sec_ok:
            return {
                "success": False,
                "error": "security module activation failed (FilePathCheckDLL)",
            }

        open_result = hwp.open(file_path)
        if open_result is False:
            return {"success": False, "error": "hwp.open returned False"}

        extractor = CVDExtractor(hwp)
        extracted = extractor.extract_cvd(
            {
                "start": int(start_page),
                "end": int(end_page),
                "current_page": int(start_page),
            }
        )
        if not extracted:
            return {"success": False, "error": "extract_cvd returned None"}

        cvd_text, id_to_pos = extracted
        registry = SegmentRegistry((cvd_text, id_to_pos))
        processor = _make_processor()

        td_segments = [
            seg for seg in registry.segments.values()
            if (getattr(seg, "segment_type", None) or getattr(seg, "block_type", None)) == "td"
        ]

        reasons_counter = Counter()
        exact_match_ok = 0
        exact_match_failed = 0
        mismatch_rejected = 0
        mismatch_unexpected_ok = 0
        diagonal_ids: List[int] = []

        for seg in td_segments:
            seg_id = int(seg.id)
            target_uid = f"td-{seg_id}"
            processor._target_uid_to_id[target_uid] = seg_id
            processor._id_to_target_uid[str(seg_id)] = target_uid

            attrs = getattr(seg, "attrs", {}) or {}
            if str(attrs.get("data-diagonal", "")).strip() in {"1", "true", "True"}:
                diagonal_ids.append(seg_id)

            contract = {
                "target_uid": target_uid,
                "meta": {
                    "block_type": "td",
                    "scope_table_id": getattr(seg, "table_group_id", None),
                    "td_sig_v1": getattr(seg, "td_sig", None),
                    "table_path": getattr(seg, "table_path", None),
                },
            }
            ok, reason = processor._validate_target_contract(
                block_manager=registry,
                element_id=seg_id,
                contract=contract,
            )
            if ok:
                exact_match_ok += 1
            else:
                exact_match_failed += 1
                if reason:
                    reasons_counter[reason] += 1

            bad_sig = (getattr(seg, "td_sig", None) or "") + "__mismatch"
            bad_contract = {
                "target_uid": target_uid,
                "meta": {
                    "block_type": "td",
                    "scope_table_id": getattr(seg, "table_group_id", None),
                    "td_sig_v1": bad_sig,
                    "table_path": getattr(seg, "table_path", None),
                },
            }
            mismatch_ok, mismatch_reason = processor._validate_target_contract(
                block_manager=registry,
                element_id=seg_id,
                contract=bad_contract,
            )
            if mismatch_ok:
                mismatch_unexpected_ok += 1
            else:
                mismatch_rejected += 1
                if mismatch_reason:
                    reasons_counter[mismatch_reason] += 1

        return {
            "success": True,
            "file_path": file_path,
            "pages": {"start": start_page, "end": end_page},
            "security_module_id": module_id,
            "cvd_length": len(cvd_text),
            "id_count": len(id_to_pos),
            "td_count": len(td_segments),
            "guard_reason_counts": dict(reasons_counter),
            "exact_match_ok": exact_match_ok,
            "exact_match_failed": exact_match_failed,
            "mismatch_rejected": mismatch_rejected,
            "mismatch_unexpected_ok": mismatch_unexpected_ok,
            "diagonal_id_count": len(diagonal_ids),
            "diagonal_id_samples": diagonal_ids[:20],
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
    finally:
        if hwp is not None:
            try:
                hwp.quit()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run hybrid cell-guard test on a real HWP document without launching Inserty UI."
    )
    parser.add_argument("--file", required=True, help="absolute path to target .hwp file")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=5)
    parser.add_argument("--out", default="", help="optional json output path")
    args = parser.parse_args()

    result = run_real_doc_guard_test(
        file_path=args.file,
        start_page=args.start_page,
        end_page=args.end_page,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

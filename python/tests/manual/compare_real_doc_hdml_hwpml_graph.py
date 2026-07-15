import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


from engine.connection.security_module import activate_security_module
from processing.extraction.hdml_extractor import HDMLExtractor
from processing.extraction.hwp_raw_wrapper import HwpRawWrapper
from processing.structure.hwpml_direct_graph_builder import build_document_graph_from_hwpml
from processing.structure.hwpml_direct_graph_builder import extract_raw_hwpml_nodes
from processing.structure.hwpml_runtime_registry_builder import build_segment_registry_from_extractor
from processing.structure.enriched_hdml_serializer import serialize_enriched_hdml
from processing.structure.segment_registry import SegmentRegistry


_ATTR_RE = re.compile(r'([a-zA-Z_:-]+)="([^"]*)"')
_HDML_TD_TAG_RE = re.compile(r"<td\b([^>]*)>", flags=re.IGNORECASE)
_HDML_P_TAG_RE = re.compile(r"<p\b([^>]*)>", flags=re.IGNORECASE)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _local_name(tag: Any) -> str:
    text = str(tag or "")
    if "}" in text:
        return text.split("}", 1)[1]
    return text


def _parse_attrs(attr_text: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for key, value in _ATTR_RE.findall(attr_text or ""):
        attrs[key] = value
    return attrs


def _normalize_color(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("#"):
        hex_part = text[1:].strip()
        if len(hex_part) == 3:
            hex_part = "".join(ch * 2 for ch in hex_part)
        if len(hex_part) == 6:
            return f"#{hex_part.lower()}"
    return text.lower()


def _split_csv_tokens(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return sorted(str(v).strip().lower() for v in value if str(v).strip())
    text = str(value).strip()
    if not text:
        return []
    return sorted(part.strip().lower() for part in text.split(",") if part.strip())


def _truthy(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def _normalize_id_to_pos(id_to_pos: Dict[Any, Any]) -> Dict[int, Tuple[int, int, int]]:
    out: Dict[int, Tuple[int, int, int]] = {}
    for raw_id, raw_pos in (id_to_pos or {}).items():
        sid = _safe_int(raw_id)
        if sid is None:
            continue
        if not isinstance(raw_pos, (list, tuple)) or len(raw_pos) < 3:
            continue
        try:
            out[sid] = (int(raw_pos[0]), int(raw_pos[1]), int(raw_pos[2]))
        except Exception:
            continue
    return out


def _normalize_pos_to_page(raw_map: Dict[Any, Any]) -> Dict[Tuple[int, int, int], int]:
    out: Dict[Tuple[int, int, int], int] = {}
    for raw_pos, raw_page in (raw_map or {}).items():
        if not isinstance(raw_pos, (list, tuple)) or len(raw_pos) < 3:
            continue
        page_no = _safe_int(raw_page)
        if page_no is None:
            continue
        try:
            out[(int(raw_pos[0]), int(raw_pos[1]), int(raw_pos[2]))] = page_no
        except Exception:
            continue
    return out


def _extract_hdml_td_entries(
    hdml_text: str,
    id_to_pos: Dict[int, Tuple[int, int, int]],
    pos_to_page: Dict[Tuple[int, int, int], int],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for match in _HDML_TD_TAG_RE.finditer(hdml_text or ""):
        attrs = _parse_attrs(match.group(1))
        sid = _safe_int(attrs.get("id"))
        pos = id_to_pos.get(sid) if sid is not None else None
        page = pos_to_page.get(pos) if pos else None
        border_sides = _split_csv_tokens(attrs.get("border"))
        present = {
            "td_sig_v1": "data-td-sig" in attrs,
            "row": "data-row" in attrs,
            "col": "data-col" in attrs,
            "rowspan": "data-rowspan" in attrs,
            "colspan": "data-colspan" in attrs,
            "table_path": "data-table-path" in attrs,
            "page": ("data-page" in attrs) or ("data-page-no" in attrs),
            "bgcolor": "bgcolor" in attrs,
            "text_color": ("color" in attrs) or ("font-color" in attrs),
            "font_size": "font-size" in attrs,
            "border_sides": "border" in attrs,
            "border_complete": "border" in attrs,
            "diagonal": "data-diagonal" in attrs,
            "diagonal_flags": "data-diagonal-flags" in attrs,
        }
        entries.append(
            {
                "id": sid,
                "td_sig_v1": attrs.get("data-td-sig"),
                "row": _safe_int(attrs.get("data-row")),
                "col": _safe_int(attrs.get("data-col")),
                "rowspan": _safe_int(attrs.get("data-rowspan")) or 1,
                "colspan": _safe_int(attrs.get("data-colspan")) or 1,
                "table_path": attrs.get("data-table-path") or None,
                "page": page,
                "bgcolor": _normalize_color(attrs.get("bgcolor")),
                "text_color": _normalize_color(attrs.get("color") or attrs.get("font-color")),
                "font_size": (attrs.get("font-size") or "").strip() or None,
                "border_sides": border_sides,
                "border_complete": len(border_sides) == 4,
                "diagonal": _truthy(attrs.get("data-diagonal")),
                "diagonal_flags": _split_csv_tokens(attrs.get("data-diagonal-flags")),
                "_present": present,
            }
        )
    return entries


def _extract_hdml_p_count(hdml_text: str) -> int:
    return len(_HDML_P_TAG_RE.findall(hdml_text or ""))


def _iter_elements_by_local_name(root: ET.Element, name: str) -> Iterable[ET.Element]:
    target = (name or "").strip().upper()
    for elem in root.iter():
        if _local_name(elem.tag).strip().upper() == target:
            yield elem


def _extract_hwpml_summary(hwpml_text: str) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "exists": bool(hwpml_text),
        "length": len(hwpml_text or ""),
        "xml_parse_ok": False,
        "table_count": 0,
        "row_count": 0,
        "cell_count": 0,
        "paragraph_count": 0,
        "char_count": 0,
        "char_text_len": 0,
        "parse_error": "",
    }
    if not hwpml_text:
        return summary

    try:
        root = ET.fromstring(hwpml_text)
        summary["xml_parse_ok"] = True
    except Exception as exc:
        summary["parse_error"] = str(exc)
        return summary

    tables = list(_iter_elements_by_local_name(root, "TABLE"))
    rows = list(_iter_elements_by_local_name(root, "ROW"))
    cells = list(_iter_elements_by_local_name(root, "CELL"))
    paras = list(_iter_elements_by_local_name(root, "P"))
    chars = list(_iter_elements_by_local_name(root, "CHAR"))

    summary["table_count"] = len(tables)
    summary["row_count"] = len(rows)
    summary["cell_count"] = len(cells)
    summary["paragraph_count"] = len(paras)
    summary["char_count"] = len(chars)
    summary["char_text_len"] = sum(len((ch.text or "").strip()) for ch in chars)
    return summary


def _extract_page_range_hwpml_saveblock(
    pyhwpx_hwp: Any,
    start_page: int,
    end_page: int,
) -> str:
    raw = getattr(pyhwpx_hwp, "hwp", pyhwpx_hwp)
    wrapper = HwpRawWrapper(raw)
    extractor = HDMLExtractor(wrapper)

    start_pos = extractor._move_to_page_start(wrapper, int(start_page))
    if not start_pos:
        return ""

    try:
        try:
            wrapper.set_pos(*start_pos)
        except Exception:
            pass

        extractor._page_down(wrapper, int(start_page), int(end_page))
        hwpml_text = wrapper.GetTextFile("HWPML2X", option="saveblock") or ""
        return hwpml_text
    except Exception:
        return ""
    finally:
        try:
            wrapper.Cancel()
        except Exception:
            pass
        try:
            wrapper.set_pos(*start_pos)
        except Exception:
            pass


def _normalize_graph_td_entries(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for node in list((graph or {}).get("nodes") or []):
        if not isinstance(node, dict):
            continue
        if str(node.get("block_type") or "").lower() != "td":
            continue
        entries.append(
            {
                "id": _safe_int(node.get("id")),
                "td_sig_v1": node.get("td_sig_v1"),
                "row": _safe_int(node.get("row")),
                "col": _safe_int(node.get("col")),
                "rowspan": _safe_int(node.get("rowspan")) or 1,
                "colspan": _safe_int(node.get("colspan")) or 1,
                "table_path": node.get("table_path") or None,
                "page": _safe_int(node.get("page")),
                "bgcolor": _normalize_color(node.get("bgcolor")),
                "text_color": _normalize_color(node.get("text_color")),
                "font_size": (str(node.get("font_size")).strip() if node.get("font_size") is not None else None),
                "border_sides": _split_csv_tokens(node.get("border_sides")),
                "border_complete": bool(node.get("border_complete", False)),
                "diagonal": bool(node.get("diagonal", False)),
                "diagonal_flags": _split_csv_tokens(node.get("diagonal_flags")),
            }
        )
    return entries


def _normalize_hwpml_td_entries(raw_td_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for raw in raw_td_nodes or []:
        if not isinstance(raw, dict):
            continue
        entries.append(
            {
                "row": _safe_int(raw.get("row")),
                "col": _safe_int(raw.get("col")),
                "rowspan": _safe_int(raw.get("rowspan")) or 1,
                "colspan": _safe_int(raw.get("colspan")) or 1,
                "table_path": raw.get("table_path") or None,
                "bgcolor": _normalize_color(raw.get("bgcolor")),
                "border_sides": _split_csv_tokens(raw.get("border_sides")),
                "border_complete": bool(raw.get("border_complete", False)),
                "diagonal": bool(raw.get("diagonal", False)),
                "diagonal_flags": _split_csv_tokens(raw.get("diagonal_flags")),
            }
        )
    return entries


def _rekey_graph_entries_by_hdml_sig(
    hdml_td_entries: List[Dict[str, Any]],
    graph_td_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    sig_to_hdml_id: Dict[str, int] = {}
    for entry in hdml_td_entries or []:
        sig = entry.get("td_sig_v1")
        sid = _safe_int(entry.get("id"))
        if sig and sid is not None and sig not in sig_to_hdml_id:
            sig_to_hdml_id[str(sig)] = sid

    rekeyed: List[Dict[str, Any]] = []
    for entry in graph_td_entries or []:
        copied = dict(entry)
        sig = copied.get("td_sig_v1")
        mapped_id = sig_to_hdml_id.get(str(sig)) if sig else None
        if mapped_id is not None:
            copied["id"] = mapped_id
        rekeyed.append(copied)
    return rekeyed


def _histogram(values: Iterable[Any]) -> Dict[str, int]:
    cnt = Counter()
    for value in values:
        key = str(value) if value is not None else "None"
        cnt[key] += 1
    return dict(cnt)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(float(numerator) / float(denominator), 6)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.strip()) == 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _compare_hdml_vs_graph(
    hdml_td_entries: List[Dict[str, Any]],
    graph_td_entries: List[Dict[str, Any]],
    start_page: int,
    end_page: int,
) -> Dict[str, Any]:
    hdml_by_id = {e["id"]: e for e in hdml_td_entries if e.get("id") is not None}
    graph_by_id = {e["id"]: e for e in graph_td_entries if e.get("id") is not None}
    common_ids = sorted(set(hdml_by_id.keys()) & set(graph_by_id.keys()))

    hdml_by_sig = {e["td_sig_v1"]: e for e in hdml_td_entries if e.get("td_sig_v1")}
    graph_by_sig = {e["td_sig_v1"]: e for e in graph_td_entries if e.get("td_sig_v1")}
    common_sigs = sorted(set(hdml_by_sig.keys()) & set(graph_by_sig.keys()))

    field_match_counts = {
        "td_sig_v1": 0,
        "row": 0,
        "col": 0,
        "rowspan": 0,
        "colspan": 0,
        "table_path": 0,
        "page": 0,
        "bgcolor": 0,
        "text_color": 0,
        "font_size": 0,
        "border_sides": 0,
        "border_complete": 0,
        "diagonal": 0,
        "diagonal_flags": 0,
    }
    field_nonnull_match_counts = {key: 0 for key in field_match_counts.keys()}
    field_nonnull_denominators = {key: 0 for key in field_match_counts.keys()}
    style_fields = {
        "bgcolor",
        "text_color",
        "font_size",
        "border_sides",
        "border_complete",
        "diagonal",
        "diagonal_flags",
    }
    field_enrichment_counts = {key: 0 for key in style_fields}
    field_enrichment_denominators = {key: 0 for key in style_fields}
    mismatches: List[Dict[str, Any]] = []

    for sid in common_ids:
        hdml = hdml_by_id[sid]
        graph = graph_by_id[sid]
        mismatch: Dict[str, Any] = {"id": sid, "fields": {}}
        present_map = hdml.get("_present") if isinstance(hdml.get("_present"), dict) else {}

        for field in field_match_counts.keys():
            hdml_value = hdml.get(field)
            graph_value = graph.get(field)
            hdml_present = bool(present_map.get(field, False))

            if hdml_value == graph_value:
                field_match_counts[field] += 1
            elif field not in style_fields or hdml_present or not _is_empty(hdml_value):
                mismatch["fields"][field] = {
                    "hdml": hdml_value,
                    "graph": graph_value,
                }

            if field in style_fields:
                if hdml_present:
                    field_nonnull_denominators[field] += 1
                    if hdml_value == graph_value:
                        field_nonnull_match_counts[field] += 1
                else:
                    field_enrichment_denominators[field] += 1
                    graph_has_value = not _is_empty(graph_value)
                    if field in {"border_complete", "diagonal"}:
                        graph_has_value = bool(graph_value)
                    if graph_has_value:
                        field_enrichment_counts[field] += 1
            else:
                field_nonnull_denominators[field] += 1
                if hdml_value == graph_value:
                    field_nonnull_match_counts[field] += 1

        if mismatch["fields"]:
            mismatches.append(mismatch)

    graph_in_range = [
        e for e in graph_td_entries
        if e.get("page") is not None and int(start_page) <= int(e["page"]) <= int(end_page)
    ]

    ratios = {
        "id_coverage": _ratio(len(common_ids), len(hdml_by_id)),
        "td_sig_coverage": _ratio(len(common_sigs), len(hdml_by_sig)),
        "td_sig_match_ratio": _ratio(field_match_counts["td_sig_v1"], len(common_ids)),
        "row_match_ratio": _ratio(field_match_counts["row"], len(common_ids)),
        "col_match_ratio": _ratio(field_match_counts["col"], len(common_ids)),
        "rowspan_match_ratio": _ratio(field_match_counts["rowspan"], len(common_ids)),
        "colspan_match_ratio": _ratio(field_match_counts["colspan"], len(common_ids)),
        "table_path_match_ratio": _ratio(field_match_counts["table_path"], len(common_ids)),
        "page_match_ratio": _ratio(field_match_counts["page"], len(common_ids)),
        "bgcolor_match_ratio": _ratio(field_match_counts["bgcolor"], len(common_ids)),
        "text_color_match_ratio": _ratio(field_match_counts["text_color"], len(common_ids)),
        "font_size_match_ratio": _ratio(field_match_counts["font_size"], len(common_ids)),
        "border_sides_match_ratio": _ratio(field_match_counts["border_sides"], len(common_ids)),
        "border_complete_match_ratio": _ratio(field_match_counts["border_complete"], len(common_ids)),
        "diagonal_match_ratio": _ratio(field_match_counts["diagonal"], len(common_ids)),
        "diagonal_flags_match_ratio": _ratio(field_match_counts["diagonal_flags"], len(common_ids)),
        "graph_page_in_range_ratio": _ratio(len(graph_in_range), len(graph_td_entries)),
    }

    preservation_ratios = {
        f"{field}_preservation_ratio": _ratio(
            field_nonnull_match_counts[field],
            field_nonnull_denominators[field],
        )
        for field in field_nonnull_denominators.keys()
    }
    enrichment_ratios = {
        f"{field}_enrichment_ratio": _ratio(
            field_enrichment_counts[field],
            field_enrichment_denominators[field],
        )
        for field in style_fields
    }

    score_components = [
        ratios["id_coverage"],
        ratios["td_sig_coverage"],
        ratios["td_sig_match_ratio"],
        ratios["row_match_ratio"],
        ratios["col_match_ratio"],
        ratios["rowspan_match_ratio"],
        ratios["colspan_match_ratio"],
        ratios["table_path_match_ratio"],
        ratios["page_match_ratio"],
        ratios["bgcolor_match_ratio"],
        ratios["text_color_match_ratio"],
        ratios["font_size_match_ratio"],
        ratios["border_sides_match_ratio"],
        ratios["border_complete_match_ratio"],
        ratios["diagonal_match_ratio"],
        ratios["diagonal_flags_match_ratio"],
        ratios["graph_page_in_range_ratio"],
    ]
    overall_score = round(sum(score_components) / len(score_components), 6)

    structure_score_v2 = round(
        sum(
            [
                ratios["id_coverage"],
                ratios["td_sig_coverage"],
                ratios["td_sig_match_ratio"],
                ratios["row_match_ratio"],
                ratios["col_match_ratio"],
                ratios["rowspan_match_ratio"],
                ratios["colspan_match_ratio"],
                ratios["table_path_match_ratio"],
                ratios["page_match_ratio"],
                ratios["graph_page_in_range_ratio"],
            ]
        )
        / 10.0,
        6,
    )
    style_preservation_fields = [
        "bgcolor",
        "text_color",
        "font_size",
        "border_sides",
        "border_complete",
        "diagonal",
        "diagonal_flags",
    ]
    style_preservation_score_v2 = round(
        sum(preservation_ratios[f"{field}_preservation_ratio"] for field in style_preservation_fields)
        / float(len(style_preservation_fields)),
        6,
    )
    style_enrichment_score_v2 = round(
        sum(enrichment_ratios[f"{field}_enrichment_ratio"] for field in style_preservation_fields)
        / float(len(style_preservation_fields)),
        6,
    )
    overall_score_v2 = round(
        (0.65 * structure_score_v2)
        + (0.25 * style_preservation_score_v2)
        + (0.10 * style_enrichment_score_v2),
        6,
    )

    return {
        "counts": {
            "hdml_td_ids": len(hdml_by_id),
            "graph_td_ids": len(graph_by_id),
            "common_ids": len(common_ids),
            "hdml_td_sigs": len(hdml_by_sig),
            "graph_td_sigs": len(graph_by_sig),
            "common_sigs": len(common_sigs),
            "graph_td_in_range": len(graph_in_range),
        },
        "ratios": ratios,
        "preservation_ratios": preservation_ratios,
        "enrichment_ratios": enrichment_ratios,
        "scores_v2": {
            "structure_score_v2": structure_score_v2,
            "style_preservation_score_v2": style_preservation_score_v2,
            "style_enrichment_score_v2": style_enrichment_score_v2,
            "overall_score_v2": overall_score_v2,
        },
        "overall_score": overall_score,
        "mismatches_top_50": mismatches[:50],
    }


def _compare_hwpml_vs_graph(
    hwpml_td_entries: List[Dict[str, Any]],
    graph_td_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    def _signature_counter(entries: List[Dict[str, Any]], include_path: bool) -> Counter:
        counter: Counter = Counter()
        for entry in entries or []:
            key = (
                entry.get("table_path") if include_path else None,
                entry.get("row"),
                entry.get("col"),
                entry.get("rowspan"),
                entry.get("colspan"),
            )
            counter[key] += 1
        return counter

    def _counter_overlap(lhs: Counter, rhs: Counter) -> int:
        total = 0
        for key, lhs_count in lhs.items():
            total += min(int(lhs_count), int(rhs.get(key, 0)))
        return total

    def _style_presence_count(entries: List[Dict[str, Any]], field: str) -> int:
        count = 0
        for entry in entries or []:
            value = entry.get(field)
            if field in {"diagonal", "border_complete"}:
                if bool(value):
                    count += 1
            elif field in {"border_sides", "diagonal_flags"}:
                if len(_split_csv_tokens(value)) > 0:
                    count += 1
            elif not _is_empty(value):
                count += 1
        return count

    def _style_token_overlap(entries_a: List[Dict[str, Any]], entries_b: List[Dict[str, Any]], field: str) -> float:
        token_counter_a: Counter = Counter()
        token_counter_b: Counter = Counter()
        for entry in entries_a or []:
            for token in _split_csv_tokens(entry.get(field)):
                token_counter_a[token] += 1
        for entry in entries_b or []:
            for token in _split_csv_tokens(entry.get(field)):
                token_counter_b[token] += 1
        denominator = sum(int(v) for v in token_counter_a.values())
        if denominator <= 0:
            return 1.0
        overlap = _counter_overlap(token_counter_a, token_counter_b)
        return round(float(overlap) / float(denominator), 6)

    hwp_sig_with_path = _signature_counter(hwpml_td_entries, include_path=True)
    graph_sig_with_path = _signature_counter(graph_td_entries, include_path=True)
    hwp_sig_rowcol = _signature_counter(hwpml_td_entries, include_path=False)
    graph_sig_rowcol = _signature_counter(graph_td_entries, include_path=False)

    common_with_path = _counter_overlap(hwp_sig_with_path, graph_sig_with_path)
    common_rowcol = _counter_overlap(hwp_sig_rowcol, graph_sig_rowcol)

    style_fields = ("bgcolor", "border_sides", "border_complete", "diagonal", "diagonal_flags")
    style_presence_ratios: Dict[str, float] = {}
    style_count_deltas: Dict[str, Dict[str, int]] = {}
    for field in style_fields:
        h_count = _style_presence_count(hwpml_td_entries, field)
        g_count = _style_presence_count(graph_td_entries, field)
        style_presence_ratios[f"{field}_presence_ratio"] = _ratio(min(g_count, h_count), h_count)
        style_count_deltas[field] = {"hwpml": h_count, "graph": g_count}

    border_sides_token_overlap = _style_token_overlap(hwpml_td_entries, graph_td_entries, "border_sides")
    diagonal_flags_token_overlap = _style_token_overlap(hwpml_td_entries, graph_td_entries, "diagonal_flags")

    ratio_values = list(style_presence_ratios.values()) + [border_sides_token_overlap, diagonal_flags_token_overlap]
    overall_style_score = round(sum(ratio_values) / float(len(ratio_values)), 6) if ratio_values else 1.0

    return {
        "counts": {
            "hwpml_td_count": len(hwpml_td_entries),
            "graph_td_count": len(graph_td_entries),
            "common_signature_with_path": common_with_path,
            "common_signature_rowcol": common_rowcol,
        },
        "ratios": {
            "td_count_ratio": _ratio(min(len(hwpml_td_entries), len(graph_td_entries)), len(hwpml_td_entries)),
            "signature_coverage_with_path": _ratio(common_with_path, len(hwpml_td_entries)),
            "signature_coverage_rowcol": _ratio(common_rowcol, len(hwpml_td_entries)),
            "signature_coverage": max(
                _ratio(common_with_path, len(hwpml_td_entries)),
                _ratio(common_rowcol, len(hwpml_td_entries)),
            ),
            **style_presence_ratios,
            "border_sides_token_overlap": border_sides_token_overlap,
            "diagonal_flags_token_overlap": diagonal_flags_token_overlap,
        },
        "style_count_deltas": style_count_deltas,
        "overall_style_score": overall_style_score,
        "mismatches_top_50": [],
    }


def run_real_doc_compare(
    file_path: str,
    start_page: int,
    end_page: int,
    registry_source: str = "runtime",
) -> Dict[str, Any]:
    try:
        from pyhwpx import Hwp
    except Exception as exc:
        return {
            "success": False,
            "error": f"pyhwpx import failed: {exc}",
        }

    if not os.path.exists(file_path):
        return {"success": False, "error": f"file not found: {file_path}"}

    source = str(registry_source or "runtime").strip().lower()
    if source not in {"runtime", "hdml"}:
        source = "runtime"

    hwp = None
    try:
        hwp = Hwp(new=True, visible=False)
        sec_ok, module_id, dll_path = activate_security_module(hwp)
        if not sec_ok:
            return {
                "success": False,
                "error": "security module activation failed (FilePathCheckDLL)",
            }

        open_result = hwp.open(file_path)
        if open_result is False:
            return {"success": False, "error": "hwp.open returned False"}

        extractor = HDMLExtractor(hwp)
        extracted = extractor.extract_hdml(
            {
                "start": int(start_page),
                "end": int(end_page),
                "current_page": int(start_page),
            }
        )
        if not extracted:
            return {"success": False, "error": "extract_hdml returned None"}

        hdml_text, id_to_pos_raw = extracted
        id_to_pos = _normalize_id_to_pos(id_to_pos_raw)
        pos_to_page = _normalize_pos_to_page(getattr(extractor, "pos_to_page", {}) or {})

        if source == "runtime":
            registry, id_to_pos_runtime = build_segment_registry_from_extractor(extractor)
            runtime_id_count = len(id_to_pos_runtime or {})
        else:
            registry = SegmentRegistry((hdml_text, id_to_pos_raw))
            runtime_id_count = len(id_to_pos_raw or {})
        hwpml_full = ""
        hwpml_page_range = ""
        try:
            hwpml_full = hwp.GetTextFile("HWPML2X", "") or ""
        except Exception:
            hwpml_full = ""

        hwpml_page_range = _extract_page_range_hwpml_saveblock(
            pyhwpx_hwp=hwp,
            start_page=int(start_page),
            end_page=int(end_page),
        )

        hwpml_for_graph = hwpml_page_range or hwpml_full
        graph = build_document_graph_from_hwpml(
            hwpml_text=hwpml_for_graph,
            block_manager=registry,
            id_to_pos=id_to_pos_raw,
            page_range=(int(start_page), int(end_page)),
            page_by_pos=pos_to_page or None,
        )
        graph_json = serialize_enriched_hdml(
            graph,
            page_range=(int(start_page), int(end_page)),
        )

        hdml_td_entries = _extract_hdml_td_entries(hdml_text, id_to_pos, pos_to_page)
        hwpml_raw_nodes = extract_raw_hwpml_nodes(hwpml_for_graph)
        hwpml_td_entries = _normalize_hwpml_td_entries(list((hwpml_raw_nodes or {}).get("td") or []))
        graph_td_entries = _normalize_graph_td_entries(graph)
        graph_nodes = list(graph.get("nodes") or [])
        graph_types = Counter(str(node.get("block_type") or "unknown") for node in graph_nodes)
        graph_td_entries_for_hdml_compare = _rekey_graph_entries_by_hdml_sig(
            hdml_td_entries=hdml_td_entries,
            graph_td_entries=graph_td_entries,
        )

        comparison = _compare_hdml_vs_graph(
            hdml_td_entries=hdml_td_entries,
            graph_td_entries=graph_td_entries_for_hdml_compare,
            start_page=int(start_page),
            end_page=int(end_page),
        )
        hwpml_graph_comparison = _compare_hwpml_vs_graph(
            hwpml_td_entries=hwpml_td_entries,
            graph_td_entries=graph_td_entries,
        )

        report: Dict[str, Any] = {
            "success": True,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "file_path": file_path,
            "pages": {"start": int(start_page), "end": int(end_page)},
            "registry_source": source,
            "registry_id_count": runtime_id_count,
            "security_module_id": module_id,
            "security_module_dll": dll_path,
            "hdml": {
                "length": len(hdml_text or ""),
                "id_count": len(id_to_pos),
                "paragraph_count": _extract_hdml_p_count(hdml_text),
                "td_count": len(hdml_td_entries),
                "td_sig_count": sum(1 for e in hdml_td_entries if e.get("td_sig_v1")),
                "td_page_hist": _histogram(e.get("page") for e in hdml_td_entries),
                "td_bgcolor_count": sum(1 for e in hdml_td_entries if e.get("bgcolor")),
                "td_text_color_count": sum(1 for e in hdml_td_entries if e.get("text_color")),
                "td_diagonal_count": sum(1 for e in hdml_td_entries if e.get("diagonal")),
            },
            "hwpml": {
                "full": _extract_hwpml_summary(hwpml_full),
                "page_range_saveblock": _extract_hwpml_summary(hwpml_page_range),
                "graph_source": "page_range_saveblock" if hwpml_page_range else "full_document",
                "td_count": len(hwpml_td_entries),
                "td_nested_count": sum(
                    1
                    for e in hwpml_td_entries
                    if len(str(e.get("table_path") or "").split("/")) > 1
                ),
                "td_merged_count": sum(
                    1
                    for e in hwpml_td_entries
                    if int(e.get("rowspan") or 1) > 1 or int(e.get("colspan") or 1) > 1
                ),
                "td_bgcolor_count": sum(1 for e in hwpml_td_entries if e.get("bgcolor")),
                "td_diagonal_count": sum(1 for e in hwpml_td_entries if e.get("diagonal")),
                "td_border_complete_count": sum(1 for e in hwpml_td_entries if e.get("border_complete")),
            },
            "graph": {
                "json_length": len(graph_json or ""),
                "node_count": len(graph_nodes),
                "edge_count": len(list(graph.get("edges") or [])),
                "type_hist": dict(graph_types),
                "td_count": len(graph_td_entries),
                "td_sig_count": sum(1 for e in graph_td_entries if e.get("td_sig_v1")),
                "td_page_hist": _histogram(e.get("page") for e in graph_td_entries),
                "td_bgcolor_count": sum(1 for e in graph_td_entries if e.get("bgcolor")),
                "td_text_color_count": sum(1 for e in graph_td_entries if e.get("text_color")),
                "td_diagonal_count": sum(1 for e in graph_td_entries if e.get("diagonal")),
                "td_nested_count": sum(
                    1
                    for e in graph_td_entries
                    if len(str(e.get("table_path") or "").split("/")) > 1
                ),
                "td_merged_count": sum(
                    1
                    for e in graph_td_entries
                    if int(e.get("rowspan") or 1) > 1 or int(e.get("colspan") or 1) > 1
                ),
                "td_border_complete_count": sum(1 for e in graph_td_entries if e.get("border_complete")),
                "parse_warnings": list((graph.get("quality") or {}).get("parse_warnings") or []),
            },
            "comparison": comparison,
            "hwpml_graph_comparison": hwpml_graph_comparison,
            "diagnosis": [],
        }

        diag = report["diagnosis"]
        ratios = comparison.get("ratios") or {}
        if ratios.get("id_coverage", 1.0) < 1.0:
            diag.append("HDML TD ID 대비 Graph TD ID 커버리지가 100%가 아닙니다.")
        if ratios.get("table_path_match_ratio", 1.0) < 1.0:
            diag.append("table_path 불일치가 존재합니다. table_path 우선순위/매핑 확인이 필요합니다.")
        if ratios.get("graph_page_in_range_ratio", 1.0) < 1.0:
            diag.append("Graph TD 중 일부가 요청 페이지 범위를 벗어났습니다.")
        if ratios.get("bgcolor_match_ratio", 1.0) < 1.0:
            diag.append("셀 배경색(bgcolor) 불일치가 존재합니다.")
        if ratios.get("text_color_match_ratio", 1.0) < 1.0:
            diag.append("글자색(text_color) 불일치가 존재합니다.")
        if ratios.get("diagonal_match_ratio", 1.0) < 1.0:
            diag.append("대각선 셀(diagonal) 불일치가 존재합니다.")
        if ratios.get("border_sides_match_ratio", 1.0) < 1.0:
            diag.append("테두리 방향(border_sides) 불일치가 존재합니다.")
        hwpml_ratios = (hwpml_graph_comparison.get("ratios") or {})
        if hwpml_ratios.get("signature_coverage", 1.0) < 1.0:
            diag.append("HWPML 기준 td 시그니처(table_path/row/col/span) 커버리지가 100%가 아닙니다.")
        if hwpml_ratios.get("diagonal_presence_ratio", 1.0) < 1.0:
            diag.append("HWPML 대비 Graph 대각선(diagonal) 보존율이 100%가 아닙니다.")
        if hwpml_ratios.get("border_sides_presence_ratio", 1.0) < 1.0:
            diag.append("HWPML 대비 Graph 테두리 방향(border_sides) 보존율이 100%가 아닙니다.")
        parse_warnings = report["graph"].get("parse_warnings") or []
        if parse_warnings:
            diag.append(f"Graph parse_warnings 존재: {', '.join(parse_warnings)}")
        if not diag:
            diag.append("주요 정합성 지표에서 눈에 띄는 불일치가 없습니다.")

        return report
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
        description="Compare HDML/HWPML/DocumentGraph completeness for a real .hwp file."
    )
    parser.add_argument("--file", required=True, help="absolute path to target .hwp file")
    parser.add_argument("--start-page", type=int, default=2)
    parser.add_argument("--end-page", type=int, default=6)
    parser.add_argument(
        "--registry-source",
        default="runtime",
        choices=["runtime", "hdml"],
        help="registry source for graph build: runtime(extractor-based) or hdml(text-parse-based)",
    )
    parser.add_argument("--out", default="", help="optional json output path")
    args = parser.parse_args()

    result = run_real_doc_compare(
        file_path=args.file,
        start_page=args.start_page,
        end_page=args.end_page,
        registry_source=args.registry_source,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

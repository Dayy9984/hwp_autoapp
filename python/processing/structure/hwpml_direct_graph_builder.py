"""
Direct HWPML -> Document Graph builder.

This module builds graph payloads for LLM input directly from HWPML markup
while keeping runtime target IDs/signatures from SegmentRegistry.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _local_name(tag: Any) -> str:
    text = str(tag or "")
    if "}" in text:
        return text.split("}", 1)[1]
    return text


def _normalize_table_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return "/".join(str(v) for v in value)
    text = str(value).strip()
    return text or None


def _segment_attrs(segment: Any) -> Dict[str, Any]:
    attrs = getattr(segment, "attrs", None)
    return attrs if isinstance(attrs, dict) else {}


def _truthy(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def _normalize_color(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Hex color
    if text.startswith("#"):
        hex_part = text[1:].strip()
        if len(hex_part) == 3:
            hex_part = "".join(ch * 2 for ch in hex_part)
        if len(hex_part) == 6:
            return f"#{hex_part.lower()}"
        return None

    # Decimal color (BGR integer from HWP APIs)
    parsed_int = _safe_int(text)
    if parsed_int is not None:
        if parsed_int < 0:
            parsed_int &= 0xFFFFFF
        b = parsed_int & 0xFF
        g = (parsed_int >> 8) & 0xFF
        r = (parsed_int >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"

    lowered = text.lower()
    if lowered in {"black", "white"}:
        return "#000000" if lowered == "black" else "#ffffff"

    return None


def _split_csv_tokens(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            token = str(item or "").strip()
            if token:
                result.append(token.lower())
        return result
    text = str(value).strip()
    if not text:
        return []
    return [part.strip().lower() for part in text.split(",") if part.strip()]


def _border_elem_has_line(elem: ET.Element) -> bool:
    attrs = _attrs_ci(elem)
    for key, value in attrs.items():
        k = key.lower()
        v = str(value or "").strip().lower()
        if any(token in k for token in ("width", "thick", "thickness", "weight")):
            try:
                if float(v) <= 0:
                    return False
            except Exception:
                pass
        if any(token in k for token in ("style", "type")) and v in {"0", "none", "null", "false"}:
            return False
    return True


def _extract_border_sides_from_borderfill(borderfill_elem: ET.Element) -> Tuple[List[str], bool]:
    side_state: Dict[str, Optional[bool]] = {
        "left": None,
        "right": None,
        "top": None,
        "bottom": None,
    }

    for elem in borderfill_elem.iter():
        tag_lower = _local_name(elem.tag).strip().lower()
        if "border" not in tag_lower and "line" not in tag_lower:
            continue

        target_side = None
        for side in ("left", "right", "top", "bottom"):
            if side in tag_lower:
                target_side = side
                break

        if target_side and side_state[target_side] is None:
            side_state[target_side] = _border_elem_has_line(elem)

    sides = [side for side in ("left", "right", "top", "bottom") if side_state[side]]
    complete = all(side_state[side] is not None for side in ("left", "right", "top", "bottom"))
    return sides, complete


def _attrs_ci(element: ET.Element) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for key, value in (element.attrib or {}).items():
        attrs[_local_name(key).strip().lower()] = value
    return attrs


def _get_attr_ci(element: ET.Element, candidates: Iterable[str]) -> Optional[str]:
    attrs = _attrs_ci(element)
    for key in candidates:
        found = attrs.get(key.lower())
        if found is not None and str(found).strip() != "":
            return str(found)
    return None


def _extract_borderfill_map(root: ET.Element) -> Dict[str, Dict[str, Any]]:
    style_map: Dict[str, Dict[str, Any]] = {}

    for elem in root.iter():
        if _local_name(elem.tag).strip().upper() != "BORDERFILL":
            continue

        style_id = _get_attr_ci(
            elem,
            ("id", "borderfillid", "borderfill_id", "refid"),
        )
        if not style_id:
            continue

        color: Optional[str] = None
        diagonal = False
        diagonal_flags: List[str] = []

        for sub in elem.iter():
            for attr_key, attr_val in _attrs_ci(sub).items():
                key = attr_key.lower()
                if "color" in key and any(token in key for token in ("face", "fill", "back", "win")):
                    parsed_color = _normalize_color(attr_val)
                    if parsed_color:
                        color = parsed_color
                if "diagonal" in key or "slash" in key:
                    if _truthy(attr_val):
                        diagonal = True
                        diagonal_flags.append(key)

        border_sides, border_complete = _extract_border_sides_from_borderfill(elem)

        style_map[str(style_id)] = {
            "bgcolor": color,
            "border_sides": border_sides,
            "border_complete": border_complete,
            "diagonal": diagonal,
            "diagonal_flags": sorted(set(diagonal_flags)),
        }

    return style_map


def _has_table_ancestor(node: ET.Element, stop_node: Optional[ET.Element], parent_map: Dict[ET.Element, ET.Element]) -> bool:
    current = parent_map.get(node)
    while current is not None and current is not stop_node:
        if _local_name(current.tag).strip().upper() == "TABLE":
            return True
        current = parent_map.get(current)
    return False


def _collect_chars_excluding_tables(node: ET.Element) -> str:
    chunks: List[str] = []

    def _walk(cur: ET.Element) -> None:
        for child in list(cur):
            name = _local_name(child.tag).strip().upper()
            if name == "TABLE":
                # Nested table is represented by its own td nodes.
                continue
            if name == "CHAR":
                text = child.text or ""
                if text:
                    chunks.append(text)
            _walk(child)

    _walk(node)
    return "".join(chunks).strip()


def _extract_cell_text(cell_elem: ET.Element) -> str:
    """부모 cell 의 직접 text 만 추출 — nested TABLE 안 의 PARALIST 는 제외.

    BUG fix (beta.11): 이전 = ``cell_elem.iter()`` 으로 deep iter → nested
    TABLE 의 label cell 의 PARALIST 도 포함 → 부모 cell 의 preview_text 에
    "업 력 / 주요상품/서비스 / ..." 같은 nested label 들 이 join 되어 LLM
    에게 = nested 의 답 cell 들 specify 불가 → ``replace_cell_content`` 으로
    nested TABLE 통째 파괴.

    fix: nested TABLE 의 ancestor 안 의 PARALIST 는 skip.
    """
    # nested TABLE 안 의 모든 element 의 set 구성
    nested_descendants: set = set()
    for child in cell_elem.iter():
        if child is cell_elem:
            continue
        if _local_name(child.tag).strip().upper() != "TABLE":
            continue
        for sub in child.iter():
            nested_descendants.add(id(sub))

    paragraphs: List[str] = []
    for para_list in cell_elem.iter():
        if _local_name(para_list.tag).strip().upper() != "PARALIST":
            continue
        # nested TABLE 안 의 PARALIST 는 skip
        if id(para_list) in nested_descendants:
            continue
        for paragraph in list(para_list):
            if _local_name(paragraph.tag).strip().upper() != "P":
                continue
            text = _collect_chars_excluding_tables(paragraph)
            if text:
                paragraphs.append(text)
    if paragraphs:
        return "\n".join(paragraphs).strip()
    return _collect_chars_excluding_tables(cell_elem)


def _parse_span(cell_elem: ET.Element) -> Tuple[int, int]:
    attrs = _attrs_ci(cell_elem)
    colspan = _safe_int(attrs.get("colspan") or attrs.get("col_span") or attrs.get("colspancount"))
    rowspan = _safe_int(attrs.get("rowspan") or attrs.get("row_span") or attrs.get("rowspancount"))

    merge_text = attrs.get("merge")
    if merge_text and (colspan is None or rowspan is None):
        parts = [part.strip() for part in merge_text.split(",")]
        if len(parts) >= 1 and colspan is None:
            colspan = _safe_int(parts[0])
        if len(parts) >= 2 and rowspan is None:
            rowspan = _safe_int(parts[1])

    if not colspan or colspan <= 0:
        colspan = 1
    if not rowspan or rowspan <= 0:
        rowspan = 1
    return colspan, rowspan


def _extract_cell_style(cell_elem: ET.Element, borderfill_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    attrs = _attrs_ci(cell_elem)
    borderfill_id = _get_attr_ci(
        cell_elem,
        ("borderfill", "borderfillid", "borderfill_id", "borderfillref", "borderfillidref"),
    )
    style: Dict[str, Any] = {}
    if borderfill_id:
        style = dict(borderfill_map.get(str(borderfill_id), {}) or {})

    # Inline fallback from CELL attrs when BORDERFILL lookup is missing/partial.
    inline_bg = _normalize_color(
        attrs.get("bgcolor")
        or attrs.get("backgroundcolor")
        or attrs.get("background-color")
        or attrs.get("fillcolor")
        or attrs.get("backcolor")
        or attrs.get("facecolor")
    )
    if inline_bg:
        style["bgcolor"] = inline_bg

    inline_text_color = _normalize_color(
        attrs.get("color")
        or attrs.get("fontcolor")
        or attrs.get("font-color")
        or attrs.get("textcolor")
        or attrs.get("text-color")
    )
    if inline_text_color:
        style["text_color"] = inline_text_color

    inline_font_size = (
        attrs.get("font-size")
        or attrs.get("fontsize")
        or attrs.get("charshapefontsize")
        or attrs.get("textsize")
    )
    if inline_font_size is not None and str(inline_font_size).strip():
        style["font_size"] = str(inline_font_size).strip()

    inline_border_sides = _split_csv_tokens(attrs.get("border") or attrs.get("data-border-sides"))
    if not inline_border_sides:
        directional_map = {
            "left": ("leftborder", "borderleft", "left-line", "lineleft"),
            "right": ("rightborder", "borderright", "right-line", "lineright"),
            "top": ("topborder", "bordertop", "top-line", "linetop"),
            "bottom": ("bottomborder", "borderbottom", "bottom-line", "linebottom"),
        }
        for side, keys in directional_map.items():
            for key in keys:
                val = attrs.get(key)
                if val is None:
                    continue
                if _truthy(val) or str(val).strip().lower() not in {"0", "none", "null", "false"}:
                    inline_border_sides.append(side)
                    break
    if inline_border_sides:
        style["border_sides"] = sorted(set(inline_border_sides))

    inline_border_complete = _truthy(attrs.get("data-border-complete"))
    if inline_border_complete:
        style["border_complete"] = True
    elif "border_sides" in style:
        style["border_complete"] = len(_split_csv_tokens(style.get("border_sides"))) == 4

    inline_diagonal_flags = _split_csv_tokens(
        attrs.get("data-diagonal-flags")
        or attrs.get("diagonal-flags")
    )
    inline_diagonal = (
        _truthy(attrs.get("data-diagonal"))
        or _truthy(attrs.get("diagonal"))
        or _truthy(attrs.get("slash"))
        or _truthy(attrs.get("backslash"))
    )
    if inline_diagonal:
        style["diagonal"] = True
        if not inline_diagonal_flags:
            inline_diagonal_flags = ["inline"]
    if inline_diagonal_flags:
        style["diagonal_flags"] = sorted(set(inline_diagonal_flags))

    return style


def _collect_raw_hwpml_nodes(hwpml_text: str) -> Dict[str, Deque[Dict[str, Any]]]:
    raw_nodes: Dict[str, Deque[Dict[str, Any]]] = {
        "td": deque(),
        "text": deque(),
    }
    if not hwpml_text or not str(hwpml_text).strip():
        return raw_nodes

    try:
        root = ET.fromstring(hwpml_text)
    except Exception:
        return raw_nodes

    parent_map: Dict[ET.Element, ET.Element] = {
        child: parent
        for parent in root.iter()
        for child in list(parent)
    }

    borderfill_map = _extract_borderfill_map(root)

    def _iter_direct_nested_tables(cell_elem: ET.Element) -> List[ET.Element]:
        nested_tables: List[ET.Element] = []
        for nested in cell_elem.iter():
            if nested is cell_elem:
                continue
            if _local_name(nested.tag).strip().upper() != "TABLE":
                continue
            # Keep only direct nested tables under this cell (exclude deeper descendants).
            if _has_table_ancestor(nested, cell_elem, parent_map):
                continue
            nested_tables.append(nested)
        return nested_tables

    def _process_table(table_elem: ET.Element, table_path_tokens: List[int]) -> None:
        table_path = "/".join(str(v) for v in table_path_tokens)

        rows = [row for row in list(table_elem) if _local_name(row.tag).strip().upper() == "ROW"]
        for row_idx, row_elem in enumerate(rows):
            cells = [cell for cell in list(row_elem) if _local_name(cell.tag).strip().upper() == "CELL"]
            for cell_idx, cell_elem in enumerate(cells):
                colspan, rowspan = _parse_span(cell_elem)
                style = _extract_cell_style(cell_elem, borderfill_map)
                cell_text = _extract_cell_text(cell_elem)

                raw_nodes["td"].append(
                    {
                        "block_type": "td",
                        "row": row_idx,
                        "col": cell_idx,
                        "rowspan": rowspan,
                        "colspan": colspan,
                        "table_path": table_path,
                        "preview_text": cell_text or "",
                        "bgcolor": style.get("bgcolor"),
                        "text_color": style.get("text_color"),
                        "font_size": style.get("font_size"),
                        "border_sides": list(style.get("border_sides") or []),
                        "border_complete": bool(style.get("border_complete", False)),
                        "diagonal": bool(style.get("diagonal", False)),
                        "diagonal_flags": list(style.get("diagonal_flags") or []),
                    }
                )

                for nested_idx, nested in enumerate(_iter_direct_nested_tables(cell_elem)):
                    nested_path = list(table_path_tokens) + [row_idx, cell_idx, nested_idx]
                    _process_table(nested, nested_path)

    top_level_tables: List[ET.Element] = []
    for elem in root.iter():
        if _local_name(elem.tag).strip().upper() != "TABLE":
            continue
        if _has_table_ancestor(elem, None, parent_map):
            continue
        top_level_tables.append(elem)

    for table_idx, table_elem in enumerate(top_level_tables):
        _process_table(table_elem, [table_idx])

    for para in root.iter():
        if _local_name(para.tag).strip().upper() != "P":
            continue
        if _has_table_ancestor(para, None, parent_map):
            continue
        para_text = _collect_chars_excluding_tables(para)
        if not para_text:
            continue
        raw_nodes["text"].append(
            {
                "block_type": "text",
                "role": "input",
                "editable": True,
                "preview_text": para_text,
            }
        )

    return raw_nodes


def extract_raw_hwpml_nodes(hwpml_text: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Public helper for diagnostics/tests that need raw HWPML-derived nodes.

    Returns simple list containers so callers can serialize/compare safely.
    """
    raw_nodes = _collect_raw_hwpml_nodes(hwpml_text)
    return {
        "td": list(raw_nodes.get("td") or []),
        "text": list(raw_nodes.get("text") or []),
    }


def _sorted_segments(block_manager: Any) -> List[Any]:
    segments = getattr(block_manager, "segments", None)
    if not isinstance(segments, dict):
        return []
    values = list(segments.values())

    def _key(seg: Any) -> Tuple[int, int, int, int]:
        list_pos = _safe_int(getattr(seg, "list_pos", 0)) or 0
        para_pos = _safe_int(getattr(seg, "para_pos", 0)) or 0
        char_pos = _safe_int(getattr(seg, "char_pos", 0)) or 0
        sid = _safe_int(getattr(seg, "id", 0)) or 0
        return (list_pos, para_pos, char_pos, sid)

    return sorted(values, key=_key)


def _resolve_page(
    segment: Any,
    page_by_pos: Optional[Dict[Tuple[int, int, int], int]],
    page_hint: Optional[int],
) -> int:
    attrs = getattr(segment, "attrs", None)
    if isinstance(attrs, dict):
        for key in ("data-page", "page", "data-page-no", "data-page-num", "data-page-number"):
            parsed = _safe_int(attrs.get(key))
            if parsed is not None:
                return parsed

    pos_key = (
        _safe_int(getattr(segment, "list_pos", None)),
        _safe_int(getattr(segment, "para_pos", None)),
        _safe_int(getattr(segment, "char_pos", None)),
    )
    if page_by_pos and None not in pos_key:
        mapped = _safe_int(page_by_pos.get(pos_key))
        if mapped is not None:
            return mapped

    segment_page = _safe_int(getattr(segment, "page", None))
    if segment_page is not None:
        return segment_page

    return page_hint or 0


def _fallback_role_from_segment(segment: Any) -> str:
    attrs = _segment_attrs(segment)

    role_raw = str(attrs.get("data-role", "")).strip().lower()
    if role_raw in {"input", "guide", "label", "forbidden"}:
        return role_raw

    diagonal_raw = str(attrs.get("data-diagonal", "")).strip().lower()
    if diagonal_raw in {"1", "true", "yes", "y"}:
        return "forbidden"

    color = str(attrs.get("color") or attrs.get("font-color") or "").strip().lower()
    text = str(getattr(segment, "text", "") or "").strip()
    if color and color not in {"#000", "#000000", "black", "rgb(0,0,0)"} and text:
        return "guide"
    return "input"


def _is_editable(segment_type: str, role: str) -> bool:
    if role == "forbidden":
        return False
    editable_types = {
        "td",
        "text",
        "list",
        "textbox",
        "footnote",
        "footnote_anchor",
        "footnote_content",
        "annotation_anchor",
        "annotation_content",
    }
    return segment_type in editable_types


def _best_text(raw_hint: Optional[Dict[str, Any]], segment: Any) -> str:
    raw_text = ""
    if raw_hint:
        # HWPML text is canonical for graph content fidelity.
        raw_text = str(raw_hint.get("full_text") or raw_hint.get("preview_text") or "").strip()
    if raw_text:
        return raw_text
    return str(getattr(segment, "text", "") or "").strip()


def _value_from_attrs(attrs: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _build_node_from_segment(
    segment: Any,
    raw_hint: Optional[Dict[str, Any]],
    page_by_pos: Optional[Dict[Tuple[int, int, int], int]],
    page_hint: Optional[int],
) -> Dict[str, Any]:
    seg_id_raw = getattr(segment, "id", None)
    seg_id_int = _safe_int(seg_id_raw)
    seg_id: Any = seg_id_int if seg_id_int is not None else str(seg_id_raw)
    segment_type = str(
        getattr(segment, "segment_type", None)
        or getattr(segment, "block_type", None)
        or "unknown"
    ).strip().lower()

    role = (
        str(raw_hint.get("role")).strip().lower()
        if raw_hint and raw_hint.get("role")
        else _fallback_role_from_segment(segment)
    )
    if role not in {"input", "guide", "label", "forbidden"}:
        role = "input"

    editable = bool(raw_hint.get("editable")) if raw_hint and ("editable" in raw_hint) else _is_editable(segment_type, role)
    page_value = _resolve_page(segment, page_by_pos=page_by_pos, page_hint=page_hint)
    attrs = _segment_attrs(segment)
    full_text = _best_text(raw_hint, segment)
    ocr_confidence = _safe_float(attrs.get("data-ocr-confidence"), default=1.0)

    row = _safe_int(_value_from_attrs(attrs, ("data-row", "row", "data-r")))
    col = _safe_int(_value_from_attrs(attrs, ("data-col", "col", "data-c")))
    rowspan = _safe_int(_value_from_attrs(attrs, ("data-rowspan", "rowspan")))
    colspan = _safe_int(_value_from_attrs(attrs, ("data-colspan", "colspan")))

    if row is None:
        row = _safe_int((raw_hint or {}).get("row"))
    if col is None:
        col = _safe_int((raw_hint or {}).get("col"))
    if rowspan is None:
        rowspan = _safe_int((raw_hint or {}).get("rowspan"))
    if colspan is None:
        colspan = _safe_int((raw_hint or {}).get("colspan"))

    text_color = _normalize_color(_value_from_attrs(attrs, ("color", "font-color", "text-color")))
    if not text_color:
        text_color = _normalize_color((raw_hint or {}).get("text_color"))

    font_size = _value_from_attrs(attrs, ("font-size", "fontsize", "data-font-size"))
    if font_size is not None:
        font_size = str(font_size)
    if not font_size:
        raw_font_size = (raw_hint or {}).get("font_size")
        if raw_font_size is not None and str(raw_font_size).strip():
            font_size = str(raw_font_size).strip()

    bgcolor = _normalize_color(_value_from_attrs(attrs, ("bgcolor", "background-color", "backgroundcolor")))
    if not bgcolor:
        bgcolor = _normalize_color((raw_hint or {}).get("bgcolor"))

    border_sides = _split_csv_tokens(_value_from_attrs(attrs, ("border", "data-border-sides")))
    if not border_sides:
        border_sides = _split_csv_tokens((raw_hint or {}).get("border_sides"))

    border_complete = _truthy(_value_from_attrs(attrs, ("data-border-complete",)))
    if not border_complete:
        border_complete = bool((raw_hint or {}).get("border_complete", False))

    diagonal = _truthy(_value_from_attrs(attrs, ("data-diagonal",)))
    if not diagonal:
        diagonal = bool((raw_hint or {}).get("diagonal", False))

    diagonal_flags = _split_csv_tokens(_value_from_attrs(attrs, ("data-diagonal-flags",)))
    if not diagonal_flags:
        diagonal_flags = _split_csv_tokens((raw_hint or {}).get("diagonal_flags"))

    node: Dict[str, Any] = {
        "target_uid": f"uid:{seg_id}",
        "id": seg_id,
        "block_type": segment_type,
        "page": page_value,
        "editable": editable,
        "role": role,
        "td_sig_v1": getattr(segment, "td_sig", None),
        "p_sig_v1": getattr(segment, "p_sig", None),
        "table_path": _normalize_table_path(
            _value_from_attrs(attrs, ("data-table-path", "table_path"))
            or (raw_hint or {}).get("table_path")
            or getattr(segment, "table_path", None)
        ),
        "scope_table_id": _safe_int(getattr(segment, "table_group_id", None)),
        "ocr_confidence": ocr_confidence,
        "preview_text": full_text,
        "full_text": full_text,
        "bgcolor": bgcolor,
        "border_sides": border_sides,
        "border_complete": border_complete,
        "diagonal": diagonal,
        "diagonal_flags": diagonal_flags,
        "position": {
            "list_pos": _safe_int(getattr(segment, "list_pos", None)),
            "para_pos": _safe_int(getattr(segment, "para_pos", None)),
            "char_pos": _safe_int(getattr(segment, "char_pos", None)),
        },
    }
    if text_color:
        node["text_color"] = text_color
    if font_size:
        node["font_size"] = font_size
    for key, value in (
        ("row", row),
        ("col", col),
        ("rowspan", rowspan),
        ("colspan", colspan),
    ):
        if value is not None:
            node[key] = value

    return node


def _pick_td_hint_for_segment(
    segment: Any,
    raw_td_all: List[Dict[str, Any]],
    used_td_hint_ids: Set[int],
    td_by_exact_key: Dict[Tuple[str, int, int], Deque[Dict[str, Any]]],
    td_by_row_col: Dict[Tuple[int, int], Deque[Dict[str, Any]]],
    td_queue: Deque[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    attrs = _segment_attrs(segment)
    seg_row = _safe_int(_value_from_attrs(attrs, ("data-row", "row", "data-r")))
    seg_col = _safe_int(_value_from_attrs(attrs, ("data-col", "col", "data-c")))
    seg_table_path = _normalize_table_path(
        getattr(segment, "table_path", None) or _value_from_attrs(attrs, ("data-table-path", "table_path"))
    )

    candidates: List[Dict[str, Any]] = []
    if seg_row is not None and seg_col is not None and seg_table_path:
        candidates.extend(list(td_by_exact_key.get((seg_table_path, seg_row, seg_col), deque())))
    if seg_row is not None and seg_col is not None:
        candidates.extend(list(td_by_row_col.get((seg_row, seg_col), deque())))
    candidates.extend(list(td_queue))

    for candidate in candidates:
        marker = id(candidate)
        if marker in used_td_hint_ids:
            continue
        used_td_hint_ids.add(marker)
        return candidate

    for candidate in raw_td_all:
        marker = id(candidate)
        if marker in used_td_hint_ids:
            continue
        used_td_hint_ids.add(marker)
        return candidate

    return None


def build_document_graph_from_hwpml(
    hwpml_text: str,
    block_manager: Any,
    id_to_pos: Optional[Dict[Any, Tuple[int, int, int]]] = None,
    page_range: Optional[Tuple[int, int]] = None,
    page_by_pos: Optional[Dict[Tuple[int, int, int], int]] = None,
) -> Dict[str, Any]:
    """
    Build graph payload using direct HWPML parsing + runtime segment IDs.

    - Content/structure hints (row/col/role/preview) come from HWPML.
    - Runtime IDs/signatures/positions come from SegmentRegistry.
    """
    del id_to_pos  # kept for interface compatibility

    page_hint = None
    if isinstance(page_range, (list, tuple)) and len(page_range) >= 1:
        page_hint = _safe_int(page_range[0])

    raw_nodes = _collect_raw_hwpml_nodes(hwpml_text)
    raw_td = raw_nodes.get("td", deque())
    raw_text = raw_nodes.get("text", deque())
    raw_td_all = list(raw_td)
    td_queue: Deque[Dict[str, Any]] = deque(raw_td_all)
    used_td_hint_ids: Set[int] = set()

    td_by_exact_key: Dict[Tuple[str, int, int], Deque[Dict[str, Any]]] = defaultdict(deque)
    td_by_row_col: Dict[Tuple[int, int], Deque[Dict[str, Any]]] = defaultdict(deque)
    for td_hint in raw_td_all:
        row = _safe_int(td_hint.get("row"))
        col = _safe_int(td_hint.get("col"))
        table_path = _normalize_table_path(td_hint.get("table_path"))
        if row is None or col is None:
            continue
        td_by_row_col[(row, col)].append(td_hint)
        if table_path:
            td_by_exact_key[(table_path, row, col)].append(td_hint)

    segments = _sorted_segments(block_manager)
    nodes: List[Dict[str, Any]] = []

    # Pre-collect list_pos values owned by td segments so that child text
    # segments sharing the same list_pos do NOT consume from raw_text queue.
    # Without this, td-child text segments steal raw_text entries meant for
    # standalone paragraphs, causing text misalignment in the CVD output.
    td_list_positions: Set[int] = set()
    _text_child_types = {"text", "list", "textbox", "annotation_anchor", "annotation_content"}
    for seg in segments:
        _st = str(
            getattr(seg, "segment_type", None) or getattr(seg, "block_type", None) or "unknown"
        ).strip().lower()
        if _st == "td":
            lp = _safe_int(getattr(seg, "list_pos", None))
            if lp is not None:
                td_list_positions.add(lp)

    parse_warnings: List[str] = []
    runtime_type_counts: Dict[str, int] = defaultdict(int)
    for seg in segments:
        seg_type = str(
            getattr(seg, "segment_type", None) or getattr(seg, "block_type", None) or "unknown"
        ).strip().lower()
        runtime_type_counts[seg_type] += 1

        raw_hint: Optional[Dict[str, Any]] = None
        if seg_type == "td":
            raw_hint = _pick_td_hint_for_segment(
                segment=seg,
                raw_td_all=raw_td_all,
                used_td_hint_ids=used_td_hint_ids,
                td_by_exact_key=td_by_exact_key,
                td_by_row_col=td_by_row_col,
                td_queue=td_queue,
            )
        elif seg_type in _text_child_types:
            seg_lp = _safe_int(getattr(seg, "list_pos", None))
            if seg_lp is not None and seg_lp in td_list_positions:
                raw_hint = None
            else:
                raw_hint = raw_text.popleft() if raw_text else None

        nodes.append(
            _build_node_from_segment(
                segment=seg,
                raw_hint=raw_hint,
                page_by_pos=page_by_pos,
                page_hint=page_hint,
            )
        )

    # TD 컨테이너 텍스트가 비어 있으면 같은 list_pos 자식 텍스트를 합성
    child_types = {"text", "list", "textbox", "annotation_anchor", "annotation_content"}
    for node in nodes:
        if node.get("block_type") != "td":
            continue
        existing = str(node.get("preview_text") or "").strip()
        if existing:
            continue
        lp = (node.get("position") or {}).get("list_pos")
        if lp is None:
            continue
        chunks: List[str] = []
        for child in nodes:
            if child.get("target_uid") == node.get("target_uid"):
                continue
            if child.get("block_type") not in child_types:
                continue
            child_lp = (child.get("position") or {}).get("list_pos")
            if child_lp != lp:
                continue
            child_text = str(child.get("preview_text") or "").strip()
            if child_text:
                chunks.append(child_text)
        if chunks:
            merged = "\n".join(chunks).strip()
            node["preview_text"] = merged
            node["full_text"] = merged

    unmapped_td = max(0, len(raw_td_all) - len(used_td_hint_ids))
    if unmapped_td > 0:
        parse_warnings.append(f"unmapped_hwpml_td_nodes:{unmapped_td}")
    if raw_text:
        parse_warnings.append(f"unmapped_hwpml_text_nodes:{len(raw_text)}")

    if runtime_type_counts.get("td", 0) == 0:
        parse_warnings.append("runtime_td_segments_missing")
    if not nodes:
        parse_warnings.append("runtime_segments_empty")

    node_by_uid = {n["target_uid"]: n for n in nodes if isinstance(n.get("target_uid"), str)}
    edges: List[Dict[str, Any]] = []

    for idx in range(len(nodes) - 1):
        edges.append(
            {
                "type": "next",
                "source": nodes[idx]["target_uid"],
                "target": nodes[idx + 1]["target_uid"],
            }
        )

    td_nodes = [n for n in nodes if n.get("block_type") == "td"]
    for td in td_nodes:
        td_lp = (td.get("position") or {}).get("list_pos")
        if td_lp is None:
            continue
        for child in nodes:
            if child["target_uid"] == td["target_uid"]:
                continue
            child_lp = (child.get("position") or {}).get("list_pos")
            if child_lp != td_lp:
                continue
            if child.get("block_type") in {"text", "list", "textbox", "annotation_anchor", "annotation_content"}:
                edges.append({"type": "contains", "source": td["target_uid"], "target": child["target_uid"]})

    for idx_a in range(len(td_nodes)):
        a = td_nodes[idx_a]
        a_row = _safe_int(a.get("row"))
        a_col = _safe_int(a.get("col"))
        a_scope = _safe_int(a.get("scope_table_id"))
        if a_row is None or a_col is None or a_scope is None:
            continue
        for idx_b in range(idx_a + 1, len(td_nodes)):
            b = td_nodes[idx_b]
            b_scope = _safe_int(b.get("scope_table_id"))
            if b_scope != a_scope:
                continue
            b_row = _safe_int(b.get("row"))
            b_col = _safe_int(b.get("col"))
            if b_row is None or b_col is None:
                continue
            if abs(a_row - b_row) + abs(a_col - b_col) == 1:
                edges.append({"type": "adjacent", "source": a["target_uid"], "target": b["target_uid"]})
                edges.append({"type": "adjacent", "source": b["target_uid"], "target": a["target_uid"]})

    for edge in list(edges):
        if edge.get("type") != "adjacent":
            continue
        src = node_by_uid.get(edge.get("source"))
        dst = node_by_uid.get(edge.get("target"))
        if not src or not dst:
            continue
        if src.get("role") == "label" and dst.get("role") == "input":
            edges.append({"type": "label_for", "source": src["target_uid"], "target": dst["target_uid"]})

    by_table_group: Dict[str, List[str]] = {}
    blocked_targets: List[str] = []
    low_conf_targets: List[str] = []

    for node in nodes:
        scope = _safe_int(node.get("scope_table_id"))
        if scope is not None:
            by_table_group.setdefault(str(scope), []).append(node["target_uid"])
        if node.get("role") in {"guide", "label", "forbidden"} or not bool(node.get("editable", True)):
            blocked_targets.append(node["target_uid"])
        if _safe_float(node.get("ocr_confidence"), 1.0) < 0.7:
            low_conf_targets.append(node["target_uid"])

    return {
        "doc_id": "active_doc",
        "version": "graph_v1",
        "nodes": nodes,
        "edges": edges,
        "index": {
            "by_target_uid": {n["target_uid"]: n["id"] for n in nodes},
            "by_block_id": {str(n["id"]): n["target_uid"] for n in nodes},
            "by_table_group": by_table_group,
            "blocked_targets": blocked_targets,
        },
        "quality": {
            "scan_based": False,
            "low_confidence_targets": low_conf_targets,
            "parse_warnings": parse_warnings,
        },
    }

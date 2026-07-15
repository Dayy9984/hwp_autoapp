"""Enriched CVD Serializer (v7.11).

Converts a Document Graph dict (from hwpml_direct_graph_builder) into
an HTML-like CVD markup string with style attributes.  This format is
designed for LLM consumption: the LLM infers cell roles (label / input
/ guide / forbidden) from position, content, and style cues rather than
relying on pre-classified ``role`` values.

Token reduction vs JSON Graph: ~60-70 %.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape


def serialize_enriched_cvd(
    graph: Dict[str, Any],
    page_range: Optional[Tuple[int, int]] = None,
) -> str:
    """Return Enriched CVD markup for the given Document Graph dict.

    Parameters
    ----------
    graph:
        Output of ``build_document_graph_from_hwpml()``.
    page_range:
        Optional ``(start_page, end_page)`` used for the
        ``<scanned_page_range>`` header.  May be ``None``.

    Returns
    -------
    str
        Enriched CVD markup string.
    """
    nodes: List[Dict[str, Any]] = graph.get("nodes") or []
    edges: List[Dict[str, Any]] = graph.get("edges") or []

    if not nodes:
        header = _page_range_header(page_range)
        return f"{header}<main_content>\n</main_content>"

    # ------------------------------------------------------------------
    # 1. Build helper indexes
    # ------------------------------------------------------------------
    node_by_uid: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        uid = n.get("target_uid")
        if uid:
            node_by_uid[uid] = n

    # contains edges: td_uid -> [child_uids]
    td_children: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("type") == "contains":
            td_children[edge["source"]].append(edge["target"])

    # ------------------------------------------------------------------
    # 2. Separate td nodes from non-td nodes
    # ------------------------------------------------------------------
    td_nodes: List[Dict[str, Any]] = []
    non_td_nodes: List[Dict[str, Any]] = []
    # Track which UIDs are children of a td (they are emitted inline)
    child_uids: set = set()
    for uid_list in td_children.values():
        child_uids.update(uid_list)

    for n in nodes:
        uid = n.get("target_uid", "")
        if n.get("block_type") == "td":
            td_nodes.append(n)
        elif uid not in child_uids:
            non_td_nodes.append(n)

    # ------------------------------------------------------------------
    # 3. Group td nodes by scope_table_id → reconstruct table structures
    # ------------------------------------------------------------------
    tables_by_scope: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for td in td_nodes:
        scope = td.get("scope_table_id")
        if scope is None:
            scope = -1
        tables_by_scope[int(scope)].append(td)

    # Build table objects: each table has rows, each row has cells
    table_objects: List[Dict[str, Any]] = []
    for scope_id, tds in tables_by_scope.items():
        table_obj = _build_table_object(scope_id, tds, td_children, node_by_uid)
        table_objects.append(table_obj)

    # ------------------------------------------------------------------
    # 4. Determine document order: interleave non-td nodes with tables
    # ------------------------------------------------------------------
    # Use node ID (segment UID) as sort key.  IDs are assigned in
    # document-parse order so they correctly interleave standalone
    # paragraphs with tables.  The previous (list_pos, para_pos,
    # char_pos) key placed ALL standalone paragraphs (list_pos=0)
    # before ALL table cells (list_pos>0), breaking document order.
    ordered_items: List[Tuple[int, str, Any]] = []

    for n in non_td_nodes:
        ordered_items.append((_safe_int(n.get("id"), 0), "node", n))

    for tbl in table_objects:
        min_id = min(
            (_safe_int(td.get("id"), 0) for row in tbl["rows"] for td in row),
            default=0,
        )
        ordered_items.append((min_id, "table", tbl))

    ordered_items.sort(key=lambda x: x[0])

    # ------------------------------------------------------------------
    # 5. Emit Enriched CVD markup
    # ------------------------------------------------------------------
    parts: List[str] = []
    parts.append(_page_range_header(page_range))
    parts.append("<main_content>")

    # B6 fix (beta.11): page sequence monotonic 강제 — nested TABLE 의
    # PageBreak="Cell" 으로 인한 페이지 역행 (1→2→3→1→3→4) 방지.
    # 한 번 진행 한 page 이하 로 = 직전 값 유지.
    current_page: Optional[int] = None
    max_page_seen: int = 0
    for i, (_, item_type, item) in enumerate(ordered_items):
        item_page = item.get("page")
        if item_page is not None:
            try:
                ip = int(item_page)
            except (ValueError, TypeError):
                ip = None
            if ip is not None:
                if ip < max_page_seen:
                    # 역행 = 직전 값 유지 (= page num 출력 안 함)
                    ip = current_page
                else:
                    max_page_seen = max(max_page_seen, ip)
                if ip is not None and ip != current_page:
                    parts.append(f'<page num="{ip}" />')
                    current_page = ip
        if item_type == "node":
            # 표와 표 사이에 끼인 빈 단락은 HWP 구조 필수 구분자이므로 제외.
            # 내용이 있는 단락(◦/- 불릿 등) 또는 표 사이가 아닌 빈 단락은 유지.
            prev_is_table = i > 0 and ordered_items[i - 1][1] == "table"
            next_is_table = (
                i < len(ordered_items) - 1 and ordered_items[i + 1][1] == "table"
            )
            if prev_is_table and next_is_table:
                node_text = (
                    item.get("full_text") or item.get("preview_text") or ""
                ).strip()
                if not node_text:
                    continue
            parts.append(_emit_node(item))
        elif item_type == "table":
            parts.append(_emit_table(item))

    parts.append("</main_content>")
    return "\n".join(parts)


# ======================================================================
# Internal helpers
# ======================================================================


def _page_range_header(page_range: Optional[Tuple[int, int]]) -> str:
    if page_range and len(page_range) == 2:
        start, end = page_range
        if start == end:
            return f"<scanned_page_range>{start} 페이지</scanned_page_range>"
        return f"<scanned_page_range>{start} ~ {end} 페이지</scanned_page_range>"
    return ""


def _position_key(node: Dict[str, Any]) -> Tuple[int, int, int]:
    pos = node.get("position") or {}
    return (
        int(pos.get("list_pos", 0)),
        int(pos.get("para_pos", 0)),
        int(pos.get("char_pos", 0)),
    )


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ------------------------------------------------------------------
# Attribute building
# ------------------------------------------------------------------

_SKIP_COLORS = frozenset({"#000000", "#ffffff", ""})

# 단위 / placeholder 패턴: "(㎡)", "(원)", "(명)", "(개)" 같은 단위 표기 만 인 cell.
# replace_cell_content 시 단위 사라지지 않도록 LLM 에 metadata 으로 전달.
import re as _re
_UNIT_PLACEHOLDER_RE = _re.compile(r'^\s*[\(（][^)）]+[\)）]\s*$')


def _build_td_attrs(node: Dict[str, Any]) -> str:
    """Build attribute string for a ``<td>`` element."""
    parts: List[str] = []
    parts.append(f'id="{node.get("id", "")}"')

    # 단위 placeholder cell detect (B5 fix - beta.11)
    preview = (node.get("preview_text") or node.get("full_text") or "").strip()
    if preview and _UNIT_PLACEHOLDER_RE.match(preview):
        parts.append('data-role="unit-placeholder"')

    bgcolor = (node.get("bgcolor") or "").lower().strip()
    if bgcolor and bgcolor not in _SKIP_COLORS:
        parts.append(f'bgcolor="{_xml_escape(bgcolor)}"')

    text_color = (node.get("text_color") or "").lower().strip()
    if text_color and text_color not in _SKIP_COLORS:
        parts.append(f'color="{_xml_escape(text_color)}"')

    font_size = node.get("font_size")
    if font_size:
        parts.append(f'font-size="{_xml_escape(str(font_size))}"')

    border_sides = node.get("border_sides")
    if border_sides and isinstance(border_sides, list) and len(border_sides) > 0:
        parts.append(f'border="{",".join(str(s) for s in border_sides)}"')

    colspan = _safe_int(node.get("colspan"), 1)
    if colspan > 1:
        parts.append(f'colspan="{colspan}"')

    rowspan = _safe_int(node.get("rowspan"), 1)
    if rowspan > 1:
        parts.append(f'rowspan="{rowspan}"')

    if node.get("diagonal"):
        parts.append('diagonal="true"')

    return " ".join(parts)


def _build_p_attrs(node: Dict[str, Any]) -> str:
    """Build attribute string for a ``<p>`` element."""
    parts: List[str] = []
    parts.append(f'id="{node.get("id", "")}"')

    text_color = (node.get("text_color") or "").lower().strip()
    if text_color and text_color not in _SKIP_COLORS:
        parts.append(f'color="{_xml_escape(text_color)}"')

    font_size = node.get("font_size")
    if font_size:
        parts.append(f'font-size="{_xml_escape(str(font_size))}"')

    return " ".join(parts)


# ------------------------------------------------------------------
# Table reconstruction
# ------------------------------------------------------------------

def _build_table_object(
    scope_id: int,
    tds: List[Dict[str, Any]],
    td_children: Dict[str, List[str]],
    node_by_uid: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a table dict with rows → cells → children structure."""

    # Determine table sort position from earliest td
    earliest_pos = (999999, 999999, 999999)
    for td in tds:
        pos = _position_key(td)
        if pos < earliest_pos:
            earliest_pos = pos

    # Build row structure from all tds.
    # Nested tables have a separate scope_table_id so they form their own
    # table group and are emitted as independent <table> elements in
    # document order.  No root/nested split needed within one scope group.
    rows: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for td in tds:
        row_idx = _safe_int(td.get("row"), 0)
        rows[row_idx].append(td)

    # Sort cells within each row by col
    for row_idx in rows:
        rows[row_idx].sort(key=lambda td: _safe_int(td.get("col"), 0))

    # Build sorted row list
    sorted_rows: List[List[Dict[str, Any]]] = []
    for row_idx in sorted(rows.keys()):
        sorted_rows.append(rows[row_idx])

    earliest_page: Optional[int] = None
    for td in tds:
        p = td.get("page")
        if p is not None and (earliest_page is None or p < earliest_page):
            earliest_page = p

    return {
        "scope_id": scope_id,
        "sort_position": earliest_pos,
        "rows": sorted_rows,
        "td_children": td_children,
        "node_by_uid": node_by_uid,
        "page": earliest_page,
    }


# ------------------------------------------------------------------
# Emitting markup
# ------------------------------------------------------------------

def _emit_node(node: Dict[str, Any]) -> str:
    """Emit a single non-td node as a ``<p>`` or specialized element."""
    block_type = node.get("block_type", "text")
    text = _xml_escape(node.get("full_text") or node.get("preview_text") or "")

    if block_type == "textbox":
        return f'<textbox id="{node.get("id", "")}">{text}</textbox>'
    if block_type in ("annotation_anchor", "annotation_content", "footnote"):
        return f'<footnote id="{node.get("id", "")}">{text}</footnote>'

    # Default: <p> element
    attrs = _build_p_attrs(node)
    return f"<p {attrs}>{text}</p>"


def _emit_table(table_obj: Dict[str, Any]) -> str:
    """Emit a ``<table>`` element with rows and cells."""
    td_children = table_obj["td_children"]
    node_by_uid = table_obj["node_by_uid"]
    rows = table_obj["rows"]

    lines: List[str] = []
    lines.append("<table>")

    # table-level: 안내문 가이드 셀 판별
    # 같은 표에 diagonal 셀 + 비어있는 입력 셀이 공존하면
    # "※"로 시작하는 안내문 셀은 가이드(편집 금지)로 마킹
    _all_flat_cells = [td for row in rows for td in row]
    _table_has_diagonal = any(td.get("diagonal") for td in _all_flat_cells)
    _table_has_empty = any(
        not (td.get("full_text") or td.get("preview_text") or "").strip()
        and not td.get("diagonal")
        and not (td.get("bgcolor") or "").strip()
        for td in _all_flat_cells
    )
    _guide_cell_ids: set = set()
    if _table_has_diagonal and _table_has_empty:
        for td in _all_flat_cells:
            if td.get("diagonal"):
                continue
            _txt = (td.get("full_text") or td.get("preview_text") or "").strip()
            _clr = (td.get("text_color") or "").lower().strip()
            if _txt.startswith("※") and _clr and _clr not in _SKIP_COLORS:
                _guide_cell_ids.add(td.get("id"))

    for row_cells in rows:
        lines.append("  <tr>")
        # row-level: diagonal 셀이 하나라도 있는지 판별
        _has_diagonal_in_row = any(td.get("diagonal") for td in row_cells)
        for td in row_cells:
            td_attrs = _build_td_attrs(td)
            td_uid = td.get("target_uid", "")

            # 가이드 셀 마킹: ※ 안내문 + 같은 표에 diagonal+empty 공존
            if td.get("id") in _guide_cell_ids:
                td_attrs += ' guide="true"'

            # 장식 셀 판별: diagonal row + 빈 셀 + 불완전 border + 색상 없음
            if _has_diagonal_in_row and not td.get("diagonal"):
                _td_text_raw = (td.get("full_text") or td.get("preview_text") or "").strip()
                _td_border = td.get("border_sides") or []
                _td_bgcolor = (td.get("bgcolor") or "").strip()
                _td_color = (td.get("text_color") or "").strip()
                _is_decoration = (
                    not _td_text_raw
                    and len(_td_border) < 4
                    and not _td_bgcolor
                    and not _td_color
                )
                if _is_decoration:
                    td_attrs += ' data-decoration="true"'

            # Get children of this td
            children_uids = td_children.get(td_uid, [])
            children = [node_by_uid[uid] for uid in children_uids if uid in node_by_uid]

            # Sort children by position
            children.sort(key=_position_key)

            if len(children) == 1:
                # 단일 자식: <p> id를 숨겨 replace_cell_content(td_id) 사용 유도
                child = children[0]
                child_text = _xml_escape(
                    child.get("full_text") or child.get("preview_text") or ""
                )
                lines.append(f"    <td {td_attrs}><p>{child_text}</p></td>")
            elif children:
                # 복수 자식: 개별 <p id> 노출
                has_text = any(
                    (c.get("full_text") or c.get("preview_text") or "").strip()
                    for c in children
                )
                has_empty = any(
                    not (c.get("full_text") or c.get("preview_text") or "").strip()
                    for c in children
                )
                if has_text and has_empty:
                    # 혼합 (텍스트 + 빈 문단): 개별 <p id> 표시
                    lines.append(f"    <td {td_attrs}>")
                    for child in children:
                        child_text = _xml_escape(
                            child.get("full_text") or child.get("preview_text") or ""
                        )
                        child_attrs = _build_p_attrs(child)
                        lines.append(f"      <p {child_attrs}>{child_text}</p>")
                    lines.append("    </td>")
                elif not has_text:
                    # 모두 빈 문단: compact 포맷
                    lines.append(f"    <td {td_attrs}><p></p></td>")
                else:
                    # 모두 텍스트 있음: 개별 <p id> 표시
                    lines.append(f"    <td {td_attrs}>")
                    for child in children:
                        child_text = _xml_escape(
                            child.get("full_text") or child.get("preview_text") or ""
                        )
                        child_attrs = _build_p_attrs(child)
                        lines.append(f"      <p {child_attrs}>{child_text}</p>")
                    lines.append("    </td>")
            else:
                # Single-content td: use td's own text.
                # The synthetic <p> has no id — editing this cell uses
                # replace_cell_content(td_id), never replace_paragraph(p_id).
                # Giving p the same id as td caused LLM to confuse the two.
                td_text = _xml_escape(
                    td.get("full_text") or td.get("preview_text") or ""
                )
                lines.append(
                    f"    <td {td_attrs}><p>{td_text}</p></td>"
                )

        lines.append("  </tr>")

    lines.append("</table>")
    return "\n".join(lines)

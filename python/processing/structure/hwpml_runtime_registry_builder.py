"""
Runtime SegmentRegistry builder without CVD text parsing.

Builds SegmentRegistry directly from CVDExtractor.extracted_elements metadata,
keeping runtime positions/signatures while avoiding CVD markup dependency in
prepare_context graph flow.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from processing.structure.segment_registry import ContentSegment, SegmentRegistry


_ATTR_RE = re.compile(r'([a-zA-Z_:-]+)="([^"]*)"')


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _normalize_pos(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return (int(value[0]), int(value[1]), int(value[2]))
    except Exception:
        return None


def _normalize_table_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return "/".join(str(v) for v in value)
    text = str(value).strip()
    return text or None


def _attrs_from_style_snippet(snippet: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    if not snippet:
        return attrs
    for key, value in _ATTR_RE.findall(snippet):
        attrs[str(key)] = str(value)
    return attrs


def _extract_style_attrs(extractor: Any, pos: Tuple[int, int, int]) -> Dict[str, str]:
    try:
        snippet = extractor._get_style_attrs_from_pos(pos)  # noqa: SLF001
    except Exception:
        snippet = ""
    return _attrs_from_style_snippet(str(snippet or ""))


def _extract_cell_style_attrs(extractor: Any, table_index: int, row: Optional[int], col: Optional[int]) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    if row is None or col is None:
        return attrs

    style_map = getattr(extractor, "cell_style_map", None)
    if not isinstance(style_map, dict):
        return attrs

    style = style_map.get((int(table_index), int(row), int(col))) or {}
    if not isinstance(style, dict):
        return attrs

    bgcolor = style.get("bgcolor")
    if bgcolor:
        attrs["bgcolor"] = str(bgcolor)

    border_sides = style.get("border_sides") or []
    if isinstance(border_sides, (list, tuple)) and border_sides:
        attrs["border"] = ",".join(str(side) for side in border_sides if str(side).strip())

    if style.get("border_complete"):
        attrs["data-border-complete"] = "1"
    if style.get("diagonal"):
        attrs["data-diagonal"] = "1"

    diagonal_flags = style.get("diagonal_flags") or []
    if isinstance(diagonal_flags, (list, tuple)) and diagonal_flags:
        attrs["data-diagonal-flags"] = ",".join(str(flag) for flag in diagonal_flags if str(flag).strip())

    return attrs


def build_segment_registry_from_extractor(extractor: Any) -> Tuple[SegmentRegistry, Dict[int, Tuple[int, int, int]]]:
    """
    Build runtime SegmentRegistry from extractor.extracted_elements.

    Args:
        extractor: CVDExtractor-like instance after extract_elements() execution

    Returns:
        (SegmentRegistry, id_to_pos)
    """

    extracted_elements = list(getattr(extractor, "extracted_elements", []) or [])

    # Re-seed runtime IDs for this context.
    setattr(extractor, "global_id", 1)
    setattr(extractor, "id_to_pos", {})

    segments: Dict[str, ContentSegment] = {}
    id_to_pos: Dict[int, Tuple[int, int, int]] = {}
    table_group_by_key: Dict[str, int] = {}
    next_group_id = 1
    table_context_index = 0

    def _alloc_id(pos: Any) -> Optional[int]:
        normalized = _normalize_pos(pos)
        if normalized is None:
            return None
        try:
            sid = extractor._map_id_to_pos(normalized)  # noqa: SLF001
        except Exception:
            sid = None
        if sid is None:
            return None
        sid_int = int(sid)
        id_to_pos[sid_int] = normalized
        return sid_int

    def _resolve_group_id(table_index: int, table_path: Optional[str]) -> Optional[int]:
        nonlocal next_group_id
        if table_path is None:
            key = f"{table_index}"
        else:
            key = f"{table_index}:{table_path}"
        if key not in table_group_by_key:
            table_group_by_key[key] = next_group_id
            next_group_id += 1
        return table_group_by_key[key]

    def _register_text_like_segment(
        *,
        segment_type: str,
        pos: Any,
        text: str,
        extra_attrs: Optional[Dict[str, str]] = None,
        table_group_id: Optional[int] = None,
        table_path: Optional[str] = None,
    ) -> Optional[int]:
        sid = _alloc_id(pos)
        if sid is None:
            return None
        norm_pos = id_to_pos[sid]
        attrs = _extract_style_attrs(extractor, norm_pos)
        if extra_attrs:
            attrs.update(extra_attrs)
        try:
            p_sig = extractor._build_paragraph_signature(norm_pos, text or "")  # noqa: SLF001
        except Exception:
            p_sig = None
        segments[str(sid)] = ContentSegment(
            segment_id=str(sid),
            position=norm_pos,
            text=text or "",
            segment_type=segment_type,
            table_group_id=table_group_id,
            attrs=attrs,
            p_sig=p_sig,
            table_path=table_path,
        )
        return sid

    def _register_table_segments(table_element: Dict[str, Any], table_index: int) -> None:
        td_metas = table_element.get("td_metas") or []
        if not isinstance(td_metas, list):
            return

        ordered_metas = sorted(
            td_metas,
            key=lambda meta: (_normalize_pos(meta.get("pos")) or (0, 0, 0), _safe_int(meta.get("row")) or 0, _safe_int(meta.get("col")) or 0),
        )

        for meta in ordered_metas:
            pos = _normalize_pos(meta.get("pos"))
            if pos is None:
                continue

            row = _safe_int(meta.get("row"))
            col = _safe_int(meta.get("col"))
            rowspan = _safe_int(meta.get("rowspan")) or 1
            colspan = _safe_int(meta.get("colspan")) or 1
            table_path = _normalize_table_path(meta.get("table_path"))
            group_id = _resolve_group_id(table_index, table_path)

            td_id = _alloc_id(pos)
            if td_id is None:
                continue

            td_attrs: Dict[str, str] = _extract_style_attrs(extractor, pos)
            if row is not None:
                td_attrs["data-row"] = str(row)
            if col is not None:
                td_attrs["data-col"] = str(col)
            td_attrs["data-rowspan"] = str(rowspan)
            td_attrs["data-colspan"] = str(colspan)
            if table_path:
                td_attrs["data-table-path"] = table_path
            td_attrs.update(_extract_cell_style_attrs(extractor, table_index, row, col))

            try:
                td_sig = extractor._build_td_signature(table_index, meta)  # noqa: SLF001
            except Exception:
                td_sig = None

            segments[str(td_id)] = ContentSegment(
                segment_id=str(td_id),
                position=pos,
                text="",
                segment_type="td",
                table_group_id=group_id,
                attrs=td_attrs,
                td_sig=td_sig,
                table_path=table_path,
            )

            inner_metas = meta.get("inner_metas") or []
            if not isinstance(inner_metas, list):
                continue

            for inner in inner_metas:
                inner_type = str(inner.get("type") or "").strip().lower()
                if inner_type not in {"paragraph", "outline", "list"}:
                    continue

                extra_attrs: Dict[str, str] = {}
                if table_path:
                    extra_attrs["data-table-path"] = table_path
                if inner_type == "outline":
                    level = inner.get("outline_level")
                    if level is not None:
                        extra_attrs["outline-level"] = str(level)
                if inner_type == "list":
                    heading_text = inner.get("heading_text")
                    if heading_text:
                        extra_attrs["heading-text"] = str(heading_text)

                _register_text_like_segment(
                    segment_type="list" if inner_type == "list" else "text",
                    pos=inner.get("pos"),
                    text=str(inner.get("text") or ""),
                    extra_attrs=extra_attrs,
                    table_group_id=group_id,
                    table_path=table_path,
                )

    for element in extracted_elements:
        element_type = str(element.get("type") or "").strip().lower()

        if element_type == "table":
            _register_table_segments(element, table_context_index)
            table_context_index += 1
            continue

        if element_type == "paragraph":
            _register_text_like_segment(
                segment_type="text",
                pos=element.get("pos"),
                text=str(element.get("text") or ""),
            )
            continue

        if element_type == "list":
            heading_text = element.get("heading_text")
            extra_attrs = {"heading-text": str(heading_text)} if heading_text else None
            _register_text_like_segment(
                segment_type="list",
                pos=element.get("pos"),
                text=str(element.get("text") or ""),
                extra_attrs=extra_attrs,
            )
            continue

        if element_type == "textbox":
            box_pos = _normalize_pos(element.get("pos"))
            if box_pos is None:
                continue
            box_id = _alloc_id(box_pos)
            if box_id is None:
                continue
            segments[str(box_id)] = ContentSegment(
                segment_id=str(box_id),
                position=box_pos,
                text="",
                segment_type="textbox",
                attrs=_extract_style_attrs(extractor, box_pos),
            )
            _register_text_like_segment(
                segment_type="text",
                pos=box_pos,
                text=str(element.get("text") or ""),
            )
            continue

        if element_type in {"fn", "en"}:
            anchor_pos = _normalize_pos(element.get("anchor_pos"))
            if anchor_pos is None:
                continue
            anchor_id = _alloc_id(anchor_pos)
            if anchor_id is None:
                continue
            note_index = _safe_int(element.get("index")) or 0
            note_label = "각주" if element_type == "fn" else "미주"
            segments[str(anchor_id)] = ContentSegment(
                segment_id=str(anchor_id),
                position=anchor_pos,
                text=f"[{note_label}{note_index}]",
                segment_type="annotation_anchor",
                attrs=_extract_style_attrs(extractor, anchor_pos),
            )
            note_text = str(element.get("text") or "")
            if note_text.strip():
                _register_text_like_segment(
                    segment_type="annotation_content",
                    pos=anchor_pos,
                    text=note_text,
                )
            continue

        if element_type == "image":
            _register_text_like_segment(
                segment_type="image",
                pos=element.get("pos"),
                text=str(element.get("user_desc") or "[이미지]"),
            )
            continue

    registry = SegmentRegistry.from_segments(segments)
    return registry, id_to_pos


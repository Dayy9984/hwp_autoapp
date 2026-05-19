"""Parser for v7.10/v7.11 edit tool calls -> normalized StreamingCommand payload."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .edit_tools_schema import BATCH_OP_NAMES, EDIT_TOOL_NAMES


_MESSAGE_TOOLS = {"thinking", "message"}


def _parse_args(args: Any) -> Dict[str, Any]:
    if isinstance(args, dict):
        return args
    if args is None:
        return {}
    if isinstance(args, str):
        stripped = args.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _to_int_if_possible(value: Any) -> Any:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return value
        if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            try:
                return int(v)
            except Exception:
                return value
    return value


def _id_from_target_uid(target_uid: Any) -> Any:
    if target_uid is None:
        return None
    text = str(target_uid).strip()
    if not text:
        return None
    # Common uid shapes: cell-12, para-7, td-19, p-3, uid:cell-11
    match = re.search(r"(?:^|[-_:])(-?\d+)$", text)
    if match:
        return _to_int_if_possible(match.group(1))
    # Runtime accepts string ids in 일부 경로(예: "19-3"), so keep raw uid as fallback.
    return text


def _normalize_meta(meta: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(meta, dict):
        merged = dict(meta)
    elif isinstance(meta, str):
        try:
            parsed = json.loads(meta)
            merged = dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            merged = {}
    else:
        merged = {}

    for key in ("scope_table_id", "td_sig_v1", "p_sig_v1", "table_path", "block_type"):
        if args.get(key) is not None:
            merged[key] = args.get(key)
    return merged


def _has_meta_contract(meta: Dict[str, Any]) -> bool:
    for key in ("scope_table_id", "td_sig_v1", "p_sig_v1", "table_path", "block_type"):
        value = meta.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _action_for_tool(name: str) -> str:
    if name in _MESSAGE_TOOLS:
        return name
    if name == "append_table_row":
        return "append_table_row"
    if name in {"apply_para_style", "apply_charshape"}:
        return "format_text"
    if name in {"insert_footnote", "replace_footnote", "delete_footnote", "add_endnote"}:
        return "insert_note"
    if name == "add_source_ref":
        return "insert_source_ref"
    if name == "line_break":
        return "line_break"
    return "edit_document"


def _missing(name: str, field: str) -> Tuple[None, str]:
    return None, f"missing_required:{name}.{field}"


def parse_v710_tool_call(tool_name: str, raw_args: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse a single v7.10 tool call to normalized command payload."""
    if tool_name not in EDIT_TOOL_NAMES:
        return None, f"unknown_tool:{tool_name}"

    args = _parse_args(raw_args)

    if tool_name in _MESSAGE_TOOLS:
        text = args.get("content") or args.get("message") or args.get("text") or ""
        text = str(text).strip()
        if not text:
            return _missing(tool_name, "content")
        return {
            "action": tool_name,
            "message": text,
            "metadata": {"operation": tool_name},
        }, None

    target_uid = args.get("target_uid")
    raw_id = args.get("id")
    if raw_id is None:
        raw_id = args.get("block_id")
    if raw_id is None:
        raw_id = args.get("element_id")
    if raw_id is None:
        raw_id = _id_from_target_uid(target_uid)

    if tool_name != "find_and_replace_all" and raw_id is None:
        return _missing(tool_name, "id")

    meta = _normalize_meta(args.get("meta"), args)
    target_uid = target_uid or meta.get("target_uid")
    intent_proof = args.get("intent_proof") or meta.get("intent_proof")
    block_id = _to_int_if_possible(raw_id)

    metadata: Dict[str, Any] = dict(meta)
    metadata["operation"] = tool_name
    if target_uid is not None:
        metadata["target_uid"] = str(target_uid)
    if intent_proof is not None:
        metadata["intent_proof"] = str(intent_proof)

    payload: Dict[str, Any] = {
        "action": _action_for_tool(tool_name),
        "id": block_id,
        "metadata": metadata,
    }

    # text content commands
    if tool_name in {"replace_cell_content", "replace_paragraph", "append_paragraph", "replace_list", "append_list"}:
        new_text = args.get("new_text")
        if new_text is None:
            new_text = args.get("content")
        if new_text is None:
            return _missing(tool_name, "new_text")
        payload["content"] = str(new_text)
        return payload, None

    if tool_name in {"delete_cell_content", "delete_paragraph", "delete_list", "delete_table_row", "delete_table", "delete_textbox", "delete_footnote"}:
        return payload, None

    if tool_name in {"append_table_row", "replace_table_row"}:
        row_texts = args.get("row_texts")
        if not isinstance(row_texts, list):
            return _missing(tool_name, "row_texts")
        payload["rows"] = [str(v) for v in row_texts]
        payload["metadata"]["row_texts"] = [str(v) for v in row_texts]
        return payload, None

    if tool_name == "create_table":
        row_texts = args.get("row_texts")
        if isinstance(row_texts, list):
            payload["rows"] = [str(v) for v in row_texts]
            payload["metadata"]["row_texts"] = [str(v) for v in row_texts]
        return payload, None

    if tool_name == "insert_footnote":
        footnote_text = args.get("footnote_text")
        if footnote_text is None:
            footnote_text = args.get("new_text")
        if footnote_text is None:
            footnote_text = args.get("content")
        if footnote_text is None:
            return _missing(tool_name, "new_text")
        anchor_text = args.get("old_text") or args.get("footnote_anchor_text")
        if anchor_text:
            payload["metadata"]["footnote_anchor_text"] = str(anchor_text)
        payload["metadata"]["footnote_text"] = str(footnote_text)
        payload["content"] = str(footnote_text)
        return payload, None

    if tool_name == "replace_footnote":
        new_text = args.get("new_text")
        if new_text is None:
            new_text = args.get("content")
        if new_text is None:
            return _missing(tool_name, "new_text")
        payload["metadata"]["footnote_text"] = str(new_text)
        payload["content"] = str(new_text)
        return payload, None

    if tool_name == "find_and_replace_in_paragraph":
        old_text = args.get("old_text")
        if old_text is None:
            old_text = args.get("find")
        new_text = args.get("new_text")
        if new_text is None:
            new_text = args.get("replace")
        if new_text is None:
            new_text = args.get("content")
        if old_text is None:
            return _missing(tool_name, "old_text")
        if new_text is None:
            return _missing(tool_name, "new_text")
        payload["metadata"]["old_text"] = str(old_text)
        payload["metadata"]["find"] = str(old_text)
        payload["content"] = str(new_text)
        return payload, None

    if tool_name == "find_and_replace_all":
        old_text = args.get("old_text")
        if old_text is None:
            old_text = args.get("find")
        new_text = args.get("new_text")
        if new_text is None:
            new_text = args.get("replace")
        if new_text is None:
            new_text = args.get("content")
        if old_text is None:
            return _missing(tool_name, "old_text")
        if new_text is None:
            return _missing(tool_name, "new_text")
        payload["id"] = None
        payload["metadata"]["old_text"] = str(old_text)
        payload["metadata"]["find"] = str(old_text)
        payload["content"] = str(new_text)
        return payload, None

    if tool_name == "apply_para_style":
        style_name = args.get("style_name")
        if style_name is None:
            return _missing(tool_name, "style_name")
        payload["metadata"]["style_name"] = str(style_name)
        return payload, None

    if tool_name == "apply_charshape":
        for key in ("bold", "italic", "underline", "font_size", "font_name"):
            if args.get(key) is not None:
                payload["metadata"][key] = args.get(key)
        return payload, None

    if tool_name == "add_endnote":
        new_text = args.get("new_text")
        if new_text is None:
            new_text = args.get("content")
        if new_text is None:
            return _missing(tool_name, "new_text")
        payload["content"] = str(new_text)
        return payload, None

    if tool_name == "add_source_ref":
        ref_id = args.get("ref_id")
        title = args.get("title")
        if ref_id is None:
            return _missing(tool_name, "ref_id")
        if title is None:
            return _missing(tool_name, "title")
        payload["metadata"]["ref_id"] = str(ref_id)
        payload["metadata"]["title"] = str(title)
        for key in ("publisher", "year", "url"):
            if args.get(key) is not None:
                payload["metadata"][key] = args.get(key)
        return payload, None

    if tool_name == "line_break":
        return payload, None

    # fallback: unknown branch in known tool set
    return payload, None


# ---------------------------------------------------------------------------
# v7.11 batch parser: execute_edits → List[parsed payloads]
# ---------------------------------------------------------------------------

def parse_execute_edits(raw_args: Any) -> List[Tuple[Optional[Dict[str, Any]], Optional[str]]]:
    """Parse execute_edits batch tool call into individual command payloads.

    Returns a list of (payload, error) tuples — one per operation.  Each
    payload is identical in shape to what ``parse_v710_tool_call`` returns so
    the streaming pipeline can emit them as individual ``StreamingCommand``s.

    If the top-level ``message`` field is present (v7.11 embedded message),
    it is appended as a final ``message`` payload so the streaming pipeline
    emits it as a deferred message command.
    """
    args = _parse_args(raw_args)
    operations = args.get("operations")

    results: List[Tuple[Optional[Dict[str, Any]], Optional[str]]] = []

    if not isinstance(operations, list) or len(operations) == 0:
        # operations 없어도 embedded message는 추출 (message-only 응답 처리)
        results.append((None, "missing_required:execute_edits.operations"))
        embedded_message = args.get("message")
        if isinstance(embedded_message, str) and embedded_message.strip():
            msg_payload, msg_error = parse_v710_tool_call("message", {"content": embedded_message})
            results.append((msg_payload, msg_error))
        return results

    for idx, op_item in enumerate(operations):
        if not isinstance(op_item, dict):
            results.append((None, f"invalid_operation_at_index:{idx}"))
            continue

        op_name = op_item.get("op")
        if not op_name or op_name not in BATCH_OP_NAMES:
            results.append((None, f"unknown_op:{op_name}_at_index:{idx}"))
            continue

        # Build args dict for the individual parser (exclude "op" key)
        individual_args = {k: v for k, v in op_item.items() if k != "op"}
        payload, error = parse_v710_tool_call(op_name, individual_args)
        results.append((payload, error))

    # ── replace_paragraph 역순 정렬 (좌표 밀림 방지) ──
    # HWP COM에서 문단 교체 시 상위 문단의 내용 길이가 변하면
    # 하위 문단의 좌표가 밀린다. 역순(id 내림차순)으로 실행하면
    # 아래쪽 문단부터 처리되어 위쪽 좌표에 영향을 주지 않는다.
    # payload 구조: {"action":..., "id":..., "metadata":{"operation":...}, ...}
    # operation은 metadata 안에 있음 — 키 경로 오류로 정렬이 한 번도 동작 안 했음
    para_ops = [(i, r) for i, r in enumerate(results)
                if r[0] is not None
                and r[0].get("metadata", {}).get("operation") == "replace_paragraph"]
    if len(para_ops) > 1:
        # id 내림차순 정렬
        para_ops_sorted = sorted(
            para_ops,
            key=lambda x: int(x[1][0].get("id", 0)),
            reverse=True,
        )
        # 원래 위치에 재배치
        original_indices = [i for i, _ in para_ops]
        for dest_idx, (_, op_tuple) in zip(original_indices, para_ops_sorted):
            results[dest_idx] = op_tuple

    # v7.11: extract embedded message from execute_edits
    embedded_message = args.get("message")
    if isinstance(embedded_message, str) and embedded_message.strip():
        msg_payload, msg_error = parse_v710_tool_call("message", {"content": embedded_message})
        results.append((msg_payload, msg_error))

    return results

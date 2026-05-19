import os
import sys
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.edit_tools_parser import parse_v710_tool_call
from llm.edit_tools_schema import build_v710_edit_tools, EDIT_TOOL_NAMES


def test_tool_parser_merges_signature_meta_from_direct_fields():
    payload, err = parse_v710_tool_call(
        "replace_cell_content",
        {
            "target_uid": "td-12",
            "id": "12",
            "new_text": "수정",
            "intent_proof": "빈 셀 채우기",
            "meta": {"block_type": "td"},
            "scope_table_id": "4",
            "td_sig_v1": "tdv1:aaaa",
            "p_sig_v1": "pv1:bbbb",
            "table_path": "0/2",
        },
    )

    assert err is None
    assert payload is not None
    assert payload["action"] == "edit_document"
    assert payload["id"] == 12
    assert payload["metadata"]["operation"] == "replace_cell_content"
    assert payload["metadata"]["scope_table_id"] == "4"
    assert payload["metadata"]["td_sig_v1"] == "tdv1:aaaa"
    assert payload["metadata"]["p_sig_v1"] == "pv1:bbbb"
    assert payload["metadata"]["table_path"] == "0/2"


def test_tool_parser_merges_signature_meta_from_meta_object():
    payload, err = parse_v710_tool_call(
        "replace_cell_content",
        {
            "target_uid": "td-9",
            "id": "9",
            "new_text": "수정",
            "intent_proof": "빈 셀 채우기",
            "meta": {"scope_table_id": 7, "td_sig_v1": "tdv1:x1", "table_path": "1/0"},
        },
    )

    assert err is None
    assert payload is not None
    assert payload["metadata"]["operation"] == "replace_cell_content"
    assert payload["metadata"]["scope_table_id"] == 7
    assert payload["metadata"]["td_sig_v1"] == "tdv1:x1"
    assert payload["metadata"]["table_path"] == "1/0"


def test_tool_parser_allows_missing_intent_proof_for_edit_tool():
    payload, err = parse_v710_tool_call(
        "replace_paragraph",
        {"target_uid": "p-1", "id": 1, "new_text": "본문"},
    )
    assert err is None
    assert payload is not None
    assert payload["action"] == "edit_document"
    assert payload["id"] == 1
    assert payload["metadata"]["operation"] == "replace_paragraph"
    assert payload["content"] == "본문"


def test_tool_parser_allows_missing_meta_for_edit_tool():
    payload, err = parse_v710_tool_call(
        "replace_paragraph",
        {
            "target_uid": "p-1",
            "id": 1,
            "new_text": "본문",
            "intent_proof": "본문 교체",
        },
    )
    assert err is None
    assert payload is not None
    assert payload["action"] == "edit_document"
    assert payload["metadata"]["intent_proof"] == "본문 교체"


def test_tool_parser_allows_empty_meta_contract_for_edit_tool():
    payload, err = parse_v710_tool_call(
        "replace_paragraph",
        {
            "target_uid": "p-1",
            "id": 1,
            "new_text": "본문",
            "intent_proof": "본문 교체",
            "meta": {},
        },
    )
    assert err is None
    assert payload is not None
    assert payload["metadata"]["operation"] == "replace_paragraph"


def test_tool_parser_append_table_row_requires_list():
    payload, err = parse_v710_tool_call(
        "append_table_row",
        {
            "target_uid": "tr-1",
            "id": "12",
            "intent_proof": "새 행 추가",
            "meta": {"scope_table_id": 2, "block_type": "td"},
            "row_texts": "[\"h1\",\"h2\"]",
        },
    )
    assert payload is None
    assert err == "missing_required:append_table_row.row_texts"


def test_tool_parser_append_table_row_accepts_list():
    payload, err = parse_v710_tool_call(
        "append_table_row",
        {
            "target_uid": "tr-1",
            "id": "12",
            "intent_proof": "새 행 추가",
            "meta": {"scope_table_id": 2, "block_type": "td"},
            "row_texts": ["h1", "h2"],
        },
    )
    assert err is None
    assert payload is not None
    assert payload["metadata"]["operation"] == "append_table_row"
    assert payload["metadata"]["row_texts"] == ["h1", "h2"]
    assert payload["rows"] == ["h1", "h2"]


def test_tool_parser_append_table_row_allows_empty_meta_contract():
    payload, err = parse_v710_tool_call(
        "append_table_row",
        {
            "target_uid": "tr-1",
            "id": "12",
            "intent_proof": "새 행 추가",
            "meta": {},
            "row_texts": ["h1", "h2"],
        },
    )
    assert err is None
    assert payload is not None
    assert payload["action"] == "append_table_row"
    assert payload["rows"] == ["h1", "h2"]


def test_tool_parser_insert_footnote_uses_new_text_when_footnote_text_missing():
    payload, err = parse_v710_tool_call(
        "insert_footnote",
        {
            "target_uid": "footnote-7",
            "id": "7",
            "new_text": "각주 내용",
            "old_text": "앵커 문구",
            "intent_proof": "근거 각주 추가",
            "meta": {"block_type": "footnote"},
        },
    )
    assert err is None
    assert payload is not None
    assert payload["metadata"]["operation"] == "insert_footnote"
    assert payload["metadata"]["footnote_anchor_text"] == "앵커 문구"
    assert payload["metadata"]["footnote_text"] == "각주 내용"
    assert payload["content"] == "각주 내용"


def test_tool_parser_unknown_tool_is_rejected():
    payload, err = parse_v710_tool_call("add_footnote", {"content": "x"})
    assert payload is None
    assert err == "unknown_tool:add_footnote"


def test_tool_parser_uses_target_uid_when_id_missing():
    payload, err = parse_v710_tool_call(
        "replace_cell_content",
        {
            "target_uid": "cell-42",
            "content": "본문",
            "intent_proof": "작성",
        },
    )
    assert err is None
    assert payload is not None
    assert payload["id"] == 42
    assert payload["content"] == "본문"


def test_tool_parser_message_accepts_text_alias():
    payload, err = parse_v710_tool_call(
        "message",
        {
            "text": "작성을 완료했습니다.",
        },
    )
    assert err is None
    assert payload is not None
    assert payload["action"] == "message"
    assert payload["message"] == "작성을 완료했습니다."


def test_v710_tool_schema_covers_all_26_commands():
    tools = build_v710_edit_tools()
    tool_names = [tool["name"] for tool in tools]
    assert len(EDIT_TOOL_NAMES) == 26
    assert set(tool_names) == set(EDIT_TOOL_NAMES)


def test_v710_tool_schema_requires_id_for_edit_tools():
    tools = build_v710_edit_tools()
    by_name = {tool["name"]: tool for tool in tools}

    for name in EDIT_TOOL_NAMES:
        if name in {"thinking", "message", "find_and_replace_all"}:
            continue
        required = set(by_name[name]["parameters"].get("required", []))
        assert "id" in required


def test_v710_tool_schema_table_path_array_has_items():
    tools = build_v710_edit_tools()
    by_name = {tool["name"]: tool for tool in tools}
    replace_cell = by_name["replace_cell_content"]
    meta_props = replace_cell["parameters"]["properties"]["meta"]["properties"]
    table_path = meta_props["table_path"]
    assert "anyOf" in table_path
    array_variant = next((v for v in table_path["anyOf"] if v.get("type") == "array"), None)
    assert array_variant is not None
    assert "items" in array_variant


def test_integrated_prompt_builder_is_v711():
    prompt_module = pytest.importorskip("llm.system_prompt_v7_11")
    build_system_prompt_v7_11 = prompt_module.build_system_prompt_v7_11

    prompt = build_system_prompt_v7_11()
    # v7.11 핵심 섹션 존재 확인
    assert "ENRICHED_CVD" in prompt
    assert "문서 분석 추론" in prompt
    assert "셀 역할 추론" in prompt
    assert "Delta Validation Contract (v7.11)" in prompt
    assert "scope_table_id" in prompt
    assert "td_sig_v1" in prompt
    # v7.10 Graph 참조 제거 확인
    assert "DOCUMENT_GRAPH_JSON" not in prompt


def test_v711_custom_rules_and_override():
    prompt_module = pytest.importorskip("llm.system_prompt_v7_11")
    build_system_prompt_v7_11 = prompt_module.build_system_prompt_v7_11

    prompt = build_system_prompt_v7_11(custom_rules="- 문장 끝맺음을 단정형으로 유지")
    assert "<USER_WRITING_PREFERENCES" in prompt
    assert "문장 끝맺음을 단정형으로 유지" in prompt
    assert "보안 정책" in prompt

    assert build_system_prompt_v7_11(full_override="OVERRIDE") == "OVERRIDE"


def test_execute_edits_embedded_message_extracted():
    """execute_edits의 message 필드가 파서에 의해 별도 message payload로 추출됨."""
    from llm.edit_tools_parser import parse_execute_edits

    results = parse_execute_edits({
        "operations": [
            {"op": "replace_cell_content", "id": 7, "new_text": "내용"},
        ],
        "message": "작성 완료!",
    })
    # 2개: replace_cell_content + embedded message
    assert len(results) == 2
    edit_payload, edit_err = results[0]
    assert edit_payload is not None
    assert edit_payload.get("action") in ("edit_document", "replace_cell_content")

    msg_payload, msg_err = results[1]
    assert msg_payload is not None
    assert msg_payload["action"] == "message"
    assert msg_payload["message"] == "작성 완료!"


def test_execute_edits_schema_requires_message_field():
    """execute_edits 스키마에 message가 required 필드로 포함됨."""
    from llm.edit_tools_schema import build_v711_batch_tools

    tools = build_v711_batch_tools()
    exec_tool = next(t for t in tools if t["name"] == "execute_edits")
    params = exec_tool["parameters"]
    assert "message" in params["properties"]
    assert "message" in params["required"]

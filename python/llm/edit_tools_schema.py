"""v7.11 edit tool schema definitions (Responses API function tools)."""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Tool descriptions: each tool gets a concise, actionable description so the
# LLM knows *when* and *for which element* to use it.
# ---------------------------------------------------------------------------
_TOOL_DESCRIPTIONS: Dict[str, str] = {
    # 표 셀 (<td>)
    "replace_cell_content": "표 셀(<td>) 전체 텍스트를 새 내용으로 교체. 빈 셀 채우기, placeholder 교체에 사용",
    "delete_cell_content": "표 셀(<td>) 내용 삭제",
    # 문단 (<p>, 표 밖)
    "replace_paragraph": "표 밖 문단(<p>) 전체 교체. 표 안 셀은 replace_cell_content 사용",
    "append_paragraph": "문단(<p>) 뒤에 새 텍스트 추가",
    "delete_paragraph": "문단(<p>) 삭제",
    # 리스트
    "replace_list": "리스트 항목 전체 교체",
    "append_list": "리스트에 항목 추가",
    "delete_list": "리스트 삭제",
    # 표 행
    "append_table_row": "표에 새 행 추가. row_texts: 각 셀 텍스트 배열",
    "replace_table_row": "표의 특정 행 전체 교체. row_texts: 각 셀 텍스트 배열",
    "delete_table_row": "표의 특정 행 삭제",
    "create_table": "새 표 생성",
    "delete_table": "표 전체 삭제",
    # 텍스트박스
    "delete_textbox": "텍스트박스 삭제",
    # 각주/미주
    "insert_footnote": "각주 삽입. new_text: 각주 내용",
    "replace_footnote": "기존 각주 내용 교체",
    "delete_footnote": "각주 삭제",
    # 찾기/바꾸기
    "find_and_replace_in_paragraph": "특정 블록 내 텍스트 부분 치환. old_text→new_text. 선택영역 편집에 필수",
    "find_and_replace_all": "문서 전체에서 텍스트 찾아 바꾸기. id 불필요",
    # 서식
    "apply_para_style": "문단 스타일 적용 (정렬, 간격, 들여쓰기)",
    "apply_charshape": "글자 스타일 적용 (굵게, 기울임, 크기, 폰트). 기존 텍스트 서식만 변경 시 사용",
    # 기타
    "add_endnote": "미주 삽입",
    "add_source_ref": "출처 참조 각주 삽입. ref_id, title 필수",
    "line_break": "줄바꿈 삽입",
}


EDIT_TOOL_NAMES: List[str] = [
    "replace_cell_content",
    "delete_cell_content",
    "replace_paragraph",
    "append_paragraph",
    "delete_paragraph",
    "replace_list",
    "append_list",
    "delete_list",
    "append_table_row",
    "replace_table_row",
    "delete_table_row",
    "create_table",
    "delete_table",
    "delete_textbox",
    "insert_footnote",
    "replace_footnote",
    "delete_footnote",
    "find_and_replace_in_paragraph",
    "find_and_replace_all",
    "apply_para_style",
    "apply_charshape",
    "add_endnote",
    "add_source_ref",
    "line_break",
    "thinking",
    "message",
]


def _meta_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "scope_table_id": {"type": ["string", "number"]},
            "td_sig_v1": {"type": "string"},
            "p_sig_v1": {"type": "string"},
            "table_path": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": ["integer", "string"]}},
                ]
            },
            "block_type": {"type": "string"},
        },
    }


def _base_edit_properties() -> Dict[str, Any]:
    return {
        "target_uid": {"type": "string"},
        "id": {"type": ["string", "integer"]},
        "meta": _meta_schema(),
        "intent_proof": {"type": "string"},
        # 편의 입력 허용 (parser에서 meta로 병합)
        "scope_table_id": {"type": ["string", "number"]},
        "td_sig_v1": {"type": "string"},
        "p_sig_v1": {"type": "string"},
        "table_path": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": ["integer", "string"]}},
            ]
        },
    }


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "additionalProperties": True,
            "properties": properties,
            "required": required,
        },
    }


def _desc(name: str) -> str:
    return _TOOL_DESCRIPTIONS.get(name, f"{name} operation")


def _build_schema_for(name: str) -> Dict[str, Any]:
    if name == "thinking":
        return _tool(
            "thinking",
            "추론/계획 기록. 반드시 파일 확인 또는 편집 명령과 같은 응답에서 함께 호출. 단독 호출 금지.",
            {"content": {"type": "string"}},
            ["content"],
        )
    if name == "message":
        return _tool(
            "message",
            "편집 없이 사용자에게 메시지만 전달할 때 사용 (보안 거절, 질문 응답 등). 편집 시에는 execute_edits의 message 필드를 사용하세요.",
            {"content": {"type": "string"}},
            ["content"],
        )

    base = _base_edit_properties()
    # Runtime executor는 block id 중심으로 동작한다.
    # target_uid/meta/intent_proof는 선택 메타로 유지하고, 최소 필수값은 id로 완화한다.
    required_base = ["id"]

    if name in {"replace_cell_content", "replace_paragraph", "append_paragraph", "replace_list", "append_list"}:
        props = {**base, "new_text": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["new_text"])

    if name in {"delete_cell_content", "delete_paragraph", "delete_list", "delete_table_row", "delete_table", "delete_textbox", "delete_footnote"}:
        return _tool(name, _desc(name), base, required_base)

    if name in {"append_table_row", "replace_table_row"}:
        props = {
            **base,
            "row_texts": {"type": "array", "items": {"type": "string"}},
        }
        return _tool(name, _desc(name), props, required_base + ["row_texts"])

    if name == "create_table":
        props = {
            **base,
            "row_texts": {"type": "array", "items": {"type": "string"}},
        }
        return _tool(name, _desc(name), props, required_base)

    if name == "insert_footnote":
        props = {**base, "new_text": {"type": "string"}, "old_text": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["new_text"])

    if name == "replace_footnote":
        props = {**base, "new_text": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["new_text"])

    if name == "find_and_replace_in_paragraph":
        props = {**base, "old_text": {"type": "string"}, "new_text": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["old_text", "new_text"])

    if name == "find_and_replace_all":
        props = {
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "intent_proof": {"type": "string"},
            "meta": _meta_schema(),
        }
        return _tool(name, _desc(name), props, ["old_text", "new_text"])

    if name == "apply_para_style":
        props = {**base, "style_name": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["style_name"])

    if name == "apply_charshape":
        props = {
            **base,
            "bold": {"type": "boolean"},
            "italic": {"type": "boolean"},
            "underline": {"type": "boolean"},
            "font_size": {"type": ["number", "string"]},
            "font_name": {"type": "string"},
        }
        return _tool(name, _desc(name), props, required_base)

    if name == "add_endnote":
        props = {**base, "new_text": {"type": "string"}}
        return _tool(name, _desc(name), props, required_base + ["new_text"])

    if name == "add_source_ref":
        props = {
            **base,
            "ref_id": {"type": "string"},
            "title": {"type": "string"},
            "publisher": {"type": "string"},
            "year": {"type": "string"},
            "url": {"type": "string"},
        }
        return _tool(name, _desc(name), props, required_base + ["ref_id", "title"])

    if name == "line_break":
        return _tool(name, _desc(name), base, required_base)

    # fallback (should not happen due to EDIT_TOOL_NAMES)
    return _tool(name, _desc(name), base, required_base)


def build_v710_edit_tools() -> List[Dict[str, Any]]:
    """Build Responses API tool schemas for v7.10 edit operations."""
    return [_build_schema_for(name) for name in EDIT_TOOL_NAMES]


# ---------------------------------------------------------------------------
# v7.11 batch tool: execute_edits
# ---------------------------------------------------------------------------

# All edit operation names that can appear in execute_edits.operations[].op
BATCH_OP_NAMES: List[str] = [
    "replace_cell_content",
    "delete_cell_content",
    "replace_paragraph",
    "append_paragraph",
    "delete_paragraph",
    "replace_list",
    "append_list",
    "delete_list",
    "append_table_row",
    "replace_table_row",
    "delete_table_row",
    "create_table",
    "delete_table",
    "delete_textbox",
    "insert_footnote",
    "replace_footnote",
    "delete_footnote",
    "find_and_replace_in_paragraph",
    "find_and_replace_all",
    "apply_para_style",
    "apply_charshape",
    "add_endnote",
    "add_source_ref",
    "line_break",
]


def _build_execute_edits_schema() -> Dict[str, Any]:
    """Build the batch execute_edits tool schema.

    Single tool that wraps all 24 edit operations into an ``operations`` array,
    reducing total tool count from 27 → 4.
    """
    operation_schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "op": {
                "type": "string",
                "enum": BATCH_OP_NAMES,
                "description": "편집 연산 이름",
            },
            "id": {
                "type": ["string", "integer"],
                "description": "대상 블록 ID (bare integer)",
            },
            "new_text": {"type": "string"},
            "old_text": {"type": "string"},
            "row_texts": {"type": "array", "items": {"type": "string"}},
            "target_uid": {"type": "string"},
            "intent_proof": {"type": "string"},
            "meta": _meta_schema(),
            "scope_table_id": {"type": ["string", "number"]},
            "td_sig_v1": {"type": "string"},
            "p_sig_v1": {"type": "string"},
            "table_path": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": ["integer", "string"]}},
                ]
            },
            # apply_charshape fields
            "bold": {"type": "boolean"},
            "italic": {"type": "boolean"},
            "underline": {"type": "boolean"},
            "font_size": {"type": ["number", "string"]},
            "font_name": {"type": "string"},
            # apply_para_style
            "style_name": {"type": "string"},
            # add_source_ref
            "ref_id": {"type": "string"},
            "title": {"type": "string"},
            "publisher": {"type": "string"},
            "year": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["op"],
    }

    return _tool(
        "execute_edits",
        "문서 편집 명령 배치 실행 + 사용자 완료 보고서. operations와 message를 반드시 함께 제공",
        {
            "operations": {
                "type": "array",
                "items": operation_schema,
                "description": "편집 연산 배열. 각 항목은 op(연산명)과 해당 연산의 인자를 포함",
            },
            "message": {
                "type": "string",
                "description": "사용자에게 보여줄 작업 완료 보고서. FINAL_MESSAGE_RULES 형식으로 작성",
            },
        },
        ["operations", "message"],
    )


def build_v711_batch_tools() -> List[Dict[str, Any]]:
    """Build v7.11 batch tool set: [thinking, execute_edits, message].

    Total 3 tools (search_rag is added separately in streaming_client).
    """
    return [
        _build_schema_for("thinking"),
        _build_execute_edits_schema(),
        _build_schema_for("message"),
    ]


def build_v711_analysis_tools() -> List[Dict[str, Any]]:
    """analysis phase 전용 도구: thinking만 반환.

    search_rag는 streaming_client.py에서 별도로 prepend한다.
    execute_edits·message는 analysis 단계에서 보이면 안 됨 (수정4).
    """
    return [_build_schema_for("thinking")]


def build_v711_plan_tools() -> List[Dict[str, Any]]:
    """plan phase 전용 도구: thinking만 반환.

    Analysis Loop 종료 후 편집 계획 추론 전용 단계.
    tool_choice를 thinking으로 강제하여 편집 계획을 자연어로 작성하게 함.
    ID/명령어 노출 금지 규칙은 THINKING_RULES 적용.
    """
    return [_build_schema_for("thinking")]


def build_v711_editing_tools() -> List[Dict[str, Any]]:
    """editing phase 전용 도구: execute_edits + message만 반환.

    thinking tool은 제외 — Plan Call에서 이미 편집 계획 추론 완료.
    tool_choice="required"와 결합 시 LLM은 execute_edits 또는 message 중
    반드시 하나를 호출해야 함. thinking-only 종료 패턴 원천 차단.
    """
    return [
        _build_execute_edits_schema(),
        _build_schema_for("message"),
    ]

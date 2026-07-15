import os
import sys
from types import SimpleNamespace


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm.streaming_client as streaming_module
from llm.streaming_client import OpenAIStreamingClient
from llm.edit_tools_schema import EDIT_TOOL_NAMES


def _make_stream_event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


def _make_fake_stream():
    usage = SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5)
    response = SimpleNamespace(id="resp_test_1", usage=usage)
    events = [_make_stream_event("response.completed", response=response)]

    class _FakeStream:
        def __iter__(self):
            for event in events:
                yield event

        def close(self):
            return None

    return _FakeStream()


def _make_fake_tool_stream(events):
    class _FakeStream:
        def __iter__(self):
            for event in events:
                yield event

        def close(self):
            return None

    return _FakeStream()


def _make_client_with_fake_responses(captured_params: list):
    client = OpenAIStreamingClient.__new__(OpenAIStreamingClient)
    client.api_key = "test-key"
    client.model = "gpt-5.1"
    client.reasoning_effort = "low"
    client.timeout = 1.0
    client._http_client = None
    client._cancelled = False
    client._active_streams = []

    def _create(**kwargs):
        captured_params.append(kwargs)
        return _make_fake_stream()

    client.client = SimpleNamespace(
        responses=SimpleNamespace(create=_create),
    )
    return client


def test_no_legacy_text_parser_methods_left_in_client():
    assert not hasattr(OpenAIStreamingClient, "_parse_chunk")
    assert not hasattr(OpenAIStreamingClient, "_delta_to_command")
    assert not hasattr(OpenAIStreamingClient, "_parse_line")


def test_generate_commands_streaming_forces_tool_only_even_when_legacy_flags_passed(monkeypatch, tmp_path):
    captured: list = []
    client = _make_client_with_fake_responses(captured)

    monkeypatch.setattr(streaming_module, "get_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(streaming_module, "log_llm_interaction", lambda **kwargs: None)

    result = client.generate_commands_streaming(
        html='{"doc_id":"d1","nodes":[]}',
        prompt="테스트",
        on_command=lambda _cmd: None,
        use_delta=False,   # legacy 호출을 시도해도
        use_html=True,     # HTML 모드를 켜도
        enable_file_search=False,
    )

    assert result.success is True
    assert len(captured) >= 1
    first_call = captured[0]
    assert "tools" in first_call
    tool_names = [tool.get("name") for tool in first_call["tools"]]
    # v7.11.4: batch tools = [execute_edits, message] (thinking is analysis-phase only)
    assert "execute_edits" in tool_names
    assert "message" in tool_names
    assert "<ENRICHED_HDML>" in first_call["input"]
    assert "Enriched HDML" in first_call["instructions"]


def test_generate_commands_streaming_enforces_thinking_first_and_message_last(monkeypatch, tmp_path):
    captured: list = []
    commands = []
    client = _make_client_with_fake_responses(captured)

    usage = SimpleNamespace(input_tokens=5, output_tokens=7, total_tokens=12)
    response = SimpleNamespace(id="resp_test_2", usage=usage)
    events = [
        _make_stream_event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c1", id="c1", name="replace_cell_content"),
        ),
        _make_stream_event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c1",
                id="c1",
                name="replace_cell_content",
                arguments='{"target_uid":"td-1","id":1,"new_text":"A","intent_proof":"채움","meta":{"td_sig_v1":"tdv1:1"}}',
            ),
        ),
        _make_stream_event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c2", id="c2", name="message"),
        ),
        _make_stream_event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c2",
                id="c2",
                name="message",
                arguments='{"content":"중간 메시지"}',
            ),
        ),
        _make_stream_event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c3", id="c3", name="replace_paragraph"),
        ),
        _make_stream_event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c3",
                id="c3",
                name="replace_paragraph",
                arguments='{"target_uid":"p-2","id":2,"new_text":"B","intent_proof":"교체","meta":{"p_sig_v1":"pv1:2"}}',
            ),
        ),
        _make_stream_event("response.completed", response=response),
    ]

    def _create(**kwargs):
        captured.append(kwargs)
        return _make_fake_tool_stream(events)

    client.client = SimpleNamespace(responses=SimpleNamespace(create=_create))

    monkeypatch.setattr(streaming_module, "get_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(streaming_module, "log_llm_interaction", lambda **kwargs: None)

    result = client.generate_commands_streaming(
        html='{"doc_id":"d2","nodes":[]}',
        prompt="작성",
        on_command=lambda cmd: commands.append(cmd),
        enable_file_search=False,
    )

    assert result.success is True
    assert commands, "no commands emitted"
    actions = [cmd.action for cmd in commands]
    assert actions[0] == "thinking"
    assert actions[-1] == "message"
    assert actions.count("message") == 1


def test_generate_commands_streaming_no_retry_when_message_missing(monkeypatch, tmp_path):
    """message tool이 LLM 응답에 없으면 retry 없이 그대로 종료 (1회 호출 원칙)"""
    captured: list = []
    commands = []

    usage = SimpleNamespace(input_tokens=3, output_tokens=4, total_tokens=7)
    response = SimpleNamespace(id="resp_test_3", usage=usage)
    events = [
        _make_stream_event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c1", id="c1", name="replace_cell_content"),
        ),
        _make_stream_event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c1",
                id="c1",
                name="replace_cell_content",
                arguments='{"id":1,"new_text":"A"}',
            ),
        ),
        _make_stream_event("response.completed", response=response),
    ]

    def _create(**kwargs):
        captured.append(kwargs)
        return _make_fake_tool_stream(events)

    client = OpenAIStreamingClient.__new__(OpenAIStreamingClient)
    client.api_key = "test-key"
    client.model = "gpt-5.1"
    client.reasoning_effort = "low"
    client.timeout = 1.0
    client._http_client = None
    client._cancelled = False
    client._active_streams = []
    client.client = SimpleNamespace(responses=SimpleNamespace(create=_create))

    monkeypatch.setattr(streaming_module, "get_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(streaming_module, "log_llm_interaction", lambda **kwargs: None)

    result = client.generate_commands_streaming(
        html='{"doc_id":"d3","nodes":[{"uid":"cell-1","editable":true}]}',
        prompt="작성",
        on_command=lambda cmd: commands.append(cmd),
        enable_file_search=False,
    )

    assert result.success is True
    assert len(captured) == 1, "API 호출은 1회만 발생해야 함 (retry 없음)"
    actions = [cmd.action for cmd in commands]
    assert actions[0] == "thinking"
    assert "message" not in actions, "LLM이 message를 호출하지 않았으므로 message 없어야 함"

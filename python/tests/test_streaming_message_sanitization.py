import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.streaming_client import _normalize_message_text


def test_normalize_message_removes_basic_markdown_tokens():
    raw = "**완료** 처리했고 `코드` 및 _기울임_도 정리했습니다."
    assert _normalize_message_text(raw) == "완료 처리했고 코드 및 기울임도 정리했습니다."


def test_normalize_message_keeps_identifier_with_underscore():
    raw = "파일명 foo_bar 는 그대로 유지됩니다."
    assert _normalize_message_text(raw) == "파일명 foo_bar 는 그대로 유지됩니다."


def test_normalize_message_converts_links_lists_and_blockquotes():
    raw = "> [가이드](https://example.com)\n- **항목1**\n- 항목2"
    assert _normalize_message_text(raw) == "가이드\n항목1\n항목2"

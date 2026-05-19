import os
import sys
import types


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modification.content_modifier import ContentModifier


def _make_modifier_stub() -> ContentModifier:
    modifier = ContentModifier.__new__(ContentModifier)
    calls = []

    def _fake_append_content_elements(self, block_id, content_value, block_type="paragraph", **style_opts):
        calls.append((block_id, content_value, block_type, style_opts))
        return True

    modifier.append_content_elements = types.MethodType(_fake_append_content_elements, modifier)
    modifier._append_calls = calls
    return modifier


def test_append_paragraph_aliases_use_same_impl():
    modifier = _make_modifier_stub()

    assert modifier.append_paragraph("10", new_text="A")
    assert modifier.append_paragraph_content("11", new_text="B")
    assert modifier.append_to_paragraph("12", new_text="C")

    calls = modifier._append_calls
    assert len(calls) == 3
    assert calls[0][0] == "10"
    assert calls[1][0] == "11"
    assert calls[2][0] == "12"
    assert all(call[2] == "paragraph" for call in calls)

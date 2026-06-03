import json
import os
import sys

# Match repo test convention: make python/ importable so `llm.*` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_FIXED = {
    "items": [
        {"requested": "이름=홍길동", "verdict": "correct", "found_value": "홍길동",
         "location_ok": True, "content_ok": True, "actual_desc": "이름 칸", "confidence": 0.95},
        {"requested": "날짜=2026", "verdict": "wrong_location", "found_value": "2026",
         "location_ok": False, "content_ok": True, "actual_desc": "주소 칸에 들어감", "confidence": 0.8},
    ],
    "invariants": {"scope_respected": True, "structure_preserved": True, "labels_preserved": True},
    "scores": {"location": 0.5, "content": 0.9, "preservation": 1.0, "scope": 1.0},
    "overall": {"correct": 1, "wrong_location": 1, "wrong_content": 0, "missing": 0,
                "over_edit": 0, "rag_retrieval_fail": 0, "rag_interpret_fail": 0, "uncertain": 0},
}


class _FakeResp:
    def __init__(self, text):
        self.output_text = text
        self.id = "resp_fake"
        self.output = []


class _FakeResponses:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._text)


class _FakeClient:
    def __init__(self, text):
        self.responses = _FakeResponses(text)


def test_verify_vision_parses_fixed_json():
    from llm.vision_verifier import verify_vision

    client = _FakeClient(json.dumps(_FIXED))
    out = verify_vision(
        images=[b"\x89PNG-fake-1", b"\x89PNG-fake-2"],
        user_intent="이름=홍길동, 날짜=2026",
        op_list=[{"op": "replace_cell_content", "id": 5, "content": "홍길동"}],
        structure_summary="표 1개, 라벨 셀 2개",
        model="gpt-5.1",
        client=client,
    )
    assert isinstance(out, dict)
    assert "error" not in out
    assert len(out["items"]) == 2
    assert out["items"][1]["verdict"] == "wrong_location"
    assert out["invariants"]["scope_respected"] is True
    assert out["scores"]["location"] == 0.5
    assert out["overall"]["correct"] == 1


def test_verify_vision_sends_images_and_intent():
    from llm.vision_verifier import verify_vision

    client = _FakeClient(json.dumps(_FIXED))
    verify_vision(
        images=[b"\x89PNG-a", b"\x89PNG-b"],
        user_intent="라벨에 값 채우기",
        op_list=[],
        structure_summary=None,
        model="gpt-5.1",
        client=client,
    )
    # One Responses API call was made with image inputs + the user intent text.
    assert len(client.responses.calls) == 1
    payload = client.responses.calls[0]
    # Walk the structured input to find image parts + the intent text part
    # (inspect the actual objects, not json.dumps which escapes non-ASCII).
    parts = payload["input"][0]["content"]
    image_parts = [p for p in parts if p.get("type") == "input_image"]
    text_parts = [p for p in parts if p.get("type") == "input_text"]
    assert len(image_parts) == 2
    assert all(p["image_url"].startswith("data:image/png;base64,") for p in image_parts)
    assert len(text_parts) == 1
    assert "라벨에 값 채우기" in text_parts[0]["text"]


def test_verify_vision_never_raises_on_bad_json():
    from llm.vision_verifier import verify_vision

    client = _FakeClient("not-json at all {{{")
    out = verify_vision(
        images=[b"\x89PNG"], user_intent="x", op_list=[],
        structure_summary=None, model="gpt-5.1", client=client,
    )
    assert out["items"] == []
    assert "error" in out


def test_verify_vision_never_raises_on_client_exception():
    from llm.vision_verifier import verify_vision

    class BoomClient:
        class _R:
            def create(self, **kwargs):
                raise RuntimeError("vision api down")
        responses = _R()

    out = verify_vision(
        images=[b"\x89PNG"], user_intent="x", op_list=[],
        structure_summary=None, model="gpt-5.1", client=BoomClient(),
    )
    assert out["items"] == []
    assert "error" in out

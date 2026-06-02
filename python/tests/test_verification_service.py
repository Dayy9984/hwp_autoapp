import os
import sys

# Match repo test convention: make python/ importable so `services.*` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_VERDICT = {
    "items": [
        {"requested": "이름=홍길동", "verdict": "correct", "found_value": "홍길동",
         "location_ok": True, "content_ok": True, "actual_desc": "이름 칸", "confidence": 0.95},
        {"requested": "날짜=2026", "verdict": "wrong_location", "found_value": "2026",
         "location_ok": False, "content_ok": True, "actual_desc": "주소 칸", "confidence": 0.8},
        {"requested": "연락처", "verdict": "missing", "found_value": None,
         "location_ok": False, "content_ok": False, "actual_desc": "비어있음", "confidence": 0.7},
    ],
    "invariants": {"scope_respected": True, "structure_preserved": True, "labels_preserved": False},
    "scores": {"location": 0.5, "content": 0.9, "preservation": 0.6, "scope": 1.0},
    "overall": {"correct": 1, "wrong_location": 1, "missing": 1},
}


class _FakeSession:
    def __init__(self):
        self.verify_calls = []
        self.render_calls = []

    def verify(self, result):
        self.verify_calls.append(result)

    def upload_render(self, request_id, pngs):
        self.render_calls.append((request_id, pngs))


def _patch(monkeypatch, pngs, verdict, session):
    import services.verification_service as vs
    monkeypatch.setattr(vs, "render_doc_to_pngs", lambda hwp, dpi=200: list(pngs))
    monkeypatch.setattr(vs, "verify_vision",
                        lambda images, user_intent, op_list, structure_summary, model, client=None: verdict)
    monkeypatch.setattr(vs, "get_session", lambda: session)
    return vs


def test_run_verification_aggregates_and_sends(monkeypatch):
    session = _FakeSession()
    vs = _patch(monkeypatch, [b"png1", b"png2"], _VERDICT, session)

    vs.run_verification(
        object(), request_id="r1", user_intent="이름/날짜/연락처 채우기",
        op_list=[{"op": "replace_cell_content", "id": 5, "content": "홍길동"}],
        model="gpt-5.1",
    )

    assert len(session.verify_calls) == 1
    result = session.verify_calls[0]
    # join key + identity
    assert result["request_id"] == "r1"
    assert isinstance(result.get("verify_id"), str) and len(result["verify_id"]) >= 8
    assert result["model"] == "gpt-5.1"
    assert result["page_count"] == 2
    # counts derived from item verdicts
    assert result["item_count"] == 3
    assert result["correct_count"] == 1
    assert result["wrong_location_count"] == 1
    assert result["missing_count"] == 1
    # scores carried from vision
    assert result["location_score"] == 0.5
    assert result["preservation_score"] == 0.6
    # invariants mapped to 1/0/None
    assert result["inv_scope_respected"] == 1
    assert result["inv_labels_preserved"] == 0
    # items passed through
    assert len(result["items"]) == 3
    # render uploaded with same request_id + pngs
    assert session.render_calls == [("r1", [b"png1", b"png2"])]


def test_run_verification_aborts_when_no_pngs(monkeypatch):
    session = _FakeSession()
    vs = _patch(monkeypatch, [], _VERDICT, session)
    vs.run_verification(object(), request_id="r1", user_intent="x", op_list=[], model="gpt-5.1")
    # empty render → abort: no verify, no upload
    assert session.verify_calls == []
    assert session.render_calls == []


def test_run_verification_never_raises_without_session(monkeypatch):
    import services.verification_service as vs
    monkeypatch.setattr(vs, "render_doc_to_pngs", lambda hwp, dpi=200: [b"png"])
    monkeypatch.setattr(vs, "verify_vision",
                        lambda images, user_intent, op_list, structure_summary, model, client=None: _VERDICT)
    monkeypatch.setattr(vs, "get_session", lambda: None)
    # no session → must not raise
    vs.run_verification(object(), request_id="r1", user_intent="x", op_list=[], model="gpt-5.1")


def test_run_verification_never_raises_on_render_error(monkeypatch):
    session = _FakeSession()
    import services.verification_service as vs

    def _boom(hwp, dpi=200):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(vs, "render_doc_to_pngs", _boom)
    monkeypatch.setattr(vs, "get_session", lambda: session)
    # render raised internally — run_verification swallows everything
    vs.run_verification(object(), request_id="r1", user_intent="x", op_list=[], model="gpt-5.1")
    assert session.verify_calls == []

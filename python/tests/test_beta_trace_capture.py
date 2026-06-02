import os
import sys
import importlib

# Match repo test convention: make python/ importable so `services.*` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh(monkeypatch, base_env=None):
    if base_env is not None:
        monkeypatch.setenv("INSERTY_TRACE_BASE", base_env)
    else:
        monkeypatch.delenv("INSERTY_TRACE_BASE", raising=False)
    import services.beta_trace as bt
    importlib.reload(bt)
    return bt


def test_worker_base_env_override(monkeypatch):
    bt = _fresh(monkeypatch, "https://staging.example.workers.dev")
    assert bt.WORKER_BASE == "https://staging.example.workers.dev"


def test_worker_base_default(monkeypatch):
    bt = _fresh(monkeypatch, None)
    assert bt.WORKER_BASE == "https://inserty-beta-worker.snsoffice.workers.dev"


def test_block_cmd_includes_request_id_and_applied(monkeypatch):
    bt = _fresh(monkeypatch, None)
    s = bt.HwpTraceSession("tok", "dev", "hash", None)
    s.block_cmd("replace_cell_content", target_id="5", applied=1, success=True, request_id="req-1")
    ev = s.queue[-1]
    assert ev["type"] == "block_cmd"
    assert ev["applied"] == 1
    assert ev["request_id"] == "req-1"


def test_response_decision_event(monkeypatch):
    bt = _fresh(monkeypatch, None)
    s = bt.HwpTraceSession("tok", "dev", "hash", None)
    s.response_decision(decision="reject", request_id="req-1", chat_id="c1",
                        message_id="m1", delta_count=5, failed_count=2)
    ev = s.queue[-1]
    assert ev["type"] == "response_decision"
    assert ev["decision"] == "reject"
    assert ev["request_id"] == "req-1"


def test_consent_record_event(monkeypatch):
    bt = _fresh(monkeypatch, None)
    s = bt.HwpTraceSession("tok", "dev", "hash", None)
    # consent_record() flushes immediately (sends + clears queue), so capture
    # the queued event before flush empties it.
    captured = {}

    def _capture_flush():
        if s.queue:
            captured["ev"] = s.queue[-1]
        s.queue = []

    monkeypatch.setattr(s, "flush", _capture_flush)
    s.consent_record(consented=True, privacy_policy_version="v1")
    ev = captured["ev"]
    assert ev["type"] == "consent_record"
    assert ev["consented"] is True


def test_verify_enqueues_result_and_items(monkeypatch):
    bt = _fresh(monkeypatch, None)
    s = bt.HwpTraceSession("tok", "dev", "hash", None)
    sent = {}
    monkeypatch.setattr(bt, "send_trace", lambda events, t, d: sent.setdefault("ev", events))
    s.verify({
        "verify_id": "v1", "request_id": "r1", "item_count": 2, "correct_count": 1,
        "wrong_location_count": 1, "location_score": 0.5, "model": "gpt-5.1",
        "inv_scope_respected": 1,
        "items": [{"verdict": "correct", "requested": "이름=홍", "location_ok": 1},
                  {"verdict": "wrong_location", "requested": "날짜=x", "location_ok": 0}],
    })
    types = [e["type"] for e in sent["ev"]]
    assert types.count("verify_result") == 1 and types.count("verify_item") == 2
    vr = [e for e in sent["ev"] if e["type"] == "verify_result"][0]
    assert vr["verify_id"] == "v1" and vr["request_id"] == "r1"


def test_upload_render_posts_each_page(monkeypatch):
    bt = _fresh(monkeypatch, "https://stg.workers.dev")
    s = bt.HwpTraceSession("tok", "dev", "hash", None)
    calls = []
    monkeypatch.setattr(bt, "_post_async", lambda url, body, headers, timeout: calls.append((url, len(body))))
    s.upload_render("r1", [b"\x89PNG1", b"\x89PNG2"])
    assert len(calls) == 2
    assert "/hwp/render?request_id=r1&page=0" in calls[0][0]
    assert "/hwp/render?request_id=r1&page=1" in calls[1][0]

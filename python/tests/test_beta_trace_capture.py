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

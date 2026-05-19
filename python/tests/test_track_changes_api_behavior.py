import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.track_changes_api as track_changes_api


def test_apply_selected_shows_toast_when_no_selection(monkeypatch):
    class FakeManager:
        def __init__(self, _connector):
            pass

        def accept_selected(self, cached_range=None):
            return False, 0

        def has_remaining_changes(self):
            return True

    monkeypatch.setattr(track_changes_api, "TrackChangesManager", FakeManager)
    track_changes_api._cached_selection_range = None

    result = track_changes_api.apply_selected_changes(object())

    assert result["success"] is False
    assert result["processed"] == 0
    assert result["showToast"] is True


def test_apply_selected_uses_cached_range_and_clears_cache(monkeypatch):
    received = []

    class FakeManager:
        def __init__(self, _connector):
            pass

        def accept_selected(self, cached_range=None):
            received.append(cached_range)
            return True, 3

        def has_remaining_changes(self):
            return True

    monkeypatch.setattr(track_changes_api, "TrackChangesManager", FakeManager)
    cached_range = ((1, 1, 0), (1, 2, 10))
    track_changes_api._cached_selection_range = cached_range

    result = track_changes_api.apply_selected_changes(object())

    assert received == [cached_range]
    assert result["success"] is True
    assert result["processed"] == 3
    assert result["showToast"] is False
    assert track_changes_api._cached_selection_range is None


def test_reject_selected_shows_toast_when_no_selection(monkeypatch):
    class FakeManager:
        def __init__(self, _connector):
            pass

        def _get_selection_range(self):
            return None

        def reject_selected(self, cached_range=None):
            return False, 0

        def has_remaining_changes(self):
            return True

    monkeypatch.setattr(track_changes_api, "TrackChangesManager", FakeManager)
    monkeypatch.setattr(track_changes_api, "_get_selected_text", lambda _connector: None)
    monkeypatch.setattr(track_changes_api, "_is_cursor_in_table", lambda _connector: None)
    track_changes_api._cached_selection_range = None

    result = track_changes_api.reject_selected_changes(object())

    assert result["success"] is False
    assert result["processed"] == 0
    assert result["showToast"] is True


def test_reject_selected_builds_rejected_ops_from_cached_selection(monkeypatch):
    received = []

    class FakeManager:
        def __init__(self, _connector):
            pass

        def _get_selection_range(self):
            return None

        def reject_selected(self, cached_range=None):
            received.append(cached_range)
            return True, 2

        def has_remaining_changes(self):
            return True

    monkeypatch.setattr(track_changes_api, "TrackChangesManager", FakeManager)
    monkeypatch.setattr(track_changes_api, "_get_selected_text", lambda _connector: "선택 텍스트")
    monkeypatch.setattr(track_changes_api, "_is_cursor_in_table", lambda _connector: True)
    cached_range = ((3, 1, 0), (3, 5, 0))
    track_changes_api._cached_selection_range = cached_range

    result = track_changes_api.reject_selected_changes(object())

    assert received == [cached_range]
    assert result["success"] is True
    assert result["processed"] == 2
    assert result["showToast"] is False
    assert result["count"] == 2
    assert result["extractionQuality"] == "partial"
    assert len(result["rejectedOps"]) >= 1
    assert track_changes_api._cached_selection_range is None


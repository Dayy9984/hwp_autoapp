"""
API 인터페이스 모듈

Electron UI ↔ Python 간 통신을 위한 IPC 엔드포인트
"""

from .track_changes_api import (
    get_track_change_context,
    cache_track_change_selection,
    apply_all_changes,
    reject_all_changes,
    apply_selected_changes,
    reject_selected_changes,
)

__all__ = [
    "get_track_change_context",
    "cache_track_change_selection",
    "apply_all_changes",
    "reject_all_changes",
    "apply_selected_changes",
    "reject_selected_changes",
]

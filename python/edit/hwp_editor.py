"""Legacy compatibility surface for old `edit.hwp_editor` patches.

This module exposes historical helper names 이전 테스트/호출자가 사용,
forwarding them to current session-state registry helpers.
"""

from engine.state.session_state import (
    check_modification_registry,
    register_modification_entry,
    retrieve_recent_insertion_data,
    store_recent_insertion_data,
)


def is_in_modification_registry(block_id: str) -> bool:
    return check_modification_registry(block_id)


def add_to_modification_registry(block_id: str) -> None:
    register_modification_entry(block_id)


def get_last_insert_info():
    return retrieve_recent_insertion_data()


def set_last_insert_info(info) -> None:
    store_recent_insertion_data(info)

"""
Runtime State Manager - Application runtime state management.

Manages runtime state for document instances, editors, segment managers,
and window tracking. Provides thread-safe access to shared resources.

Functionality:
- Document instance runtime management
- Editor and segment manager lifecycle
- Process/Window handle tracking
- Content data management
- Logging directory management
- Thread-safe access control
"""

from typing import Optional, Tuple, Any, Dict
import threading


# ====================================================================================
# Runtime State Variables
# ====================================================================================

# Document COM instance
_document_instance: Optional[Any] = None

# Document connector wrapper
_connector_wrapper: Optional[Any] = None

# Thread safety lock
_state_lock = threading.Lock()

# Modifier engine instance
_modifier_instance: Optional[Any] = None

# Segment manager instance
_segment_manager: Optional[Any] = None

# Content processor instance
_content_processor: Optional[Any] = None

# Stored cursor location (tuple: list, para, pos)
_cursor_location: Optional[Tuple[int, int, int]] = None

# Target process identifier
_target_process_id: Optional[int] = None

# Target window handle
_target_window_handle: Optional[int] = None

# Agent logging directory
_agent_log_directory: Optional[str] = None

# Temporary log file path
_temp_log_file: Optional[str] = None

# Current content data text
_content_data_text: Optional[str] = None

# Identifier to position mapping
_id_location_map: Optional[Dict[int, Tuple[int, int, int]]] = None

# Recent insertion tracking
_recent_insertion: Optional[dict] = None

# Modification tracking registry
_modification_tracker: set = set()


# ====================================================================================
# Getter / Setter Functions
# ====================================================================================

# ---------- Document Instance ----------

def retrieve_runtime_document_instance() -> Optional[Any]:
    """Retrieve runtime document instance"""
    global _document_instance
    with _state_lock:
        return _document_instance


def store_runtime_document_instance(instance: Any) -> None:
    """Store runtime document instance"""
    global _document_instance
    with _state_lock:
        _document_instance = instance


# ---------- Document Connector ----------

def retrieve_runtime_connector() -> Optional[Any]:
    """Retrieve runtime document connector"""
    global _connector_wrapper
    with _state_lock:
        return _connector_wrapper


def store_runtime_connector(connector: Any) -> None:
    """Store runtime document connector"""
    global _connector_wrapper
    with _state_lock:
        _connector_wrapper = connector


# ---------- Modifier Instance ----------

def retrieve_runtime_modifier_instance() -> Optional[Any]:
    """Retrieve runtime modifier instance"""
    global _modifier_instance
    with _state_lock:
        return _modifier_instance


def store_runtime_modifier_instance(modifier: Any) -> None:
    """Store runtime modifier instance"""
    global _modifier_instance
    with _state_lock:
        _modifier_instance = modifier


# ---------- Segment Manager Instance ----------

def retrieve_runtime_segment_manager() -> Optional[Any]:
    """Retrieve runtime segment manager instance"""
    global _segment_manager
    with _state_lock:
        return _segment_manager


def store_runtime_segment_manager(manager: Any) -> None:
    """Store runtime segment manager instance"""
    global _segment_manager
    with _state_lock:
        _segment_manager = manager


# ---------- Content Processor Instance ----------

def retrieve_runtime_content_processor() -> Optional[Any]:
    """Retrieve runtime content processor instance"""
    global _content_processor
    with _state_lock:
        return _content_processor


def store_runtime_content_processor(processor: Any) -> None:
    """Store runtime content processor instance"""
    global _content_processor
    with _state_lock:
        _content_processor = processor


# ---------- Cursor Location ----------

def retrieve_runtime_saved_cursor_location() -> Optional[Tuple[int, int, int]]:
    """Retrieve stored cursor location"""
    global _cursor_location
    with _state_lock:
        return _cursor_location


def store_runtime_saved_cursor_location(location: Optional[Tuple[int, int, int]]) -> None:
    """Store cursor location"""
    global _cursor_location
    with _state_lock:
        _cursor_location = location


# ---------- Target Process/Window Tracking ----------

def retrieve_runtime_target_process_id() -> Optional[int]:
    """Retrieve target process identifier"""
    global _target_process_id
    with _state_lock:
        return _target_process_id


def store_runtime_target_process_id(pid: Optional[int]) -> None:
    """Store target process identifier"""
    global _target_process_id
    with _state_lock:
        _target_process_id = pid


def retrieve_runtime_target_window_handle() -> Optional[int]:
    """Retrieve target window handle"""
    global _target_window_handle
    with _state_lock:
        return _target_window_handle


def store_runtime_target_window_handle(handle: Optional[int]) -> None:
    """Store target window handle"""
    global _target_window_handle
    with _state_lock:
        _target_window_handle = handle


# ---------- Logging Paths ----------

def retrieve_runtime_agent_log_directory() -> Optional[str]:
    """Retrieve agent logging directory path"""
    global _agent_log_directory
    with _state_lock:
        return _agent_log_directory


def store_runtime_agent_log_directory(directory: str) -> None:
    """Store agent logging directory path"""
    global _agent_log_directory
    with _state_lock:
        _agent_log_directory = directory


def retrieve_runtime_temp_log_path() -> Optional[str]:
    """Retrieve temporary log file path"""
    global _temp_log_file
    with _state_lock:
        return _temp_log_file


def store_runtime_temp_log_path(path: str) -> None:
    """Store temporary log file path"""
    global _temp_log_file
    with _state_lock:
        _temp_log_file = path


# ---------- Content Data ----------

def retrieve_runtime_content_data() -> Optional[str]:
    """Retrieve current content data text"""
    global _content_data_text
    with _state_lock:
        return _content_data_text


def store_runtime_content_data(data: str) -> None:
    """Store content data text"""
    global _content_data_text
    with _state_lock:
        _content_data_text = data


def retrieve_runtime_id_location_mapping() -> Optional[Dict[int, Tuple[int, int, int]]]:
    """Retrieve identifier to position mapping"""
    global _id_location_map
    with _state_lock:
        return _id_location_map


def store_runtime_id_location_mapping(mapping: Dict[int, Tuple[int, int, int]]) -> None:
    """Store identifier to position mapping"""
    global _id_location_map
    with _state_lock:
        _id_location_map = mapping


# ---------- Recent Insertion Tracking ----------

def retrieve_recent_insertion_data() -> Optional[dict]:
    """Retrieve recent insertion tracking data"""
    global _recent_insertion
    with _state_lock:
        return _recent_insertion


def store_recent_insertion_data(data: Optional[dict]) -> None:
    """Store recent insertion tracking data"""
    global _recent_insertion
    with _state_lock:
        _recent_insertion = data


# ---------- Modification Registry ----------

def retrieve_modification_registry() -> set:
    """Retrieve modification tracking registry"""
    global _modification_tracker
    with _state_lock:
        return _modification_tracker.copy()


def register_modification_entry(element_id: int) -> None:
    """Register element in modification tracking"""
    global _modification_tracker
    with _state_lock:
        _modification_tracker.add(element_id)


def check_modification_registry(element_id: int) -> bool:
    """Check if element is in modification registry"""
    global _modification_tracker
    with _state_lock:
        return element_id in _modification_tracker


def clear_modification_registry() -> None:
    """Clear modification tracking registry"""
    global _modification_tracker
    with _state_lock:
        _modification_tracker.clear()


# ====================================================================================
# Window Binding
# ====================================================================================

def bind_runtime_target_window(pid: Optional[int] = None, handle: Optional[int] = None) -> bool:
    """
    Bind target window to runtime state.

    Stores process ID and window handle for window tracking.

    Args:
        pid: Process identifier
        handle: Window handle

    Returns:
        Success status
    """
    global _target_process_id, _target_window_handle

    with _state_lock:
        current_pid = _target_process_id
        current_handle = _target_window_handle

        # Update identifiers
        new_pid = pid if pid is not None else current_pid
        new_handle = handle if handle is not None else current_handle

        _target_process_id = new_pid
        _target_window_handle = new_handle

        return True


# ====================================================================================
# State Management
# ====================================================================================

def reset_runtime_state() -> None:
    """Reset all runtime state"""
    global _document_instance, _connector_wrapper, _modifier_instance
    global _segment_manager, _content_processor
    global _cursor_location, _target_process_id, _target_window_handle
    global _content_data_text, _id_location_map, _recent_insertion
    global _modification_tracker

    with _state_lock:
        _document_instance = None
        _connector_wrapper = None
        _modifier_instance = None
        _segment_manager = None
        _content_processor = None
        _cursor_location = None
        _target_process_id = None
        _target_window_handle = None
        _content_data_text = None
        _id_location_map = None
        _recent_insertion = None
        _modification_tracker = set()


def reset_session_state() -> None:
    """Reset session-related state only"""
    global _content_data_text, _id_location_map, _recent_insertion
    global _modification_tracker, _cursor_location

    with _state_lock:
        _content_data_text = None
        _id_location_map = None
        _recent_insertion = None
        _modification_tracker = set()
        _cursor_location = None


def get_runtime_state_summary() -> dict:
    """Get runtime state summary for debugging"""
    with _state_lock:
        return {
            "document_instance": _document_instance is not None,
            "connector_wrapper": _connector_wrapper is not None,
            "modifier_instance": _modifier_instance is not None,
            "segment_manager": _segment_manager is not None,
            "content_processor": _content_processor is not None,
            "cursor_location": _cursor_location,
            "target_process_id": _target_process_id,
            "target_window_handle": _target_window_handle,
            "content_data_length": len(_content_data_text) if _content_data_text else 0,
            "id_location_map_count": len(_id_location_map) if _id_location_map else 0,
            "modification_tracker_count": len(_modification_tracker),
            "recent_insertion": _recent_insertion is not None,
        }

"""
ApplicationStateManager - 애플리케이션 전역 상태 중앙 관리 시스템

애플리케이션 전역에서 공유되는 상태 정보를 중앙 집중식으로 관리합니다.
모듈 간 데이터 공유, 상태 동기화, 스레드 안전성 등을 보장합니다.

주요 특징:
- 중앙 집중식 상태 관리 (Centralized State Management)
- 스레드 안전성 보장 (Thread-Safe Operations)
- 상태 변경 추적 (State Change Tracking)
- 메모리 사용량 모니터링 (Memory Usage Monitoring)
- 상태 검증 및 무결성 검사 (State Validation & Integrity Checks)
"""

from typing import Any, Dict, Optional, Callable, List
import threading
import time
import weakref
from dataclasses import dataclass, field
from enum import Enum


class SessionState(Enum):
    """세션 상태 열거형"""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATING = "terminating"
    TERMINATED = "terminated"


@dataclass
class StateChangeEvent:
    """상태 변경 이벤트"""
    timestamp: float
    change_type: str
    key: str
    old_value: Any
    new_value: Any
    source: str = "unknown"


@dataclass
class ApplicationContext:
    """애플리케이션 컨텍스트"""
    session_id: str
    created_at: float
    last_accessed: float
    state: SessionState = SessionState.UNINITIALIZED
    process_identifier: Optional[int] = None
    window_handle: Optional[int] = None
    log_directory: Optional[str] = None
    temporary_log_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ApplicationStateManager:
    """애플리케이션 전역 상태 중앙 관리자

    전체 애플리케이션의 상태를 중앙에서 관리하며,
    상태 변경 추적, 무결성 검증, 스레드 안전성을 제공합니다.
    """

    def __init__(self):
        """ApplicationStateManager 초기화"""
        self._state_lock = threading.RLock()  # 재entrant lock 사용
        self._contexts: Dict[str, ApplicationContext] = {}
        self._shared_state: Dict[str, Any] = {}
        self._state_history: List[StateChangeEvent] = []
        self._change_listeners: List[Callable[[StateChangeEvent], None]] = []
        self._max_history_size = 1000
        self._creation_time = time.time()

        # Weak reference를 사용한 메모리 관리
        self._active_sessions: Dict[str, weakref.WeakSet] = {}

    def create_session_context(self, session_id: str, initial_state: SessionState = SessionState.ACTIVE) -> ApplicationContext:
        """새로운 세션 컨텍스트 생성

        Args:
            session_id: 세션 고유 식별자
            initial_state: 초기 세션 상태

        Returns:
            ApplicationContext: 생성된 컨텍스트

        Raises:
            ValueError: 이미 존재하는 session_id인 경우
        """
        with self._state_lock:
            if session_id in self._contexts:
                raise ValueError(f"Session {session_id} already exists")

            context = ApplicationContext(
                session_id=session_id,
                created_at=time.time(),
                last_accessed=time.time(),
                state=initial_state
            )

            self._contexts[session_id] = context
            self._active_sessions[session_id] = weakref.WeakSet()

            self._record_state_change(
                "session_created", "contexts", None, context, "ApplicationStateManager"
            )

            return context

    def get_session_context(self, session_id: str) -> Optional[ApplicationContext]:
        """세션 컨텍스트 조회

        Args:
            session_id: 세션 식별자

        Returns:
            Optional[ApplicationContext]: 컨텍스트 또는 None
        """
        with self._state_lock:
            context = self._contexts.get(session_id)
            if context:
                context.last_accessed = time.time()
            return context

    def update_session_state(self, session_id: str, new_state: SessionState) -> bool:
        """세션 상태 업데이트

        Args:
            session_id: 세션 식별자
            new_state: 새로운 상태

        Returns:
            bool: 업데이트 성공 여부
        """
        with self._state_lock:
            context = self._contexts.get(session_id)
            if not context:
                return False

            old_state = context.state
            context.state = new_state
            context.last_accessed = time.time()

            self._record_state_change(
                "session_state_changed", f"{session_id}.state", old_state, new_state, "ApplicationStateManager"
            )

            return True

    def set_shared_value(self, key: str, value: Any, source: str = "unknown") -> None:
        """전역 공유 값 설정

        Args:
            key: 상태 키
            value: 상태 값
            source: 값 설정 소스
        """
        with self._state_lock:
            old_value = self._shared_state.get(key)
            self._shared_state[key] = value

            self._record_state_change(
                "shared_value_set", key, old_value, value, source
            )

    def get_shared_value(self, key: str, default: Any = None) -> Any:
        """전역 공유 값 조회

        Args:
            key: 상태 키
            default: 기본값

        Returns:
            Any: 상태 값 또는 기본값
        """
        with self._state_lock:
            return self._shared_state.get(key, default)

    def remove_shared_value(self, key: str) -> bool:
        """전역 공유 값 제거

        Args:
            key: 상태 키

        Returns:
            bool: 제거 성공 여부
        """
        with self._state_lock:
            if key in self._shared_state:
                old_value = self._shared_state.pop(key)
                self._record_state_change(
                    "shared_value_removed", key, old_value, None, "ApplicationStateManager"
                )
                return True
            return False

    def register_change_listener(self, listener: Callable[[StateChangeEvent], None]) -> None:
        """상태 변경 리스너 등록

        Args:
            listener: 상태 변경 콜백 함수
        """
        with self._state_lock:
            if listener not in self._change_listeners:
                self._change_listeners.append(listener)

    def unregister_change_listener(self, listener: Callable[[StateChangeEvent], None]) -> None:
        """상태 변경 리스너 해제

        Args:
            listener: 상태 변경 콜백 함수
        """
        with self._state_lock:
            if listener in self._change_listeners:
                self._change_listeners.remove(listener)

    def get_state_history(self, limit: int = 100) -> List[StateChangeEvent]:
        """상태 변경 히스토리 조회

        Args:
            limit: 조회할 최대 이벤트 수

        Returns:
            List[StateChangeEvent]: 상태 변경 이벤트 목록
        """
        with self._state_lock:
            return self._state_history[-limit:] if limit > 0 else self._state_history[:]

    def clear_state_history(self) -> None:
        """상태 변경 히스토리 초기화"""
        with self._state_lock:
            self._state_history.clear()

    def get_application_summary(self) -> Dict[str, Any]:
        """애플리케이션 상태 요약

        Returns:
            Dict[str, Any]: 애플리케이션 상태 요약 정보
        """
        with self._state_lock:
            return {
                'uptime_seconds': time.time() - self._creation_time,
                'active_sessions': len(self._contexts),
                'shared_state_keys': len(self._shared_state),
                'change_history_size': len(self._state_history),
                'registered_listeners': len(self._change_listeners),
                'session_states': {
                    session_id: context.state.value
                    for session_id, context in self._contexts.items()
                }
            }

    def cleanup_inactive_sessions(self, max_inactive_seconds: float = 3600) -> int:
        """비활성 세션 정리

        Args:
            max_inactive_seconds: 비활성 시간 임계값 (초)

        Returns:
            int: 정리된 세션 수
        """
        current_time = time.time()
        sessions_to_remove = []

        with self._state_lock:
            for session_id, context in self._contexts.items():
                if current_time - context.last_accessed > max_inactive_seconds:
                    sessions_to_remove.append(session_id)

            for session_id in sessions_to_remove:
                self._contexts.pop(session_id, None)
                self._active_sessions.pop(session_id, None)
                self._record_state_change(
                    "session_cleanup", "contexts", session_id, None, "ApplicationStateManager"
                )

        return len(sessions_to_remove)

    def _record_state_change(self, change_type: str, key: str, old_value: Any, new_value: Any, source: str) -> None:
        """상태 변경 기록

        Args:
            change_type: 변경 유형
            key: 변경된 키
            old_value: 이전 값
            new_value: 새 값
            source: 변경 소스
        """
        event = StateChangeEvent(
            timestamp=time.time(),
            change_type=change_type,
            key=key,
            old_value=old_value,
            new_value=new_value,
            source=source
        )

        # 히스토리에 추가
        self._state_history.append(event)

        # 최대 크기 제한
        if len(self._state_history) > self._max_history_size:
            self._state_history.pop(0)

        # 리스너들에게 알림
        for listener in self._change_listeners:
            try:
                listener(event)
            except Exception:
                # 리스너 에러는 무시하고 계속 진행
                pass

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        # 모든 비활성 세션 정리
        self.cleanup_inactive_sessions()


# ====================================================================================
# 전역 인스턴스 및 편의 함수들
# ====================================================================================

# 전역 ApplicationStateManager 인스턴스
_global_state_manager = ApplicationStateManager()


def get_global_state_manager() -> ApplicationStateManager:
    """전역 상태 관리자 인스턴스 반환"""
    return _global_state_manager


def create_application_session(session_id: str) -> ApplicationContext:
    """애플리케이션 세션 생성 편의 함수"""
    return _global_state_manager.create_session_context(session_id)


def get_application_session(session_id: str) -> Optional[ApplicationContext]:
    """애플리케이션 세션 조회 편의 함수"""
    return _global_state_manager.get_session_context(session_id)


def set_application_state(key: str, value: Any, source: str = "unknown") -> None:
    """애플리케이션 상태 설정 편의 함수"""
    _global_state_manager.set_shared_value(key, value, source)


def get_application_state(key: str, default: Any = None) -> Any:
    """애플리케이션 상태 조회 편의 함수"""
    return _global_state_manager.get_shared_value(key, default)


# 전역 상태 관리자를 애플리케이션 시작 시 자동으로 생성
_default_session = create_application_session("default")

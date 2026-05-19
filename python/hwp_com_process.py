"""
HWP COM Process (hwp_com_process.py)

HWP COM 객체 관리 및 문서 편집 작업을 전담하는 별도 프로세스입니다.
Electron Main Process와 stdin/stdout으로 JSON-RPC 통신합니다.

**격리 목적:**
- HWP COM 크래시 시 Electron 전체 영향 방지
- 안정적인 재연결 및 복구
- PID/HWND 기반 정확한 HWP 프로세스 바인딩

사용법:
    uv run python hwp_com_process.py

통신 프로토콜:
    - stdin으로 JSON 명령 수신
    - stdout으로 JSON 응답 전송
    - 각 메시지는 줄바꿈으로 구분

Window Monitor 연동:
    - method: "window:bind", params: {pid, hwnd}
    - hwp_window_monitor.py로부터 PID/HWND 전달받음
"""

import sys
import os
import re
import json
import asyncio
import traceback
from html.parser import HTMLParser
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path

# 선택적 의존성 (없어도 동작함)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[Python] ⚠️ psutil not available - process filtering disabled", file=sys.stderr)

# DPI Awareness 설정 (고해상도 디스플레이 지원)
try:
    from ctypes import windll, c_int
    from ctypes.wintypes import BOOL

    # Windows 10 1703+ (가장 정확한 방법)
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        SetProcessDpiAwarenessContext = windll.user32.SetProcessDpiAwarenessContext
        SetProcessDpiAwarenessContext.argtypes = [c_int]
        SetProcessDpiAwarenessContext.restype = BOOL
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        # Windows 8.1+ fallback
        try:
            PROCESS_PER_MONITOR_DPI_AWARE = 2
            SetProcessDpiAwareness = windll.shcore.SetProcessDpiAwareness
            SetProcessDpiAwareness.argtypes = [c_int]
            SetProcessDpiAwareness.restype = c_int
            SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        except Exception:
            pass
except Exception:
    pass

# Windows COM 초기화
try:
    import pythoncom
    pythoncom.CoInitialize()
except ImportError:
    pass  # pythoncom이 없는 환경에서는 스킵

# 현재 구조 기반 import
from engine.connection.document_connector import HwpConnector
from engine.connection.document_collector import DocumentMetadataCollector
from engine.connection.rot_access import ROTAccessManager
from utilities.error_handler import ErrorHandler

# 선택 영역 정보 추출용 import
from hwp_window_monitor import get_selection_info_from_hwp
from engine.state.session_state import (
    retrieve_runtime_connector,
    store_runtime_connector,
    retrieve_runtime_segment_manager,
    store_runtime_segment_manager,
    retrieve_runtime_modifier_instance,
    store_runtime_modifier_instance,
    retrieve_runtime_content_processor,
    store_runtime_content_processor,
    retrieve_runtime_target_process_id,
    store_runtime_target_process_id,
    retrieve_runtime_target_window_handle,
    store_runtime_target_window_handle,
    bind_runtime_target_window,
    reset_session_state,
    retrieve_runtime_content_data,
    store_runtime_content_data,
    retrieve_runtime_id_location_mapping,
    store_runtime_id_location_mapping,
    retrieve_recent_insertion_data,
    store_recent_insertion_data,
    clear_modification_registry,
)
from processing.structure.view_generator import DocumentViewGenerator
from processing.structure.view_structures import DocumentView, Element
from processing.structure.segment_registry import SegmentRegistry as DocumentBlockManager
from processing.structure.enriched_cvd_serializer import serialize_enriched_cvd
from processing.structure.hwpml_direct_graph_builder import (
    build_document_graph_from_hwpml,
)
from processing.structure.hwpml_runtime_registry_builder import (
    build_segment_registry_from_extractor,
)
from processing.extraction.cvd_extractor import CVDExtractor
from processing.detection.form_detector import FormDetector
from modification.content_modifier import ContentModifier
from llm.client import get_llm_client
from llm.streaming_client import get_streaming_client, StreamingCommand
from readers.txt_reader import TxtReader
from readers.excel_reader import ExcelReader
from api.track_changes_api import (
    get_track_change_context,
    cache_track_change_selection,
    apply_all_changes,
    reject_all_changes,
    apply_selected_changes,
    reject_selected_changes,
)
# Phase 4: CVD/Diff 서비스
from services.config import config
from services.cvd_service import CVDService
from services.diff_service import DiffService



# 연속 실패 로그 억제 (문서 프로세서)
_doc_processor_failure_count = 0
_DOC_PROCESSOR_LOG_THRESHOLD = 1

class DocumentProcessor:
    """문서 처리기 - HWP 문서 편집 작업 수행

    SafeHwp 패턴 지원: Window Monitor로부터 전달받은 PID/HWND로 정확한 HWP 바인딩
    전역 상태 관리: core.global_state 모듈을 통한 Thread-safe 상태 관리
    """

    def __init__(self):
        # HWP 연결 관리 (전역 상태 → 인스턴스 캐시)
        self._connector: Optional[HwpConnector] = None
        self._current_file: Optional[str] = None

        # Window Monitor 연동 (SafeHwp 패턴) → 전역 상태에서 관리
        self._process_identifier: Optional[int] = retrieve_runtime_target_process_id()
        self._window_handle: Optional[int] = retrieve_runtime_target_window_handle()

        # Undo/Diff 관련 상태
        self._diff_mode_enabled: bool = False
        self._track_changes_active: bool = False
        self._pending_track_changes: bool = False
        self._edit_history: list = []  # [{id, chatId, editCount, timestamp}, ...]
        self._current_chat_id: Optional[str] = None

        # ContentModifier 인스턴스 (기존 편집기)
        self._content_modifier: Optional[ContentModifier] = None

        # CVDExtractor 인스턴스 (CVD 형식 문서 추출)
        self._cvd_extractor: Optional[CVDExtractor] = None

        # 문서 뷰 생성기 (HTML 형식 - 하위 호환)
        self._view_generator: Optional[DocumentViewGenerator] = None

        # 편집 허용 블록 ID 목록 (prepare_context에서 설정)
        self._allowed_element_ids: Optional[List[int]] = None
        self._active_context_id: Optional[str] = None
        self._target_uid_to_id: Dict[str, int] = {}
        self._id_to_target_uid: Dict[str, str] = {}
        self._document_graph_json: Optional[str] = None

        # HTML 기반 편집을 위한 DocumentView 캐시
        self._current_doc_view: Optional[DocumentView] = None
        # 양식/디자인 영역 감지
        self._form_detector: Optional[FormDetector] = None
        self._form_regions: List[Any] = []
        self._design_table_groups: set[int] = set()
        # v7.11: role 사전 분류 제거 → LLM 추론 기반 셀 역할 판별

        # 전역 상태에 DocumentProcessor 등록
        store_runtime_content_processor(self)

    def _ensure_connector(self) -> HwpConnector:
        """HWP Connector 초기화 (lazy)

        process_identifier/window_handle이 설정되어 있으면 SafeHwp 패턴으로 바인딩
        전역 상태와 동기화합니다.
        """
        # 1. 먼저 현재 PID/HWND 가져오기 (Window Monitor가 설정한 값)
        new_pid = retrieve_runtime_target_process_id() or self._process_identifier
        new_hwnd = retrieve_runtime_target_window_handle() or self._window_handle

        # 2. global_connector PID 변경 검증 (문서 껐다 켰을 때 대응)
        global_connector = retrieve_runtime_connector()
        if global_connector is not None:
            old_pid = getattr(global_connector, 'process_identifier', None)

            # PID 변경 감지 → global connector 무효화
            if old_pid and new_pid and old_pid != new_pid:
                print(f"[DocumentProcessor] Global connector PID changed ({old_pid}->{new_pid}), invalidating", file=sys.stderr)
                try:
                    global_connector.disconnect()
                except Exception:
                    pass
                store_runtime_connector(None)
                global_connector = None

                # ✅ 컴포넌트 초기화 (RPC 서버 오류 방지)
                print(f"[DocumentProcessor] PID 변경으로 인한 컴포넌트 초기화", file=sys.stderr)
                self._cvd_extractor = None
                self._content_modifier = None
                self._form_detector = None
                self._current_doc_view = None
            # PID 동일 → validate_and_reconnect() 검증 (무효화 시 재바인딩)
            elif global_connector.validate_and_reconnect():
                self._connector = global_connector
                return self._connector
            # 죽은 connector 정리
            else:
                print("[DocumentProcessor] Global connector is dead, cleaning up", file=sys.stderr)
                try:
                    global_connector.disconnect()
                except Exception:
                    pass
                store_runtime_connector(None)
                store_runtime_target_process_id(None)
                store_runtime_target_window_handle(None)

        # 3. 기존 connector 검증
        if self._connector is not None:
            old_pid = getattr(self._connector, 'process_identifier', None)

            # PID 변경 감지
            if old_pid and new_pid and old_pid != new_pid:
                print(f"[DocumentProcessor] Instance connector PID changed ({old_pid}->{new_pid}), recreating", file=sys.stderr)
                try:
                    self._connector.disconnect()
                except Exception:
                    pass
                self._connector = None

                # ✅ 컴포넌트 초기화 (RPC 서버 오류 방지)
                print(f"[DocumentProcessor] PID 변경으로 인한 컴포넌트 초기화", file=sys.stderr)
                self._cvd_extractor = None
                self._content_modifier = None
                self._form_detector = None
                self._current_doc_view = None
            # validate_and_reconnect() 검증 (무효화 시 재바인딩)
            elif not self._connector.validate_and_reconnect():
                global _doc_processor_failure_count
                if _doc_processor_failure_count <= _DOC_PROCESSOR_LOG_THRESHOLD:
                    print("[DocumentProcessor] Instance connector is dead, cleaning up", file=sys.stderr)
                _doc_processor_failure_count += 1
                try:
                    self._connector.disconnect()
                except Exception:
                    pass
                self._connector = None

        # 4. 새로운 connector 생성
        if self._connector is None:
            self._connector = HwpConnector(
                visible=True,
                new=False,
                process_identifier=new_pid,
                window_handle=new_hwnd
            )
            if not self._connector.connect():
                # 연결 실패 시 PID/HWND 초기화
                if new_pid or new_hwnd:
                    store_runtime_target_process_id(None)
                    store_runtime_target_window_handle(None)
                raise RuntimeError("HWP 연결 실패")

            # 전역 상태에 저장
            store_runtime_connector(self._connector)

        return self._connector

    def _apply_initial_document_settings(self, hwp) -> None:
        """문서 초기화 시 기본 설정 적용

        Args:
            hwp: HWP COM 객체 또는 HwpRawWrapper
        """
        try:
            from processing.extraction.hwp_raw_wrapper import HwpRawWrapper

            # HwpRawWrapper로 래핑 (이미 래핑되어 있으면 그대로 사용)
            if not isinstance(hwp, HwpRawWrapper):
                hwp = HwpRawWrapper(hwp)

            # Track Changes 색상 설정 적용 (매크로 값과 일치)
            # 삽입: 녹색(11), 표시방식(1)
            # 삭제: 빨강(6)
            # 서식: 녹색(11), 표시방식(1)
            hwp.set_track_change_colors(
                insert_color=11,   # 녹색
                delete_color=6,    # 빨강
                format_color=11,   # 녹색
                insert_shape=1,    # 매크로와 일치
                format_shape=1     # 매크로와 일치
            )

        except Exception as e:
            # 초기화 실패는 치명적이지 않으므로 무시
            pass

    def _get_pid_from_hwnd(self, hwnd: int) -> Optional[int]:
        try:
            import ctypes
            from ctypes.wintypes import DWORD, HWND

            pid = DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(HWND(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return None

    def _get_hwp_from_rot(
        self,
        preferred_pid: Optional[int] = None,
        preferred_hwnd: Optional[int] = None,
    ):
        """
        ROT(Running Object Table)에서 HWP 객체를 가져옵니다.

        **중요**: XHwpWindows.WindowHandle 접근을 회피하여 HWP 2018 호환성을 보장합니다.
        PID/HWND 매칭은 Win32 API와 문서 메타데이터를 통해 수행합니다.
        """
        try:
            strict_target = preferred_pid is not None or preferred_hwnd is not None
            hwp_instance = ROTAccessManager.get_hwp_instance_by_target(
                preferred_pid=preferred_pid,
                preferred_hwnd=preferred_hwnd,
                strict=strict_target,
            )

            if hwp_instance is None:
                return None

            # HWP 객체 유효성 확인
            if not ROTAccessManager.verify_hwp_instance(hwp_instance):
                return None

            # 좀비 프로세스 필터링: 창이 실제로 존재하는지 확인
            try:
                window_count = hwp_instance.XHwpWindows.Count
                if window_count == 0:
                    print(f"[Python] _get_hwp_from_rot: 좀비 HWP 프로세스 감지 (창 없음), 무시", file=sys.stderr)
                    return None

                # 창 개수는 있지만 실제 윈도우 핸들이 유효한지 확인
                try:
                    from ctypes import windll
                    first_window = hwp_instance.XHwpWindows.Item(0)
                    if first_window:
                        hwnd = first_window.WindowHandle
                        # Win32 API IsWindow로 창이 유효한지 확인
                        is_valid = windll.user32.IsWindow(hwnd)
                        if not is_valid:
                            print(f"[Python] _get_hwp_from_rot: 좀비 HWP 프로세스 감지 (유효하지 않은 창 핸들), 무시", file=sys.stderr)
                            return None
                except Exception:
                    # WindowHandle 접근 실패 시에도 좀비로 간주
                    print(f"[Python] _get_hwp_from_rot: 좀비 HWP 프로세스 감지 (창 핸들 접근 실패), 무시", file=sys.stderr)
                    return None
            except Exception as e:
                print(f"[Python] _get_hwp_from_rot: XHwpWindows 확인 실패: {e}, 계속 진행", file=sys.stderr)

            return hwp_instance

        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def _ensure_track_changes_state(self, enabled: bool) -> None:
        """Track Changes 상태를 안전하게 동기화한다."""
        try:
            connector = self._ensure_connector()
            if enabled:
                if not self._track_changes_active:
                    if connector.start_track_changes():
                        self._track_changes_active = True
            else:
                if self._track_changes_active:
                    if connector.stop_track_changes():
                        self._track_changes_active = False
                self._pending_track_changes = False
        except Exception as e:
            print(f"[Python] Track Changes 상태 동기화 실패: {e}", file=sys.stderr)

    def _mark_tracked_edit(self, success: bool) -> None:
        if not success or not self._diff_mode_enabled:
            return
        self._track_changes_active = True
        self._pending_track_changes = True

    def _ensure_content_modifier(self) -> ContentModifier:
        """ContentModifier 초기화 (lazy)"""
        if self._content_modifier is None:
            connector = self._ensure_connector()
            block_manager = retrieve_runtime_segment_manager()

            def log_fn(level, msg):
                # ERROR만 출력, INFO/DEBUG는 무시
                if level == "ERROR":
                    print(f"[ContentModifier][{level}] {msg}", file=sys.stderr)

            self._content_modifier = ContentModifier(
                hwp=connector,
                block_manager=block_manager,
                log_to_main=log_fn,
            )
            store_runtime_modifier_instance(self._content_modifier)

        return self._content_modifier

    def _ensure_cvd_extractor(self) -> CVDExtractor:
        """CVDExtractor 초기화 (lazy, connector 변경 시 재생성)"""
        connector = self._ensure_connector()

        # connector의 hwp가 변경되었으면 extractor 재생성 (stale COM 방지)
        if self._cvd_extractor is not None:
            current_hwp = connector.hwp
            if current_hwp is not self._cvd_extractor.hwp:
                self._cvd_extractor = None

        if self._cvd_extractor is None:
            def log_fn(level, msg):
                # ERROR만 출력, INFO/DEBUG는 무시
                # 파라미터 순서 처리: (level, msg) 또는 (msg, level) 모두 지원
                if msg == "ERROR" or msg == "INFO" or msg == "DEBUG":
                    # (msg, level) 순서로 호출된 경우 - 순서 바꾸기
                    level, msg = msg, level
                if level == "ERROR":
                    print(f"[CVDExtractor][{level}] {msg}", file=sys.stderr)

            self._cvd_extractor = CVDExtractor(
                hwp=connector,
                log_to_main=log_fn,
            )

        return self._cvd_extractor

    def _ensure_form_detector(self) -> FormDetector:
        """FormDetector 초기화 (lazy)"""
        if self._form_detector is None:
            connector = self._ensure_connector()

            def log_fn(level, msg):
                # ERROR만 출력, INFO/DEBUG는 무시
                if level == "ERROR":
                    print(f"[FormDetector][{level}] {msg}", file=sys.stderr)

            self._form_detector = FormDetector(
                hwp=connector,
                log_callback=log_fn,
            )
        return self._form_detector

    def _pos_in_range(
        self,
        pos: Tuple[int, int, int],
        start: Tuple[int, int, int],
        end: Tuple[int, int, int],
    ) -> bool:
        return start <= pos <= end

    def _is_position_in_form_region(self, pos: Tuple[int, int, int]) -> bool:
        for region in self._form_regions or []:
            try:
                if self._pos_in_range(pos, region.start_position, region.end_position):
                    return True
            except Exception:
                continue
        return False

    def _map_design_tables_to_groups(
        self,
        block_manager: Optional[DocumentBlockManager],
        design_tables: List[Any],
    ) -> set[int]:
        design_groups: set[int] = set()
        if not block_manager or not design_tables:
            return design_groups

        for table in design_tables:
            anchor_pos = getattr(table, "anchor_pos", None)
            if not anchor_pos or not isinstance(anchor_pos, (list, tuple)):
                continue
            list_pos = anchor_pos[0]
            group_id = block_manager.get_table_group_id_by_list_pos(list_pos)
            if group_id is not None:
                design_groups.add(int(group_id))

        return design_groups


    def _get_element_position(
        self,
        element_id: Optional[int],
        editor: Optional[ContentModifier],
    ) -> Optional[Tuple[int, int, int]]:
        if element_id is None:
            return None
        try:
            id_to_pos = retrieve_runtime_id_location_mapping()
            if id_to_pos and element_id in id_to_pos:
                return tuple(id_to_pos[element_id])
        except Exception:
            pass
        try:
            block_manager = getattr(editor, "segment_registry", None) if editor else None
            if block_manager:
                return block_manager.get_position(str(element_id))
        except Exception:
            pass
        return None

    def _is_block_in_design_table(
        self,
        element_id: Optional[int],
        editor: Optional[ContentModifier],
    ) -> bool:
        if element_id is None or not self._design_table_groups:
            return False
        block_manager = getattr(editor, "segment_registry", None) if editor else None
        if not block_manager:
            return False
        try:
            group_id = block_manager.get_table_group_id(str(element_id))
            return group_id is not None and int(group_id) in self._design_table_groups
        except Exception:
            return False

    def _iter_registry_blocks(self, block_manager: Any) -> List[Any]:
        """segment_registry 구현 편차(blocks/segments)를 흡수해 블록 목록 반환."""
        if not block_manager:
            return []
        for container_name in ("segments", "blocks"):
            container = getattr(block_manager, container_name, None)
            if isinstance(container, dict):
                return list(container.values())
        return []

    def _to_block_id(self, block: Any) -> Optional[int]:
        try:
            return int(getattr(block, "id"))
        except Exception:
            return None

    def _get_block_attrs(self, block: Any) -> Dict[str, str]:
        attrs = getattr(block, "attrs", None)
        return attrs if isinstance(attrs, dict) else {}

    def _parse_block_scope(self, block_id: object) -> Tuple[Optional[int], Optional[int]]:
        """Parse scoped IDs like "tableId:cellId" into (table_id, element_id)."""
        def _safe_int(value: object) -> Optional[int]:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        if block_id is None:
            return None, None
        if isinstance(block_id, int):
            return None, block_id
        if isinstance(block_id, str):
            text = block_id.strip()
            if not text:
                return None, None
            if ":" in text:
                parts = [part for part in text.split(":") if part]
                if len(parts) < 2:
                    return None, _safe_int(parts[0]) if parts else None
                return _safe_int(parts[0]), _safe_int(parts[-1])
            return None, _safe_int(text)
        return None, _safe_int(block_id)

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        """외부 입력(문자열/실수 포함)을 안전하게 정수로 변환한다."""
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            try:
                return int(value)
            except Exception:
                return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(text)
            except Exception:
                try:
                    return int(float(text))
                except Exception:
                    return None
        try:
            return int(value)
        except Exception:
            return None

    def _is_block_id_allowed(self, element_id: Optional[int]) -> bool:
        if self._allowed_element_ids is None:
            return True
        if element_id is None:
            return False
        return element_id in self._allowed_element_ids

    def _is_block_scope_valid(
        self,
        scope_table_id: Optional[int],
        element_id: Optional[int],
        editor: Optional[ContentModifier],
    ) -> bool:
        if scope_table_id is None:
            return True
        if element_id is None:
            return False
        block_manager = getattr(editor, "segment_registry", None) if editor else None
        if not block_manager:
            return False
        table_group_id = block_manager.get_table_group_id(str(element_id))
        if table_group_id is None:
            return False
        try:
            return int(table_group_id) == int(scope_table_id)
        except Exception:
            return table_group_id == scope_table_id

    def _extract_signature_meta(
        self,
        delta_data: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Delta 메타에서 시그니처 검증 정보를 정규화해 추출한다."""
        nested_meta: Dict[str, Any] = {}
        raw_nested = metadata.get("meta")
        if isinstance(raw_nested, dict):
            nested_meta = raw_nested
        elif isinstance(raw_nested, str) and raw_nested.strip():
            try:
                parsed = json.loads(raw_nested)
                if isinstance(parsed, dict):
                    nested_meta = parsed
            except Exception:
                nested_meta = {}

        def _pick(key: str) -> Any:
            if key in delta_data and delta_data.get(key) is not None:
                return delta_data.get(key)
            if key in metadata and metadata.get(key) is not None:
                return metadata.get(key)
            return nested_meta.get(key)

        scope_val = _pick("scope_table_id")
        scope_table_id = None
        if scope_val is not None:
            try:
                scope_table_id = int(scope_val)
            except Exception:
                scope_table_id = None

        return {
            "scope_table_id": scope_table_id,
            "td_sig_v1": _pick("td_sig_v1"),
            "p_sig_v1": _pick("p_sig_v1"),
            "table_path": _pick("table_path"),
            "block_type": _pick("block_type"),
        }

    def _normalize_table_path(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return "/".join(str(v) for v in value)
        text = str(value).strip()
        return text or None

    def _extract_target_contract(
        self,
        delta_data: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        signature_meta = self._extract_signature_meta(delta_data, metadata)
        target_uid = (
            metadata.get("target_uid")
            or delta_data.get("target_uid")
        )

        contract: Dict[str, Any] = {}
        if target_uid:
            contract["target_uid"] = str(target_uid)

        meta_contract: Dict[str, Any] = {}
        for key in ("scope_table_id", "td_sig_v1", "p_sig_v1", "table_path", "block_type"):
            value = signature_meta.get(key)
            if value is not None and value != "":
                meta_contract[key] = value
        if meta_contract:
            contract["meta"] = meta_contract

        return contract

    def _build_runtime_target_contract(
        self,
        *,
        element_id: Optional[int],
        block_manager: Any,
    ) -> Dict[str, Any]:
        """Build a best-effort target contract from runtime registry/block info.

        This path is used when the model omits target_uid/meta fields. We keep
        scope validation by deriving metadata from the resolved runtime block.
        """
        if element_id is None:
            return {}
        if not block_manager:
            return {}

        try:
            block = block_manager.get_block(str(element_id))
        except Exception:
            block = None
        if not block:
            return {}

        contract: Dict[str, Any] = {}
        mapped_uid = self._id_to_target_uid.get(str(element_id))
        if mapped_uid:
            contract["target_uid"] = str(mapped_uid)

        meta: Dict[str, Any] = {}
        scope_table_id = getattr(block, "table_group_id", None)
        if scope_table_id is not None:
            meta["scope_table_id"] = scope_table_id
        td_sig = getattr(block, "td_sig", None)
        if td_sig:
            meta["td_sig_v1"] = str(td_sig)
        p_sig = getattr(block, "p_sig", None)
        if p_sig:
            meta["p_sig_v1"] = str(p_sig)
        table_path = getattr(block, "table_path", None)
        normalized_table_path = self._normalize_table_path(table_path)
        if normalized_table_path:
            meta["table_path"] = normalized_table_path
        block_type = (
            getattr(block, "block_type", None)
            or getattr(block, "segment_type", None)
        )
        if block_type:
            meta["block_type"] = str(block_type)

        if meta:
            contract["meta"] = meta
        return contract

    def _merge_target_contract_with_runtime(
        self,
        *,
        target_contract: Dict[str, Any],
        element_id: Optional[int],
        block_manager: Any,
    ) -> Dict[str, Any]:
        """Merge model-provided contract with runtime-derived fallback values."""
        merged: Dict[str, Any] = dict(target_contract or {})
        runtime_contract = self._build_runtime_target_contract(
            element_id=element_id,
            block_manager=block_manager,
        )

        if not merged.get("target_uid") and runtime_contract.get("target_uid"):
            merged["target_uid"] = runtime_contract.get("target_uid")

        merged_meta = merged.get("meta") if isinstance(merged.get("meta"), dict) else {}
        runtime_meta = runtime_contract.get("meta") if isinstance(runtime_contract.get("meta"), dict) else {}
        if runtime_meta:
            final_meta = dict(runtime_meta)
            final_meta.update({k: v for k, v in merged_meta.items() if v not in (None, "")})
            merged["meta"] = final_meta
        elif merged_meta:
            merged["meta"] = merged_meta

        return merged

    def _validate_target_contract(
        self,
        block_manager: Any,
        element_id: Optional[int],
        contract: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        if not contract:
            return True, None

        target_uid = contract.get("target_uid")
        contract_meta = contract.get("meta") if isinstance(contract.get("meta"), dict) else {}

        if target_uid:
            expected_id = self._target_uid_to_id.get(str(target_uid))
            if expected_id is None:
                return False, "unknown_target_uid"
            if element_id is None:
                return False, "missing_id_for_target_uid"
            if int(expected_id) != int(element_id):
                return False, "target_uid_mismatch"

            mapped_uid = self._id_to_target_uid.get(str(element_id))
            if mapped_uid and mapped_uid != str(target_uid):
                return False, "id_target_uid_mismatch"

        if element_id is None:
            return True, None
        if not block_manager:
            return True, None

        try:
            block = block_manager.get_block(str(element_id))
        except Exception:
            block = None
        if not block:
            return False, "target_block_not_found"

        expected_block_type = contract_meta.get("block_type")
        if expected_block_type:
            actual_block_type = str(
                getattr(block, "block_type", None)
                or getattr(block, "segment_type", None)
                or ""
            ).strip().lower()
            if actual_block_type != str(expected_block_type).strip().lower():
                return False, "block_type_mismatch"

        expected_scope = contract_meta.get("scope_table_id")
        if expected_scope is not None:
            actual_scope = getattr(block, "table_group_id", None)
            if actual_scope is None:
                try:
                    actual_scope = block_manager.get_table_group_id(str(element_id))
                except Exception:
                    actual_scope = None
            try:
                if int(actual_scope) != int(expected_scope):
                    return False, "scope_table_id_mismatch"
            except Exception:
                if actual_scope != expected_scope:
                    return False, "scope_table_id_mismatch"

        expected_td_sig = contract_meta.get("td_sig_v1")
        if expected_td_sig:
            actual_td_sig = getattr(block, "td_sig", None)
            if str(actual_td_sig) != str(expected_td_sig):
                return False, "td_sig_mismatch"

        expected_p_sig = contract_meta.get("p_sig_v1")
        if expected_p_sig:
            actual_p_sig = getattr(block, "p_sig", None)
            if str(actual_p_sig) != str(expected_p_sig):
                return False, "p_sig_mismatch"

        expected_table_path = self._normalize_table_path(contract_meta.get("table_path"))
        if expected_table_path:
            actual_table_path = self._normalize_table_path(getattr(block, "table_path", None))
            if actual_table_path != expected_table_path:
                return False, "table_path_mismatch"

        return True, None

    def _is_editable_for_method(
        self,
        method_type: Optional[str],
        block_type: Optional[str],
    ) -> bool:
        if not method_type:
            return True
        allowed_by_method = {
            "replace_paragraph": {"text"},
            "append_paragraph": {"text"},
            "delete_paragraph": {"text"},
            "replace_list": {"list"},
            "append_list": {"list"},
            "delete_list": {"list"},
            "replace_cell_content": {"td", "textbox"},  # v6.2: textbox 편집 지원
            "delete_cell_content": {"td"},
            "replace_table_row": {"td"},
            "append_table_row": {"td"},
            "delete_table_row": {"td"},
            "delete_textbox": {"textbox"},
            "replace_footnote": {"footnote_anchor", "footnote_content"},
            "delete_footnote": {"footnote_anchor", "footnote_content"},
            "replace": {"text", "list", "td", "textbox", "footnote_content"},  # v6.2: textbox 추가
            "delete": {"text", "list", "td", "footnote_content"},
            "apply_para_style": {"text", "list", "td", "textbox"},  # v6.2: textbox 추가
            "apply_charshape": {"text", "list", "td", "textbox"},
            "find_and_replace_in_paragraph": {"text", "list", "td", "textbox", "footnote_content"},  # v6.2: textbox 추가
        }
        allowed_types = allowed_by_method.get(method_type)
        if not allowed_types:
            return True
        if not block_type:
            return True
        return block_type in allowed_types

    def _cleanup_session_state(self) -> None:
        """Drop per-session caches to reduce memory growth."""
        try:
            reset_session_state()
            self._allowed_element_ids = None
            self._current_doc_view = None
            self._form_regions = []
            self._design_table_groups = set()
            self._target_uid_to_id = {}
            self._id_to_target_uid = {}
            self._document_graph_json = None
            self._form_detector = None
            store_runtime_segment_manager(None)

            if self._content_modifier is not None:
                try:
                    self._content_modifier.segment_registry = None
                except Exception:
                    pass
                if hasattr(self._content_modifier, "reset_runtime_state"):
                    self._content_modifier.reset_runtime_state()

            if self._cvd_extractor is not None and hasattr(self._cvd_extractor, "reset_cache"):
                self._cvd_extractor.reset_cache()

            try:
                import gc

                gc.collect()
            except Exception:
                pass
        except Exception as exc:
            print(f"[Python] session cleanup failed: {exc}", file=sys.stderr)

    def get_open_documents(self) -> dict:
        """열린 문서 목록 조회 (HWP, Word, Excel)"""
        documents = []

        # HWP 문서 조회
        try:
            hwp_docs = self._get_hwp_documents()
            documents.extend(hwp_docs)
        except Exception as e:
            print(f"[Python] HWP 문서 조회 오류: {e}", file=sys.stderr)

        # Word 문서 조회
        try:
            word_docs = self._get_word_documents()
            documents.extend(word_docs)
        except Exception as e:
            print(f"[Python] Word 문서 조회 오류: {e}", file=sys.stderr)

        # Excel 문서 조회
        try:
            excel_docs = self._get_excel_documents()
            documents.extend(excel_docs)
        except Exception as e:
            print(f"[Python] Excel 문서 조회 오류: {e}", file=sys.stderr)

        return {"success": True, "documents": documents}

    def _get_hwp_documents(self) -> list:
        """HWP 문서 목록 조회 (Win32 API 메타데이터 포함)"""
        documents = []
        import os

        try:
            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                return documents

            # 좀비 프로세스 필터링: 창이 실제로 존재하는지 확인
            try:
                window_count = hwp.XHwpWindows.Count
                if window_count == 0:
                    print(f"[Python] 좀비 HWP 프로세스 감지 (창 없음), 무시", file=sys.stderr)
                    return documents

                # 창 개수는 있지만 실제 윈도우 핸들이 유효한지 확인
                try:
                    from ctypes import windll
                    first_window = hwp.XHwpWindows.Item(0)
                    if first_window:
                        hwnd = first_window.WindowHandle
                        # Win32 API IsWindow로 창이 유효한지 확인
                        is_valid = windll.user32.IsWindow(hwnd)
                        if not is_valid:
                            print(f"[Python] 좀비 HWP 프로세스 감지 (유효하지 않은 창 핸들), 무시", file=sys.stderr)
                            return documents
                except Exception:
                    # WindowHandle 접근 실패 시에도 좀비로 간주
                    print(f"[Python] 좀비 HWP 프로세스 감지 (창 핸들 접근 실패), 무시", file=sys.stderr)
                    return documents
            except Exception as e:
                print(f"[Python] XHwpWindows 확인 실패: {e}, 계속 진행", file=sys.stderr)

            # Win32 API로 창 메타데이터 수집
            try:
                collector = DocumentMetadataCollector()
                window_info_list = collector.collect_hwp_window_info()
                metadata_list = collector.build_document_metadata(window_info_list)
            except Exception as e:
                print(f"[Python] 창 메타데이터 수집 실패: {e}", file=sys.stderr)
                metadata_list = []

            # 현재 바인딩된 HWND 가져오기 (UI에서 선택 표시용)
            bound_hwnd = retrieve_runtime_target_window_handle() or self._window_handle

            # XHwpDocuments 컬렉션으로 열린 문서 목록 가져오기
            try:
                xdocs = hwp.XHwpDocuments
                doc_count = xdocs.Count

                for i in range(doc_count):
                    doc = xdocs.Item(i)
                    if doc:
                        # FullName에서 전체 경로 가져오기
                        try:
                            path = doc.FullName if hasattr(doc, 'FullName') else ''
                        except:
                            path = ''

                        # 저장되지 않은 문서는 포함 (빈 문서도 표시)
                        name = os.path.basename(path) if path else "새 문서"

                        doc_id = None
                        try:
                            doc_id = int(doc.DocumentID)
                        except Exception:
                            doc_id = None

                        # 메타데이터와 매칭하여 HWND/PID 추가
                        hwnd = None
                        pid = None
                        for meta in metadata_list:
                            if meta['filename'] == name:
                                hwnd = meta['hwnd']
                                pid = meta['pid']
                                break

                        doc_entry = {
                            "id": f"hwp-{doc_id}" if doc_id is not None else f"hwp-{i}",
                            "name": name,
                            "path": path,
                            "type": "hwp",
                            "index": i,
                        }
                        if doc_id is not None:
                            doc_entry["documentId"] = doc_id
                        if hwnd is not None:
                            doc_entry["hwnd"] = hwnd
                            # 현재 바인딩된 HWND와 일치하면 isBound=true
                            if bound_hwnd and hwnd == bound_hwnd:
                                doc_entry["isBound"] = True
                        if pid is not None:
                            doc_entry["pid"] = pid

                        documents.append(doc_entry)
            except Exception as e:
                print(f"[Python] XHwpDocuments 접근 실패: {e}", file=sys.stderr)

        except Exception as e:
            print(f"[Python] HWP ROT 접근 실패: {e}", file=sys.stderr)

        return documents

    def _get_word_documents(self) -> list:
        """win32com으로 열린 Word 문서 목록 조회"""
        documents = []

        try:
            import win32com.client

            word = win32com.client.GetActiveObject("Word.Application")
            for i, doc in enumerate(word.Documents):
                name = doc.Name
                path = doc.FullName if doc.Path else ""

                documents.append({
                    "id": f"word-{i}",
                    "name": name,
                    "path": path,
                    "type": "word",
                    "index": i,
                })

        except Exception:
            # Word가 실행 중이 아님
            pass

        return documents

    def _get_excel_documents(self) -> list:
        """win32com으로 열린 Excel 문서 목록 조회"""
        documents = []

        try:
            import win32com.client

            excel = win32com.client.GetActiveObject("Excel.Application")
            for i, wb in enumerate(excel.Workbooks):
                name = wb.Name
                path = wb.FullName if wb.Path else ""

                documents.append({
                    "id": f"excel-{i}",
                    "name": name,
                    "path": path,
                    "type": "excel",
                    "index": i,
                })

        except Exception:
            # Excel이 실행 중이 아님
            pass

        return documents

    def select_document(self, doc_type: str, index: int = None, doc_id: str = None) -> dict:
        """특정 문서 선택 (편집 대상으로 지정)

        Args:
            doc_type: 문서 타입 (hwp, word, excel)
            index: 문서 인덱스 (기존 방식)
            doc_id: 문서 ID (hwp-{DocumentID}, hwp-win-{HWND} 형식 지원)
        """
        try:
            # doc_id로 선택 (우선순위)
            if doc_id:
                # hwp-win-{HWND} 형식 파싱
                if doc_id.startswith("hwp-win-"):
                    hwnd_str = doc_id.replace("hwp-win-", "")
                    try:
                        hwnd = int(hwnd_str)
                        return self._select_hwp_by_hwnd(hwnd)
                    except ValueError:
                        return {"success": False, "error": f"Invalid HWND format: {doc_id}"}

                # hwp-{DocumentID} 형식 파싱
                elif doc_id.startswith("hwp-"):
                    doc_id_str = doc_id.replace("hwp-", "")
                    try:
                        document_id = int(doc_id_str)
                        return self._select_hwp_by_document_id(document_id)
                    except ValueError:
                        # 인덱스로 시도
                        try:
                            idx = int(doc_id_str)
                            return self._select_hwp_document(idx)
                        except:
                            return {"success": False, "error": f"Invalid document ID: {doc_id}"}

            # 기존 인덱스 방식
            if index is not None:
                if doc_type == "hwp":
                    return self._select_hwp_document(index)
                elif doc_type == "word":
                    return self._select_word_document(index)
                elif doc_type == "excel":
                    return self._select_excel_document(index)

            return {"success": False, "error": "Either index or doc_id must be provided"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _bring_window_to_front(self, keywords: list) -> bool:
        """특정 키워드가 포함된 창을 앞으로 가져오기"""
        import win32gui
        import win32con
        import win32process
        import win32api
        import ctypes

        def find_window(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                class_name = win32gui.GetClassName(hwnd)
                for kw in keywords:
                    if kw.lower() in title.lower() or kw.lower() in class_name.lower():
                        windows.append((hwnd, title))
                        break
            return True

        windows = []
        win32gui.EnumWindows(find_window, windows)

        if not windows:
            return False

        hwnd = windows[0][0]
        try:
            # 최소화된 경우 복원
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # AttachThreadInput 트릭으로 SetForegroundWindow 제한 우회
            current_thread = win32api.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

            if current_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)

            # 창을 앞으로 가져오기
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

            if current_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)

            return True
        except Exception as e:
            print(f"[Python] 창 활성화 실패: {e}", file=sys.stderr)
            # 최후의 수단: Alt 키 시뮬레이션
            try:
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
                win32gui.SetForegroundWindow(hwnd)
                return True
            except:
                pass
        return False

    def _select_hwp_by_document_id(self, document_id: int) -> dict:
        """DocumentID로 HWP 문서 선택"""
        try:
            import pythoncom
            pythoncom.CoInitialize()

            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                return {"success": False, "error": "HWP 프로세스를 찾을 수 없습니다"}

            xdocs = hwp.XHwpDocuments
            for i in range(xdocs.Count):
                doc = xdocs.Item(i)
                if doc and hasattr(doc, 'DocumentID'):
                    try:
                        if int(doc.DocumentID) == document_id:
                            doc.SetActive_XHwpDocument()

                            # HWND/PID 업데이트 (WindowHandle 접근 없음!)
                            self._update_active_document_handles(hwp)

                            path = doc.FullName if hasattr(doc, 'FullName') else None
                            self._current_file = path
                            self._bring_window_to_front(['Hwp', '한글'])

                            # 문서 초기 설정 적용 (Track Changes 색상 등)
                            self._apply_initial_document_settings(hwp)

                            return {"success": True, "documentId": document_id, "path": path}
                    except Exception:
                        continue

            return {"success": False, "error": f"DocumentID {document_id}를 찾을 수 없습니다"}

        except Exception as e:
            print(f"[Python] DocumentID 선택 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def _select_hwp_by_hwnd(self, hwnd: int) -> dict:
        """HWND로 HWP 문서 선택"""
        try:
            import pythoncom
            pythoncom.CoInitialize()

            hwp = self._get_hwp_from_rot(None, hwnd)

            if hwp is None:
                return {"success": False, "error": f"HWND {hwnd}에 해당하는 HWP 프로세스를 찾을 수 없습니다"}

            # DocumentMetadataCollector로 HWND 매칭 (WindowHandle 접근 없음!)
            from engine.connection.document_collector import DocumentMetadataCollector

            collector = DocumentMetadataCollector()
            window_list = collector.collect_hwp_window_info()
            metadata_list = collector.build_document_metadata(window_list)

            # COM 문서와 창 정보 매칭
            xdocs = hwp.XHwpDocuments
            matched_documents = collector.match_com_documents(hwp, metadata_list)

            # HWND로 문서 찾기
            for doc_info in matched_documents:
                if doc_info.get('hwnd') == hwnd:
                    doc_index = doc_info['com_index']
                    doc = xdocs.Item(doc_index)
                    if doc:
                        doc.SetActive_XHwpDocument()

                        # HWND/PID 업데이트
                        import ctypes
                        from ctypes import wintypes
                        user32 = ctypes.windll.user32
                        pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                        store_runtime_target_window_handle(hwnd)
                        store_runtime_target_process_id(pid.value)
                        self._process_identifier = pid.value
                        self._window_handle = hwnd

                        path = doc.FullName if hasattr(doc, 'FullName') else None
                        self._current_file = path
                        self._bring_window_to_front(['Hwp', '한글'])

                        # 문서 초기 설정 적용 (Track Changes 색상 등)
                        self._apply_initial_document_settings(hwp)

                        return {"success": True, "hwnd": hwnd, "path": path}

            return {"success": False, "error": f"HWND {hwnd}의 문서를 찾을 수 없습니다"}

        except Exception as e:
            print(f"[Python] HWND 선택 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def _update_active_document_handles(self, hwp) -> None:
        """활성화된 문서의 HWND/PID 업데이트

        DocumentMetadataCollector를 사용하여 WindowHandle 접근을 회피합니다.
        """
        try:
            from engine.connection.document_collector import DocumentMetadataCollector

            # Win32 API로 HWP 창 정보 수집 (WindowHandle 접근 없음!)
            collector = DocumentMetadataCollector()
            window_list = collector.collect_hwp_window_info()

            if not window_list:
                print(f"[Python] ⚠️ HWP 창을 찾을 수 없음", file=sys.stderr)
                return

            # 활성 문서 경로 가져오기
            try:
                active_doc = hwp.XHwpDocuments.Active_XHwpDocument
                if active_doc and hasattr(active_doc, 'FullName'):
                    active_path = active_doc.FullName
                    active_filename = os.path.basename(active_path) if active_path else None

                    # 파일명으로 창 매칭
                    if active_filename:
                        for window_info in window_list:
                            parsed_name = collector.parse_document_name(window_info['title'])
                            if parsed_name == active_filename:
                                actual_pid = window_info['pid']
                                actual_hwnd = window_info['hwnd']

                                print(f"[Python] ✅ 활성 문서 HWND 업데이트: {actual_hwnd} (파일: {active_filename})", file=sys.stderr)

                                store_runtime_target_process_id(actual_pid)
                                store_runtime_target_window_handle(actual_hwnd)
                                self._process_identifier = actual_pid
                                self._window_handle = actual_hwnd
                                return
            except Exception as e:
                print(f"[Python] ⚠️ 활성 문서 경로 조회 실패: {e}", file=sys.stderr)

            # 매칭 실패 시 첫 번째 창 사용
            first_window = window_list[0]
            store_runtime_target_process_id(first_window['pid'])
            store_runtime_target_window_handle(first_window['hwnd'])
            self._process_identifier = first_window['pid']
            self._window_handle = first_window['hwnd']
            print(f"[Python] ⚠️ 파일명 매칭 실패, 첫 번째 창 사용: HWND={first_window['hwnd']}", file=sys.stderr)

        except Exception as e:
            print(f"[Python] ⚠️ 활성 문서 HWND/PID 업데이트 실패: {e}", file=sys.stderr)

    def _select_hwp_document(self, index: int) -> dict:
        """HWP 문서 선택 및 창 활성화"""
        try:
            import pythoncom
            pythoncom.CoInitialize()

            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle

            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                return {"success": False, "error": "HWP 프로세스를 찾을 수 없습니다"}

            xdocs = hwp.XHwpDocuments

            if index >= xdocs.Count:
                return {"success": False, "error": f"문서 인덱스 {index}가 범위를 벗어남"}

            doc = xdocs.Item(index)
            if doc:
                doc.SetActive_XHwpDocument()
                self._current_file = doc.FullName if hasattr(doc, 'FullName') else None

                # HWND/PID 업데이트
                self._update_active_document_handles(hwp)

                # 창 앞으로 가져오기
                if self._window_handle:
                    import ctypes
                    user32 = ctypes.windll.user32
                    user32.SetForegroundWindow(self._window_handle)
                    user32.ShowWindow(self._window_handle, 9)

                # 문서 초기 설정 적용 (Track Changes 색상 등)
                self._apply_initial_document_settings(hwp)

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"success": False, "error": str(e)}

    def _select_word_document(self, index: int) -> dict:
        """Word 문서 선택 및 창 활성화"""
        import win32com.client

        try:
            word = win32com.client.GetActiveObject("Word.Application")

            # 1-based index
            doc = word.Documents.Item(index + 1)
            if doc:
                doc.Activate()
                self._current_file = doc.FullName

            # Word 창을 앞으로
            word.Visible = True
            word.Activate()
            self._bring_window_to_front(['Word', 'WINWORD'])

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _select_excel_document(self, index: int) -> dict:
        """Excel 문서 선택 및 창 활성화"""
        import win32com.client

        try:
            excel = win32com.client.GetActiveObject("Excel.Application")

            # 1-based index
            wb = excel.Workbooks.Item(index + 1)
            if wb:
                wb.Activate()
                self._current_file = wb.FullName

            # Excel 창을 앞으로
            excel.Visible = True
            self._bring_window_to_front(['Excel', 'XLMAIN'])

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_document(self, file_path: str) -> dict:
        """문서 열기"""
        try:
            adapter = self._ensure_connector()

            if not adapter.open_file(file_path):
                return {"success": False, "error": "파일 열기 실패"}

            self._current_file = file_path
            return {"success": True, "file": file_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extract_html(self) -> dict:
        """현재 문서에서 DocumentView 생성"""
        try:
            connector = self._ensure_connector()

            # DocumentViewGenerator 초기화
            if self._view_generator is None:
                self._view_generator = DocumentViewGenerator(connector)

            # DocumentView 생성
            doc_view = self._view_generator.generate_view()
            if not doc_view:
                return {"success": False, "error": "DocumentView 생성 실패"}

            # HTML 형식 마크다운 추출
            html_content = doc_view.html

            return {
                "success": True,
                "html": html_content,
                "element_count": len(doc_view.elements) if doc_view.elements else 0
            }

        except Exception as e:
            return {"success": False, "error": str(e), "trace": traceback.format_exc()}

    def extract_cvd(self, page_range: Optional[dict] = None) -> dict:
        """이전 버전 호환: CVD 추출"""
        try:
            connector = self._ensure_connector()
            hwp = connector.hwp

            if page_range is None:
                page_range = {}

            start_page = page_range.get("start_page") or page_range.get("start") or page_range.get("startPage")
            end_page = page_range.get("end_page") or page_range.get("end") or page_range.get("endPage")

            current_page = None
            try:
                active_doc = hwp.XHwpDocuments.Active_XHwpDocument
                doc_info = active_doc.XHwpDocumentInfo if active_doc else None
                page_index = getattr(doc_info, "CurrentPage", None)
                if isinstance(page_index, int):
                    current_page = page_index + 1
            except Exception:
                current_page = None

            if not current_page:
                try:
                    current_page = hwp.current_page
                except Exception:
                    current_page = None

            if start_page is None:
                start_page = current_page or 1
            if end_page is None:
                end_page = start_page

            extractor = self._ensure_cvd_extractor()
            result = extractor.extract_cvd({
                "start": int(start_page),
                "end": int(end_page),
                "current_page": current_page,
            })

            if not result:
                return {"success": False, "error": "CVD 추출 실패"}

            cvd_text, id_to_pos = result

            try:
                block_manager = DocumentBlockManager((cvd_text, id_to_pos))
                store_runtime_segment_manager(block_manager)
            except Exception:
                pass

            return {
                "success": True,
                "cvd": cvd_text,
                "id_to_pos": id_to_pos,
            }

        except Exception as e:
            return {"success": False, "error": str(e), "trace": traceback.format_exc()}

    def _extract_hwpml_saveblock_for_page_range(
        self,
        extractor: Optional[CVDExtractor],
        start_page: int,
        end_page: int,
    ) -> str:
        """페이지 범위 선택 후 HWPML(saveblock) 추출."""
        if extractor is None:
            return ""

        hwp = getattr(extractor, "hwp", None)
        if hwp is None:
            return ""

        start_pos: Optional[Tuple[int, int, int]] = None
        try:
            start_pos = extractor._move_to_page_start(hwp, int(start_page))  # noqa: SLF001
            if not start_pos:
                return ""

            try:
                hwp.set_pos(*start_pos)
            except Exception:
                pass

            extractor._page_down(hwp, int(start_page), int(end_page))  # noqa: SLF001

            try:
                return hwp.GetTextFile("HWPML2X", option="saveblock") or ""
            except TypeError:
                return hwp.GetTextFile("HWPML2X", "saveblock") or ""
        except Exception as exc:
            print(f"[Python] saveblock HWPML 추출 실패: {exc}", file=sys.stderr)
            return ""
        finally:
            try:
                hwp.Cancel()
            except Exception:
                try:
                    hwp.HAction.Run("Cancel")
                except Exception:
                    pass
            if start_pos:
                try:
                    hwp.set_pos(*start_pos)
                except Exception:
                    pass

    def set_target_binding(self, pid: Optional[int], hwnd: Optional[int]) -> dict:
        """이전 버전 호환: PID/HWND 바인딩 + 모니터 정보 반환"""
        if not pid or not hwnd:
            return {"success": False, "error": "PID and HWND required"}

        self._process_identifier = pid
        self._window_handle = hwnd

        store_runtime_target_process_id(pid)
        store_runtime_target_window_handle(hwnd)
        bind_runtime_target_window(pid=pid, handle=hwnd)

        if self._connector is not None:
            self._connector.disconnect()
            self._connector = None

        # 바인딩 변경 시 종속 객체 초기화 (stale COM 객체 방지)
        self._cvd_extractor = None
        self._content_modifier = None
        self._form_detector = None

        # 자동 바인딩 시 문서 초기 설정 적용 (Track Changes 색상 등)
        try:
            import pythoncom
            pythoncom.CoInitialize()
            hwp = self._get_hwp_from_rot(pid, hwnd)
            if hwp:
                self._apply_initial_document_settings(hwp)
        except Exception:
            pass

        # HWP 창의 모니터 정보 가져오기
        monitor_info = self._get_hwp_window_monitor_info(hwnd)

        return {
            "success": True,
            "pid": pid,
            "hwnd": hwnd,
            "monitorInfo": monitor_info
        }

    def _get_hwp_window_monitor_info(self, hwnd: int) -> dict:
        """
        HWP 창의 모니터 정보 가져오기 (멀티 모니터 지원)

        Returns:
            {
                "windowRect": {"x": int, "y": int, "width": int, "height": int},
                "monitorWorkArea": {"x": int, "y": int, "width": int, "height": int},
                "monitorRect": {"x": int, "y": int, "width": int, "height": int},
                "dpiScale": float,
                "isPrimary": bool
            }
        """
        try:
            import win32gui
            import win32api
            import win32con
            import ctypes
            from ctypes import wintypes

            # 1. 창 위치 및 크기 가져오기
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                window_rect = {
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top
                }
            except Exception:
                window_rect = {"x": 0, "y": 0, "width": 0, "height": 0}

            # 2. 창이 속한 모니터 핸들 가져오기
            user32 = ctypes.windll.user32
            MONITOR_DEFAULTTONEAREST = 2
            hmonitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

            # 3. 모니터 정보 가져오기
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)
                ]

            monitor_info = MONITORINFO()
            monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info))

            # 작업 영역 (태스크바 제외)
            work_area = {
                "x": monitor_info.rcWork.left,
                "y": monitor_info.rcWork.top,
                "width": monitor_info.rcWork.right - monitor_info.rcWork.left,
                "height": monitor_info.rcWork.bottom - monitor_info.rcWork.top
            }

            # 전체 영역
            monitor_rect = {
                "x": monitor_info.rcMonitor.left,
                "y": monitor_info.rcMonitor.top,
                "width": monitor_info.rcMonitor.right - monitor_info.rcMonitor.left,
                "height": monitor_info.rcMonitor.bottom - monitor_info.rcMonitor.top
            }

            # 주 모니터 여부
            MONITORINFOF_PRIMARY = 1
            is_primary = bool(monitor_info.dwFlags & MONITORINFOF_PRIMARY)

            # 4. DPI 스케일링 가져오기
            dpi_scale = 1.0
            try:
                shcore = ctypes.windll.shcore
                dpi_x = ctypes.c_uint()
                dpi_y = ctypes.c_uint()
                MDT_EFFECTIVE_DPI = 0
                if shcore.GetDpiForMonitor(hmonitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                    if dpi_x.value:
                        dpi_scale = dpi_x.value / 96.0
            except Exception:
                # Fallback: GetDpiForWindow (Windows 10 1607+)
                try:
                    get_dpi_for_window = getattr(user32, "GetDpiForWindow", None)
                    if get_dpi_for_window:
                        get_dpi_for_window.restype = ctypes.c_uint
                        dpi = get_dpi_for_window(hwnd)
                        if dpi:
                            dpi_scale = dpi / 96.0
                except Exception:
                    pass

            return {
                "windowRect": window_rect,
                "monitorWorkArea": work_area,
                "monitorRect": monitor_rect,
                "dpiScale": dpi_scale,
                "isPrimary": is_primary
            }

        except Exception as e:
            self.log(f"[ERROR] Failed to get monitor info: {e}")
            # 실패 시 기본값 반환
            return {
                "windowRect": {"x": 0, "y": 0, "width": 0, "height": 0},
                "monitorWorkArea": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "monitorRect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "dpiScale": 1.0,
                "isPrimary": True
            }

    def arrange_hwp_window(self, bounds: dict) -> dict:
        """
        HWP 창 배치 (v2: 모니터 정보 기반 자동 배치)

        Args:
            bounds: {
                "mode": "right_side",  # 자동 배치 모드
                "leftWidthPercent": 40  # 왼쪽 영역 비율 (Inserty 창)
            }
            또는 레거시 방식: {"x": ..., "y": ..., "width": ..., "height": ...}
        """
        try:
            import win32gui
            import win32con
            import ctypes
            from ctypes import wintypes

            hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            if not hwnd:
                return {"success": False, "error": "HWP window handle not set"}
            if not win32gui.IsWindow(hwnd):
                return {"success": False, "error": "HWP window not available"}

            # 루트 창 가져오기
            try:
                root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
                if root_hwnd:
                    hwnd = root_hwnd
            except Exception:
                pass

            # 새로운 방식: mode='right_side'
            mode = bounds.get("mode")
            if mode == "right_side":
                # HWP 창의 모니터 정보 가져오기
                user32 = ctypes.windll.user32
                MONITOR_DEFAULTTONEAREST = 2
                hmonitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

                # 모니터 정보 구조체
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)
                    ]

                monitor_info = MONITORINFO()
                monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
                user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info))

                # 작업 영역 (태스크바 제외)
                work_left = monitor_info.rcWork.left
                work_top = monitor_info.rcWork.top
                work_width = monitor_info.rcWork.right - monitor_info.rcWork.left
                work_height = monitor_info.rcWork.bottom - monitor_info.rcWork.top

                # Inserty 창의 실제 너비 사용 (Electron에서 전달)
                inserty_width = bounds.get("insertyWidth") or bounds.get("malloWidth")
                if inserty_width:
                    # Electron이 실제 설정한 Inserty 창 너비 사용
                    left_width = inserty_width
                else:
                    # 레거시: 비율로 계산
                    left_percent = bounds.get("leftWidthPercent", 40)
                    left_width = int(work_width * left_percent / 100)

                # HWP 창은 오른쪽 영역에 배치
                x = work_left + left_width
                y = work_top
                width = work_width - left_width
                height = work_height

                # 디버그 로그 (stderr로 출력하여 JSON 파싱 에러 방지)
                import sys
                print(f"[Arrange] Monitor work area: ({work_left}, {work_top}, {work_width}x{work_height})", file=sys.stderr, flush=True)
                print(f"[Arrange] Inserty width: {left_width}", file=sys.stderr, flush=True)
                print(f"[Arrange] HWP window position: ({x}, {y}, {width}x{height})", file=sys.stderr, flush=True)

            # 레거시 방식: 직접 좌표 지정
            else:
                def _get_window_scale(target_hwnd: int) -> float:
                    try:
                        user32 = ctypes.windll.user32
                        get_dpi_for_window = getattr(user32, "GetDpiForWindow", None)
                        if get_dpi_for_window:
                            get_dpi_for_window.restype = ctypes.c_uint
                            dpi = get_dpi_for_window(target_hwnd)
                            if dpi:
                                return dpi / 96.0
                        monitor = user32.MonitorFromWindow(target_hwnd, 2)
                        shcore = ctypes.windll.shcore
                        dpi_x = ctypes.c_uint()
                        dpi_y = ctypes.c_uint()
                        if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                            if dpi_x.value:
                                return dpi_x.value / 96.0
                    except Exception:
                        pass
                    return 1.0

                unit = bounds.get("unit")
                scale = _get_window_scale(hwnd) if unit == "dip" else 1.0
                if scale <= 0:
                    scale = 1.0

                x = int(round(bounds.get("x", 0) * scale))
                y = int(round(bounds.get("y", 0) * scale))
                width = int(round(bounds.get("width", 0) * scale))
                height = int(round(bounds.get("height", 0) * scale))

                if width <= 0 or height <= 0:
                    return {"success": False, "error": "Invalid bounds"}

            # 최소화 상태면 복원 (먼저 복원해야 SetWindowPos가 제대로 작동함)
            import sys
            if win32gui.IsIconic(hwnd):
                print(f"[Arrange] Window is minimized, restoring...", file=sys.stderr, flush=True)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                # 복원 후 약간의 대기 (창이 완전히 복원되도록)
                import time
                time.sleep(0.1)

            # 창 위치 및 크기 설정
            print(f"[Arrange] SetWindowPos: x={x}, y={y}, width={width}, height={height}", file=sys.stderr, flush=True)

            # SWP_FRAMECHANGED 플래그 추가로 창 프레임 강제 재계산
            result = win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,  # 창을 맨 앞으로 (None 대신)
                x,
                y,
                width,
                height,
                win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED,  # FRAMECHANGED 추가
            )
            print(f"[Arrange] SetWindowPos result: {result}", file=sys.stderr, flush=True)

            # 강제 이동 (일부 창에서 SetWindowPos가 실패할 수 있음)
            try:
                win32gui.MoveWindow(hwnd, x, y, width, height, True)
                print(f"[Arrange] MoveWindow completed", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Arrange] MoveWindow failed (non-critical): {e}", file=sys.stderr, flush=True)

            # 창 활성화 (포커스)
            try:
                win32gui.SetForegroundWindow(hwnd)
                print(f"[Arrange] Window activated", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Arrange] SetForegroundWindow failed (non-critical): {e}", file=sys.stderr, flush=True)

            return {
                "success": True,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
            }

        except Exception as e:
            import traceback
            import sys
            print(f"[ERROR] arrange_hwp_window failed: {e}", file=sys.stderr, flush=True)
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            return {"success": False, "error": str(e)}

    def apply_commands(self, commands: list, mappings_data: dict = None) -> dict:
        """편집 명령 적용 (스트리밍 방식으로 대체됨)"""
        # NOTE: 실제 편집은 streaming_client의 on_command 콜백에서 실시간으로 수행됨
        # 이 메서드는 레거시 호환성을 위해 유지
        return {
            "success": True,
            "message": "스트리밍 방식으로 실시간 편집이 수행됩니다"
        }

    def save_document(self) -> dict:
        """문서 저장"""
        try:
            connector = self._ensure_connector()
            connector.save()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def close_document(self) -> dict:
        """문서 닫기"""
        try:
            if self._connector:
                self._connector.disconnect()
                self._connector = None
                self._current_file = None
                self._view_generator = None
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_current_page_info(self) -> dict:
        """현재 커서 위치의 페이지 정보 조회"""
        try:
            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            active_doc = hwp.XHwpDocuments.Active_XHwpDocument
            current_page = active_doc.XHwpDocumentInfo.CurrentPage + 1
            total_pages = hwp.PageCount

            active_path = active_doc.FullName if hasattr(active_doc, "FullName") else ""
            active_name = os.path.basename(active_path) if active_path else "새 문서"
            active_document_id = None
            try:
                active_document_id = int(active_doc.DocumentID)
            except Exception:
                active_document_id = None

            MAX_PAGES = 5
            start_page = current_page
            end_page = min(current_page + MAX_PAGES - 1, total_pages)

            return {
                "success": True,
                "currentPage": current_page,
                "totalPages": total_pages,
                "startPage": start_page,
                "endPage": end_page,
                "activeDocument": {
                    "name": active_name,
                    "path": active_path or "",
                    "type": "hwp",
                    "documentId": active_document_id,
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def get_selection_info(self) -> dict:
        """현재 선택 영역 정보 조회 (표 셀 선택 또는 텍스트 드래그)"""
        try:
            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            # hwp_window_monitor의 get_selection_info_from_hwp() 사용
            selection_info = get_selection_info_from_hwp(hwp)

            if selection_info:
                return {
                    "success": True,
                    **selection_info
                }
            else:
                return {
                    "success": False,
                    "error": "선택 영역 정보 추출 실패"
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _send_progress(self, event_type: str, data: dict):
        """진행 상황 이벤트를 stdout으로 전송 (Electron에서 수신)"""
        progress_event = {
            "type": "progress",
            "event": event_type,
            "data": data
        }
        print(json.dumps(progress_event, ensure_ascii=False))
        sys.stdout.flush()




    def chat(
        self,
        prompt: str,
        doc_type: str = None,
        doc_index: int = None,
        reference_content: str = None,
        reference_file_name: str = None,
        start_page: int = None,
        end_page: int = None,
        openai_api_key: str = None,
    ) -> dict:
        """
        채팅 기반 문서 편집 (스트리밍 + 실시간 편집)

        Args:
            prompt: 사용자 편집 요청
            doc_type: 문서 타입 (hwp, word, excel) - None이면 현재 활성 문서
            doc_index: 문서 인덱스 - None이면 현재 활성 문서
            reference_content: 참조 자료 내용 (txt/excel 파일 - 서버 분리 대비)
            reference_file_name: 참조 자료 파일명
            start_page: 시작 페이지 (None이면 현재 커서 위치)
            end_page: 끝 페이지 (None이면 start_page + 4)
            openai_api_key: OpenAI API 키 (필수)

        Returns:
            {
                "success": bool,
                "message": str,  # AI 응답 메시지
                "edits": int,    # 편집 횟수
                "error": str     # 에러 시
            }
        """
        try:
            import pythoncom
            pythoncom.CoInitialize()

            # 진행 상황 알림: 시작
            self._send_progress("start", {"stage": "initializing"})
            if not openai_api_key:
                return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

            # 1. 문서 타입 확인 (현재는 HWP만 지원)
            if doc_type and doc_type != "hwp":
                return {
                    "success": False,
                    "error": f"현재 HWP 문서만 지원합니다. (요청: {doc_type})"
                }

            # 2. HWP 인스턴스 연결
            preferred_pid = retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                return {"success": False, "error": "HWP 프로세스를 찾을 수 없습니다"}

            # 3. 활성 문서 확인
            xdocs = hwp.XHwpDocuments
            if xdocs.Count == 0:
                return {
                    "success": False,
                    "error": "열린 HWP 문서가 없습니다."
                }

            # 특정 문서 선택 (인덱스 지정된 경우)
            doc_index_value = self._coerce_int(doc_index)
            if doc_index_value is not None:
                doc_count = self._coerce_int(getattr(xdocs, "Count", None))
                if doc_count is None:
                    try:
                        doc_count = int(xdocs.Count)
                    except Exception:
                        doc_count = 0
                if doc_index_value >= doc_count:
                    return {
                        "success": False,
                        "error": f"문서 인덱스 {doc_index_value}가 범위를 벗어남 (총 {doc_count}개)"
                    }
                doc = xdocs.Item(doc_index_value)
                doc.SetActive_XHwpDocument()

            # 3.5. 페이지 범위 결정
            current_page = None
            try:
                active_doc = hwp.XHwpDocuments.Active_XHwpDocument
                doc_info = active_doc.XHwpDocumentInfo if active_doc else None
                page_index = getattr(doc_info, "CurrentPage", None)
                if isinstance(page_index, int):
                    current_page = page_index + 1
            except Exception:
                current_page = None

            current_page = self._coerce_int(current_page)
            if not current_page:
                current_page = self._coerce_int(hwp.current_page)

            total_pages = self._coerce_int(hwp.PageCount) or 1
            MAX_PAGES = 5
            start_page_value = self._coerce_int(start_page)
            end_page_value = self._coerce_int(end_page)

            # 사용자 지정 페이지 범위가 있으면 사용, 없으면 현재 커서 위치 기준
            if start_page_value is not None:
                # 사용자 지정 시작 페이지
                actual_start = max(1, min(start_page_value, total_pages))
                if end_page_value is not None:
                    # 사용자 지정 끝 페이지
                    actual_end = max(actual_start, min(end_page_value, total_pages))
                else:
                    actual_end = min(actual_start + MAX_PAGES - 1, total_pages)
            else:
                actual_start = current_page or 1
                actual_start = max(1, min(actual_start, total_pages))
                actual_end = min(actual_start + MAX_PAGES - 1, total_pages)

            print(f"[Python] 페이지 범위: {actual_start}~{actual_end} (전체 {total_pages}페이지, 현재 커서 {current_page}페이지)", file=sys.stderr)
            self._send_progress("stage", {"stage": "page_info", "message": f"페이지 {actual_start}~{actual_end} 읽는 중..."})

            # 4. DocumentView 생성
            self._send_progress("stage", {"stage": "reading", "message": "문서 읽는 중..."})
            print(f"[Python] DocumentView 생성 중...", file=sys.stderr)

            # HwpConnector 인스턴스 생성 (SafeHwp 패턴)
            connector = self._ensure_connector()

            # DocumentViewGenerator로 문서 뷰 생성
            if self._view_generator is None:
                self._view_generator = DocumentViewGenerator(connector)

            doc_view = self._view_generator.generate_view()
            if not doc_view:
                return {
                    "success": False,
                    "error": "DocumentView 생성 실패"
                }

            print(f"[Python] DocumentView 생성 완료: {len(doc_view.elements)}개 요소", file=sys.stderr)

            # 초기 커서 위치 저장 (편집 완료 후 복원용 - 흰 화면 방지)
            initial_cursor_pos = None
            if connector.check_alive():
                try:
                    initial_cursor_pos = connector.hwp.get_pos()
                    print(f"[Python] 초기 커서 위치 저장: {initial_cursor_pos}", file=sys.stderr)
                except Exception as e:
                    print(f"[Python] 커서 위치 저장 실패 (무시): {e}", file=sys.stderr)
            else:
                print("[Python] HWP 연결 끊김, 커서 위치 저장 건너뜀", file=sys.stderr)

            # 5. DocumentBlockManager 초기화
            block_manager = DocumentBlockManager(doc_view.position_map)
            print(f"[Python] DocumentBlockManager 초기화: {len(doc_view.position_map)}개 블록", file=sys.stderr)

            # 6. 각 element에 페이지 정보 매핑
            self._send_progress("stage", {"stage": "mapping_pages", "message": "페이지 매핑 중..."})
            self._map_element_pages(connector, doc_view)

            # 7. 페이지 범위 내 element만 필터링
            filtered_elements, allowed_element_ids = self._filter_elements_by_page(
                doc_view, actual_start, actual_end
            )

            print(f"[Python] 원본 요소 수: {len(doc_view.elements)}, 필터링 후: {len(allowed_element_ids)}개 요소 허용", file=sys.stderr)

            # 편집 허용 element ID 저장 (편집 시 범위 체크용)
            self._allowed_element_ids = allowed_element_ids

            # HTML 마크다운 생성 (필터링된 요소만)
            html_content = self._generate_filtered_html(filtered_elements)
            print(f"[Python] HTML 길이: {len(html_content)}, 요소 수: {len(filtered_elements)}", file=sys.stderr)

            # 디버그: HTML 저장
            from utils.logger import log_llm_interaction, log_token_usage, save_to_excel_usage, LOG_DIR
            from datetime import datetime
            debug_ts = datetime.now().strftime('%Y%m%d_%H%M%S')

            # HTML 마크다운 저장
            html_file = LOG_DIR / f"debug_html_{debug_ts}.md"
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"[Python] HTML 저장: {html_file}", file=sys.stderr)

            log_llm_interaction(
                prompt=prompt,
                html=html_content,
                response_raw={"stage": "before_llm"},
                commands=[],
                success=True
            )

            # 6. 참조 자료가 있으면 프롬프트에 포함 (서버 분리 대비)
            final_prompt = prompt
            if reference_content:
                reference_section = f"""
[참조 자료: {reference_file_name or '첨부 파일'}]
---
{reference_content}
---

위 참조 자료를 바탕으로 아래 요청을 수행해주세요:
"""
                final_prompt = reference_section + prompt
                print(f"[Python] 참조 자료 포함: {reference_file_name} ({len(reference_content)} chars)", file=sys.stderr)

            # 7. LLM 스트리밍 호출
            self._send_progress("stage", {"stage": "thinking", "message": "AI가 분석 중..."})
            print(f"[Python] LLM 스트리밍 호출 중...", file=sys.stderr)

            # HwpConnector 사용 (실시간 편집용)
            connector = self._ensure_connector()

            # Diff 모드 상태 재적용 (Track Changes 제어)
            self._ensure_track_changes_state(self._diff_mode_enabled)

            # 모델 설정 (Frontend에서 전달)
            model = params.get("model", "gpt-5.1")

            # 스트리밍 클라이언트
            streaming_client = get_streaming_client(openai_api_key)
            streaming_client.model = model  # 모델 설정

            # 편집 카운터 및 메시지 수집
            edits_count = 0
            messages_collected = []
            editing_started = False  # editDocument 시작 여부
            seen_ops = set()

            def on_command(cmd: StreamingCommand):
                """명령이 파싱될 때마다 호출되는 콜백 (DocumentView 기반)"""
                nonlocal edits_count
                nonlocal block_manager

                if cmd.action == "thinking":
                    # AI 사고 과정 표시 (Delta 모드)
                    self._send_progress("thinking", {
                        "content": cmd.message,
                        "duration": cmd.metadata.get("duration") if cmd.metadata else None
                    })

                elif cmd.action == "message":
                    # AI 메시지
                    messages_collected.append(cmd.message)
                    self._send_progress("message", {"text": cmd.message})

                elif cmd.action == "edit_document":
                    # DocumentView element 편집 - 즉시 실행
                    nonlocal editing_started
                    
                    # 편집 시작 알림 (첫 번째 편집 시)
                    if not editing_started:
                        editing_started = True
                        self._send_progress("editDocument", {"status": "start"})

                    operation = cmd.metadata.get("operation") if cmd.metadata else None

                    if operation == "find_and_replace_all":
                        old_text = None
                        if cmd.metadata:
                            old_text = (
                                cmd.metadata.get("old_text")
                                or cmd.metadata.get("find")
                                or cmd.metadata.get("text")
                            )
                        new_text = cmd.content or ""

                        if not old_text or old_text == new_text:
                            self._send_progress("edit_skipped", {
                                "id": cmd.id,
                                "reason": "find_and_replace_all no-op"
                            })
                            return

                        replaced_count = connector.find_replace_all(
                            old_text, new_text, match_case=False
                        )
                        if replaced_count >= 0:
                            edits_count += 1
                            self._mark_tracked_edit(True)
                            self._send_progress("edit", {
                                "type": "replace_all",
                                "content": f"'{old_text}' → '{new_text}' ({replaced_count}회)"
                            })
                        else:
                            self._send_progress("edit_failed", {
                                "id": cmd.id,
                                "reason": "전체 찾기/바꾸기 실패"
                            })
                        return

                    # cmd.id는 element_id (int 기대). LLM이 "cell-4" 같은 prefix 문자열을
                    # 보낼 수 있어 정규화 + 예외 격리 필요
                    # 패턴: 문자열 끝의 숫자만 추출 (prefix 하이픈은 구분자, 음수 부호 아님)
                    try:
                        if isinstance(cmd.id, int):
                            element_id = cmd.id
                        else:
                            _raw = str(cmd.id).strip()
                            _m = re.search(r"(?:^|[-_:])(-?\d+)$", _raw)
                            if _m:
                                element_id = int(_m.group(1))
                            else:
                                # 순수 숫자 문자열도 허용 ("5", " 12 ")
                                _m2 = re.fullmatch(r"\s*(-?\d+)\s*", _raw)
                                if not _m2:
                                    raise ValueError(f"id에서 정수 추출 실패: {cmd.id!r}")
                                element_id = int(_m2.group(1))
                    except Exception as _exc:
                        self._send_progress("edit_failed", {
                            "id": cmd.id,
                            "reason": f"invalid_id: {_exc}"
                        })
                        return

                    # 동일 명령 반복 방지
                    op_key = (
                        operation or "",
                        element_id,
                        cmd.content or "",
                        (cmd.metadata.get("old_text") if cmd.metadata else None)
                        or (cmd.metadata.get("find") if cmd.metadata else None)
                        or "",
                    )
                    if op_key in seen_ops:
                        self._send_progress("edit_skipped", {
                            "id": element_id,
                            "reason": "duplicate_command"
                        })
                        return
                    seen_ops.add(op_key)

                    # Element 조회
                    element = doc_view.get_element_by_id(element_id)
                    if element is None:
                        print(f"[Python] 알 수 없는 element ID: {element_id}", file=sys.stderr)
                        self._send_progress("edit_failed", {
                            "id": element_id,
                            "reason": "알 수 없는 element ID"
                        })
                        return

                    # 페이지 범위 체크 - 허용된 element ID만 편집
                    if self._allowed_element_ids and element_id not in self._allowed_element_ids:
                        page = element.attributes.get('page', 'unknown')
                        print(f"[Python] 페이지 범위 외 element 무시: {element_id} (page: {page})", file=sys.stderr)
                        self._send_progress("edit_skipped", {
                            "id": element_id,
                            "reason": "페이지 범위 외",
                            "page": page
                        })
                        return
                    # HWP 위치로 이동 (DocumentBlockManager에서 조정된 위치 사용)
                    adjusted_position = block_manager.get_adjusted_position(element_id)
                    if adjusted_position is None:
                        # DocumentBlockManager에 없으면 원본 위치 사용
                        adjusted_position = element.position

                    # 디버깅 로그: 편집 대상 정보
                    print(f"[Python] 편집 대상: id={element_id}, type={element.element_type}, operation={operation}, content_preview={str(element.content)[:50]}", file=sys.stderr)
                    print(f"[Python] 원본 위치: {element.position}, 조정된 위치: {adjusted_position}", file=sys.stderr)

                    # metadata에 따른 operation 처리
                    if operation in ("replace", "find_and_replace_in_paragraph"):
                        # F 명령어: Find and Replace
                        find_text = None
                        if cmd.metadata:
                            find_text = (
                                cmd.metadata.get("find")
                                or cmd.metadata.get("old_text")
                                or cmd.metadata.get("text")
                            )
                        replace_text = cmd.content or ""

                        if not find_text or find_text == replace_text:
                            self._send_progress("edit_skipped", {
                                "id": element_id,
                                "reason": "find_and_replace no-op"
                            })
                            return

                        print(f"[Python] Find and Replace: '{find_text}' → '{replace_text}'", file=sys.stderr)

                        # 위치 이동
                        if not connector.set_pos(adjusted_position[0], adjusted_position[1], 0):
                            print(f"[Python] 위치 이동 실패: {adjusted_position}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": element_id,
                                "reason": "위치 이동 실패"
                            })
                            return

                        # 문단 전체 선택 후 텍스트 찾기/교체
                        connector.select_current_para()
                        selected_text = connector.get_selected_text()

                        if selected_text and find_text in selected_text:
                            new_text = selected_text.replace(find_text, replace_text)
                            connector.insert_text(new_text)
                            print(f"[Python] ✅ Find and Replace 성공", file=sys.stderr)

                            # DocumentBlockManager 업데이트
                            old_length = len(selected_text)
                            new_length = len(new_text)
                            block_manager.update_block(element_id, len_delta=new_length - old_length)

                            edits_count += 1
                            self._mark_tracked_edit(True)
                            self._send_progress("edit", {"type": "replace", "content": f"'{find_text}' → '{replace_text}'"})
                        else:
                            print(f"[Python] ⚠️  Find and Replace 대상 없음: '{find_text}'", file=sys.stderr)
                            self._send_progress("edit_skipped", {
                                "id": element_id,
                                "reason": f"'{find_text}' 텍스트를 찾을 수 없음"
                            })

                        connector.cancel_selection()
                        return

                    elif operation == "delete":
                        # D 명령어: Delete
                        delete_text = cmd.metadata.get("text")

                        print(f"[Python] Delete: '{delete_text}'", file=sys.stderr)

                        # 위치 이동
                        if not connector.set_pos(adjusted_position[0], adjusted_position[1], 0):
                            print(f"[Python] 위치 이동 실패: {adjusted_position}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": element_id,
                                "reason": "위치 이동 실패"
                            })
                            return

                        # 문단 전체 선택 후 텍스트 삭제
                        connector.select_current_para()
                        selected_text = connector.get_selected_text()

                        if delete_text in selected_text:
                            new_text = selected_text.replace(delete_text, "")
                            connector.insert_text(new_text)
                            print(f"[Python] ✅ Delete 성공", file=sys.stderr)

                            # DocumentBlockManager 업데이트
                            old_length = len(selected_text)
                            new_length = len(new_text)
                            block_manager.update_block(element_id, len_delta=new_length - old_length)

                            edits_count += 1
                            self._send_progress("edit", {"type": "delete", "content": f"삭제: '{delete_text}'"})
                        else:
                            print(f"[Python] ⚠️  Delete 실패: '{delete_text}' 찾을 수 없음", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": element_id,
                                "reason": f"'{delete_text}' 텍스트를 찾을 수 없음"
                            })

                        connector.cancel_selection()
                        return

                    # 표 셀 편집 vs 일반 문단 편집 분기 (기본 E 명령어)
                    if element.element_type == "table_cell":
                        # 표 셀 편집
                        row = element.attributes.get("row", 0)
                        col = element.attributes.get("col", 0)
                        table_id = element.attributes.get("table_id")

                        print(f"[Python] 표 셀 편집: table_id={table_id}, row={row}, col={col}", file=sys.stderr)

                        # 1. 셀 선택
                        if not connector.select_table_cell(adjusted_position, row, col):
                            print(f"[Python] 표 셀 선택 실패: row={row}, col={col}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": element_id,
                                "reason": "표 셀 선택 실패"
                            })
                            return

                        # 2. 원본 길이 저장
                        old_length = len(element.content) if isinstance(element.content, str) else 0
                        new_length = len(cmd.content)

                        # 3. 선택 영역에 새 텍스트 삽입
                        success = connector.insert_text(cmd.content)

                        # 4. 선택 해제 (HWP 상태 정리)
                        connector.cancel_selection()

                    else:
                        # 일반 문단 편집
                        # 문단 전체 선택을 위해 시작점(char_pos=0)으로 이동
                        if not connector.set_pos(adjusted_position[0], adjusted_position[1], 0):
                            print(f"[Python] 위치 이동 실패: {adjusted_position}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": element_id,
                                "reason": "위치 이동 실패"
                            })
                            return

                        # 1. 문단 전체 선택
                        connector.select_current_para()

                        # 2. 원본 길이 저장 (DocumentBlockManager 업데이트용)
                        old_length = len(element.content) if isinstance(element.content, str) else 0
                        new_length = len(cmd.content)

                        # 3. 선택 영역에 새 텍스트 삽입
                        success = connector.insert_text(cmd.content)
                    edit_mode = "track" if self._diff_mode_enabled else "normal"

                    if success:
                        edits_count += 1
                        self._mark_tracked_edit(True)

                        # 4. DocumentBlockManager 업데이트 (표 셀은 제외 - 모두 같은 위치 공유)
                        if element.element_type != "table_cell":
                            block_manager.update_after_edit(element_id, old_length, new_length)
                            offset = block_manager.get_offset(element_id)
                            print(f"[Python] DocumentBlockManager 업데이트: element={element_id}, old={old_length}, new={new_length}, offset={offset}", file=sys.stderr)

                        self._send_progress("edit", {
                            "type": "element",
                            "id": element_id,
                            "mode": edit_mode,
                            "content": cmd.content[:50] + "..." if len(cmd.content) > 50 else cmd.content
                        })

                elif cmd.action == "append_table_row":
                    # 표 행 추가 - 즉시 실행
                    # cmd.id는 element_id (int 기대). LLM이 "cell-4" 같은 prefix 문자열을
                    # 보낼 수 있어 정규화 + 예외 격리 필요
                    # 패턴: 문자열 끝의 숫자만 추출 (prefix 하이픈은 구분자, 음수 부호 아님)
                    try:
                        if isinstance(cmd.id, int):
                            element_id = cmd.id
                        else:
                            _raw = str(cmd.id).strip()
                            _m = re.search(r"(?:^|[-_:])(-?\d+)$", _raw)
                            if _m:
                                element_id = int(_m.group(1))
                            else:
                                # 순수 숫자 문자열도 허용 ("5", " 12 ")
                                _m2 = re.fullmatch(r"\s*(-?\d+)\s*", _raw)
                                if not _m2:
                                    raise ValueError(f"id에서 정수 추출 실패: {cmd.id!r}")
                                element_id = int(_m2.group(1))
                    except Exception as _exc:
                        self._send_progress("edit_failed", {
                            "id": cmd.id,
                            "reason": f"invalid_id: {_exc}"
                        })
                        return
                    row_key = tuple(cmd.rows or [])
                    op_key = ("append_table_row", element_id, row_key)
                    if op_key in seen_ops:
                        self._send_progress("edit_skipped", {
                            "id": element_id,
                            "reason": "duplicate_command"
                        })
                        return
                    seen_ops.add(op_key)

                    # Element 조회
                    element = doc_view.get_element_by_id(element_id)
                    if element is None:
                        print(f"[Python] 행 추가 실패: 잘못된 element ID {element_id}", file=sys.stderr)
                        self._send_progress("edit_failed", {
                            "id": element_id,
                            "reason": "잘못된 element ID"
                        })
                        return

                    # 표 관련 속성 확인
                    table_id = element.attributes.get("table_id")
                    row = element.attributes.get("row", 0)
                    col = element.attributes.get("col", 0)

                    if table_id is None:
                        print(f"[Python] 행 추가 실패: element {element_id}는 표 셀이 아님", file=sys.stderr)
                        self._send_progress("edit_failed", {
                            "id": element_id,
                            "reason": "표 셀이 아님"
                        })
                        return

                    # 페이지 범위 체크
                    if self._allowed_element_ids and element_id not in self._allowed_element_ids:
                        page = element.attributes.get('page', 'unknown')
                        print(f"[Python] 페이지 범위 외 element 무시 (행추가): {element_id}", file=sys.stderr)
                        self._send_progress("edit_skipped", {
                            "id": element_id,
                            "reason": "페이지 범위 외",
                            "page": page
                        })
                        return

                    # 행 추가
                    current_row = row
                    for row_str in cmd.rows:
                        cells = row_str.split("|")

                        if connector.append_table_row(table_id, current_row, cells, col):
                            edits_count += 1
                            self._mark_tracked_edit(True)
                            current_row += 1
                            self._send_progress("edit", {
                                "type": "append_row",
                                "id": element_id,
                                "table": table_id,
                                "row": current_row,
                                "cells": len(cells)
                            })

            # 스트리밍 실행 (Delta 모드 - 기존 구조 유지)
            result = streaming_client.generate_commands_streaming(
                html=html_content,
                prompt=final_prompt,
                on_command=on_command,
                use_html=False,  # Delta 모드 (textbox/p 구조 유지)
                use_delta=True   # Delta 형식 (op: replace_cell_content)
            )

            # 토큰 사용량 로깅 (터미널 + 파일)
            log_token_usage(
                token_usage=result.token_usage,
                prompt=prompt,
                context_info=f"HTML: {len(html_content):,} chars, edits: {edits_count}"
            )

            # 엑셀에 통합 저장 (LLM)
            if result.token_usage:
                save_to_excel_usage(
                    api_type="LLM",
                    operation="chat:send",
                    model="gpt-5.1",
                    input_tokens=result.token_usage.get("input", 0),
                    output_tokens=result.token_usage.get("output", 0),
                    cost_usd=(
                        result.token_usage.get("input", 0) * 1.25 / 1_000_000 +
                        result.token_usage.get("output", 0) * 10.00 / 1_000_000
                    ),
                    context_info=f"HTML: {len(html_content):,} chars, edits: {edits_count}"
                )

            print(f"[Python] 스트리밍 완료: {edits_count}개 편집", file=sys.stderr)

            # 커서 위치 복원 (흰 화면 방지)
            if initial_cursor_pos is not None and isinstance(initial_cursor_pos, tuple) and len(initial_cursor_pos) == 3:
                try:
                    connector.hwp.set_pos(*initial_cursor_pos)
                    print(f"[Python] 커서 위치 복원 완료: {initial_cursor_pos}", file=sys.stderr)
                except Exception as e:
                    print(f"[Python] 커서 위치 복원 실패: {e}", file=sys.stderr)
            else:
                print(f"[Python] 커서 위치 복원 건너뜀 (초기 위치 없음)", file=sys.stderr)

            # 편집 이력 기록 (TASK-004)
            if edits_count > 0:
                import uuid
                chat_id = str(uuid.uuid4())[:8]  # 짧은 고유 ID
                self._record_edit(chat_id, edits_count)
                print(f"[Python] 편집 이력 기록: chatId={chat_id}, count={edits_count}", file=sys.stderr)

            # 편집 종료 알림 (기존 스타일)
            if editing_started:
                self._send_progress("editDocument", {"status": "end"})

            # 완료 알림
            self._send_progress("complete", {
                "edits": edits_count,
                "messages": messages_collected,
                "editHistory": self.get_edit_history()
            })

            # 최종 메시지 구성
            if messages_collected:
                final_message = "\n".join(messages_collected)
            elif edits_count > 0:
                final_message = f"문서를 수정했습니다. ({edits_count}개 항목 편집)"
            else:
                # LLM이 thinking만 호출하고 종료하거나, 모든 편집이 실패한 케이스
                # 진단: stderr에 상세 로그 (왜 0건인지 추적 가능)
                print(
                    f"[Python] WARN: 편집 0건, 메시지 0건. "
                    f"LLM이 execute_edits/message를 호출하지 않았거나 모든 편집이 실패.",
                    file=sys.stderr
                )
                final_message = (
                    "AI가 분석은 완료했지만 편집 명령을 생성하지 못했습니다.\n"
                    "다시 시도하거나 더 구체적인 지시를 해보세요."
                )

            # 세션 토큰 통계 가져오기
            from utils.logger import get_session_token_stats
            session_stats = get_session_token_stats()

            return {
                "success": True,
                "message": final_message,
                "edits": edits_count,
                "token_usage": result.token_usage,
                "session_tokens": session_stats  # 세션 누적 토큰 정보
            }

        except Exception as e:
            print(f"[Python] chat 오류: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self._send_progress("error", {"error": str(e)})
            return {
                "success": False,
                "error": str(e),
                "trace": traceback.format_exc()
            }

    def _map_element_pages(self, connector: HwpConnector, doc_view: DocumentView):
        """각 element에 페이지 정보 매핑

        Args:
            connector: HwpConnector 인스턴스
            doc_view: DocumentView 인스턴스
        """
        try:
            for element in doc_view.elements:
                # Element의 위치로 이동
                position = element.position
                if connector.set_pos(position[0], position[1], position[2]):
                    # 현재 페이지 번호 가져오기
                    page = connector.hwp.current_page
                    # Element의 attributes에 페이지 번호 저장
                    element.attributes['page'] = page
                else:
                    # 위치 이동 실패 시 unknown 표시
                    element.attributes['page'] = 'unknown'

            print(f"[Python] {len(doc_view.elements)}개 element 페이지 매핑 완료", file=sys.stderr)

        except Exception as e:
            print(f"[Python] 페이지 매핑 중 에러: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    def _filter_elements_by_page(self, doc_view: DocumentView, start_page: int, end_page: int):
        """페이지 범위 내 element만 필터링

        Args:
            doc_view: DocumentView 인스턴스
            start_page: 시작 페이지 (포함)
            end_page: 끝 페이지 (포함)

        Returns:
            Tuple[List[Element], Set[int]]: (필터링된 element 리스트, 허용된 element_id 집합)
        """
        filtered_elements = []
        allowed_element_ids = set()

        for element in doc_view.elements:
            page = element.attributes.get('page')
            if page is not None and page != 'unknown':
                if start_page <= page <= end_page:
                    filtered_elements.append(element)
                    allowed_element_ids.add(element.element_id)

        return filtered_elements, allowed_element_ids

    def _generate_filtered_html(self, filtered_elements: list) -> str:
        """필터링된 element로 HTML 마크다운 생성

        Args:
            filtered_elements: 필터링된 Element 리스트

        Returns:
            str: HTML 마크다운
        """
        html_lines = []

        for element in filtered_elements:
            element_type = element.element_type
            content = element.content
            element_id = element.element_id

            # Element 타입별 HTML 생성
            if element_type == "heading":
                level = element.attributes.get("level", 1)
                html_lines.append(f"{'#' * level} {content} <!-- id: {element_id} -->")

            elif element_type == "paragraph":
                html_lines.append(f"<p id=\"{element_id}\">{content}</p>")

            elif element_type == "table":
                # 표 전체 (실제 셀 데이터 포함)
                rows = element.attributes.get("rows", 0)
                cols = element.attributes.get("cols", 0)

                # content가 리스트 (셀 데이터)인지 확인
                if isinstance(content, list):
                    # 디버그: HTML 생성 전 셀 데이터 출력
                    print(f"[HTML] 표 HTML 생성: table_id={element_id}, {rows}행 x {cols}열", file=sys.stderr)
                    for i, row_data in enumerate(content):
                        print(f"[HTML]   행{i}: {len(row_data)}개 셀", file=sys.stderr)
                        for j, cell_data in enumerate(row_data):
                            cell_text = cell_data.get("text", "")
                            print(f"[HTML]     셀[{i},{j}]: '{cell_text}' (길이={len(cell_text)})", file=sys.stderr)

                    # HTML 테이블 생성
                    table_html = [f'<table id="{element_id}" data-rows="{rows}" data-cols="{cols}">']

                    for row_data in content:
                        table_html.append("  <tr>")
                        for cell_data in row_data:
                            cell_id = cell_data.get("id", "")
                            cell_text = cell_data.get("text", "")
                            cell_row = cell_data.get("row", 0)
                            cell_col = cell_data.get("col", 0)

                            table_html.append(
                                f'    <td id="{cell_id}" data-row="{cell_row}" data-col="{cell_col}">{cell_text}</td>'
                            )
                        table_html.append("  </tr>")

                    table_html.append("</table>")
                    final_html = "\n".join(table_html)
                    html_lines.append(final_html)

                    # 디버그: 생성된 HTML 출력
                    print(f"[HTML] 생성된 HTML:\n{final_html}", file=sys.stderr)
                else:
                    # 임시 구현 (content가 "[표]" 문자열인 경우)
                    html_lines.append(f"<table id=\"{element_id}\">{content}</table>")

            elif element_type == "table_cell":
                # 개별 표 셀 정보 (이전 - 현재는 table 타입 내부에 포함됨)
                row = element.attributes.get("row", 0)
                col = element.attributes.get("col", 0)
                html_lines.append(f"<td id=\"{element_id}\" data-row=\"{row}\" data-col=\"{col}\">{content}</td>")

            elif element_type == "image":
                user_desc = element.attributes.get("user_desc", "")
                html_lines.append(f"<img id=\"{element_id}\" alt=\"{user_desc}\" />")

            elif element_type == "footnote":
                html_lines.append(f"<aside id=\"{element_id}\">{content}</aside>")

            else:
                # 기타 타입
                html_lines.append(f"<div id=\"{element_id}\" data-type=\"{element_type}\">{content}</div>")

        return "\n".join(html_lines)

    def _extract_top_level_td_ids(self, html_content: str) -> List[int]:
        """상위 테이블의 td id만 순서대로 추출"""
        if not isinstance(html_content, str) or "<td" not in html_content:
            return []

        class _TdDepthParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.table_depth = 0
                self.seen: List[Tuple[int, int]] = []

            def handle_starttag(self, tag, attrs):
                if tag == "table":
                    self.table_depth += 1
                    return
                if tag != "td":
                    return

                attrs_dict = dict(attrs)
                raw_id = attrs_dict.get("id")
                if raw_id is None:
                    return
                try:
                    self.seen.append((self.table_depth, int(raw_id)))
                except (TypeError, ValueError):
                    return

            def handle_endtag(self, tag):
                if tag == "table":
                    self.table_depth = max(0, self.table_depth - 1)

        parser = _TdDepthParser()
        parser.feed(html_content)

        if not parser.seen:
            return []

        min_depth = min(depth for depth, _ in parser.seen)
        return [cell_id for depth, cell_id in parser.seen if depth == min_depth]

    def _get_pos_by_set(self, hwp) -> Optional[Tuple[int, int, int]]:
        try:
            pos_set = hwp.GetPosBySet()
            if not pos_set:
                return None
            return (
                pos_set.Item("List"),
                pos_set.Item("Para"),
                pos_set.Item("Pos"),
            )
        except Exception:
            return None

    def _move_to_first_table_cell(self, connector: HwpConnector, element: Element) -> bool:
        hwp = connector.hwp
        first_coords = element.attributes.get("first_coords")
        if isinstance(first_coords, (tuple, list)) and len(first_coords) >= 3:
            if connector.set_pos(first_coords[0], first_coords[1], first_coords[2]):
                try:
                    if hasattr(hwp, "is_cell") and hwp.is_cell():
                        return True
                except Exception:
                    return True

        if not connector.set_pos(element.position[0], element.position[1], element.position[2]):
            return False

        try:
            hwp.FindCtrl()
        except Exception:
            pass

        try:
            hwp.HAction.Run("TableCellBlock")
        except Exception:
            try:
                hwp.TableCellBlock()
            except Exception:
                pass

        try:
            hwp.HAction.Run("TableColBegin")
            hwp.HAction.Run("TableColPageUp")
        except Exception:
            try:
                hwp.TableColBegin()
                hwp.TableColPageUp()
            except Exception:
                pass

        try:
            if hasattr(hwp, "is_cell") and not hwp.is_cell():
                return False
        except Exception:
            pass

        return True

    def _move_to_next_table_cell(self, hwp) -> bool:
        try:
            return bool(hwp.HAction.Run("TableRightCell"))
        except Exception:
            try:
                return bool(hwp.TableRightCell())
            except Exception:
                return False

    def _move_to_lower_table_cell(self, hwp) -> bool:
        try:
            return bool(hwp.HAction.Run("TableLowerCell"))
        except Exception:
            try:
                return bool(hwp.TableLowerCell())
            except Exception:
                return False

    def _extend_id_to_pos_with_table_cells(
        self,
        connector: HwpConnector,
        elements: list,
        id_to_pos: Dict[int, Tuple[int, int, int]],
    ) -> None:
        hwp = connector.hwp

        for element in elements:
            if element.element_type != "table":
                continue
            if not isinstance(element.content, str):
                continue

            cell_ids = self._extract_top_level_td_ids(element.content)
            if not cell_ids:
                print(
                    f"[Python] 표 {element.element_id} 셀 ID 추출 실패",
                    file=sys.stderr,
                )
                continue

            if not self._move_to_first_table_cell(connector, element):
                print(
                    f"[Python] 표 {element.element_id} 첫 셀 이동 실패",
                    file=sys.stderr,
                )
                continue

            mapped_count = 0
            for idx, cell_id in enumerate(cell_ids):
                pos = connector.get_pos()
                if not pos:
                    pos = self._get_pos_by_set(hwp)
                if pos:
                    id_to_pos[cell_id] = pos
                    mapped_count += 1

                if idx < len(cell_ids) - 1:
                    if self._move_to_next_table_cell(hwp):
                        continue

                    if not self._move_to_lower_table_cell(hwp):
                        break

                    try:
                        hwp.HAction.Run("TableColBegin")
                        hwp.HAction.Run("TableColPageUp")
                    except Exception:
                        try:
                            hwp.TableColBegin()
                            hwp.TableColPageUp()
                        except Exception:
                            pass

            print(
                f"[Python] 표 {element.element_id} 셀 매핑 완료: {mapped_count}/{len(cell_ids)}",
                file=sys.stderr,
            )

            try:
                hwp.HAction.Run("Cancel")
            except Exception:
                pass

    def _extract_hwpml_from_active(self, hwp) -> str:
        """활성 HWP 문서에서 HWPML 추출"""
        import tempfile
        import uuid

        try:
            # 임시 파일로 HWPML 저장 후 읽기
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"hwpml_{uuid.uuid4()}.xml")

            # HWPML 형식으로 저장
            hwp.save_as(temp_path, format="HWPML2X")

            # 파일 읽기
            with open(temp_path, 'r', encoding='utf-8') as f:
                hwpml = f.read()

            # 임시 파일 삭제
            try:
                os.remove(temp_path)
            except:
                pass

            return hwpml

        except Exception as e:
            print(f"[Python] HWPML 추출 실패: {e}", file=sys.stderr)
            return ""

    # ============================================================
    # Undo/Redo 및 Diff 모드 API (TASK-004)
    # ============================================================

    def undo(self, count: int = 1) -> dict:
        """
        Undo 실행

        Args:
            count: 실행할 Undo 횟수 (기본 1)

        Returns:
            {"success": bool, "undone": int, "remaining": int}
        """
        try:
            adapter = self._ensure_connector()
            undone = adapter.undo(count)

            # 편집 이력 업데이트
            if undone > 0 and self._edit_history:
                # 가장 최근 편집의 카운트 감소
                for i in range(len(self._edit_history) - 1, -1, -1):
                    if self._edit_history[i]["editCount"] > 0:
                        self._edit_history[i]["editCount"] -= undone
                        if self._edit_history[i]["editCount"] <= 0:
                            self._edit_history.pop(i)
                        break

            total_remaining = sum(h["editCount"] for h in self._edit_history)

            return {
                "success": True,
                "undone": undone,
                "remaining": total_remaining
            }
        except Exception as e:
            print(f"[Python] Undo 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def redo(self, count: int = 1) -> dict:
        """
        Redo 실행

        Args:
            count: 실행할 Redo 횟수 (기본 1)

        Returns:
            {"success": bool, "redone": int}
        """
        try:
            adapter = self._ensure_connector()
            redone = adapter.redo(count)
            return {"success": True, "redone": redone}
        except Exception as e:
            print(f"[Python] Redo 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def set_diff_mode(self, enabled: bool, skip_track_changes: bool = False) -> dict:
        """
        Diff 모드 설정 - Track Changes 기능 연동

        Args:
            enabled: True면 Diff 모드 활성화 (Track Changes ON)

        Returns:
            {"success": bool, "diffMode": bool}
        """
        try:
            self._diff_mode_enabled = enabled
            if enabled:
                self._pending_track_changes = False
            if skip_track_changes:
                if not enabled:
                    try:
                        connector = self._ensure_connector()
                        connector.set_track_changes_flag(False)
                    except Exception:
                        pass
                    self._track_changes_active = False
                    self._pending_track_changes = False
            else:
                self._ensure_track_changes_state(enabled)
            print(f"[Python] Diff 모드: {'ON' if enabled else 'OFF'} (Track Changes)", file=sys.stderr)
            return {"success": True, "diffMode": self._diff_mode_enabled}
        except Exception as e:
            print(f"[Python] Diff 모드 설정 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def get_diff_mode(self) -> dict:
        """
        현재 Diff 모드 상태 조회

        Returns:
            {"success": bool, "diffMode": bool}
        """
        return {"success": True, "diffMode": self._diff_mode_enabled}

    def apply_changes(self) -> dict:
        """
        Diff 변경사항 승인

        Track Changes 기반으로 모든 변경 사항을 문서에 반영합니다.
        TrackChangeApplyAll은 변경사항이 없어도 오류 없이 처리됩니다.

        Returns:
            {"success": bool}
        """
        try:
            connector = self._ensure_connector()

            # 로컬 플래그만 확인 (TrackChangeNext 사용 금지 - 커서 이동 부작용)
            if not self._pending_track_changes:
                print("[Python] 변경사항 없음(로컬 플래그): 승인 생략", file=sys.stderr)
                return {"success": True, "skipped": True, "reason": "no_tracked_changes"}

            # TrackChangeApplyAll 직접 실행 (변경사항 없어도 안전)
            success = connector.accept_all_changes()
            self._pending_track_changes = False

            print(f"[Python] 변경사항 승인: {'성공' if success else '실패'}", file=sys.stderr)
            return {"success": success}
        except Exception as e:
            print(f"[Python] 승인 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}


    def reject_changes(self) -> dict:
        """
        Reject tracked changes.

        TrackChangeCancelAll 직접 실행. Fallback으로 undo 사용.
        """
        try:
            connector = self._ensure_connector()

            # 로컬 플래그 기반 확인 (TrackChangeNext 사용 금지)
            has_tracked = self._pending_track_changes

            track_reject = False
            if has_tracked:
                # TrackChangeCancelAll 직접 실행 (변경사항 없어도 안전)
                track_reject = connector.reject_all_changes()

            undo_result = None
            undo_success = False
            if not track_reject:
                undo_count = self._get_recent_edit_count()
                if undo_count > 0:
                    undo_result = self.undo(undo_count)
                    undo_success = bool(undo_result.get("undone", 0))
                elif not has_tracked:
                    print("[Python] No tracked changes or undo history; reject skipped.", file=sys.stderr)
                    return {"success": True, "skipped": True, "reason": "no_tracked_changes_or_undo"}

            self._pending_track_changes = False
            success = track_reject or undo_success

            print(
                f"[Python] Reject changes: {'ok' if success else 'failed'} "
                f"(track={track_reject}, undo={undo_result})",
                file=sys.stderr,
            )
            return {"success": success, "track_reject": track_reject, "undo": undo_result}
        except Exception as e:
            print(f"[Python] Reject failed: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    # ============================================================
    # TrackChange 부분 승인/거절 API (HWP 2022 이전)
    # ============================================================

    def get_track_change_context(self) -> dict:
        """
        TrackChange UI 상태 계산

        Returns:
            {
                "pending": bool,         # 남은 변경 있음
                "selectionCount": int,   # 선택된 변경 개수
                "contextVisible": bool   # 컨텍스트 버튼 표시 여부
            }
        """
        try:
            connector = self._ensure_connector()
            quiet = os.getenv("TRACKCHANGES_QUIET", "1") != "0"
            if quiet:
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    context = get_track_change_context(connector)
            else:
                context = get_track_change_context(connector)

            if self._pending_track_changes and not context.get("pending"):
                context = {
                    **context,
                    "pending": True,
                    "contextVisible": True
                }
            return context
        except Exception as e:
            print(f"[Python] get_track_change_context failed: {e}", file=sys.stderr)
            return {"pending": False, "selectionCount": 0, "contextVisible": False}

    def cache_track_change_selection(self) -> dict:
        """TrackChange 선택 범위 캐시 (커서 이동 없음)"""
        try:
            connector = self._ensure_connector()
            return cache_track_change_selection(connector)
        except Exception as e:
            print(f"[Python] cache_track_change_selection failed: {e}", file=sys.stderr)
            return {"success": False, "selectionCount": 0}

    def apply_all_track_changes(self) -> dict:
        """
        모든 TrackChange 승인

        Returns:
            {
                "success": bool,
                "hasRemaining": bool,
                "autoComplete": bool
            }
        """
        try:
            connector = self._ensure_connector()
            result = apply_all_changes(connector)

            # 자동 완료 시 플래그 업데이트
            if result.get("autoComplete"):
                self._pending_track_changes = False

            return result
        except Exception as e:
            print(f"[Python] apply_all_track_changes failed: {e}", file=sys.stderr)
            return {"success": False, "hasRemaining": True, "autoComplete": False}

    def get_active_doc_info(self) -> dict:
        """현재 활성 문서의 원시 식별자 반환 (v4.1.4)
        
        Main에서 docKey 비교에 사용. Python은 docKey를 계산하지 않음.
        
        Returns:
            {
                "activeDocumentId": int | None,
                "activePath": str | None
            }
        """
        try:
            from api.track_changes_api import get_active_doc_info

            connector = retrieve_runtime_connector()
            try:
                if connector and connector.check_alive():
                    return get_active_doc_info(connector)
            except Exception:
                connector = None

            connector = self._connector
            try:
                if connector and connector.check_alive():
                    return get_active_doc_info(connector)
            except Exception:
                pass

            return {"activeDocumentId": None, "activePath": None}
        except Exception as e:
            print(f"[Python] get_active_doc_info failed: {e}", file=sys.stderr)
            return {"activeDocumentId": None, "activePath": None}


    def reject_all_track_changes(self) -> dict:
        """
        모든 TrackChange 거절

        Returns:
            {
                "success": bool,
                "hasRemaining": bool,
                "autoComplete": bool
            }
        """
        try:
            connector = self._ensure_connector()
            result = reject_all_changes(connector)

            # 자동 완료 시 플래그 업데이트
            if result.get("autoComplete"):
                self._pending_track_changes = False

            return result
        except Exception as e:
            print(f"[Python] reject_all_track_changes failed: {e}", file=sys.stderr)
            return {"success": False, "hasRemaining": True, "autoComplete": False}

    def apply_selected_track_changes(self) -> dict:
        """
        선택된 TrackChange 승인 (클릭 또는 드래그 선택)

        Returns:
            {
                "success": bool,
                "processed": int,
                "hasRemaining": bool,
                "autoComplete": bool,
                "showToast": bool
            }
        """
        try:
            connector = self._ensure_connector()
            result = apply_selected_changes(connector)

            # 자동 완료 시 플래그 업데이트
            if result.get("autoComplete"):
                self._pending_track_changes = False

            return result
        except Exception as e:
            print(f"[Python] apply_selected_track_changes failed: {e}", file=sys.stderr)
            return {
                "success": False,
                "processed": 0,
                "hasRemaining": True,
                "autoComplete": False,
                "showToast": False
            }

    def reject_selected_track_changes(self) -> dict:
        """
        선택된 TrackChange 거절 (클릭 또는 드래그 선택)

        Returns:
            {
                "success": bool,
                "processed": int,
                "hasRemaining": bool,
                "autoComplete": bool,
                "showToast": bool
            }
        """
        try:
            connector = self._ensure_connector()
            result = reject_selected_changes(connector)

            # 자동 완료 시 플래그 업데이트
            if result.get("autoComplete"):
                self._pending_track_changes = False

            return result
        except Exception as e:
            print(f"[Python] reject_selected_track_changes failed: {e}", file=sys.stderr)
            return {
                "success": False,
                "processed": 0,
                "hasRemaining": True,
                "autoComplete": False,
                "showToast": False
            }

    def get_edit_history(self) -> dict:
        """
        편집 이력 조회

        Returns:
            {"success": bool, "history": list, "totalEdits": int}
        """
        total_edits = sum(h["editCount"] for h in self._edit_history)
        return {
            "success": True,
            "history": self._edit_history,
            "totalEdits": total_edits
        }

    def clear_edit_history(self) -> dict:
        """
        편집 이력 초기화

        Returns:
            {"success": bool}
        """
        self._edit_history = []
        self._current_chat_id = None
        print("[Python] 편집 이력 초기화됨", file=sys.stderr)
        return {"success": True}

    def _get_recent_edit_count(self) -> int:
        if not self._edit_history:
            return 0
        try:
            return int(self._edit_history[-1].get("editCount", 0))
        except Exception:
            return 0

    def _record_edit(self, chat_id: str, edit_count: int = 1):
        """
        편집 이력 기록 (내부용)

        Args:
            chat_id: 채팅 세션 ID
            edit_count: 편집 횟수
        """
        from datetime import datetime

        # 같은 채팅 세션이면 카운트 증가
        if self._edit_history and self._edit_history[-1]["chatId"] == chat_id:
            self._edit_history[-1]["editCount"] += edit_count
        else:
            # 새 세션 기록
            self._edit_history.append({
                "id": len(self._edit_history) + 1,
                "chatId": chat_id,
                "editCount": edit_count,
                "timestamp": datetime.now().isoformat()
            })

        self._current_chat_id = chat_id

    # ============================================================================
    # Agent Child Process 패턴 지원 메서드
    # ============================================================================

    def prepare_context(
        self,
        prompt: str,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
        reference_file: Optional[str] = None,
        reference_file_name: Optional[str] = None,
        doc_index: Optional[int] = None,
        doc_type: Optional[str] = None
    ) -> dict:
        """CVD 기반 문서 컨텍스트 생성 (Agent Process용)

        Agent Child Process가 LLM을 호출하기 전에 문서 컨텍스트를 준비합니다.
        프로세스 흐름: Main Process → COM Process (prepare_context) → Agent Process (LLM)

        Args:
            prompt: 사용자 요청
            start_page: 시작 페이지 (None이면 현재 커서 위치)
            end_page: 종료 페이지 (None이면 start_page+4)
            reference_file: 참조 파일 경로
            reference_file_name: 참조 파일 이름
            doc_index: 문서 인덱스 (None이면 활성 문서)
            doc_type: 문서 타입 ("hwp")

        Returns:
            {
                "success": bool,
                "html": str,  # CVD 텍스트 (JSONL 아님)
                "prompt": str,  # 최종 프롬프트 (참조 자료 포함)
                "context_id": str,  # 세션 ID
                "allowed_elements": list[int],  # 편집 허용 element ID 목록
                "doc_view_data": dict  # DocumentView 메타데이터
            }
        """
        try:
            self._send_progress("stage", {"stage": "init", "message": "문서 분석 시작..."})
            self._cleanup_session_state()

            # 새 편집 세션 시작 시 수정 이력 초기화
            clear_modification_registry()

            # 1. 문서 타입 확인
            if doc_type and doc_type != "hwp":
                return {"success": False, "error": f"현재 HWP 문서만 지원합니다. (요청: {doc_type})"}

            # 2. HWP 연결 (SafeHwp 패턴 사용 - 중복 생성 방지)
            connector = self._ensure_connector()
            hwp = connector.hwp

            # 문서 인덱스가 있으면 활성화
            doc_index_value = self._coerce_int(doc_index)
            if doc_index_value is not None:
                try:
                    self._send_progress("stage", {"stage": "document", "message": "문서 선택 중..."})
                    xdocs = hwp.XHwpDocuments
                    doc_count = self._coerce_int(getattr(xdocs, "Count", None))
                    if doc_count is None:
                        try:
                            doc_count = int(xdocs.Count)
                        except Exception:
                            doc_count = 0
                    if doc_index_value >= doc_count:
                        return {"success": False, "error": f"문서 인덱스 {doc_index_value}가 범위를 벗어남 (총 {doc_count}개)"}
                    doc = xdocs.Item(doc_index_value)
                    doc.SetActive_XHwpDocument()
                    print(f"[Python] 문서 {doc_index_value} 활성화됨", file=sys.stderr)
                except Exception as e:
                    print(f"[Python] 문서 활성화 실패: {e}", file=sys.stderr)

            # 3. 페이지 범위 결정
            current_page = None
            try:
                try:
                    active_doc = hwp.XHwpDocuments.Active_XHwpDocument
                    doc_info = active_doc.XHwpDocumentInfo if active_doc else None
                    page_index = getattr(doc_info, "CurrentPage", None)
                    if isinstance(page_index, int):
                        current_page = page_index + 1
                except Exception:
                    current_page = None

                current_page = self._coerce_int(current_page)
                if not current_page:
                    current_page = self._coerce_int(hwp.current_page)

                total_pages = self._coerce_int(hwp.PageCount) or 1
                MAX_PAGES = 5
                start_page_value = self._coerce_int(start_page)
                end_page_value = self._coerce_int(end_page)

                if start_page_value is not None:
                    actual_start = max(1, min(start_page_value, total_pages))
                    if end_page_value is not None:
                        actual_end = max(actual_start, min(end_page_value, total_pages))
                    else:
                        actual_end = min(actual_start + MAX_PAGES - 1, total_pages)
                else:
                    actual_start = current_page or 1
                    actual_start = max(1, min(actual_start, total_pages))
                    actual_end = min(actual_start + MAX_PAGES - 1, total_pages)

                print(f"[Python] 페이지 범위: {actual_start}~{actual_end}", file=sys.stderr)
            except Exception as e:
                print(f"[Python] 페이지 정보 수집 실패: {e}", file=sys.stderr)
                # 기본값 사용
                actual_start = 1
                actual_end = 5

            # 4. 페이지 범위 요소 추출 (CVD 텍스트 미사용)
            self._send_progress("stage", {"stage": "reading", "message": "문서 읽는 중..."})
            extractor = self._ensure_cvd_extractor()
            try:
                extractor.reset_cache()
            except Exception:
                pass
            extractor.extract_elements(int(actual_start), int(actual_end))

            extracted_elements = list(getattr(extractor, "extracted_elements", []) or [])
            if not extracted_elements:
                return {
                    "success": False,
                    "error": "문서 요소 추출 실패",
                }

            self._send_progress("stage", {"stage": "parsing", "message": "문서 분석 중..."})
            block_manager, id_to_pos = build_segment_registry_from_extractor(extractor)
            store_runtime_segment_manager(block_manager)
            store_runtime_id_location_mapping(id_to_pos)

            if self._content_modifier is not None:
                self._content_modifier.segment_registry = block_manager

            allowed_element_ids = sorted(int(raw_id) for raw_id in (id_to_pos or {}).keys())

            # 편집 허용 목록 저장 (execute_delta에서 사용)
            self._allowed_element_ids = allowed_element_ids
            self._current_doc_view = None

            # 5. v7.11: role 사전 분류 제거 → 페이지 범위만 적용
            self._form_regions = []
            self._design_table_groups = set()

            # 6. Document Graph 생성 (HWPML direct for LLM input)
            document_graph: Dict[str, Any] = {}
            document_graph_json = ""
            hwpml_text = ""
            page_by_pos: Dict[Tuple[int, int, int], int] = {}

            # 페이지 범위 saveblock 우선, 실패 시 전체 HWPML fallback
            hwpml_text = self._extract_hwpml_saveblock_for_page_range(
                extractor=extractor,
                start_page=int(actual_start),
                end_page=int(actual_end),
            )
            if not hwpml_text:
                try:
                    hwpml_text = hwp.GetTextFile("HWPML2X", "") or ""
                except Exception as hwpml_error:
                    print(f"[Python] HWPML 추출 실패: {hwpml_error}", file=sys.stderr)

            raw_page_map = getattr(extractor, "pos_to_page", None)
            if isinstance(raw_page_map, dict):
                for raw_pos, raw_page in raw_page_map.items():
                    try:
                        if not isinstance(raw_pos, (list, tuple)) or len(raw_pos) < 3:
                            continue
                        key = (int(raw_pos[0]), int(raw_pos[1]), int(raw_pos[2]))
                        page_no = int(raw_page)
                        if page_no > 0:
                            page_by_pos[key] = page_no
                    except Exception:
                        continue

            if block_manager:
                try:
                    document_graph = build_document_graph_from_hwpml(
                        hwpml_text=hwpml_text,
                        block_manager=block_manager,
                        id_to_pos=id_to_pos,
                        page_range=(actual_start, actual_end),
                        page_by_pos=page_by_pos or None,
                    )
                    # Enriched CVD: 토큰 효율적 HTML-like 마크업
                    document_graph_json = serialize_enriched_cvd(
                        document_graph,
                        page_range=(actual_start, actual_end),
                    )
                except Exception as graph_error:
                    print(f"[Python] document graph build failed: {graph_error}", file=sys.stderr)
                    document_graph = {}
                    document_graph_json = ""

            # target_uid <-> id 인덱스 캐시
            self._target_uid_to_id = {}
            self._id_to_target_uid = {}
            index_data = document_graph.get("index") if isinstance(document_graph, dict) else {}
            if isinstance(index_data, dict):
                by_target_uid = index_data.get("by_target_uid") or {}
                if isinstance(by_target_uid, dict):
                    for target_uid, block_id in by_target_uid.items():
                        try:
                            self._target_uid_to_id[str(target_uid)] = int(block_id)
                            self._id_to_target_uid[str(block_id)] = str(target_uid)
                        except Exception:
                            continue
            self._document_graph_json = document_graph_json or None

            # diagonal 셀 자식 노드를 allowed_element_ids에서 제외
            # diagonal="true" 셀 내부 <p> 노드는 절대 편집 금지 (코드 레벨 가드)
            try:
                _g_nodes = document_graph.get("nodes") or [] if isinstance(document_graph, dict) else []
                _g_edges = document_graph.get("edges") or [] if isinstance(document_graph, dict) else []
                _g_index = document_graph.get("index") or {} if isinstance(document_graph, dict) else {}
                _by_target_uid = _g_index.get("by_target_uid") or {}
                # diagonal td 노드의 target_uid 수집
                _diag_uids = {
                    n["target_uid"]
                    for n in _g_nodes
                    if n.get("diagonal") and n.get("block_type") == "td"
                }
                # diagonal td의 contains 엣지를 통해 자식 노드 ID 수집
                _diag_child_ids: set = set()
                for _edge in _g_edges:
                    if _edge.get("type") == "contains" and _edge.get("source") in _diag_uids:
                        _child_id = _by_target_uid.get(_edge.get("target"))
                        if _child_id is not None:
                            try:
                                _diag_child_ids.add(int(_child_id))
                            except (ValueError, TypeError):
                                pass
                if _diag_child_ids:
                    _prev_count = len(self._allowed_element_ids or [])
                    self._allowed_element_ids = [
                        eid for eid in (self._allowed_element_ids or [])
                        if eid not in _diag_child_ids
                    ]
                    print(
                        f"[Python] diagonal 자식 {len(_diag_child_ids)}개 편집 목록 제외 "
                        f"({_prev_count} → {len(self._allowed_element_ids)})",
                        file=sys.stderr,
                    )
            except Exception as _diag_err:
                print(f"[Python] diagonal child exclusion error: {_diag_err}", file=sys.stderr)

            # 7. 이전 CVD 필드 유지 (LLM 입력 미사용)
            html_content = ""
            cvd_text = ""

            # 8. 참조 자료 처리
            final_prompt = prompt
            if reference_file:
                try:
                    if reference_file.endswith('.txt'):
                        reader = TxtReader()
                        reference_content = reader.read(reference_file)
                    elif reference_file.endswith(('.xlsx', '.xls')):
                        reader = ExcelReader()
                        reference_content = reader.read(reference_file)
                    else:
                        reference_content = None

                    if reference_content:
                        reference_section = f"""
[참조 자료: {reference_file_name or '첨부 파일'}]
---
{reference_content}
---

위 참조 자료를 바탕으로 아래 요청을 수행해주세요:
"""
                        final_prompt = reference_section + prompt
                except Exception as e:
                    print(f"[Python] 참조 자료 로드 실패: {e}", file=sys.stderr)

            # 8. 세션 ID 생성
            import uuid
            context_id = str(uuid.uuid4())[:8]
            self._active_context_id = context_id

            self._send_progress("stage", {
                "stage": "context_ready",
                "message": "문서 분석 완료",
                "context_id": context_id,
                "elements": len(allowed_element_ids),
                "pages": f"{actual_start}-{actual_end}"
            })

            return {
                "success": True,
                "html": html_content,  # CVD (스타일 속성 포함)
                "cvd": html_content,
                "cvd_raw": cvd_text,
                "document_graph_json": document_graph_json,
                "prompt": final_prompt,
                "context_id": context_id,
                "allowed_elements": list(self._allowed_element_ids or []),
                "doc_view_data": {
                    "total_elements": len(id_to_pos),
                    "filtered_elements": len(self._allowed_element_ids or []),
                    "pages": f"{actual_start}-{actual_end}",
                    "mode": "graph_hwpml_v1",
                    "graph_nodes": len((document_graph or {}).get("nodes") or []),
                }
            }

        except Exception as e:
            print(f"[Python] prepare_context 에러: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return {"success": False, "error": str(e), "trace": traceback.format_exc()}

    def execute_delta(self, delta_data: dict) -> dict:
        """delta 형식 명령 실행 (executeMethod 패턴)

        Python Worker의 executeMethod 호출 형식으로 Delta를 처리합니다.

        Args:
            delta_data: delta 형식 명령
                {
                    "op": "method",
                    "method_type": "find_and_replace_in_paragraph",
                    "block_id": "13",
                    "old_text": "1월",
                    "new_text": "2월"
                }
                또는 스트리밍 Delta 형식:
                {
                    "action": "edit_document",
                    "id": 144,
                    "content": "새 내용",
                    "metadata": {"operation": "replace_cell_content"}
                }

        Returns:
            dict: 실행 결과
                - success: bool, 성공 여부
                - edited: bool, 실제 편집 여부
                - error: str, 에러 메시지 (실패 시)
        """
        try:
            context_id = delta_data.get("context_id") or delta_data.get("contextId")
            if context_id:
                if not self._active_context_id or context_id != self._active_context_id:
                    return {"success": False, "edited": False, "error": "stale_context"}

            # ContentModifier 사용
            editor = self._ensure_content_modifier()
            if self._diff_mode_enabled:
                actual = None
                try:
                    actual = self._ensure_connector().get_track_changes_state()
                except Exception:
                    actual = None
                if actual is not True:
                    self._ensure_track_changes_state(True)

            # method_type 추출 (두 가지 형식 지원)
            method_type = delta_data.get("method_type")
            metadata = delta_data.get("metadata") or {}
            signature_meta = self._extract_signature_meta(delta_data, metadata)
            if not method_type:
                # 스트리밍 Delta 형식: metadata.operation을 method_type으로 사용
                method_type = metadata.get("operation")
            if not method_type:
                action = delta_data.get("action")
                if action == "append_table_row":
                    method_type = "append_table_row"

            # block_id / id 추출 (표 스코프 처리 포함)
            raw_block_id = delta_data.get("block_id") or delta_data.get("id")
            scope_table_id, element_id = self._parse_block_scope(raw_block_id)
            if scope_table_id is None and signature_meta.get("scope_table_id") is not None:
                scope_table_id = signature_meta.get("scope_table_id")
            block_id = str(element_id) if element_id is not None else raw_block_id

            # content / new_text 추출
            new_text = delta_data.get("new_text") or delta_data.get("content")

            # old_text 추출 (metadata에도 있을 수 있음)
            old_text = delta_data.get("old_text") or metadata.get("old_text") or metadata.get("find") or metadata.get("text")

            # row_texts 추출
            row_texts = delta_data.get("row_texts")
            if not row_texts:
                row_texts = delta_data.get("rows")
            if not row_texts:
                row_texts = metadata.get("row_texts")

            block_manager = getattr(editor, "segment_registry", None)
            block = None
            block_type = None
            if block_manager and block_id is not None:
                try:
                    block = block_manager.get_block(str(block_id))
                    if block:
                        block_type = getattr(block, "block_type", None)
                except Exception:
                    block_type = None

            # method_type이 없으면 블록 타입 기반으로 추론
            if not method_type:
                if block_type == "td":
                    method_type = "replace_cell_content"
                elif block_type == "list":
                    method_type = "replace_list"
                elif block_type in ("footnote_anchor", "footnote_content", "footnote"):
                    method_type = "replace_footnote"
                else:
                    method_type = "replace_paragraph"

            # target contract 검증 (target_uid + id + signature/table metadata)
            # 모델이 target_uid/meta를 누락해도 id 기준 런타임 정보로 보강한다.
            target_contract = self._extract_target_contract(delta_data, metadata)
            if method_type != "find_and_replace_all":
                target_contract = self._merge_target_contract_with_runtime(
                    target_contract=target_contract,
                    element_id=element_id,
                    block_manager=block_manager,
                )

            contract_ok, contract_reason = self._validate_target_contract(
                block_manager=block_manager,
                element_id=element_id,
                contract=target_contract,
            )
            if not contract_ok:
                skip_reason = contract_reason or "target_contract_mismatch"
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": skip_reason,
                    "operation": method_type,
                })
                return {"success": True, "edited": False, "skipped": True, "reason": skip_reason}

            # 편집 허용 및 스코프 확인
            original_method_type = method_type
            if block_type == "td" and method_type in ("replace_paragraph", "append_paragraph"):
                method_type = "replace_cell_content"
            elif block_type == "td" and method_type == "delete_paragraph":
                method_type = "delete_cell_content"
            elif block_type == "list" and method_type in ("replace_paragraph", "append_paragraph", "delete_paragraph"):
                list_map = {
                    "replace_paragraph": "replace_list",
                    "append_paragraph": "append_list",
                    "delete_paragraph": "delete_list",
                }
                method_type = list_map.get(method_type, method_type)
            elif block_type in ("text", "list") and method_type in ("replace_cell_content", "delete_cell_content"):
                if method_type == "replace_cell_content":
                    method_type = "replace_paragraph" if block_type == "text" else "replace_list"
                else:
                    method_type = "delete_paragraph" if block_type == "text" else "delete_list"

            if original_method_type != method_type:
                try:
                    from utils.logger import debug as log_debug
                    log_debug(
                        f"[execute_delta] Coerced method: {original_method_type} -> {method_type} (id={element_id}, type={block_type})"
                    )
                except Exception:
                    pass

            if method_type != "find_and_replace_all":
                if not self._is_block_id_allowed(element_id):
                    print(f"[execute_delta] Block ID not allowed: id={element_id}, method={method_type}, allowed_ids count={len(self._allowed_element_ids) if hasattr(self, '_allowed_element_ids') else 'MISSING'}", file=sys.stderr)
                    try:
                        from utils.logger import debug as log_debug
                        log_debug(f"[execute_delta] Block ID not allowed: id={element_id}, method={method_type}")
                    except Exception:
                        pass
                    self._send_progress("edit_skipped", {
                        "id": element_id,
                        "reason": "out_of_scope",
                        "operation": method_type,
                    })
                    return {"success": True, "edited": False, "skipped": True, "reason": "out_of_scope"}

                if not self._is_block_scope_valid(scope_table_id, element_id, editor):
                    print(f"[execute_delta] Block scope mismatch: scope={scope_table_id}, id={element_id}, method={method_type}", file=sys.stderr)
                    try:
                        from utils.logger import debug as log_debug
                        log_debug(f"[execute_delta] Block scope mismatch: scope={scope_table_id}, id={element_id}, method={method_type}")
                    except Exception:
                        pass
                    self._send_progress("edit_skipped", {
                        "id": element_id,
                        "reason": "block_scope_mismatch",
                        "operation": method_type,
                    })
                    return {"success": True, "edited": False, "skipped": True, "reason": "block_scope_mismatch"}

            if not self._is_editable_for_method(method_type, block_type):
                print(f"[execute_delta] Block type not allowed: id={element_id}, block_type={block_type}, method={method_type}", file=sys.stderr)
                try:
                    from utils.logger import debug as log_debug
                    log_debug(f"[execute_delta] Block type not allowed: id={element_id}, block_type={block_type}, method={method_type}")
                except Exception:
                    pass
                return {
                    "success": False,
                    "edited": False,
                    "error": f"Block type '{block_type}' not allowed for {method_type}",
                }

            # CVD F/D 명령 처리
            if method_type in ("replace", "delete"):
                if not old_text:
                    return {"success": False, "error": "Missing old_text for replace/delete"}
                if method_type == "delete":
                    new_text = ""
                success = editor.search_and_replace_in_paragraph(
                    block_id, old_text=old_text, new_text=new_text or ""
                )
                self._mark_tracked_edit(success)
                return {"success": success, "replaced_count": 1 if success else 0}

            # 문단/리스트 편집
            if method_type == "replace_paragraph":
                success = editor.replace_paragraph_content(block_id, new_text=new_text)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "append_paragraph":
                append_paragraph_fn = getattr(editor, "append_paragraph", None)
                if not callable(append_paragraph_fn):
                    append_paragraph_fn = getattr(editor, "append_paragraph_content", None)
                if not callable(append_paragraph_fn):
                    append_paragraph_fn = getattr(editor, "append_to_paragraph", None)
                if not callable(append_paragraph_fn):
                    return {"success": False, "edited": False, "error": "append_paragraph handler not found (append_paragraph)"}
                success = append_paragraph_fn(block_id, new_text=new_text)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_paragraph":
                delete_paragraph_fn = getattr(editor, "delete_paragraph_content", None)
                if callable(delete_paragraph_fn):
                    success = delete_paragraph_fn(block_id)
                else:
                    success = editor.delete_paragraph(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "replace_list":
                success = editor.replace_list(block_id, new_text=new_text)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "append_list":
                success = editor.append_list(block_id, new_text=new_text)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_list":
                delete_list_fn = getattr(editor, "delete_list", None)
                if callable(delete_list_fn):
                    success = delete_list_fn(block_id)
                else:
                    success = editor.remove_list(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 셀 편집
            elif method_type == "replace_cell_content":
                success = editor.replace_cell_content(
                    block_id, new_text=new_text, old_text=old_text
                )
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_cell_content":
                success = editor.delete_cell_content(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 표 행 편집
            elif method_type == "append_table_row":
                success = editor.append_table_row(block_id, row_texts=row_texts)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "replace_table_row":
                success = editor.replace_table_row(block_id, row_texts=row_texts)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_table_row":
                success = editor.delete_table_row(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 표 생성/삭제
            elif method_type == "create_table":
                success = editor.create_table(block_id, row_texts=row_texts)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_table":
                delete_table_fn = getattr(editor, "delete_table", None)
                if callable(delete_table_fn):
                    success = delete_table_fn(block_id)
                else:
                    success = editor.remove_table(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 텍스트박스 삭제
            elif method_type == "delete_textbox":
                delete_textbox_fn = getattr(editor, "delete_textbox", None)
                if callable(delete_textbox_fn):
                    success = delete_textbox_fn(block_id)
                else:
                    success = editor.remove_textbox(block_id)
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 각주 편집
            elif method_type == "insert_footnote":
                footnote_anchor_text = (
                    delta_data.get("footnote_anchor_text")
                    or metadata.get("footnote_anchor_text")
                    or old_text
                )
                footnote_text = (
                    delta_data.get("footnote_text")
                    or metadata.get("footnote_text")
                    or new_text
                    or ""
                )

                insert_footnote_fn = getattr(editor, "insert_footnote", None)
                if not callable(insert_footnote_fn):
                    insert_footnote_fn = getattr(editor, "insert_annotation", None)
                if callable(insert_footnote_fn):
                    success = insert_footnote_fn(
                        block_id,
                        footnote_anchor_text=footnote_anchor_text,
                        footnote_text=footnote_text,
                    )
                else:
                    try:
                        hwp = self._get_hwp_instance()
                        target_pos = None
                        pos_getter = getattr(editor, "_retrieve_segment_position", None)
                        if callable(pos_getter):
                            try:
                                target_pos = pos_getter(block_id)
                            except Exception:
                                target_pos = None
                        if not target_pos and block_manager and block_id is not None:
                            try:
                                target_pos = block_manager.get_position(str(block_id))
                            except Exception:
                                target_pos = None
                        if isinstance(target_pos, (list, tuple)) and len(target_pos) >= 3:
                            hwp.set_pos(int(target_pos[0]), int(target_pos[1]), int(target_pos[2]))
                        hwp.HAction.Run("InsertFootnote")
                        hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                        hwp.HParameterSet.HInsertText.Text = str(footnote_text)
                        hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
                        hwp.HAction.Run("CloseEx")
                        success = True
                    except Exception as e:
                        print(f"[insert_footnote] 실패: {e}", file=sys.stderr)
                        success = False

                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "replace_footnote":
                replace_footnote_fn = getattr(editor, "replace_footnote", None)
                if not callable(replace_footnote_fn):
                    replace_footnote_fn = getattr(editor, "replace_annotation", None)
                if callable(replace_footnote_fn):
                    success = replace_footnote_fn(block_id, new_text=new_text)
                else:
                    success = editor.replace_paragraph_content(block_id, new_text=new_text or "")
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "delete_footnote":
                delete_footnote_fn = getattr(editor, "delete_footnote", None)
                if not callable(delete_footnote_fn):
                    delete_footnote_fn = getattr(editor, "remove_annotation", None)
                if callable(delete_footnote_fn):
                    success = delete_footnote_fn(block_id)
                else:
                    success = editor.replace_paragraph_content(block_id, new_text="")
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 스타일 적용
            elif method_type == "apply_para_style":
                font_size = delta_data.get("font_size") or metadata.get("font_size")
                font_family = delta_data.get("font_family") or metadata.get("font_family")
                align = delta_data.get("align") or metadata.get("align")
                spacing = delta_data.get("spacing") or metadata.get("spacing")
                indentation = delta_data.get("indentation") or metadata.get("indentation")
                success = editor.apply_para_style(
                    block_id,
                    font_size=font_size,
                    font_family=font_family,
                    align=align,
                    spacing=spacing,
                    indentation=indentation
                )
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            elif method_type == "apply_charshape":
                bold = delta_data.get("bold")
                italic = delta_data.get("italic")
                font_size = delta_data.get("font_size") or metadata.get("font_size")
                font_family = delta_data.get("font_family") or metadata.get("font_family")
                if bold is None:
                    bold = metadata.get("bold")
                if italic is None:
                    italic = metadata.get("italic")
                success = editor.apply_charshape(
                    block_id,
                    bold=bold,
                    italic=italic,
                    font_size=font_size,
                    font_family=font_family
                )
                self._mark_tracked_edit(success)
                return {"success": success, "edited": success}

            # 각주 삽입
            elif method_type == "add_footnote":
                footnote_text = delta_data.get("text", "")
                try:
                    hwp = self._get_hwp_instance()
                    hwp.HAction.Run("InsertFootnote")
                    # 텍스트 입력
                    hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HParameterSet.HInsertText.Text = footnote_text
                    hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HAction.Run("CloseEx")
                    self._mark_tracked_edit(True)
                    return {"success": True, "edited": True}
                except Exception as e:
                    print(f"[add_footnote] 실패: {e}", file=sys.stderr)
                    return {"success": False, "error": str(e)}

            # 미주 삽입
            elif method_type == "add_endnote":
                endnote_text = delta_data.get("text", "")
                try:
                    hwp = self._get_hwp_instance()
                    hwp.HAction.Run("InsertEndnote")
                    # 텍스트 입력
                    hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HParameterSet.HInsertText.Text = endnote_text
                    hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HAction.Run("CloseEx")
                    self._mark_tracked_edit(True)
                    return {"success": True, "edited": True}
                except Exception as e:
                    print(f"[add_endnote] 실패: {e}", file=sys.stderr)
                    return {"success": False, "error": str(e)}

            # 출처 참조 각주
            elif method_type == "add_source_ref":
                ref_id = delta_data.get("ref_id", "")
                title = delta_data.get("title", "")
                publisher = delta_data.get("publisher", "")
                year = delta_data.get("year", "")
                url = delta_data.get("url", "")
                try:
                    hwp = self._get_hwp_instance()
                    hwp.HAction.Run("InsertFootnote")
                    # 출처 형식화
                    source_text = f"[{ref_id}] {title}"
                    if publisher:
                        source_text += f", {publisher}"
                    if year:
                        source_text += f" ({year})"
                    if url:
                        source_text += f", {url}"
                    # 텍스트 입력
                    hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HParameterSet.HInsertText.Text = source_text
                    hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
                    hwp.HAction.Run("CloseEx")
                    self._mark_tracked_edit(True)
                    return {"success": True, "edited": True}
                except Exception as e:
                    print(f"[add_source_ref] 실패: {e}", file=sys.stderr)
                    return {"success": False, "error": str(e)}

            # 줄바꿈
            elif method_type == "line_break":
                try:
                    hwp = self._get_hwp_instance()
                    hwp.HAction.Run("BreakPara")
                    self._mark_tracked_edit(True)
                    return {"success": True, "edited": True}
                except Exception as e:
                    print(f"[line_break] 실패: {e}", file=sys.stderr)
                    return {"success": False, "error": str(e)}

            # 찾기/바꾸기
            elif method_type == "find_and_replace_in_paragraph":
                if not old_text or old_text == new_text:
                    return {"success": True, "edited": False, "skipped": True, "reason": "no_op_replace"}
                success = editor.search_and_replace_in_paragraph(
                    block_id, old_text=old_text, new_text=new_text
                )
                self._mark_tracked_edit(success)
                return {"success": success, "replaced_count": 1 if success else 0}

            elif method_type == "find_and_replace_all":
                success = editor.find_and_replace_all(old_text, new_text)
                self._mark_tracked_edit(success)
                return {"success": success, "replaced_count": 1 if success else 0}

            else:
                return {"success": False, "error": f"Unknown method_type: {method_type}"}

        except Exception as e:
            print(f"[Python] execute_delta 에러: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"success": False, "error": str(e)}

    def _normalize_newlines(self, text: str) -> str:
        """줄바꿈 문자 표준화 (기존 패턴)

        CRLF/CR/리터럴 "\\n"/HTML 태그/유니코드 개행을 모두 \\n으로統一处理

        Args:
            text: 원본 텍스트

        Returns:
            표준화된 텍스트
        """
        if not text:
            return text

        # HTML 줄바꿈 태그 → LF (Delta 모드에서 streaming_client.py가 생성)
        text = text.replace('<br/>', '\n')
        text = text.replace('<br>', '\n')
        text = text.replace('<BR/>', '\n')
        text = text.replace('<BR>', '\n')
        # CR+LF → LF
        text = text.replace('\r\n', '\n')
        # CR → LF
        text = text.replace('\r', '\n')
        # 리터럴 \\n → LF
        text = text.replace('\\n', '\n')
        # 리터럴 \\t → 탭
        text = text.replace('\\t', '\t')
        # 유니코드 개행 문자들 → LF
        text = text.replace('\u2028', '\n')  # Line Separator
        text = text.replace('\u2029', '\n')  # Paragraph Separator
        text = text.replace('\u0085', '\n')  # NEL (Next Line)

        return text

    def _insert_with_style(self, connector, text: str) -> None:
        """마크다운 마크업을 해석해 서식 적용하여 삽입 (기존 패턴)

        지원 마크업:
        - **텍스트**  : 굵게(Bold)
        - *텍스트*   : 이탤릭(Italic)
        - ==텍스트== : 하이라이트(형광펜)

        동작 원칙:
        - 항상 "텍스트를 먼저 삽입 → 해당 구간만 선택 → 스타일 적용" 순서로 처리
        - 마크업이 한 번이라도 등장하면 Bold/Italic 상태를 명시적으로 on/off
        - 마크업이 없는 문자열도 스타일 off 처리하여 이전 스타일 번짐 방지
        - 줄바꿈 문자(\n)가 포함된 경우 각 줄을 개별 삽입하고 BreakPara()로 문단 분리

        Args:
            connector: HWP 커넥터
            text: 삽입할 텍스트
        """
        if text is None:
            text = ""

        if not text:
            return

        import re

        # 줄바꿈 정규화
        text = self._normalize_newlines(text)

        # 줄바꿈이 포함된 경우 각 줄을 개별 처리
        if "\n" in text:
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if i > 0:
                    # 두 번째 줄부터는 문단 분리
                    try:
                        connector.break_para()
                    except Exception:
                        # break_para가 없으면 run_action 시도
                        try:
                            connector.run_action("BreakPara")
                        except Exception as e:
                            print(f"[_insert_with_style] BreakPara 실패: {e}", file=sys.stderr)
                # 각 줄을 재귀 호출로 삽입 (빈 줄은 공백으로 처리)
                self._insert_with_style(connector, line if line else " ")
            return

        # 마크업 존재 여부 판단
        has_bold_markup = "**" in text
        has_italic_markup = bool(re.search(r"\*[^*\n]+\*", text))
        has_highlight_markup = "==" in text

        # 상태 플래그
        is_bold = False
        is_italic = False
        is_highlight = False

        # 마크업이 전혀 없는 경우 (저장→설정→입력→복원 패턴)
        if not has_bold_markup and not has_italic_markup and not has_highlight_markup:
            try:
                hwp = connector.hwp
                # 1. 현재 입력 모양(CharShape) 저장
                base = hwp.HParameterSet.HCharShape
                hwp.HAction.GetDefault("CharShape", base.HSet)

                # 2. 작업용 CharShape - 기본 스타일로 명시적 설정
                work = hwp.HParameterSet.HCharShape
                hwp.HAction.GetDefault("CharShape", work.HSet)
                work.Bold = 0
                work.Italic = 0

                # 3. 현재 입력 모양을 work로 변경
                hwp.HAction.Execute("CharShape", work.HSet)

                # 4. 텍스트 입력
                hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                hwp.HParameterSet.HInsertText.Text = text
                hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)

                # 5. 현재 입력 모양을 원래대로 복원
                hwp.HAction.Execute("CharShape", base.HSet)
            except Exception as e:
                print(f"[_insert_with_style] 단순 삽입 실패: {e}", file=sys.stderr)
                connector.insert_text(text)
            return

        # 버퍼: 현재 스타일로 삽입할 텍스트
        buffer = []

        def flush_buffer():
            """현재 버퍼 내용을 현재 스타일로 삽입 (저장→설정→입력→복원 패턴)"""
            if not buffer:
                return

            segment = "".join(buffer)
            buffer.clear()

            try:
                hwp = connector.hwp

                # 1. 현재 입력 모양(CharShape) 저장
                base = hwp.HParameterSet.HCharShape
                hwp.HAction.GetDefault("CharShape", base.HSet)

                # 2. 작업용 CharShape 설정
                work = hwp.HParameterSet.HCharShape
                hwp.HAction.GetDefault("CharShape", work.HSet)

                # 스타일 적용
                if has_bold_markup:
                    work.Bold = 1 if is_bold else 0
                if has_italic_markup:
                    work.Italic = 1 if is_italic else 0

                # 3. 현재 입력 모양을 work로 변경
                hwp.HAction.Execute("CharShape", work.HSet)

                # 4. 텍스트 입력
                hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
                hwp.HParameterSet.HInsertText.Text = segment
                hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)

                # 형광펜은 선택 후 적용 필요 (별도 처리)
                if is_highlight:
                    try:
                        end_pos = connector.get_pos()
                        # 방금 입력한 텍스트 선택
                        for _ in range(len(segment)):
                            hwp.HAction.Run("MovePrevChar")
                        for _ in range(len(segment)):
                            hwp.HAction.Run("MoveSelNextChar")
                        connector.hwp.markpen_on_selection(r=255, g=255, b=0)
                        connector.Cancel()
                        connector.set_pos(*end_pos)
                    except Exception:
                        pass

                # 5. 현재 입력 모양을 원래대로 복원
                hwp.HAction.Execute("CharShape", base.HSet)

            except Exception as e:
                print(f"[_insert_with_style] flush_buffer 실패: {e}", file=sys.stderr)
                # 폴백: 단순 삽입
                try:
                    connector.insert_text(segment)
                except Exception:
                    pass

        i = 0
        length = len(text)

        while i < length:
            ch = text[i]

            # 우선 순위: **, == (2글자 토큰) → *, = (단일 문자)
            if i + 1 < length:
                two = text[i : i + 2]
                if two == "**":
                    flush_buffer()
                    is_bold = not is_bold
                    i += 2
                    continue
                if two == "==":
                    flush_buffer()
                    is_highlight = not is_highlight
                    i += 2
                    continue

            # 단일 * 체크: 다음 문자가 *가 아닐 때만 이탤릭 토글
            if ch == "*" and (i + 1 >= length or text[i + 1] != "*"):
                flush_buffer()
                is_italic = not is_italic
                i += 1
                continue

            # 일반 문자
            buffer.append(ch)
            i += 1

        # 남은 버퍼 플러시
        flush_buffer()

    def _set_font_style(self, connector, bold=None, italic=None) -> bool:
        """폰트 스타일 설정 (기존 패턴)

        Args:
            connector: HWP 커넥터
            bold: True/False/None (None이면 설정 안함)
            italic: True/False/None (None이면 설정 안함)

        Returns:
            bool: 성공 여부
        """
        try:
            if hasattr(connector, 'hwp') and hasattr(connector.hwp, 'set_font'):
                kwargs = {}
                if bold is not None:
                    kwargs['Bold'] = bold
                if italic is not None:
                    kwargs['Italic'] = italic
                if kwargs:
                    connector.hwp.set_font(**kwargs)
                return True
            return False
        except Exception as e:
            print(f"[_set_font_style] 실패: {e}", file=sys.stderr)
            return False

    def _apply_highlight(self, connector, r: int, g: int, b: int) -> bool:
        """형광펜(하이라이트) 적용 (기존 패턴)

        Args:
            connector: HWP 커넥터
            r, g, b: RGB 값 (0-255)

        Returns:
            bool: 성공 여부
        """
        try:
            if hasattr(connector, 'hwp') and hasattr(connector.hwp, 'markpen_on_selection'):
                connector.hwp.markpen_on_selection(r=r, g=g, b=b)
                return True
            return False
        except Exception as e:
            # 형광펜 적용 실패는 치명적이지 않으므로 무시
            print(f"[_apply_highlight] 실패 (무시됨): {e}", file=sys.stderr)
            return False

    def _align_leading_spaces(self, old_text: str, new_text: Optional[str]) -> tuple:
        """선행 공백 정렬 (기존 패턴)

        old_text와 new_text의 공통 선행 공백(min)을 기준으로 new_text 앞 공백을 정렬

        Args:
            old_text: 기존 텍스트
            new_text: 새 텍스트

        Returns:
            tuple: (trimmed_new_text, adjust_n, old_leading, new_leading)
            - trimmed_new_text: 공통 선행 공백만 제거된 new_text
            - adjust_n: 공통 선행 공백 수
            - old_leading: old_text의 선행 공백 수
            - new_leading: new_text의 선행 공백 수
        """
        if new_text is None:
            new_text = ""

        # 선행 공백 카운트
        old_leading = len(old_text) - len(old_text.lstrip(' '))
        new_leading = len(new_text) - len(new_text.lstrip(' '))

        # 공통 선행 공백 수 (최소값)
        adjust_n = min(old_leading, new_leading)

        # new_text에서 공통 선행 공백만 제거
        trimmed_new_text = new_text[adjust_n:] if adjust_n > 0 else new_text

        return (trimmed_new_text, adjust_n, old_leading, new_leading)

    def _detect_prefix(self, text: Optional[str]) -> Optional[str]:
        """글머리 기호/번호 접두사 감지 (기존 패턴)

        문단 시작부의 글머리 기호(+뒤 공백) 접두 문자열을 반환. 없으면 None

        지원 글머리/접두 패턴:
        - 숫자: 1. 1) 1: (한자리~세자리)
        - 알파벳: a. a) A. A) a: A:
        - 한글: 가. 가) 나. 나)
        - 로마숫자: i. ii. iii. I. II. III. + ), :
        - 원문자: ① ② ③ ㉠ ㉡ ㉢ Ⓐ Ⓑ ⓐ ⓑ 등
        - 불릿 기호: - • ◦ ∙ □ ○ ● ◆ ◇ ■ ▪ * ► ▸ → ⇒ 등

        Args:
            text: 문단 텍스트

        Returns:
            Optional[str]: 접두사 문자열 (공백 포함) 또는 None
        """
        import re

        if not text:
            return None

        # 글머리 기호 패턴 정규식 (기존 패턴 기반)
        PREFIX_PATTERN = re.compile(
            r"^(\s*)"  # 선행 공백
            r"("
            # 숫자 번호: 1. 1) 1:
            r"\d{1,3}[.):]\s+"
            # 알파벳 번호: a. a) A. A) a: A:
            r"|[a-zA-Z][.):]\s+"
            # 한글 번호: 가. 가) 나. 나)
            r"|[가-힣][.)]\s+"
            # 로마숫자: i. ii. iii. iv. v. ... (대소문자)
            r"|(?:[ivxIVX]{1,4})[.):]\s+"
            # 원문자/원형 번호: ①② ㉠㉡ Ⓐⓐ 등
            r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑-㉟Ⓐ-Ⓩⓐ-ⓩ㉮-㉻]\s*"
            # 불릿 기호
            r"|[-–—•◦∙‣⁃‧·□○●◉⦿⊙◆◇■▪▫*►▸▹▻▶▷➤➔→⇒]\s+"
            r")"
        )

        match = PREFIX_PATTERN.match(text)
        if match:
            return match.group(0)
        return None

    def _select_text_by_find(self, connector, text: str) -> bool:
        """텍스트 검색으로 선택 (기존 패턴)

        find() 메서드로 텍스트를 찾아 선택 상태로 만듦

        Args:
            connector: HWP 커넥터
            text: 찾을 텍스트

        Returns:
            bool: 찾기 성공 여부
        """
        try:
            if hasattr(connector, 'hwp') and hasattr(connector.hwp, 'find'):
                return connector.hwp.find(text, direction="Forward", regex=False)
            return False
        except Exception as e:
            print(f"[_select_text_by_find] 실패: {e}", file=sys.stderr)
            return False

    def _execute_edit_delta(self, delta_data: dict) -> dict:
        """편집 Delta 실행 (기존 패턴 적용)

        모든 편집 작업의 통합 진입점으로, 전처리 후 실제 편집 수행
        """
        element_id = delta_data.get("id")
        content = delta_data.get("content")

        if element_id is None:
            return {"success": False, "error": "element_id required"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        element_id = int(element_id)
        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # 기존 패턴: 줄바꿈 표준화
        content = self._normalize_newlines(content)

        # HWP 편집 실행
        connector = self._ensure_connector()

        # DocumentBlockManager 사용 (위치 조정)
        if not hasattr(self, '_block_manager'):
            self._block_manager = DocumentBlockManager(doc_view.position_map)

        block_manager = self._block_manager
        adjusted_position = block_manager.get_adjusted_position(element_id)

        if adjusted_position is None:
            adjusted_position = element.position

        # 문단 시작점으로 이동
        if not connector.set_pos(adjusted_position[0], adjusted_position[1], 0):
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Position move failed"
            })
            return {"success": False, "error": "Position move failed"}

        old_content = element.content if isinstance(element.content, str) else ""

        # HTML 태그 제거 (순수 텍스트 비교)
        import re
        old_text_clean = re.sub(r'<[^>]+>', '', old_content).strip()
        new_text_clean = content.strip()

        old_length = len(old_content)
        new_length = len(content)

        # **방어 로직: 기존 내용이 있는 셀은 편집 거부**
        if old_text_clean and new_text_clean:
            # 둘 다 내용이 있음 → 기존 내용을 보존해야 함
            # LLM이 프롬프트를 무시하고 명령을 생성한 경우
            print(f"[Edit] ⚠️ 편집 거부 (기존 내용 보호): ID={element_id}", file=sys.stderr)
            print(f"       기존: {old_text_clean[:50]}", file=sys.stderr)
            print(f"       요청: {new_text_clean[:50]}", file=sys.stderr)

            self._send_progress("edit_skipped", {
                "id": element_id,
                "reason": "Non-empty cell protection",
                "old_content": old_text_clean[:100]
            })
            return {"success": True, "edited": False, "skipped": True, "reason": "non_empty_protection"}

        # **빈 셀: 그냥 삽입**
        if not old_text_clean:
            # 기존 패턴: 스타일 보존하며 삽입
            self._insert_with_style(connector, content)
            print(f"[Edit] ✅ 빈 셀 {element_id}에 삽입: {content[:50]}", file=sys.stderr)

            # DocumentBlockManager 업데이트
            block_manager.update_after_edit(element_id, old_length, new_length)

            self._send_progress("edit_success", {"id": element_id})

            return {"success": True, "edited": True}
        else:
            # 이 경로는 발생하지 않아야 함 (위에서 막힘)
            # 혹시 모를 경우를 대비한 안전장치
            print(f"[Edit] ❌ 예상치 못한 경로: old={bool(old_text_clean)}, new={bool(new_text_clean)}", file=sys.stderr)
            return {"success": False, "error": "Unexpected edit path"}

    def _execute_append_table_row(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 표 행 추가

        Args:
            delta_data: {
                "block_id": "42",
                "row_texts": [["셀1", "셀2"], ["셀3", "셀4"]]
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        row_texts = delta_data.get("row_texts", [])

        if block_id is None:
            return {"success": False, "error": "append_table_row requires block_id"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # HWP 연결
        connector = self._ensure_connector()

        # 기존 패턴: 연속 표 행 추가 감지
        # last_insert_info가 있고 같은 block_id/type이면 마지막 행 위치 사용
        use_last_position = False
        if (
            self._last_insert_info
            and self._last_insert_info.get("block_id") == block_id
            and self._last_insert_info.get("type") == "table_row"
        ):
            use_last_position = True
            list_pos = self._last_insert_info["last_row_list_pos"]
            print(f"[AppendTableRow] 연속 호출 감지 - 마지막 행 위치 사용: L:{list_pos}", file=sys.stderr)
        else:
            # 연속 삽입 정보 초기화
            self._last_insert_info = None

        try:
            if use_last_position:
                # 연속 삽입: 마지막 행의 위치 사용
                if not connector.set_pos(list_pos, 0, 0):
                    return {"success": False, "error": "Failed to move to last row position"}
            else:
                # 첫 삽입: Element 좌표로 이동
                coords = doc_view.get_element_coords(block_id)
                if not coords:
                    return {"success": False, "error": "Element coordinates not found"}

                section_idx, para_idx, char_idx = coords

                # 기존 패턴: 표 셀 위치로 이동
                if not connector.set_pos(section_idx, para_idx, char_idx):
                    return {"success": False, "error": "Failed to move to element position"}

            rows_added = 0
            new_row_start_list = 0

            # row_texts 구조 처리: [[셀1, 셀2, ...], [셀3, 셀4, ...], ...]
            if row_texts and isinstance(row_texts, list):
                for row_idx, row_data in enumerate(row_texts):
                    # 2022-호환: TableAppendRow로 행 추가 (TableLowerCell로 이동 포함)
                    if connector.table_append_row():
                        rows_added += 1

                        # 기존 패턴: 현재 행의 위치 정보 얻기 (move_pos 104/105)
                        try:
                            connector.run_action("MoveRowBegin")  # 104: 행의 처음
                            row_start_pos = connector.get_pos()
                            if row_start_pos:
                                new_row_start_list = row_start_pos[0]
                        except Exception:
                            pass

                        # 각 셀에 내용 입력
                        if isinstance(row_data, list):
                            for cell_idx, cell_text in enumerate(row_data):
                                # 셀 내용 삽입
                                self._insert_with_style(connector, str(cell_text))

                                # 마지막 셀이 아니면 다음 셀로 이동
                                if cell_idx < len(row_data) - 1:
                                    # 2022-호환: TableRightCell로 다음 셀 이동
                                    connector.table_right_cell()
                        else:
                            # row_data가 단일 값인 경우
                            self._insert_with_style(connector, str(row_data))

                        print(f"[AppendTableRow] ✅ 행 {row_idx + 1} 추가 완료", file=sys.stderr)
                    else:
                        print(f"[AppendTableRow] ⚠️ 행 추가 실패 (row {rows_added + 1})", file=sys.stderr)

            # 기존 패턴: 마지막 행 위치 저장 (연속 호출 대비)
            if rows_added > 0 and new_row_start_list > 0:
                self._last_insert_info = {
                    "block_id": block_id,
                    "type": "table_row",
                    "last_row_list_pos": new_row_start_list,
                }

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "append_table_row",
                "rows_count": rows_added
            })

            return {"success": True, "edited": True, "rows_count": rows_added}

        except Exception as e:
            print(f"[AppendTableRow] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "append_table_row"
            })

            return {"success": False, "error": str(e)}

    def _execute_replace_paragraph(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 문단 내용 교체

        Args:
            delta_data: {
                "block_id": "42",
                "new_text": "새로운 문단 내용"
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        new_text = delta_data.get("new_text")

        if block_id is None or new_text is None:
            return {"success": False, "error": "replace_paragraph requires block_id, new_text"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 기존 패턴: 문단 시작으로 이동
            if not connector.set_pos(section_idx, para_idx, 0):
                return {"success": False, "error": "Failed to move to element position"}

            # 기존 내용 가져오기 (스타일 보존을 위해)
            old_content = element.content if hasattr(element, 'content') else ""
            new_text = self._normalize_newlines(new_text)

            # 기존 패턴: 선행 공백(들여쓰기) 감지 및 보존
            import re
            leading_space_match = re.match(r'^(\s*)', old_content)
            leading_space = leading_space_match.group(1) if leading_space_match else ""

            # 2022-호환: MoveSelParaEnd로 문단 끝까지 선택
            connector.MoveSelParaEnd()

            # 2022-호환: DeleteBack으로 선택 삭제
            connector.delete_back()

            # 기존 패턴: 선행 공백 보존하며 삽입
            final_text = leading_space + new_text.lstrip() if leading_space else new_text

            # 새로운 내용 삽입 (스타일 보존)
            self._insert_with_style(connector, final_text)

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "replace_paragraph",
                "new_length": len(new_text)
            })

            return {"success": True, "edited": True, "new_length": len(new_text)}

        except Exception as e:
            print(f"[ReplaceParagraph] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "replace_paragraph"
            })

            return {"success": False, "error": str(e)}

    def _execute_replace_list(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 리스트 항목 교체

        Args:
            delta_data: {
                "block_id": "42",
                "new_text": "새로운 리스트 항목"
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        new_text = delta_data.get("new_text")

        if block_id is None or new_text is None:
            return {"success": False, "error": "replace_list requires block_id, new_text"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 기존 패턴: 리스트 항목 시작으로 이동
            if not connector.set_pos(section_idx, para_idx, 0):
                return {"success": False, "error": "Failed to move to element position"}

            # 기존 내용 가져오기 (글머리 기호 보존을 위해)
            old_content = element.content if hasattr(element, 'content') else ""
            new_text = self._normalize_newlines(new_text)

            # 기존 패턴: 글머리 기호/번호 접두사 감지 및 보존
            import re
            # 불릿/번호 패턴: •, ○, ▪, 1. 2. a) b) 등
            bullet_pattern = r'^(\s*(?:[•○▪◦▸►▹▶◆◇■□●]|[\d]+[.)]\s*|[a-zA-Z][.)]\s*|[가나다라마바사아자차카타파하][.)]\s*|[ⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+[.)]\s*))'
            bullet_match = re.match(bullet_pattern, old_content)
            bullet_prefix = bullet_match.group(1) if bullet_match else ""

            # 2022-호환: MoveParaBegin으로 문단 시작점 확인
            connector.MoveParaBegin()

            # 2022-호환: MoveSelParaEnd로 문단 끝까지 선택
            connector.MoveSelParaEnd()

            # 2022-호환: DeleteBack으로 선택 삭제
            connector.delete_back()

            # 기존 패턴: 글머리 기호 보존하며 삽입
            final_text = bullet_prefix + new_text.lstrip() if bullet_prefix else new_text

            # 새로운 내용 삽입 (스타일 보존)
            self._insert_with_style(connector, final_text)

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "replace_list",
                "new_length": len(new_text)
            })

            return {"success": True, "edited": True, "new_length": len(new_text)}

        except Exception as e:
            print(f"[ReplaceList] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "replace_list"
            })

            return {"success": False, "error": str(e)}

    def _execute_append_paragraph(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 문단 추가

        Args:
            delta_data: {
                "block_id": "42",
                "new_text": "추가할 문단 내용"
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        new_text = delta_data.get("new_text")

        if block_id is None or new_text is None:
            return {"success": False, "error": "append_paragraph requires block_id, new_text"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 기존 패턴: 문단 위치로 이동
            if not connector.set_pos(section_idx, para_idx, char_idx):
                return {"success": False, "error": "Failed to move to element position"}

            # 2022-호환: MoveParaEnd로 문단 끝으로 이동
            connector.MoveParaEnd()

            # 2022-호환: BreakPara로 새 문단 생성
            connector.break_para()

            # 줄바꿈 표준화 (기존 패턴)
            new_text = self._normalize_newlines(new_text)

            # 새로운 문단 내용 삽입 (스타일 보존)
            self._insert_with_style(connector, new_text)

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "append_paragraph",
                "new_length": len(new_text)
            })

            return {"success": True, "edited": True, "new_length": len(new_text)}

        except Exception as e:
            print(f"[AppendParagraph] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "append_paragraph"
            })

            return {"success": False, "error": str(e)}

    def _execute_append_list(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 리스트 항목 추가

        Args:
            delta_data: {
                "block_id": "42",
                "new_text": "추가할 리스트 항목"
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        new_text = delta_data.get("new_text")

        if block_id is None or new_text is None:
            return {"success": False, "error": "append_list requires block_id, new_text"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 기존 패턴: 리스트 항목 위치로 이동
            if not connector.set_pos(section_idx, para_idx, char_idx):
                return {"success": False, "error": "Failed to move to element position"}

            # 2022-호환: MoveParaEnd로 리스트 항목 끝으로 이동
            connector.MoveParaEnd()

            # 2022-호환: BreakPara로 새 리스트 항목 생성 (같은 리스트 스타일 유지)
            connector.break_para()

            # 줄바꿈 표준화 (기존 패턴)
            new_text = self._normalize_newlines(new_text)

            # 새로운 리스트 항목 내용 삽입 (스타일 보존)
            self._insert_with_style(connector, new_text)

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "append_list",
                "new_length": len(new_text)
            })

            return {"success": True, "edited": True, "new_length": len(new_text)}

        except Exception as e:
            print(f"[AppendList] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "append_list"
            })

            return {"success": False, "error": str(e)}

    def _execute_find_and_replace_in_paragraph(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 특정 블록 내부 텍스트 찾기/교체

        Args:
            delta_data: {
                "block_id": "13",
                "old_text": "1월",
                "new_text": "2월"
            }

        Returns:
            {"success": bool, "replaced_count": int} or error dict
        """
        block_id = delta_data.get("block_id")
        old_text = delta_data.get("old_text")
        new_text = delta_data.get("new_text")

        if block_id is None or old_text is None or new_text is None:
            return {"success": False, "error": "find_and_replace_in_paragraph requires block_id, old_text, new_text"}

        # HWP 연결
        connector = self._ensure_connector()

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        self.doc_view = self._current_doc_view

        # Element ID를 정수로 변환 (DocumentBlockManager는 숫자 ID 사용)
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range"
                })
                return {"success": True, "edited": False, "skipped": True}

        # Find and Replace 실행
        result = self.locate_and_substitute_in_element(
            element_id=str(block_id),
            search_text=old_text,
            replacement_text=new_text,
            connector=connector
        )

        if result.get("success"):
            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "replaced_count": result.get("replaced_count", 0),
                "operation": "find_and_replace_in_paragraph"
            })
            return {"success": True, "edited": True, "replaced_count": result.get("replaced_count", 0)}
        else:
            # 실패 알림
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": result.get("error", "Unknown error"),
                "operation": "find_and_replace_in_paragraph"
            })
            return result

    def _execute_find_and_replace_all(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 문서 전체에서 텍스트 찾기/교체

        Args:
            delta_data: {
                "old_text": "1월",
                "new_text": "2월"
            }

        Returns:
            {"success": bool, "replaced_count": int} or error dict
        """
        old_text = delta_data.get("old_text")
        new_text = delta_data.get("new_text")

        if old_text is None or new_text is None:
            return {"success": False, "error": "find_and_replace_all requires old_text, new_text"}

        # HWP 연결
        connector = self._ensure_connector()

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        self.doc_view = self._current_doc_view

        # 문서 시작점으로 이동
        try:
            replaced_count = -1
            try:
                # 2022-호환: FindReplace ParameterSet 경로 우선
                replaced_count = connector.find_replace_all(
                    old_text, new_text, match_case=False
                )
            except Exception as e:
                print(f"[FindReplaceAll] find_replace_all 실패: {e}", file=sys.stderr)

            if replaced_count >= 0:
                print(
                    f"[FindReplaceAll] ✅ 문서 전체에서 '{old_text}' → '{new_text}' ({replaced_count}회 교체)",
                    file=sys.stderr
                )

                # 성공 알림
                self._send_progress("edit_success", {
                    "replaced_count": replaced_count,
                    "operation": "find_and_replace_all"
                })

                return {"success": True, "edited": True, "replaced_count": replaced_count}

            # fallback: Find 루프
            connector.hwp.MoveDocBegin()
            replaced_count = 0

            while True:
                found = connector.hwp.Find(old_text, 0x00000000)  # 0 = Forward, no regex
                if not found:
                    break
                self._insert_with_style(connector, new_text)
                replaced_count += 1

            print(
                f"[FindReplaceAll] ✅ 문서 전체에서 '{old_text}' → '{new_text}' ({replaced_count}회 교체)",
                file=sys.stderr
            )

            self._send_progress("edit_success", {
                "replaced_count": replaced_count,
                "operation": "find_and_replace_all"
            })

            return {"success": True, "edited": True, "replaced_count": replaced_count}

        except Exception as e:
            print(f"[FindReplaceAll] ❌ 실패: {e}", file=sys.stderr)
            self._send_progress("edit_failed", {
                "reason": str(e),
                "operation": "find_and_replace_all"
            })
            return {"success": False, "error": str(e)}

    def _execute_replace_cell_content(self, delta_data: dict) -> dict:
        """
        delta 분석 형식: 셀 내용 전체 교체

        Args:
            delta_data: {
                "block_id": "42",
                "new_text": "새로운 내용"
            }

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")
        new_text = delta_data.get("new_text")

        if block_id is None or new_text is None:
            return {"success": False, "error": "replace_cell_content requires block_id, new_text"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # 페이지 범위 체크
        if hasattr(self, '_allowed_element_ids') and self._allowed_element_ids:
            if element_id not in self._allowed_element_ids:
                page = element.attributes.get('page', 'unknown')
                self._send_progress("edit_skipped", {
                    "id": element_id,
                    "reason": "Out of page range",
                    "page": page
                })
                return {"success": True, "edited": False, "skipped": True}

        # 기존 패턴: 줄바꿈 표준화
        content = self._normalize_newlines(new_text)

        # HWP 편집 실행
        connector = self._ensure_connector()

        # DocumentBlockManager 사용 (위치 조정)
        if not hasattr(self, '_block_manager'):
            self._block_manager = DocumentBlockManager(doc_view.position_map)

        block_manager = self._block_manager
        adjusted_position = block_manager.get_adjusted_position(element_id)

        if adjusted_position is None:
            adjusted_position = element.position

        # 문단 시작점으로 이동
        if not connector.set_pos(adjusted_position[0], adjusted_position[1], 0):
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Position move failed"
            })
            return {"success": False, "error": "Position move failed"}

        old_content = element.content if isinstance(element.content, str) else ""

        # HTML 태그 제거 (순수 텍스트 비교)
        import re
        old_text_clean = re.sub(r'<[^>]+>', '', old_content).strip()
        new_text_clean = content.strip()

        old_length = len(old_content)
        new_length = len(content)

        # 기존 패턴: 비어있지 않은 셀도 교체 허용
        # (기존 "non_empty_protection" 정책 제거)
        
        if old_text_clean:
            # 기존 패턴: SelectAll로 셀 내용 전체 선택 후 삭제
            print(f"[Edit] 셀 {element_id} 내용 교체: {old_text_clean[:30]} → {new_text_clean[:30]}", file=sys.stderr)
            
            # 2022-호환: SelectAll로 셀 내용 전체 선택 (기존 패턴)
            try:
                if hasattr(connector, 'hwp') and hasattr(connector.hwp, 'SelectAll'):
                    connector.hwp.SelectAll()
                else:
                    # 폴백: MoveParaBegin + MoveSelParaEnd
                    connector.MoveParaBegin()
                    connector.MoveSelParaEnd()
            except Exception:
                # 폴백: MoveParaBegin + MoveSelParaEnd
                connector.MoveParaBegin()
                connector.MoveSelParaEnd()
            
            # 2022-호환: DeleteBack으로 선택 삭제
            connector.delete_back()
            # 리스트 블록인 경우 번호/불릿도 삭제
            connector.delete_back()

        # 기존 패턴: 스타일 보존하며 삽입
        self._insert_with_style(connector, content)
        print(f"[Edit] ✅ 셀 {element_id}에 삽입: {content[:50]}", file=sys.stderr)

        # DocumentBlockManager 업데이트
        block_manager.update_after_edit(element_id, old_length, new_length)

        self._send_progress("edit_success", {"id": element_id})

        return {"success": True, "edited": True}

    def locate_and_substitute_in_element(
        self,
        element_id: str,
        search_text: str,
        replacement_text: str,
        connector,
    ) -> dict:
        """
        element 내 부분 텍스트 교체

        element 내에서 특정 텍스트를 찾아 부분 교체 수행
        예: "2025년 09월 30일" → "09"를 "12"로, "30"을 "31"로 교체

        Args:
            element_id: 편집 대상 element ID
            search_text: 찾을 텍스트 (예: "09")
            replacement_text: 교체할 텍스트 (예: "12")
            connector: SafeHwp connector 객체

        Returns:
            {"success": bool, "replaced_count": int}
        """
        try:
            # 1. Element 좌표 가져오기
            coords = self.doc_view.get_element_coords(element_id)
            if not coords:
                print(f"[FindReplace] Element {element_id} 좌표 없음", file=sys.stderr)
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 2. Element 위치로 이동
            if not connector.set_pos(section_idx, para_idx, 0):
                print(f"[FindReplace] 위치 이동 실패: ({section_idx}, {para_idx}, 0)", file=sys.stderr)
                return {"success": False, "error": "Failed to move to element position"}

            start_coords = connector.get_pos()
            replaced_count = 0

            # 3. 기존 패턴: 동일 문단 내에서 모든 일치 항목 찾아 교체
            while True:
                # 텍스트 찾기 (앞으로 검색)
                found = connector.hwp.Find(search_text, 0x00000000)  # 0 = Forward, no regex

                if not found:
                    # 더 이상 찾을 수 없음
                    break

                # 현재 위치 확인 (다른 문단으로 넘어갔는지)
                current_coords = connector.get_pos()
                if current_coords[1] != start_coords[1]:
                    # 다른 문단으로 넘어감 - 종료
                    break

                # 교체 실행 (기존 스타일 보존하며 삽입)
                self._insert_with_style(connector, replacement_text)
                replaced_count += 1

            # 4. 다시 문단 앞으로 커서 이동 (후속 작업에 영향 방지)
            connector.set_pos(section_idx, para_idx, 0)

            print(f"[FindReplace] ✅ Element {element_id}에서 '{search_text}' → '{replacement_text}' ({replaced_count}회)", file=sys.stderr)

            return {
                "success": True,
                "replaced_count": replaced_count,
                "element_id": element_id,
            }

        except Exception as e:
            print(f"[FindReplace] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def finalize_edits(self, context_id: str, edits_count: int, messages: list) -> dict:
        """편집 완료 처리 및 이력 기록

        Args:
            context_id: prepare_context에서 반환받은 세션 ID
            edits_count: 실행된 편집 개수
            messages: 수집된 메시지 목록

        Returns:
            {"success": bool, "message": str, "edit_history": dict}
        """
        try:
            # 편집 이력 기록
            if edits_count > 0:
                self._record_edit(context_id, edits_count)
                print(f"[Python] 편집 이력 기록: contextId={context_id}, count={edits_count}", file=sys.stderr)

            # 완료 알림
            self._send_progress("complete", {
                "edits": edits_count,
                "messages": messages,
                "editHistory": self.get_edit_history()
            })

            # 최종 메시지 구성
            if messages:
                final_message = "\n".join(messages)
            elif edits_count > 0:
                final_message = f"문서를 수정했습니다. ({edits_count}개 항목 편집)"
            else:
                final_message = "편집 사항이 없습니다."

            return {
                "success": True,
                "message": final_message,
                "edits": edits_count,
                "edit_history": self.get_edit_history()
            }

        except Exception as e:
            print(f"[Python] finalize_edits 에러: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}
        finally:
            self._active_context_id = None
            self._cleanup_session_state()

    def cancel_context(self, context_id: Optional[str] = None) -> dict:
        """현재 편집 컨텍스트를 무효화 (취소 시 사용)"""
        try:
            if context_id is None or context_id == self._active_context_id:
                self._active_context_id = None
                # 중단 시 Track Changes 끄기
                if self._diff_mode_enabled:
                    self._ensure_track_changes_state(False)
            return {"success": True}
        except Exception as e:
            print(f"[Python] cancel_context 에러: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    # ============================================================================
    # 삭제 계열 메서드 (기존 방식 동기화)
    # ============================================================================

    def _execute_delete_element(self, delta_data: dict, element_type: str) -> dict:
        """
        문단/리스트 항목 삭제 (기존 패턴)

        Args:
            delta_data: {"block_id": "42"}
            element_type: "paragraph" 또는 "list"

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")

        if block_id is None:
            return {"success": False, "error": f"delete_{element_type} requires block_id"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 문단/리스트 시작으로 이동
            if not connector.set_pos(section_idx, para_idx, 0):
                return {"success": False, "error": "Failed to move to element position"}

            # 2022-호환: MoveParaBegin으로 문단 시작점 확인
            connector.MoveParaBegin()

            # 2022-호환: MoveSelParaEnd로 문단 끝까지 선택
            connector.MoveSelParaEnd()

            # 2022-호환: DeleteBack으로 선택 삭제 + 줄바꿈까지 삭제
            connector.delete_back()
            connector.delete_back()  # 줄바꿈 문자도 삭제

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": f"delete_{element_type}"
            })

            print(f"[Delete{element_type.capitalize()}] ✅ {element_type} {element_id} 삭제됨", file=sys.stderr)
            return {"success": True, "edited": True}

        except Exception as e:
            print(f"[Delete{element_type.capitalize()}] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": f"delete_{element_type}"
            })

            return {"success": False, "error": str(e)}

    def _execute_delete_cell_content(self, delta_data: dict) -> dict:
        """
        셀 내용 삭제 (기존 패턴)

        Args:
            delta_data: {"block_id": "42"}

        Returns:
            {"success": bool, "edited": bool} or error dict
        """
        block_id = delta_data.get("block_id")

        if block_id is None:
            return {"success": False, "error": "delete_cell_content requires block_id"}

        # DocumentView 체크
        if not hasattr(self, '_current_doc_view') or self._current_doc_view is None:
            return {"success": False, "error": "DocumentView not prepared"}

        doc_view = self._current_doc_view

        # Element 조회
        try:
            element_id = int(block_id)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid block_id: {block_id}"}

        element = doc_view.get_element_by_id(element_id)

        if element is None:
            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": "Unknown element ID"
            })
            return {"success": False, "error": "Unknown element ID"}

        # HWP 연결
        connector = self._ensure_connector()

        try:
            # Element 좌표로 이동
            coords = doc_view.get_element_coords(block_id)
            if not coords:
                return {"success": False, "error": "Element coordinates not found"}

            section_idx, para_idx, char_idx = coords

            # 셀 시작으로 이동
            if not connector.set_pos(section_idx, para_idx, 0):
                return {"success": False, "error": "Failed to move to element position"}

            # 2022-호환: MoveParaBegin으로 시작점 확인
            connector.MoveParaBegin()

            # 2022-호환: MoveSelParaEnd로 셀 내용 전체 선택
            connector.MoveSelParaEnd()

            # 2022-호환: DeleteBack으로 선택 삭제
            connector.delete_back()

            # 성공 알림
            self._send_progress("edit_success", {
                "id": element_id,
                "operation": "delete_cell_content"
            })

            print(f"[DeleteCellContent] ✅ 셀 {element_id} 내용 삭제됨", file=sys.stderr)
            return {"success": True, "edited": True}

        except Exception as e:
            print(f"[DeleteCellContent] ❌ 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            self._send_progress("edit_failed", {
                "id": element_id,
                "reason": str(e),
                "operation": "delete_cell_content"
            })

            return {"success": False, "error": str(e)}

    # ============================================================================

    def quit(self):
        """종료"""
        if self._connector:
            self._connector.disconnect()
            self._connector = None


# ============================================================
# Phase 6: 양식쌍 자동 매칭 핸들러
# ============================================================

def handle_match_template_pair(params: dict) -> dict:
    """
    바인딩된 문서와 프로젝트 양식쌍 매칭

    Args:
        params: {
            boundDocName: str,      # 바인딩된 문서 파일명 (예: "신청서_양식.hwp")
            templatePairs: list,    # 프로젝트의 양식쌍 목록
                                    # [{id, templateName, referenceName, diffPath?}, ...]
        }

    Returns:
        {
            success: True,
            matched: True/False,
            pairId: str,           # 매칭된 양식쌍 ID
            pairName: str,         # 양식쌍 이름 (표시용)
            templateName: str,     # 템플릿 파일명
            referenceName: str     # 작성 사례 파일명
        }
    """
    import os

    bound_doc_name = params.get('boundDocName', '')
    template_pairs = params.get('templatePairs', [])

    if not bound_doc_name or not template_pairs:
        return {"success": True, "matched": False}

    # 바인딩된 문서명에서 확장자 제거
    bound_name_no_ext = os.path.splitext(bound_doc_name)[0].lower()

    best_match = None
    best_score = 0

    for pair in template_pairs:
        template_name = pair.get('templateName', '')
        if not template_name:
            continue

        # 템플릿 파일명에서 확장자 제거
        template_name_no_ext = os.path.splitext(template_name)[0].lower()

        # 매칭 점수 계산
        score = 0

        # 1. 완전 일치 (최고 점수)
        if bound_name_no_ext == template_name_no_ext:
            score = 100
        # 2. 바인딩 문서가 템플릿 이름을 포함
        elif template_name_no_ext in bound_name_no_ext:
            score = 80
        # 3. 템플릿이 바인딩 문서 이름을 포함
        elif bound_name_no_ext in template_name_no_ext:
            score = 70
        # 4. 공통 부분 비율 (편집 거리 기반 유사도)
        else:
            # 간단한 공통 부분 비율 계산
            common_chars = set(bound_name_no_ext) & set(template_name_no_ext)
            total_chars = set(bound_name_no_ext) | set(template_name_no_ext)
            if total_chars:
                similarity = len(common_chars) / len(total_chars)
                if similarity > 0.5:
                    score = int(similarity * 50)

        if score > best_score:
            best_score = score
            best_match = pair

    # 최소 점수 임계값 (부분 일치 이상)
    if best_match and best_score >= 50:
        # pairName 생성: 템플릿 파일명에서 "_양식", "_템플릿" 등 제거
        template_name = best_match.get('templateName', '')
        pair_name = os.path.splitext(template_name)[0]
        # 일반적인 접미사 제거
        for suffix in ['_양식', '_템플릿', '_template', '_form', '_빈양식']:
            if pair_name.lower().endswith(suffix.lower()):
                pair_name = pair_name[:-len(suffix)]
                break

        return {
            "success": True,
            "matched": True,
            "pairId": best_match.get('id', ''),
            "pairName": pair_name,
            "templateName": best_match.get('templateName', ''),
            "referenceName": best_match.get('referenceName', ''),
            "diffPath": best_match.get('diffPath'),
            "matchScore": best_score
        }

    return {"success": True, "matched": False}


# ============================================================
# Phase 4: CVD 추출 및 Diff 핸들러 함수
# ============================================================

def handle_cvd_extract_pair(params: dict) -> dict:
    """
    Template Pair CVD 추출

    Args:
        params: {
            projectId: str,
            pairId: str,
            templatePath: str,
            filledPath: str,
            userDataPath: str
        }

    Returns:
        {
            success: bool,
            template_cvd_path: str,
            filled_cvd_path: str,
            error?: str
        }
    """
    try:
        project_id = params.get("projectId")
        pair_id = params.get("pairId")
        template_path = params.get("templatePath")
        filled_path = params.get("filledPath")
        user_data_path = params.get("userDataPath")

        if not all([project_id, pair_id, template_path, filled_path, user_data_path]):
            return {"success": False, "error": "Missing required parameters"}

        # Config 초기화
        config.set_user_data_path(user_data_path)

        # 로그 콜백
        def log_callback(level: str, message: str):
            print(f"[CVDService][{level}] {message}", file=sys.stderr)

        # 진행 콜백 (이벤트 발생)
        def progress_callback(progress: float, message: str):
            # JSON-RPC progress 이벤트 전송
            event = {
                "type": "progress",
                "event": "cvd:progress",
                "data": {
                    "pairId": pair_id,
                    "progress": int(progress * 100),
                    "message": message
                }
            }
            print(json.dumps(event, ensure_ascii=False))
            sys.stdout.flush()

        # CVD 서비스 인스턴스
        cvd_service = CVDService(log_callback, allow_existing_instance=False)

        # CVD 추출 실행
        result = cvd_service.extract_pair_cvd(
            project_id=project_id,
            pair_id=pair_id,
            template_path=template_path,
            filled_path=filled_path,
            progress_callback=progress_callback
        )

        return result

    except Exception as e:
        print(f"[CVDService] Error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def handle_cvd_generate_diff(params: dict) -> dict:
    """
    CVD Diff 생성

    Args:
        params: {
            projectId: str,
            pairId: str,
            userDataPath: str
        }

    Returns:
        {
            success: bool,
            diff_path: str,
            changes_count: int,
            stats: {...},
            error?: str
        }
    """
    try:
        project_id = params.get("projectId")
        pair_id = params.get("pairId")
        user_data_path = params.get("userDataPath")

        if not all([project_id, pair_id, user_data_path]):
            return {"success": False, "error": "Missing required parameters"}

        # Config 초기화 (이미 설정되어 있을 수 있음)
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        # Diff 서비스 인스턴스
        diff_service = DiffService()

        # Diff 생성 실행
        result = diff_service.generate_diff_for_pair(
            project_id=project_id,
            pair_id=pair_id
        )

        return result

    except Exception as e:
        print(f"[DiffService] Error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


# ============================================================
# Phase 5: RAG 인덱싱/쿼리 핸들러
# ============================================================

def handle_rag_index_pair(params: dict) -> dict:
    """
    Template Pair RAG 인덱싱 (v6.2: No-op)

    v6.2 변경: 양식쌍은 RAG 인덱싱하지 않음.
    대신 LLM 호출 시 diff.json을 직접 프롬프트에 포함.

    이 핸들러는 기존 UI 호환성을 위해 유지하되, 실제 인덱싱은 수행하지 않음.

    Args:
        params: {
            projectId: str,
            pairId: str,
            diffPath: str,
            ...
        }

    Returns:
        {success: bool, indexed_count: int, message: str}
    """
    pair_id = params.get("pairId", "unknown")
    print(f"[RAG v6.2] Template pair indexing skipped (pairId: {pair_id}) - diff sent directly in prompt", file=sys.stderr)

    # UI 호환성을 위해 성공 반환 (실제 인덱싱 없음)
    return {
        "success": True,
        "indexed_count": 0,
        "message": "v6.2: Template pair diff is now included directly in prompt, RAG indexing skipped"
    }


def handle_rag_index_file(params: dict) -> dict:
    """
    일반 파일 RAG 인덱싱

    Args:
        params: {
            projectId: str,
            fileId: str,
            textContent: str,
            fileName: str,
            userDataPath: str,
            openaiApiKey: str
        }
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        file_id = params.get("fileId")
        text_content = params.get("textContent")
        file_name = params.get("fileName")
        file_path = params.get("filePath")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not project_id or not file_id or (not text_content and not file_path):
            return {"success": False, "error": "Missing required parameters (projectId, fileId, textContent|filePath)"}

        # Config 초기화
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        # ⭐ Progress callback 정의 (IPC 이벤트 전송)
        def progress_callback(progress: float, message: str):
            event = {
                "type": "progress",
                "event": "fileSearch:progress",
                "data": {
                    "fileId": file_id,
                    "progress": int(progress * 100),  # 0~100으로 변환
                    "message": message
                }
            }
            print(json.dumps(event, ensure_ascii=False))
            sys.stdout.flush()

        # RAG 서비스 인스턴스 (API 키 필수)
        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based
        print(f"[Python] [FILE_SEARCH] 프로젝트 파일 인덱싱 호출: {file_name}", file=sys.stderr)

        # 인덱싱 실행 (progress_callback 전달)
        result = rag_service.index_file(
            project_id=project_id,
            file_id=file_id,
            text_content=text_content,
            file_name=file_name,
            file_path=file_path,
            progress_callback=progress_callback
        )

        return result

    except Exception as e:
        print(f"[RAGService] Index file error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def handle_rag_delete_pair(params: dict) -> dict:
    """
    Pair ID로 RAG 삭제
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        pair_id = params.get("pairId")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not all([project_id, pair_id]):
            return {"success": False, "error": "Missing required parameters"}

        # Config 초기화
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        # RAG 서비스 인스턴스 (삭제는 API 키 없어도 가능하지만 일관성을 위해 전달)
        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based

        # 삭제 실행
        result = rag_service.delete_by_pair_id(
            project_id=project_id,
            pair_id=pair_id
        )

        return result

    except Exception as e:
        print(f"[RAGService] Delete pair error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def handle_rag_delete_file(params: dict) -> dict:
    """
    File ID로 RAG 삭제
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        file_id = params.get("fileId")
        chat_id = params.get("chatId")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not all([project_id, file_id]):
            return {"success": False, "error": "Missing required parameters"}

        # Config 초기화
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        # RAG 서비스 인스턴스
        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based
        print(
            f"[Python] [FILE_SEARCH] Delete file: file_id={file_id}, chat_id={chat_id}",
            file=sys.stderr
        )

        # 삭제 실행
        result = rag_service.delete_by_file_id(
            project_id=project_id,
            file_id=file_id,
            chat_id=chat_id
        )

        return result

    except Exception as e:
        print(f"[RAGService] Delete file error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def handle_rag_index_chat_file(params: dict) -> dict:
    """
    채팅 파일 RAG 인덱싱 (채팅 스코프)

    Args:
        params: {
            projectId: str,
            chatId: str,
            fileId: str,
            textContent: str,
            fileName: str,
            userDataPath: str,
            openaiApiKey: str (optional)
        }

    Returns:
        {success: bool, indexed_count: int}
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        chat_id = params.get("chatId")
        file_id = params.get("fileId")
        text_content = params.get("textContent")
        file_name = params.get("fileName")
        file_path = params.get("filePath")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not project_id or not chat_id or not file_id or (not text_content and not file_path):
            return {"success": False, "error": "Missing required parameters (projectId, chatId, fileId, textContent|filePath)"}

        # Config 초기화
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        # ⭐ Progress callback 정의 (IPC 이벤트 전송)
        def progress_callback(progress: float, message: str):
            event = {
                "type": "progress",
                "event": "fileSearch:progress",
                "data": {
                    "fileId": file_id,
                    "chatId": chat_id,
                    "progress": int(progress * 100),  # 0~100으로 변환
                    "message": message
                }
            }
            print(json.dumps(event, ensure_ascii=False))
            sys.stdout.flush()

        # RAG 서비스 인스턴스
        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based

        # 채팅 스코프로 인덱싱
        result = rag_service.index_file(
            project_id=project_id,
            file_id=file_id,
            text_content=text_content,
            file_name=file_name,
            file_path=file_path,
            chat_id=chat_id,  # 채팅 스코프 지정
            progress_callback=progress_callback
        )

        return result

    except Exception as e:
        print(f"[RAGService] Index chat file error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def handle_rag_delete_chat_scope(params: dict) -> dict:
    """
    채팅 스코프 전체 삭제

    Args:
        params: {
            projectId: str,
            chatId: str,
            userDataPath: str
        }

    Returns:
        {success: bool, deleted_path: str}
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        chat_id = params.get("chatId")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not all([project_id, chat_id]):
            return {"success": False, "error": "Missing required parameters (projectId, chatId)"}

        # Config 초기화
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        # RAG 서비스 인스턴스
        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based

        # 채팅 스코프 삭제
        result = rag_service.delete_chat_scope(
            project_id=project_id,
            chat_id=chat_id
        )

        return result

    except Exception as e:
        print(f"[RAGService] Delete chat scope error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}




def handle_rag_delete_project_scope(params: dict) -> dict:
    """
    Project scope deletion

    Args:
        params: {
            projectId: str,
            userDataPath: str,
            openaiApiKey: str (optional)
        }

    Returns:
        {success: bool, deleted_path: str}
    """
    try:
        from services.file_search_service import FileSearchService

        project_id = params.get("projectId")
        user_data_path = params.get("userDataPath")
        openai_api_key = params.get("openaiApiKey")
        file_search_model = params.get("fileSearchModel")
        if file_search_model:
            os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
        embedding_model = params.get("embeddingModel")
        if embedding_model:
            os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model

        if not project_id:
            return {"success": False, "error": "Missing required parameters (projectId)"}

        # Config initialization
        if not config.initialized:
            config.set_user_data_path(user_data_path)

        if not openai_api_key:
            return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

        rag_service = FileSearchService(openai_api_key)  # OpenAI file_search based

        result = rag_service.delete_project_scope(project_id=project_id)

        return result

    except Exception as e:
        print(f"[RAGService] Delete project scope error: {str(e)}", file=sys.stderr)
        return {"success": False, "error": str(e)}

def handle_request(processor: DocumentProcessor, request: dict) -> dict:
    """요청 처리"""
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")

    result = {"id": request_id}

    try:
        if method == "ping":
            result["result"] = {"pong": True}

        elif method == "open":
            result["result"] = processor.open_document(params.get("file"))

        elif method == "extract":
            result["result"] = processor.extract_html()

        elif method == "apply":
            result["result"] = processor.apply_commands(
                params.get("commands", []),
                params.get("mappings")
            )

        elif method == "save":
            result["result"] = processor.save_document()

        elif method == "close":
            result["result"] = processor.close_document()

        elif method == "quit":
            processor.quit()
            result["result"] = {"success": True}

        elif method == "getOpenDocuments":
            result["result"] = processor.get_open_documents()

        elif method == "getCurrentPageInfo":
            result["result"] = processor.get_current_page_info()

        elif method == "getSelectionInfo":
            result["result"] = processor.get_selection_info()

        elif method == "selectDocument":
            result["result"] = processor.select_document(
                params.get("type"),
                params.get("index", 0)
            )

        elif method == "chat":
            result["result"] = processor.chat(
                params.get("prompt", ""),
                params.get("docType"),
                params.get("docIndex"),
                params.get("referenceContent"),
                params.get("referenceFileName"),
                params.get("startPage"),
                params.get("endPage"),
                params.get("openaiApiKey")
            )

        # 파일 읽기 (서버 분리 대비 - 로컬에서 읽고 문자열 반환)
        elif method == "readTxtFile":
            result["result"] = TxtReader.read(
                params.get("filePath", ""),
                params.get("maxChars")
            )

        elif method == "readExcelFile":
            result["result"] = ExcelReader.read(
                params.get("filePath", ""),
                params.get("sheetName")
            )

        elif method == "getExcelSheets":
            result["result"] = ExcelReader.get_sheet_names(
                params.get("filePath", "")
            )

        elif method == "readPdfFile":
            # PDF 파일 읽기 (CVDService의 extract_pdf 사용)
            try:
                from services.cvd_service import CVDService
                cvd_service = CVDService(lambda level, msg: print(f"[CVD][{level}] {msg}", file=sys.stderr))
                pdf_result = cvd_service.extract_pdf(params.get("filePath", ""))
                if pdf_result.get("success"):
                    result["result"] = {
                        "success": True,
                        "text": pdf_result.get("text", ""),
                        "page_count": pdf_result.get("page_count", 0)
                    }
                else:
                    result["result"] = {"success": False, "error": pdf_result.get("error", "Unknown error")}
            except Exception as e:
                result["result"] = {"success": False, "error": str(e)}

        elif method == "readHwpFile":
            # HWP/HWPX 파일 텍스트 추출 (RAG 인덱싱용)
            try:
                file_path = params.get("filePath", "")
                ext = os.path.splitext(file_path)[1].lower()
                
                if ext in ['.hwp', '.hwpx']:
                    # HWP/HWPX: CVD 로직으로 텍스트 추출
                    from services.cvd_service import CVDService
                    import tempfile
                    import shutil

                    cvd_service = CVDService(
                        lambda level, msg: print(f"[CVD][{level}] {msg}", file=sys.stderr),
                        allow_existing_instance=False  # 바인딩된 문서 보호
                    )

                    # Progress 이벤트 전송 콜백
                    def progress_callback(pct: float, message: str):
                        progress_event = {
                            "type": "progress",
                            "event": "file:upload:progress",
                            "data": {"progress": pct, "message": message, "filePath": file_path}
                        }
                        print(json.dumps(progress_event, ensure_ascii=False), flush=True)

                    temp_dir = tempfile.mkdtemp(prefix="rag_hwp_")
                    try:
                        cvd_result = cvd_service.extract_single_cvd(file_path, temp_dir, progress_callback=progress_callback)
                        if cvd_result and cvd_result.get("success"):
                            cvd_path = cvd_result.get("cvd_path")
                            if cvd_path and os.path.exists(cvd_path):
                                with open(cvd_path, "r", encoding="utf-8") as f:
                                    text_content = f.read()
                                result["result"] = {"success": True, "text": text_content}
                            else:
                                result["result"] = {"success": False, "error": "CVD output missing"}
                        else:
                            error_message = cvd_result.get("error") if cvd_result else "HWP extraction failed"
                            result["result"] = {"success": False, "error": error_message}
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                else:
                    result["result"] = {"success": False, "error": f"Unsupported extension: {ext}"}
                    
            except Exception as e:
                result["result"] = {"success": False, "error": str(e)}

        elif method == "readDocFile":
            # DOCX 파일 텍스트 추출 (RAG 인덱싱용)
            try:
                file_path = params.get("filePath", "")
                ext = os.path.splitext(file_path)[1].lower()

                if ext == '.docx':
                    # DOCX: python-docx 또는 ZIP 직접 파싱
                    try:
                        from docx import Document
                        doc = Document(file_path)
                        texts = [para.text for para in doc.paragraphs if para.text.strip()]
                        text_content = '\n'.join(texts)
                        result["result"] = {"success": True, "text": text_content}
                    except ImportError:
                        # python-docx 없으면 ZIP 직접 파싱
                        import zipfile
                        import xml.etree.ElementTree as ET

                        texts = []
                        with zipfile.ZipFile(file_path, 'r') as zf:
                            if 'word/document.xml' in zf.namelist():
                                with zf.open('word/document.xml') as f:
                                    tree = ET.parse(f)
                                    root = tree.getroot()
                                    # w:t 태그에서 텍스트 추출
                                    for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                                        if t.text:
                                            texts.append(t.text)

                        text_content = ''.join(texts)
                        result["result"] = {"success": True, "text": text_content}
                elif ext == '.doc':
                    result["result"] = {
                        "success": False,
                        "error": "Legacy .doc format is not supported. Please convert to .docx."
                    }
                else:
                    result["result"] = {"success": False, "error": f"Unsupported extension: {ext}"}

            except Exception as e:
                result["result"] = {"success": False, "error": str(e)}

        elif method == "readPptFile":
            # PPTX 파일 텍스트 추출 (RAG 인덱싱용)
            try:
                file_path = params.get("filePath", "")
                ext = os.path.splitext(file_path)[1].lower()

                if ext in ['.pptx']:
                    try:
                        from pptx import Presentation
                    except ImportError:
                        result["result"] = {"success": False, "error": "python-pptx not installed"}
                        return

                    prs = Presentation(file_path)
                    parts = []
                    for i, slide in enumerate(prs.slides, 1):
                        parts.append(f"## Slide {i}")
                        for shape in slide.shapes:
                            if hasattr(shape, "text") and shape.text:
                                parts.append(shape.text)

                    text_content = "\n".join(parts)
                    result["result"] = {"success": True, "text": text_content}
                elif ext == '.ppt':
                    result["result"] = {
                        "success": False,
                        "error": "Legacy .ppt format is not supported. Please convert to .pptx."
                    }
                else:
                    result["result"] = {"success": False, "error": f"Unsupported extension: {ext}"}
            except Exception as e:
                result["result"] = {"success": False, "error": str(e)}


        # Undo/Redo 및 Diff 모드 API (TASK-004)
        elif method == "undo":
            result["result"] = processor.undo(params.get("count", 1))

        elif method == "redo":
            result["result"] = processor.redo(params.get("count", 1))

        elif method == "setDiffMode":
            result["result"] = processor.set_diff_mode(
                params.get("enabled", False),
                params.get("skipTrackChanges", False),
            )

        elif method == "getDiffMode":
            result["result"] = processor.get_diff_mode()

        elif method == "getEditHistory":
            result["result"] = processor.get_edit_history()

        elif method == "clearEditHistory":
            result["result"] = processor.clear_edit_history()

        # Diff 변경사항 승인/거절 API
        elif method == "applyChanges":
            result["result"] = processor.apply_changes()

        elif method == "rejectChanges":
            result["result"] = processor.reject_changes()

        # TrackChange 부분 승인/거절 API (HWP 2022 이전)
        elif method == "trackChanges:getContext":
            result["result"] = processor.get_track_change_context()

        elif method == "trackChanges:cacheSelection":
            result["result"] = processor.cache_track_change_selection()

        elif method == "trackChanges:applyAll":
            result["result"] = processor.apply_all_track_changes()

        # v4.1.4: 활성 문서 정보 조회 (Main에서 docKey 비교용)
        elif method == "getActiveDocInfo":
            result["result"] = processor.get_active_doc_info()

        elif method == "trackChanges:rejectAll":
            result["result"] = processor.reject_all_track_changes()

        elif method == "trackChanges:applySelected":
            result["result"] = processor.apply_selected_track_changes()

        elif method == "trackChanges:rejectSelected":
            result["result"] = processor.reject_selected_track_changes()

        # Window Monitor로부터 PID/HWND 받기 (SafeHwp 패턴)
        elif method == "window:bind":
            pid = params.get("pid")
            hwnd = params.get("hwnd")
            result["result"] = processor.set_target_binding(pid, hwnd)

        elif method == "window:arrange":
            result["result"] = processor.arrange_hwp_window(params)

        elif method == "set_target_binding":
            pid = params.get("pid")
            hwnd = params.get("hwnd")
            result["result"] = processor.set_target_binding(pid, hwnd)

        elif method == "manage_hwp_instance":
            inner_method = (
                params.get("method")
                or params.get("method_type")
                or params.get("action")
            )
            if inner_method == "set_target_binding":
                pid = params.get("pid")
                hwnd = params.get("hwnd")
                result["result"] = processor.set_target_binding(pid, hwnd)
            else:
                result["error"] = f"Unknown manage_hwp_instance method: {inner_method}"

        # Agent Child Process 패턴 API
        elif method == "prepare_context":
            result["result"] = processor.prepare_context(
                params.get("prompt", ""),
                params.get("startPage"),
                params.get("endPage"),
                params.get("referenceFile"),
                params.get("referenceFileName"),
                params.get("docIndex"),
                params.get("docType")
            )

        elif method == "execute_delta":
            result["result"] = processor.execute_delta(params)

        elif method == "finalize_edits":
            result["result"] = processor.finalize_edits(
                params.get("contextId", ""),
                params.get("editsCount", 0),
                params.get("messages", [])
            )
        elif method == "cancel_context":
            result["result"] = processor.cancel_context(
                params.get("contextId")
            )

        elif method == "extract_cvd":
            result["result"] = processor.extract_cvd(params)

        elif method == "extract_document":
            inner_method = (
                params.get("method")
                or params.get("method_type")
                or params.get("action")
            )
            if inner_method == "extract_cvd":
                result["result"] = processor.extract_cvd(params)
            else:
                result["error"] = f"Unknown extract_document method: {inner_method}"

        # ============================================================
        # Phase 4: CVD 추출 및 Diff 생성
        # ============================================================

        elif method == "cvd:extractPair":
            # Template Pair CVD 추출
            result["result"] = handle_cvd_extract_pair(params)

        elif method == "cvd:generateDiff":
            # Diff 생성
            result["result"] = handle_cvd_generate_diff(params)

        # ============================================================
        # Phase 5: RAG 인덱싱/쿼리
        # ============================================================

        elif method == "fileSearch:indexPair":
            # Template Pair RAG 인덱싱
            result["result"] = handle_rag_index_pair(params)

        elif method == "fileSearch:indexFile":
            # 파일 RAG 인덱싱
            result["result"] = handle_rag_index_file(params)

        elif method == "fileSearch:deletePair":
            # Pair 삭제
            result["result"] = handle_rag_delete_pair(params)

        elif method == "fileSearch:deleteFile":
            # File 삭제
            result["result"] = handle_rag_delete_file(params)

        elif method == "fileSearch:indexChatFile":
            # 채팅 파일 RAG 인덱싱
            result["result"] = handle_rag_index_chat_file(params)

        elif method == "fileSearch:deleteChatScope":
            # 채팅 스코프 삭제
            result["result"] = handle_rag_delete_chat_scope(params)

        

        elif method == "fileSearch:deleteProjectScope":
            # Project scope delete
            result["result"] = handle_rag_delete_project_scope(params)

        # ============================================================
        # LLM Settings
        # ============================================================

        elif method == "llm:getDefaultSystemPrompt":
            # 기본 시스템 프롬프트 반환 (버전 파라미터 지원)
            try:
                version = params.get("version", "v7_11")

                # 버전별 동적 임포트
                version_map = {
                    "v7_11": ("llm.system_prompt_v7_11", "build_system_prompt_v7_11"),
                }

                if version not in version_map:
                    result["result"] = {
                        "success": False,
                        "error": f"Unknown version: {version}"
                    }
                else:
                    module_name, func_name = version_map[version]
                    import importlib
                    module = importlib.import_module(module_name)
                    build_func = getattr(module, func_name)
                    default_prompt = build_func(custom_rules=None, full_override=None)
                    result["result"] = {
                        "success": True,
                        "prompt": default_prompt,
                        "version": version
                    }
            except Exception as e:
                result["result"] = {
                    "success": False,
                    "error": str(e)
                }

        elif method == "llm:getSystemPromptVersions":
            # 사용 가능한 시스템 프롬프트 버전 목록 반환
            result["result"] = {
                "success": True,
                "versions": [
                    {"id": "v7_11", "name": "v7.11", "description": "Enriched CVD + LLM-driven cell role reasoning"},
                ]
            }

        # ============================================================
        # Phase 6: Template pair auto-match
        # ============================================================

        elif method == "templatePair:match":
            # 바인딩된 문서와 양식쌍 매칭
            result["result"] = handle_match_template_pair(params)

        else:
            result["error"] = f"Unknown method: {method}"

    except Exception as e:
        result["error"] = str(e)
        result["trace"] = traceback.format_exc()

    return result


def main():
    """메인 루프 - stdin에서 JSON 명령을 읽고 처리"""
    # 로깅 시스템 초기화
    from utils.logger import debug, list_log_files, save_session_summary
    from datetime import datetime

    # 시작 메시지 및 로그 파일 목록 출력
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    debug(f"=== Inserty Python Processor 시작: {start_time} ===")

    # 생성될 로그 파일 목록 출력
    log_files = list_log_files()
    debug(f"로그 디렉토리: {log_files['log_dir']}")
    debug(f"디버그 로그: {log_files['debug']}")
    debug(f"LLM 로그: {log_files['llm']}")
    debug(f"토큰 로그: {log_files['tokens']}")
    debug(f"터미널 로그: {log_files['terminal']}")

    processor = DocumentProcessor()

    # stderr로 시작 메시지 출력 (디버깅용)
    print("Inserty Python Processor started", file=sys.stderr)
    sys.stderr.flush()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = handle_request(processor, request)

                # stdout으로 JSON 응답 전송
                print(json.dumps(response, ensure_ascii=False))
                sys.stdout.flush()

                # quit 명령이면 종료
                if request.get("method") == "quit":
                    break

            except json.JSONDecodeError as e:
                error_response = {"error": f"JSON parse error: {e}"}
                print(json.dumps(error_response))
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        # 세션 종료 시 요약 저장
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        debug(f"=== Inserty Python Processor 종료: {end_time} ===")
        save_session_summary()
        processor.quit()


if __name__ == "__main__":
    main()

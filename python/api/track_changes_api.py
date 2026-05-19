"""
TrackChange IPC API 인터페이스 (스펙 8절)

UI(Electron) ↔ Python 간 TrackChange 기능 통신을 위한 엔드포인트:
- get_track_change_context: UI 상태 계산 (주기적 또는 selection-change 이벤트)
- apply_all_changes: 전체 승인
- reject_all_changes: 전체 거절
- apply_selected_changes: 부분 승인 (선택 영역 드래그 n건)
- reject_selected_changes: 부분 거절 (선택 영역 드래그 n건)
"""

import contextlib
import io
import os
import sys
from typing import Dict, Any, Optional, Tuple
from modification.track_changes import TrackChangesManager

# 전역 캐시: 선택 범위 저장 (버튼 클릭 시 선택 해제 대응)
_cached_selection_range: Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = None


def get_active_doc_info(connector) -> Dict[str, Any]:
    """현재 활성 문서의 원시 식별자 반환 (불변식 10)

    Main에서 docKey 비교에 사용. Python은 docKey를 계산하지 않음.
    v6.0: activeName 추가 - 양식쌍 매칭에 사용

    Returns:
        {
            "activeDocumentId": int | None,  # HWP 문서 ID
            "activePath": str | None,        # 파일 경로 (정규화 안 함)
            "activeName": str | None         # 파일명 (v6.0: 양식쌍 매칭용)
        }
    """
    import os

    result: Dict[str, Any] = {
        "activeDocumentId": None,
        "activePath": None,
        "activeName": None  # v6.0: 양식쌍 매칭용
    }

    try:
        hwp = connector.hwp
        if not hwp:
            return result

        # documentId / path 시도 (Active_XHwpDocument 기준)
        active_doc = None
        try:
            active_doc = hwp.XHwpDocuments.Active_XHwpDocument
        except Exception:
            active_doc = None

        if active_doc:
            try:
                doc_id = active_doc.DocumentID
                if doc_id and doc_id > 0:
                    result["activeDocumentId"] = doc_id
            except Exception:
                pass

            try:
                active_path = active_doc.FullName if hasattr(active_doc, "FullName") else ""
                if active_path:
                    result["activePath"] = active_path  # 정규화 안 함, 그대로 반환
                    # v6.0: 파일명 추출 (양식쌍 매칭용)
                    result["activeName"] = os.path.basename(active_path)
            except Exception:
                pass

        # fallback: HWP 경로
        if not result["activePath"]:
            try:
                path = hwp.Path
                if path:
                    result["activePath"] = path  # 정규화 안 함, 그대로 반환
                    # v6.0: 파일명 추출 (양식쌍 매칭용)
                    result["activeName"] = os.path.basename(path)
            except Exception:
                pass

    except Exception as e:
        print(f"get_active_doc_info 실패: {e}", file=sys.stderr)

    return result


def _is_cursor_in_table(connector) -> Optional[bool]:
    """커서가 표 안에 있는지 확인 (reason 템플릿용)"""
    try:
        hwp = connector.hwp
        if not hwp:
            return None
        # GetTableCellAddr가 -1이면 표 밖
        row = hwp.GetTableCellAddr(0)  # 0 = row
        return row >= 0
    except Exception:
        return None


def _normalize_reject_text(text: Optional[str], max_len: int = 200) -> Optional[str]:
    if not text:
        return None
    normalized = " ".join(text.split())
    if len(normalized) > max_len:
        normalized = normalized[:max_len] + "..."
    return normalized


def _get_selected_text(connector) -> Optional[str]:
    try:
        if hasattr(connector, "get_selected_text"):
            return connector.get_selected_text(keep_select=True)
        hwp = getattr(connector, "hwp", None) or connector
        if hasattr(hwp, "get_selected_text"):
            return hwp.get_selected_text()
    except Exception:
        return None
    return None


@contextlib.contextmanager
def _suppress_output(enabled: bool):
    if not enabled:
        yield
        return
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr


def _normalize_track_changes_flag(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("0", "false", "off", "no", "n", ""):
            return False
        if normalized in ("1", "true", "on", "yes", "y"):
            return True
    return bool(value)


def _get_track_changes_state(connector) -> Optional[bool]:
    try:
        if hasattr(connector, "get_track_changes_state"):
            return _normalize_track_changes_flag(connector.get_track_changes_state())
        hwp = getattr(connector, "hwp", None) or connector
        current = getattr(hwp, "lsTrackChange", None)
        return _normalize_track_changes_flag(current)
    except Exception:
        return None


def _build_rejected_ops(selected_text: Optional[str], selection_range: Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]], in_table: Optional[bool]) -> list:
    ops = []
    text_summary = _normalize_reject_text(selected_text)
    if text_summary:
        ops.append({"kind": "선택텍스트", "summary": text_summary})
    if selection_range:
        ops.append({"kind": "범위", "summary": f"{selection_range[0]} ~ {selection_range[1]}"})
    if in_table is True:
        ops.append({"kind": "영역", "summary": "표 안 선택 영역"})
    return ops


def cache_track_change_selection(connector) -> Dict[str, Any]:
    """선택 범위 캐싱만 수행 (커서 이동/전체 변경 탐색 없음)"""
    try:
        global _cached_selection_range
        quiet = os.getenv("TRACKCHANGES_QUIET", "1") != "0"
        with _suppress_output(quiet):
            tc = TrackChangesManager(connector)

            selection_range = tc._get_selection_range()
            if selection_range is not None:
                _cached_selection_range = selection_range
                selection_count = 1
            else:
                _cached_selection_range = None
                selection_count = 0

        return {
            "success": True,
            "selectionCount": selection_count
        }
    except Exception as e:
        print(f"cache_track_change_selection 실패: {e}", file=sys.stderr)
        return {
            "success": False,
            "selectionCount": 0
        }

def get_track_change_context(connector) -> Dict[str, Any]:
    """UI가 주기적 또는 selection-change 이벤트에 호출

    Returns:
        {
            "pending": bool,           # 남은 변경 있음 (전체 버튼 표시 여부)
            "selectionCount": int,     # 선택 범위 내 변경 개수
            "contextVisible": bool     # 부분 버튼 표시 여부 (selectionCount > 0)
        }

    사용 시나리오:
        - Electron에서 selection-change 이벤트 발생 시 호출
        - 주기적 폴링 (예: 1초마다) - 선택적
        - 부분 승인/거절 버튼 클릭 직전 최종 확인용

    주의:
        - 문서를 이동/스캔하지 않고 MenuEx(변경 추적) 상태만 확인한다.
    """
    try:
        global _cached_selection_range
        quiet = os.getenv("TRACKCHANGES_QUIET", "1") != "0"
        with _suppress_output(quiet):
            tc = TrackChangesManager(connector)
            track_enabled = _get_track_changes_state(connector)

            if track_enabled is not True:
                _cached_selection_range = None
                return {
                    "pending": False,
                    "selectionCount": 0,
                    "contextVisible": False
                }

            has_remaining = tc.has_remaining_changes()
            if not has_remaining:
                _cached_selection_range = None
                return {
                    "pending": False,
                    "selectionCount": 0,
                    "contextVisible": False
                }

            selection_range = tc._get_selection_range()
            return {
                "pending": True,
                "selectionCount": 1 if selection_range is not None else 0,
                "contextVisible": selection_range is not None
            }
    except Exception as e:
        print(f"get_track_change_context 실패: {e}", file=sys.stderr)
        return {
            "pending": False,
            "selectionCount": 0,
            "contextVisible": False
        }


def apply_all_changes(connector) -> Dict[str, Any]:
    """전체 승인 버튼 클릭 시 호출

    Returns:
        {
            "success": bool,           # 실행 성공 여부
            "hasRemaining": bool,      # 처리 후 남은 변경 있음
            "autoComplete": bool       # 모든 변경 처리 완료 (버튼 자동 숨김)
        }
    """
    try:
        tc = TrackChangesManager(connector)

        success = tc.accept_all()
        has_remaining = tc.has_remaining_changes()

        # ✅ 전체 승인 후 변경추적 모드 종료는 상위에서 처리

        return {
            "success": success,
            "hasRemaining": has_remaining,
            "autoComplete": not has_remaining
        }
    except Exception as e:
        print(f"apply_all_changes 실패: {e}", file=sys.stderr)
        return {
            "success": False,
            "hasRemaining": True,
            "autoComplete": False
        }


def reject_all_changes(connector) -> Dict[str, Any]:
    """전체 거절 버튼 클릭 시 호출 (v4.1.4 확장)

    Returns:
        {
            "success": bool,
            "hasRemaining": bool,
            "autoComplete": bool,
            "count": int,              # 거절된 변경 개수 (v4.1.4)
            "rejectionType": str,      # 'all'
            "requiresFullRegen": bool, # True
            "inTable": bool | None,    # 커서가 표 안인지 (v4.1.4)
            "extractionQuality": str,  # 'full' | 'partial' | 'none' (v4.1.4)
            "rejectedOps": list        # 거절 상세 요약 (v4.1.4)
        }
    """
    try:
        tc = TrackChangesManager(connector)
        
        # 거절 전 상태 확인
        in_table = _is_cursor_in_table(connector)
        selection_range = None

        success = tc.reject_all()
        has_remaining = tc.has_remaining_changes()

        # ✅ 전체 거절 후 변경추적 모드 종료는 상위에서 처리

        rejected_ops = []
        extraction_quality = "full"

        return {
            "success": success,
            "hasRemaining": has_remaining,
            "autoComplete": not has_remaining,
            # v4.1.4 추가 필드
            "count": 1 if success else 0,  # 전체 거절은 1회로 카운트
            "rejectionType": "all",
            "requiresFullRegen": True,
            "inTable": in_table,
            "extractionQuality": extraction_quality,
            "rejectedOps": rejected_ops
        }
    except Exception as e:
        print(f"reject_all_changes 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "hasRemaining": True,
            "autoComplete": False,
            "count": 0,
            "rejectionType": "all",
            "requiresFullRegen": True,
            "inTable": None,
            "extractionQuality": "full",
            "rejectedOps": []
        }


def apply_selected_changes(connector) -> Dict[str, Any]:
    """부분 승인 버튼 클릭 시 호출

    처리 방식:
        - 선택 영역 드래그: 범위 내 n건 배치 처리
        - 선택 없음: (False, 0) 반환

    Returns:
        {
            "success": bool,           # 실행 성공 여부
            "processed": int,          # 처리된 변경 개수
            "hasRemaining": bool,      # 부분 처리 후에도 항상 True
            "autoComplete": bool,      # 부분 처리에서는 항상 False
            "showToast": bool          # 토스트 표시 필요 (선택 없을 때)
        }

    UI 처리 로직:
        - showToast=True이면 "선택 영역이 없습니다. 변경표시를 드래그 선택 후 다시 시도하세요." 표시
        - 부분 처리 후에는 버튼을 유지
    """
    try:
        global _cached_selection_range
        tc = TrackChangesManager(connector)

        # ✅ 캐시된 선택 범위 사용
        print(f"[ApplySelectedChanges] 캐시된 범위 사용: {_cached_selection_range}", file=sys.stderr)
        success, processed = tc.accept_selected(cached_range=_cached_selection_range)

        # 선택 없음: (False, 0)
        show_toast = not success and processed == 0
        has_remaining = tc.has_remaining_changes()

        # 부분 처리에서는 autoComplete=False (버튼 유지)
        auto_complete = False

        # 처리 완료 후 캐시 클리어
        _cached_selection_range = None

        return {
            "success": success,
            "processed": processed,
            "hasRemaining": has_remaining,
            "autoComplete": auto_complete,
            "showToast": show_toast
        }
    except Exception as e:
        print(f"apply_selected_changes 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "processed": 0,
            "hasRemaining": True,
            "autoComplete": False,
            "showToast": False
        }


def reject_selected_changes(connector) -> Dict[str, Any]:
    """부분 거절 버튼 클릭 시 호출 (v4.1.4 확장)

    처리 방식:
        - 선택 영역 드래그: 범위 내 n건 배치 처리
        - 선택 없음: (False, 0) 반환

    Returns:
        {
            "success": bool,
            "processed": int,
            "hasRemaining": bool,
            "autoComplete": bool,
            "showToast": bool,
            "count": int,              # 거절된 개수 (v4.1.4)
            "rejectionType": str,      # 'partial'
            "requiresFullRegen": bool, # False
            "inTable": bool | None,    # 커서가 표 안인지 (v4.1.4)
            "extractionQuality": str,  # 'full' | 'partial' | 'none' (v4.1.4)
            "rejectedOps": list        # 거절 상세 요약 (v4.1.4)
        }
    """
    try:
        global _cached_selection_range
        tc = TrackChangesManager(connector)
        
        # 거절 전 상태 확인
        in_table = _is_cursor_in_table(connector)
        selected_text = _get_selected_text(connector)
        selection_range = None
        try:
            selection_range = tc._get_selection_range()
        except Exception:
            selection_range = None
        if selection_range is None and _cached_selection_range is not None:
            selection_range = _cached_selection_range

        # ✅ 캐시된 선택 범위 사용
        print(f"[RejectSelectedChanges] 캐시된 범위 사용: {_cached_selection_range}", file=sys.stderr)
        success, processed = tc.reject_selected(cached_range=_cached_selection_range)

        show_toast = not success and processed == 0
        has_remaining = tc.has_remaining_changes()

        # 부분 처리에서는 autoComplete=False (버튼 유지)
        auto_complete = False

        # 처리 완료 후 캐시 클리어
        _cached_selection_range = None

        rejected_ops = _build_rejected_ops(selected_text, selection_range, in_table) if success and processed > 0 else []
        extraction_quality = "partial" if rejected_ops else "none"

        return {
            "success": success,
            "processed": processed,
            "hasRemaining": has_remaining,
            "autoComplete": auto_complete,
            "showToast": show_toast,
            # v4.1.4 추가 필드
            "count": processed,
            "rejectionType": "partial",
            "requiresFullRegen": False,
            "inTable": in_table,
            "extractionQuality": extraction_quality,
            "rejectedOps": rejected_ops
        }
    except Exception as e:
        print(f"reject_selected_changes 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "processed": 0,
            "hasRemaining": True,
            "autoComplete": False,
            "showToast": False,
            "count": 0,
            "rejectionType": "partial",
            "requiresFullRegen": False,
            "inTable": None,
            "extractionQuality": "none",
            "rejectedOps": []
        }

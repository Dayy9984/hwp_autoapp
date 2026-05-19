"""
Track Changes (변경 추적) 관리 모듈

한글 문서의 변경 추적 기능을 관리합니다.
- 전체 승인/거절 (TrackChangeApplyAll, TrackChangeCancelAll)
- 부분 승인/거절 (선택 영역 또는 현재 변경)
- 남은 변경 감지

HWP API (2022 이전 기준):
- TrackChangeApplyAll: 전체 승인
- TrackChangeCancelAll: 전체 거절
- TrackChangeApply: 현재 변경 1건 승인
- TrackChangeCancel: 현재 변경 1건 거절
- TrackChangePrev, TrackChangeNext: 변경 항목 탐색
- TrackChangeApplyPrev, TrackChangeCancelPrev: 처리 + 이전 변경으로 이동
"""

import os
import sys
from typing import Optional, Tuple, Callable, Any

_DEBUG_TRACKCHANGES = os.getenv("TRACKCHANGES_DEBUG", "0") == "1"
_QUIET_TRACKCHANGES = os.getenv("TRACKCHANGES_QUIET", "1") != "0"


def _debug_log(message: str) -> None:
    if _DEBUG_TRACKCHANGES and not _QUIET_TRACKCHANGES:
        print(message, file=sys.stderr)


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


class TrackChangesManager:
    """Track Changes 관리 클래스

    사용자가 문서에서 변경 표시를 드래그 선택하면:
    - 선택 영역 드래그: n건 배치 처리 (역방향 루프)
    - 선택 없음: 처리 실패

    처리 후 남은 변경이 0이면 자동으로 UI 버튼 숨김
    """
    
    def __init__(
        self,
        hwp_connector,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Args:
            hwp_connector: HwpConnector 인스턴스 또는 pyhwpx.Hwp
            log_callback: 로깅 콜백 (level, message)
        """
        self._connector = hwp_connector
        self._log = log_callback or (lambda level, msg: None)
        
        # 내부 상태
        self._is_enabled: bool = False
        
    # ============================================================
    # 기본 속성
    # ============================================================
    
    @property
    def hwp(self):
        """HWP COM 객체 반환"""
        if hasattr(self._connector, 'hwp'):
            return self._connector.hwp
        if hasattr(self._connector, '_hwp'):
            return self._connector._hwp
        return self._connector

    def _raw_hwp(self):
        """pyhwpx 래퍼를 raw COM으로 해제"""
        hwp = self.hwp
        return getattr(hwp, 'hwp', None) or hwp

    def _get_selection_mode(self) -> Tuple[int, int]:
        raw_hwp = self._raw_hwp()
        selection_mode_raw = 0
        if hasattr(raw_hwp, 'SelectionMode'):
            selection_mode_raw = raw_hwp.SelectionMode
        return selection_mode_raw, selection_mode_raw & 0x0F

    def _is_table_selection(self) -> bool:
        selection_mode_raw, _ = self._get_selection_mode()
        # 비트 연산: 표 관련 비트 포함 여부 확인
        # bit 2 (값 4) = 표 셀, bit 1+0 (값 3) = 표 블록
        return (selection_mode_raw & 4) != 0 or (selection_mode_raw & 3) == 3

    def _get_pos(self) -> Optional[Tuple[int, int, int]]:
        """현재 커서 위치를 (list, para, pos) 튜플로 반환"""
        raw_hwp = self._raw_hwp()
        if hasattr(raw_hwp, 'GetPos'):
            result = raw_hwp.GetPos()
            if result:
                # GetPos는 (list, para, pos) 반환
                return (result[0], result[1], result[2])
        hwp = self.hwp
        if hasattr(hwp, 'get_pos'):
            result = hwp.get_pos()
            if result:
                return (result[0], result[1], result[2])
        return None

    def _get_table_cell_addr(self, addr_type: int) -> Optional[int]:
        """현재 커서 위치의 표 셀 주소 반환

        Args:
            addr_type: 0=행(row), 1=열(col)

        Returns:
            0-based 인덱스, -1이면 셀 아님
        """
        raw_hwp = self._raw_hwp()
        if hasattr(raw_hwp, 'GetTableCellAddr'):
            try:
                return raw_hwp.GetTableCellAddr(addr_type)
            except Exception:
                return None
        return None

    def _col_to_letters(self, col_1based: int) -> str:
        """열 번호를 A, B, ..., Z, AA, ... 형식으로 변환"""
        s = ""
        n = col_1based
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(ord("A") + r) + s
        return s

    def _get_cell_a1_notation(self) -> Optional[str]:
        """현재 커서 위치의 셀 주소를 A1 형식으로 반환"""
        row = self._get_table_cell_addr(0)  # 0-based row
        col = self._get_table_cell_addr(1)  # 0-based col
        if row is None or col is None or row < 0 or col < 0:
            return None
        return f"{self._col_to_letters(col + 1)}{row + 1}"

    def _is_in_table(self) -> bool:
        """표 안 여부 판정 (GetTableCellAddr 기반)
        
        GetTableCellAddr(0)가 0 이상이면 표 안으로 판정.
        -1 반환 또는 예외 시 표 밖으로 판정.
        """
        row = self._get_table_cell_addr(0)
        return row is not None and row >= 0

    def _process_current_cell_changes(self, action: str) -> Tuple[bool, int]:
        """현재 셀 내 변경만 처리
        
        표 안에서 SelectionMode/GetSelectedPosBySet이 실패하는 경우의 대안.
        현재 커서가 있는 셀 내 변경 항목만 처리하고, 다른 셀로 이동하면 중단.
        
        Args:
            action: "Apply" 또는 "Cancel"
        
        Returns:
            (성공 여부, 처리된 건수)
        """
        import sys
        
        # 시작 셀 주소 저장
        start_cell = self._get_cell_a1_notation()
        if not start_cell:
            print(f"[CellProcess] 시작 셀 주소 없음 → 표 안 아님", file=sys.stderr)
            return (False, 0)
        
        print(f"[CellProcess] 시작 셀: {start_cell}, action={action}", file=sys.stderr)
        
        prev_mode = self._set_message_box_mode(0)
        processed = 0
        
        try:
            # 첫 변경으로 이동
            if not self._run_action("TrackChangePrev"):
                print(f"[CellProcess] TrackChangePrev 실패 → 변경 없음", file=sys.stderr)
                return (False, 0)
            
            # ✅ 첫 진입 직후 셀 체크 (다른 셀로 점프했으면 즉시 중단)
            entry_cell = self._get_cell_a1_notation()
            if entry_cell != start_cell:
                print(f"[CellProcess] 첫 진입이 다른 셀 ({entry_cell}) → 현재 셀에 변경 없음", file=sys.stderr)
                return (False, 0)
            
            for i in range(100):  # 안전장치
                # 현재 셀 주소 확인
                current_cell = self._get_cell_a1_notation()
                print(f"[CellProcess] 반복 {i}: current_cell={current_cell}", file=sys.stderr)
                
                # ✅ 빈 값이면 표 밖으로 간주하고 중단
                if not current_cell:
                    print(f"[CellProcess] 셀 주소 없음 → 표 밖 또는 예외", file=sys.stderr)
                    break
                
                # 셀 경계 체크
                if current_cell != start_cell:
                    print(f"[CellProcess] 다른 셀로 이동 ({current_cell}) → 중단", file=sys.stderr)
                    break
                
                # 처리 + 이전으로 이동
                action_name = f"TrackChange{action}Prev"
                if not self._run_action(action_name):
                    print(f"[CellProcess] {action_name} 실패 → 개별 실행 폴백", file=sys.stderr)
                    if not self._run_action(f"TrackChange{action}"):
                        print(f"[CellProcess] TrackChange{action}도 실패 → break", file=sys.stderr)
                        break
                    if not self._run_action("TrackChangePrev"):
                        # 이전 변경 없음 → 현재 건 처리 완료
                        processed += 1
                        print(f"[CellProcess] TrackChangePrev 실패 (더 이상 변경 없음) → 처리됨: {processed}", file=sys.stderr)
                        break
                
                processed += 1
                print(f"[CellProcess] 처리됨: {processed}", file=sys.stderr)
                
        finally:
            if prev_mode is not None:
                self._set_message_box_mode(prev_mode)
        
        return (processed > 0, processed)

    def _set_message_box_mode(self, mode: int) -> Optional[int]:
        raw_hwp = self._raw_hwp()
        if hasattr(raw_hwp, "SetMessageBoxMode"):
            return raw_hwp.SetMessageBoxMode(mode)
        hwp = self.hwp
        if hasattr(hwp, "set_message_box_mode"):
            return hwp.set_message_box_mode(mode)
        return None
    
    def is_enabled(self) -> bool:
        """Track Changes 활성화 상태"""
        return self._is_enabled
    
    # ============================================================
    # 커서/선택 유틸리티
    # ============================================================
    
    def _save_cursor(self) -> Optional[Tuple[int, int, int]]:
        """현재 커서 위치 저장"""
        try:
            hwp = self.hwp
            if hasattr(hwp, 'get_pos'):
                return hwp.get_pos()
            return None
        except Exception as e:
            self._log("ERROR", f"커서 저장 실패: {e}")
            return None
    
    def _restore_cursor(self, pos: Optional[Tuple[int, int, int]]) -> bool:
        """커서 위치 복원"""
        if pos is None:
            return False
        try:
            hwp = self.hwp
            if hasattr(hwp, 'set_pos'):
                hwp.set_pos(*pos)
                return True
            return False
        except Exception as e:
            self._log("ERROR", f"커서 복원 실패: {e}")
            return False
    
    def _get_selection_range(self) -> Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """현재 선택 범위 반환 (SelectionMode 확인 후 GetSelectedPosBySet 사용)

        SelectionMode로 선택 상태를 확인:
        - 0일 수 있으므로 SelectionMode만으로 판단하지 않음
        - 1 (일반 블록): 정상 처리
        - 2 (열 선택): 정상 처리
        - 4 (표 셀 선택): 정상 처리
        - 8 (컨트롤 선택): None 반환

        Returns:
            ((start_list, start_para, start_pos), (end_list, end_para, end_pos)) 또는 None
            - 드래그 선택: (start, end) 반환
            - 선택 없음/클릭: None
        """
        try:
            import sys
            hwp = self.hwp
            raw_hwp = self._raw_hwp()

            # SelectionMode 확인 (우선순위 최상위)
            selection_mode_raw = 0
            selection_mode = 0
            if hasattr(raw_hwp, 'SelectionMode'):
                selection_mode_raw = raw_hwp.SelectionMode
                selection_mode = selection_mode_raw & 0x0F  # Strict 비트 제거
            is_table_selection = selection_mode == 4 or selection_mode_raw == 19
            _debug_log(f"[_get_selection_range] SelectionMode: raw={selection_mode_raw}, masked={selection_mode}, is_table={is_table_selection}")

            # 컨트롤 선택 (변경 처리 불가)
            if selection_mode == 8:
                _debug_log("[_get_selection_range] 컨트롤 선택 (mode=8) → None 반환")
                return None

            # 표 셀 선택 포함 모든 선택 → GetSelectedPosBySet 사용
            # 1순위: GetSelectedPosBySet (2022 이전 표준, 가장 정확)
            if hasattr(hwp, 'GetSelectedPosBySet') and hasattr(hwp, 'CreateSet'):
                try:
                    # ListParaPos ParameterSet 생성
                    sset = hwp.CreateSet("ListParaPos")
                    eset = hwp.CreateSet("ListParaPos")

                    # 선택 범위 가져오기
                    ok = hwp.GetSelectedPosBySet(sset, eset)
                    _debug_log(f"[_get_selection_range] GetSelectedPosBySet 결과: ok={ok}")

                    if ok:
                        start = (
                            sset.Item("List"),
                            sset.Item("Para"),
                            sset.Item("Pos")
                        )
                        end = (
                            eset.Item("List"),
                            eset.Item("Para"),
                            eset.Item("Pos")
                        )
                        if start == end:
                            _debug_log("[_get_selection_range] collapsed selection(start=end) -> None")
                            return None
                        _debug_log(f"[_get_selection_range] GetSelectedPosBySet 성공: start={start}, end={end}")
                        return (start, end)
                    else:
                        _debug_log("[_get_selection_range] GetSelectedPosBySet ok=False → fallback")
                except Exception as e:
                    _debug_log(f"[_get_selection_range] GetSelectedPosBySet 예외: {e}")
                    pass  # GetSelectedPosBySet 실패 시 fallback 진행

            # 2순위 fallback: pyhwpx 기반
            if hasattr(hwp, 'get_selection_pos'):
                _debug_log("[_get_selection_range] pyhwpx get_selection_pos() 시도")
                result = hwp.get_selection_pos()
                if result:
                    # 정규화: 다양한 반환 형태 처리
                    normalized = self._normalize_selection_result(result)
                    if normalized:
                        if normalized[0] == normalized[1]:
                            _debug_log("[_get_selection_range] pyhwpx collapsed selection -> None")
                            return None
                        _debug_log(f"[_get_selection_range] pyhwpx 성공: {normalized}")
                        return normalized

            # 3순위 fallback: GetSelectionPos
            if hasattr(hwp, 'GetSelectionPos'):
                _debug_log("[_get_selection_range] GetSelectionPos() 시도")
                result = hwp.GetSelectionPos()
                if result:
                    # 정규화: 다양한 반환 형태 처리
                    normalized = self._normalize_selection_result(result)
                    if normalized:
                        if normalized[0] == normalized[1]:
                            _debug_log("[_get_selection_range] GetSelectionPos collapsed selection -> None")
                            return None
                        _debug_log(f"[_get_selection_range] GetSelectionPos 성공: {normalized}")
                        return normalized

            _debug_log("[_get_selection_range] 모든 시도 실패 → None 반환")
            return None
        except Exception as e:
            return None

    def _normalize_selection_result(self, result) -> Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """선택 범위 결과를 표준 형식으로 정규화

        다양한 반환 형태 처리:
        - ((slist,spara,spos), (elist,epara,epos)) → 그대로 반환
        - (spara, spos, epara, epos) → ((0,spara,spos), (0,epara,epos))
        - (slist, spara, spos, elist, epara, epos) → ((slist,spara,spos), (elist,epara,epos))

        Returns:
            ((start_list, start_para, start_pos), (end_list, end_para, end_pos)) 또는 None
        """
        try:
            if not result:
                return None

            # 이미 정규화된 형태: ((s_tuple), (e_tuple))
            if (isinstance(result, tuple) and len(result) == 2
                and isinstance(result[0], tuple) and len(result[0]) == 3
                and isinstance(result[1], tuple) and len(result[1]) == 3):
                return result

            # 4개 형태: (spara, spos, epara, epos) - list 없음
            if isinstance(result, tuple) and len(result) == 4:
                spara, spos, epara, epos = result
                return ((0, spara, spos), (0, epara, epos))

            # 6개 형태: (slist, spara, spos, elist, epara, epos)
            if isinstance(result, tuple) and len(result) == 6:
                slist, spara, spos, elist, epara, epos = result
                return ((slist, spara, spos), (elist, epara, epos))

            self._log("WARNING", f"알 수 없는 선택 범위 형태: {result}")
            return None

        except Exception as e:
            self._log("ERROR", f"선택 범위 정규화 실패: {e}")
            return None

    def _run_action(self, action_id: str) -> bool:
        """HAction.Run() 실행"""
        try:
            hwp = self.hwp
            if hasattr(hwp, 'HAction'):
                result = hwp.HAction.Run(action_id)
                return bool(result)
            if hasattr(self._connector, 'run_action'):
                return self._connector.run_action(action_id)
            return False
        except Exception as e:
            self._log("ERROR", f"Action 실행 실패 ({action_id}): {e}")
            return False

    def _is_connector_alive(self) -> bool:
        if hasattr(self._connector, 'check_alive'):
            try:
                return bool(self._connector.check_alive())
            except Exception:
                return False
        return True

    def _is_action_enabled(self, action_id: str) -> bool:
        """액션 활성화 상태 확인 (팝업 없이)

        IsActionEnable()을 사용하여 액션 실행 가능 여부만 확인
        - 팝업이나 커서 이동 없음
        - 변경사항 존재 여부 확인에 적합

        Args:
            action_id: 확인할 액션 ID

        Returns:
            bool: 액션 실행 가능하면 True
        """
        try:
            hwp = self.hwp
            raw_hwp = self._raw_hwp()
            if hasattr(raw_hwp, 'IsActionEnable'):
                return bool(raw_hwp.IsActionEnable(action_id))
            return False
        except Exception as e:
            return False
    
    def _cancel_selection(self) -> None:
        """선택 해제 (안전장치)"""
        try:
            self._run_action("Cancel")
        except Exception:
            pass
    
    # ============================================================
    # Track Changes 활성화/비활성화
    # ============================================================
    
    def enable(self) -> bool:
        """Track Changes 활성화"""
        try:
            if hasattr(self._connector, 'start_track_changes'):
                result = self._connector.start_track_changes()
                if result:
                    self._is_enabled = True
                return result
            
            # 직접 API 호출
            if hasattr(self._connector, 'set_track_changes'):
                self._connector.set_track_changes(True)
            if hasattr(self._connector, 'set_track_changes_display_mode'):
                self._connector.set_track_changes_display_mode(1)
            
            self._is_enabled = True
            return True
        except Exception as e:
            self._log("ERROR", f"Track Changes 활성화 실패: {e}")
            return False
    
    def disable(self) -> bool:
        """Track Changes 비활성화 (MenuExTrackChange 토글)

        전제: enable()에서 이미 변경추적모드를 켰으므로, 한 번 더 토글하면 꺼짐.
        상태 확인 API가 없으므로 무조건 토글 실행.
        """
        try:
            if hasattr(self._connector, 'stop_track_changes'):
                result = self._connector.stop_track_changes()
                if result:
                    self._is_enabled = False
                return result

            current = None
            if hasattr(self._connector, 'get_track_changes_state'):
                current = _normalize_track_changes_flag(self._connector.get_track_changes_state())
            else:
                current = getattr(self.hwp, 'lsTrackChange', None)
                current = _normalize_track_changes_flag(current)

            if current is False:
                self._is_enabled = False
                return True

            result = self._run_action("MenuExTrackChange")
            if result:
                self._is_enabled = False
            return result
        except Exception as e:
            self._log("ERROR", f"Track Changes 비활성화 실패: {e}")
            return False
    
    # ============================================================
    # 전체 승인/거절
    # ============================================================
    
    def accept_all(self) -> bool:
        """전체 변경 승인 (TrackChangeApplyAll)

        모든 변경 처리 완료 여부는 API 레이어에서 확인하여 disable() 호출
        """
        try:
            self._cancel_selection()
            result = self._run_action("TrackChangeApplyAll")
            return True
        except Exception as e:
            self._log("ERROR", f"전체 승인 실패: {e}")
            return False
    
    def reject_all(self) -> bool:
        """전체 변경 거절 (TrackChangeCancelAll)

        모든 변경 처리 완료 여부는 API 레이어에서 확인하여 disable() 호출
        """
        try:
            self._cancel_selection()
            result = self._run_action("TrackChangeCancelAll")
            return True
        except Exception as e:
            self._log("ERROR", f"전체 거절 실패: {e}")
            return False
    
    # ============================================================
    # 부분 승인/거절 (핵심)
    # ============================================================
    
    def accept_current(self) -> bool:
        """현재 변경 1건 승인 (TrackChangeApply)
        
        커서가 변경 표시 위에 있을 때 해당 변경만 승인
        """
        try:
            result = self._run_action("TrackChangeApply")
            return result
        except Exception as e:
            self._log("ERROR", f"현재 변경 승인 실패: {e}")
            return False
    
    def reject_current(self) -> bool:
        """현재 변경 1건 거절 (TrackChangeCancel)

        커서가 변경 표시 위에 있을 때 해당 변경만 거절
        """
        try:
            result = self._run_action("TrackChangeCancel")
            return result
        except Exception as e:
            self._log("ERROR", f"현재 변경 거절 실패: {e}")
            return False

    def accept_selected(self, count: Optional[int] = None, cached_range: Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = None) -> Tuple[bool, int]:
        """선택 영역 내 변경 승인

        SelectionMode에 따라 처리 방식 자동 선택:
        - 표 셀 선택: 셀 블록 범위를 선형 좌표로 변환 후 역방향 루프
        - SelectionMode==1,2 (일반/열 선택): 역방향 루프 방식

        Args:
            count: 처리할 건수 (None이면 선택 범위 내 모든 변경)
            cached_range: 캐시된 선택 범위 (버튼 클릭 시 선택 해제 대응)

        Returns:
            (성공 여부, 처리된 건수)
            - 선택 없음(클릭 모드): 현재 변경 1건 처리

        사용 API:
        - TrackChangeApplyPrev (역방향)
        """
        try:
            import sys

            # SelectionMode 먼저 확인
            selection_mode_raw, selection_mode = self._get_selection_mode()
            print(f"[AcceptSelected] SelectionMode: raw={selection_mode_raw}, masked={selection_mode}", file=sys.stderr)

            # ✅ 캐시된 범위 우선 사용
            if cached_range is not None:
                selection = cached_range
                print(f"[AcceptSelected] 캐시된 범위 사용: {selection}", file=sys.stderr)
            else:
                selection = self._get_selection_range()
                print(f"[AcceptSelected] _get_selection_range() 결과: {selection}", file=sys.stderr)

            # 선택 없음: 처리하지 않음
            if selection is None:
                print("[AcceptSelected] selection=None → (False, 0) 반환", file=sys.stderr)
                return (False, 0)

            start_pos, end_pos = selection
            print(f"[AcceptSelected] start_pos={start_pos}, end_pos={end_pos}", file=sys.stderr)

            # 좌표 정규화 (end < start이면 swap)
            if end_pos < start_pos:
                print(f"[AcceptSelected] 좌표 swap: {end_pos} <-> {start_pos}", file=sys.stderr)
                start_pos, end_pos = end_pos, start_pos

            # 표 셀 선택 시 셀 주소 로깅
            is_table_sel = selection_mode == 4 or selection_mode_raw == 19
            print(f"[AcceptSelected] is_table_sel={is_table_sel}", file=sys.stderr)
            if is_table_sel:
                # 시작 셀 주소
                saved = self._save_cursor()
                hwp = self.hwp
                if hasattr(hwp, 'set_pos'):
                    hwp.set_pos(*start_pos)
                    start_cell = self._get_cell_a1_notation()
                    # 끝 셀 주소 (epos-1 보정)
                    end_pos_adjusted = (end_pos[0], end_pos[1], max(0, end_pos[2] - 1))
                    hwp.set_pos(*end_pos_adjusted)
                    end_cell = self._get_cell_a1_notation()
                    self._restore_cursor(saved)
                    if start_cell and end_cell:
                        print(f"[AcceptSelected] 표 셀 범위: {start_cell}:{end_cell}", file=sys.stderr)

            saved_pos = self._save_cursor()

            # 위치 비교 함수 (list > para > pos 순서)
            def compare_pos(a, b):
                """a와 b 비교. a < b면 -1, a == b면 0, a > b면 1"""
                if a[0] != b[0]:
                    return -1 if a[0] < b[0] else 1
                if a[1] != b[1]:
                    return -1 if a[1] < b[1] else 1
                if a[2] != b[2]:
                    return -1 if a[2] < b[2] else 1
                return 0

            # 1. 선택 끝으로 이동 (선택 유지)
            hwp = self.hwp
            if hasattr(hwp, 'set_pos'):
                hwp.set_pos(*end_pos)
            else:
                self._log("WARNING", "set_pos 메서드 없음 - 역방향 시작점 불확실")

            prev_mode = self._set_message_box_mode(0)
            try:
                # 2. 첫 변경 항목으로 진입 (TrackChangePrev 1회)
                print(f"[AcceptSelected] TrackChangePrev 호출 (첫 진입)", file=sys.stderr)
                if not self._run_action("TrackChangePrev"):
                    print("[AcceptSelected] TrackChangePrev 실패 → 선택 범위 내 변경 없음", file=sys.stderr)
                    # 선택 끝 이전에 변경 없음
                    self._restore_cursor(saved_pos)
                    return (False, 0)

                processed = 0
                max_iterations = 100  # 안전장치: 최대 100건만 처리
                prev_pos = None  # 정체 감지용
                same_pos_hits = 0

                # 3. ApplyPrev 반복 (처리 + 자동 이동)
                for i in range(max_iterations):
                    # 현재 변경 위치 확인
                    current_pos = self._get_pos()
                    print(f"[AcceptSelected] 반복 {i}: current_pos={current_pos}, start_pos={start_pos}", file=sys.stderr)
                    if current_pos is None:
                        print("[AcceptSelected] current_pos=None → break", file=sys.stderr)
                        break

                    # 정체 감지: 같은 좌표가 반복되어도 즉시 중단하지 않고 제한 재시도
                    if prev_pos is not None and current_pos == prev_pos:
                        same_pos_hits += 1
                        print(f"[AcceptSelected] 같은 위치 반복 감지: hits={same_pos_hits}", file=sys.stderr)
                        if same_pos_hits >= 4:
                            print("[AcceptSelected] 같은 위치 반복 한도 초과 → break", file=sys.stderr)
                            break
                    else:
                        same_pos_hits = 0
                    prev_pos = current_pos

                    # 범위 체크: 시작 이전이면 종료 (명시적 비교)
                    if compare_pos(current_pos, start_pos) < 0:
                        print(f"[AcceptSelected] 범위 초과 (current < start) → break", file=sys.stderr)
                        break

                    # 승인 + 이전 변경으로 자동 이동
                    result = self._run_action("TrackChangeApplyPrev")
                    print(f"[AcceptSelected] TrackChangeApplyPrev 결과: {result}", file=sys.stderr)
                    if not result:
                        print(f"[AcceptSelected] ApplyPrev 실패 → Apply + Prev 폴백", file=sys.stderr)
                        # Cancel + Prev 폴백 (반드시 세트로)
                        if not self._run_action("TrackChangeApply"):
                            print("[AcceptSelected] Apply도 실패 → break", file=sys.stderr)
                            break
                        if not self._run_action("TrackChangePrev"):
                            # 이전 변경 없음 → 범위 내 처리 완료
                            processed += 1
                            break

                    processed += 1
                    print(f"[AcceptSelected] 처리됨: processed={processed}", file=sys.stderr)

                    # 지정 건수 도달
                    if count and processed >= count:
                        break
            finally:
                if prev_mode is not None:
                    self._set_message_box_mode(prev_mode)

            self._restore_cursor(saved_pos)
            return (processed > 0, processed)

        except Exception as e:
            self._log("ERROR", f"선택 영역 승인 실패: {e}")
            return (False, 0)
    
    def reject_selected(self, count: Optional[int] = None, cached_range: Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = None) -> Tuple[bool, int]:
        """선택 영역 내 변경 거절

        SelectionMode에 따라 처리 방식 자동 선택:
        - 표 셀 선택: 셀 블록 범위를 선형 좌표로 변환 후 역방향 루프
        - SelectionMode==1,2 (일반/열 선택): 역방향 루프 방식

        Args:
            count: 처리할 건수 (None이면 선택 범위 내 모든 변경)
            cached_range: 캐시된 선택 범위 (버튼 클릭 시 선택 해제 대응)

        Returns:
            (성공 여부, 처리된 건수)
            - 선택 없음(클릭 모드): 현재 변경 1건 처리

        사용 API:
        - TrackChangeCancelPrev (역방향)
        """
        try:
            import sys

            # ✅ 캐시된 범위 우선 사용
            if cached_range is not None:
                selection = cached_range
                print(f"[RejectSelected] 캐시된 범위 사용: {selection}", file=sys.stderr)
            else:
                selection = self._get_selection_range()
                print(f"[RejectSelected] _get_selection_range() 결과: {selection}", file=sys.stderr)

            # 선택 없음: 처리하지 않음
            if selection is None:
                print("[RejectSelected] selection=None → (False, 0) 반환", file=sys.stderr)
                return (False, 0)

            start_pos, end_pos = selection
            print(f"[RejectSelected] start_pos={start_pos}, end_pos={end_pos}", file=sys.stderr)

            # 좌표 정규화 (end < start이면 swap)
            if end_pos < start_pos:
                start_pos, end_pos = end_pos, start_pos

            # 표 셀 선택 시 셀 주소 로깅
            selection_mode_raw, selection_mode = self._get_selection_mode()
            is_table_sel = selection_mode == 4 or selection_mode_raw == 19
            if is_table_sel:
                # 시작 셀 주소
                saved = self._save_cursor()
                hwp = self.hwp
                if hasattr(hwp, 'set_pos'):
                    hwp.set_pos(*start_pos)
                    start_cell = self._get_cell_a1_notation()
                    # 끝 셀 주소 (epos-1 보정)
                    end_pos_adjusted = (end_pos[0], end_pos[1], max(0, end_pos[2] - 1))
                    hwp.set_pos(*end_pos_adjusted)
                    end_cell = self._get_cell_a1_notation()
                    self._restore_cursor(saved)
                    if start_cell and end_cell:
                        self._log("ERROR", f"[표 셀 선택] 범위: {start_cell}:{end_cell}")

            saved_pos = self._save_cursor()

            # 위치 비교 함수 (list > para > pos 순서)
            def compare_pos(a, b):
                """a와 b 비교. a < b면 -1, a == b면 0, a > b면 1"""
                if a[0] != b[0]:
                    return -1 if a[0] < b[0] else 1
                if a[1] != b[1]:
                    return -1 if a[1] < b[1] else 1
                if a[2] != b[2]:
                    return -1 if a[2] < b[2] else 1
                return 0

            # 1. 선택 끝으로 이동 (선택 유지)
            hwp = self.hwp
            if hasattr(hwp, 'set_pos'):
                hwp.set_pos(*end_pos)
            else:
                self._log("WARNING", "set_pos 메서드 없음 - 역방향 시작점 불확실")

            prev_mode = self._set_message_box_mode(0)
            try:
                # 2. 첫 변경 항목으로 진입 (TrackChangePrev 1회)
                if not self._run_action("TrackChangePrev"):
                    # 선택 끝 이전에 변경 없음
                    self._restore_cursor(saved_pos)
                    return (False, 0)

                processed = 0
                max_iterations = 100  # 안전장치: 최대 100건만 처리
                prev_pos = None  # 정체 감지용
                same_pos_hits = 0

                # 3. CancelPrev 반복 (처리 + 자동 이동)
                for i in range(max_iterations):
                    # 현재 변경 위치 확인
                    current_pos = self._get_pos()
                    print(f"[TrackChanges] 반복 {i}: current_pos={current_pos}, start_pos={start_pos}", file=sys.stderr)
                    if current_pos is None:
                        print("[TrackChanges] current_pos=None → break", file=sys.stderr)
                        break

                    # 정체 감지: 같은 좌표가 반복되어도 즉시 중단하지 않고 제한 재시도
                    if prev_pos is not None and current_pos == prev_pos:
                        same_pos_hits += 1
                        print(f"[TrackChanges] 같은 위치 반복 감지: hits={same_pos_hits}", file=sys.stderr)
                        if same_pos_hits >= 4:
                            print("[TrackChanges] 같은 위치 반복 한도 초과 → break", file=sys.stderr)
                            break
                    else:
                        same_pos_hits = 0
                    prev_pos = current_pos

                    # 범위 체크: 시작 이전이면 종료 (명시적 비교)
                    if compare_pos(current_pos, start_pos) < 0:
                        print(f"[TrackChanges] 범위 초과 (current < start) → break", file=sys.stderr)
                        break

                    # 거절 + 이전 변경으로 자동 이동
                    result = self._run_action("TrackChangeCancelPrev")
                    if not result:
                        print(f"[TrackChanges] CancelPrev 실패 → Cancel + Prev 폴백", file=sys.stderr)
                        # Cancel + Prev 폴백 (반드시 세트로)
                        if not self._run_action("TrackChangeCancel"):
                            print("[TrackChanges] Cancel도 실패 → break", file=sys.stderr)
                            break
                        if not self._run_action("TrackChangePrev"):
                            # 이전 변경 없음 → 범위 내 처리 완료
                            processed += 1
                            break

                    processed += 1
                    print(f"[TrackChanges] 처리됨: processed={processed}", file=sys.stderr)

                    # 지정 건수 도달
                    if count and processed >= count:
                        break
            finally:
                if prev_mode is not None:
                    self._set_message_box_mode(prev_mode)

            self._restore_cursor(saved_pos)
            return (processed > 0, processed)

        except Exception as e:
            self._log("ERROR", f"선택 영역 거절 실패: {e}")
            return (False, 0)
    
    # ============================================================
    # 변경 감지 및 카운트
    # ============================================================
    
    def has_remaining_changes(self) -> bool:
        """문서에 남은 변경이 있는지 확인 (팝업 없이)

        커서 이동 없이 Action 활성 상태로만 확인

        Returns:
            bool: 남은 변경 있으면 True
        """
        try:
            has_changes = (
                self._is_action_enabled("TrackChangeApply")
                or self._is_action_enabled("TrackChangeCancel")
            )
            _debug_log(f"[TrackChanges] has_remaining_changes: {has_changes}")
            return has_changes
        except Exception as e:
            self._log("ERROR", f"남은 변경 확인 실패: {e}")
            return False
    
    def count_selected_changes(self) -> int:
        """선택 영역 내 변경 개수 카운트

        UI 표시용: "부분 (n건)" 계산

        알고리즘:
        - 선택이 있으면 1 반환 (정확한 개수 대신 존재 여부만)
        - 선택 없으면 현재 변경 존재 여부에 따라 0/1 반환

        Returns:
            int: 선택 범위 내 변경 개수 (존재 여부용)
        """
        try:
            selection = self._get_selection_range()

            if selection is None:
                if self._is_table_selection():
                    return 0
                # 클릭 모드: 현재 변경 1건 여부 확인
                if self._is_action_enabled("TrackChangeApply") or self._is_action_enabled("TrackChangeCancel"):
                    return 1
                return 0
            return 1

        except Exception as e:
            self._log("ERROR", f"선택 영역 변경 카운트 실패: {e}")
            return 0
    
    def _is_pos_in_range(
        self,
        pos: Tuple[int, int, int],
        start: Tuple[int, int, int],
        end: Tuple[int, int, int],
    ) -> bool:
        """위치가 범위 내에 있는지 확인"""
        return start <= pos <= end
    
    # ============================================================
    # UI 상태 계산
    # ============================================================
    
    def get_button_state(self) -> dict:
        """UI 버튼 상태 계산

        Returns:
            {
                "show_full_buttons": bool,      # 전체 승인/거절 버튼 표시 여부
                "show_partial_buttons": bool,   # 부분 승인/거절 버튼 표시 여부
                "selected_count": int,          # 선택된 변경 개수
                "has_remaining": bool,          # 남은 변경 있음
            }
        """
        has_remaining = self.has_remaining_changes()
        selected_count = self.count_selected_changes()

        return {
            "show_full_buttons": has_remaining,
            # 부분 버튼도 남은 변경이 있을 때만 표시
            "show_partial_buttons": has_remaining and selected_count > 0,
            "selected_count": selected_count,
            "has_remaining": has_remaining,
        }
    
    def process_and_check_remaining(
        self,
        action: str,
        scope: str = "all",
        count: Optional[int] = None,
    ) -> dict:
        """처리 후 남은 변경 확인
        
        Args:
            action: "accept" 또는 "reject"
            scope: "all" (전체) 또는 "selected" (선택 영역)
            count: 선택 영역일 때 처리할 건수
        
        Returns:
            {
                "success": bool,
                "processed": int,
                "has_remaining": bool,
                "auto_complete": bool,  # 모든 변경 처리 완료 (0건 남음)
            }
        """
        # 처리 실행
        if scope == "all":
            if action == "accept":
                success = self.accept_all()
            else:
                success = self.reject_all()
            processed = -1  # 전체 처리는 개수 미확인
        else:
            if action == "accept":
                success, processed = self.accept_selected(count)
            else:
                success, processed = self.reject_selected(count)
        
        # 남은 변경 확인
        has_remaining = self.has_remaining_changes()
        auto_complete = not has_remaining
        
        
        return {
            "success": success,
            "processed": processed,
            "has_remaining": has_remaining,
            "auto_complete": auto_complete,
        }

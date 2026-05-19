"""
HWP COM 연결 관리

SafeHwp 패턴을 통한 PID/HWND 기반 정확한 HWP 바인딩 및 기본 조작 기능 제공
"""

import sys
from typing import Optional, Tuple
from processing.extraction.hwp_raw_wrapper import HwpRawWrapper
from engine.connection.com_manager import SafeHwpBinder
from engine.state import session_state


# 연속 실패 로그 억제를 위한 클래스 변수
_consecutive_bind_failures = 0
_LOG_SUPPRESSION_THRESHOLD = 1  # 첫 번째 실패만 로그 출력

class HwpConnector:
    """HWP COM 연결 관리 클래스

    SafeHwp 패턴으로 여러 HWP 프로세스 중 정확한 대상 바인딩 지원
    """

    def __init__(self, visible: bool = True, new: bool = False,
                 process_identifier: Optional[int] = None, window_handle: Optional[int] = None):
        """초기화

        Args:
            visible: 한글 창을 표시할지 여부
            new: 새 인스턴스를 생성할지(True) 또는 기존 인스턴스에 연결할지(False)
            process_identifier: 바인딩할 HWP 프로세스 ID (None이면 자동 선택)
            window_handle: 바인딩할 HWP 윈도우 핸들 (선택적)
        """
        self.visible = visible
        self.new = new
        self.process_identifier = process_identifier
        self.window_handle = window_handle
        self._hwp: Optional[HwpRawWrapper] = None
        self._binder: Optional[SafeHwpBinder] = None

    @property
    def hwp(self) -> Optional[HwpRawWrapper]:
        """HWP COM 객체 반환"""
        return self._hwp

    def connect(self) -> bool:
        """COM 객체 연결

        SafeHwp 패턴으로 PID/HWND 기반 바인딩 시도.
        전역 상태에 저장된 PID/HWND를 우선 사용.
        실패 시 기존 pyhwpx 방식으로 폴백.

        Returns:
            bool: 연결 성공 여부
        """
        global _consecutive_bind_failures
        
        try:
            # 전역 상태에서 PID/HWND 가져오기 (우선순위)
            pid = self.process_identifier or session_state.retrieve_runtime_target_process_id()
            hwnd = self.window_handle or session_state.retrieve_runtime_target_window_handle()

            # SafeHwp 패턴: PID/HWND 기반 바인딩
            self._binder = SafeHwpBinder(
                process_identifier=pid,
                window_handle=hwnd,
                allow_fallback=False,
                allow_create=False  # 새 인스턴스 생성 금지
            )

            if self._binder.bind():
                return self._initialize_from_binder()
            else:
                # 바인딩 실패 - 폴백 없이 실패 반환
                if pid or hwnd:
                    if not self._is_target_alive(pid, hwnd):
                        print(f"[HwpConnector] Target PID={pid}/HWND={hwnd} is dead, clearing state", file=sys.stderr)
                        session_state.store_runtime_target_process_id(None)
                        session_state.store_runtime_target_window_handle(None)
                        self.process_identifier = None
                        self.window_handle = None

                        # 죽은 프로세스 제거 후 다시 바인딩 시도
                        self._binder = SafeHwpBinder(
                            allow_fallback=False,
                            allow_create=False
                        )  # 새 인스턴스 생성 금지
                        if self._binder.bind():
                            return self._initialize_from_binder()

                    _consecutive_bind_failures += 1
                    if _consecutive_bind_failures <= _LOG_SUPPRESSION_THRESHOLD:
                        print("[HwpConnector] SafeHwp 바인딩 실패: 지정된 PID/HWND에 연결 불가", file=sys.stderr)
                    return False

                # PID/HWND 없이 바인딩 실패 - 새문서 생성 방지
                _consecutive_bind_failures += 1
                if _consecutive_bind_failures <= _LOG_SUPPRESSION_THRESHOLD:
                    print("[HwpConnector] SafeHwp 바인딩 실패: 열린 HWP 문서 없음", file=sys.stderr)
                return False

        except Exception as e:
            print(f"HWP 연결 실패: {e}", file=sys.stderr)
            return False

    def _initialize_from_binder(self) -> bool:
        """바인딩된 COM 객체를 pyhwpx.Hwp로 래핑"""
        if not self._binder:
            return False

        raw_hwp = self._binder.get_hwp()
        if raw_hwp is None:
            return False

        # Raw COM 객체를 HwpRawWrapper로 래핑 (pyhwpx 초기화 문제 우회)
        from processing.extraction.hwp_raw_wrapper import HwpRawWrapper
        self._hwp = HwpRawWrapper(raw_hwp)

        pid, hwnd = self._binder.get_pid_hwnd()
        global _consecutive_bind_failures
        _consecutive_bind_failures = 0  # 성공 시 카운터 리셋
        print(f"SafeHwp 바인딩 성공: PID={pid}, HWND={hwnd}", file=sys.stderr)

        # 전역 상태에 PID/HWND 저장
        session_state.store_runtime_target_process_id(pid)
        session_state.store_runtime_target_window_handle(hwnd)

        return True

    def _is_target_alive(self, pid: Optional[int], hwnd: Optional[int]) -> bool:
        if pid is None and hwnd is None:
            return True

        if hwnd:
            try:
                import ctypes
                if ctypes.windll.user32.IsWindow(int(hwnd)) == 0:
                    return False
            except Exception:
                pass

        if pid:
            try:
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
                )
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                STILL_ACTIVE = 259
                if exit_code.value != STILL_ACTIVE:
                    return False
            except Exception:
                pass

        return True

    def disconnect(self):
        """COM 객체 연결 해제 (HWP 창은 종료하지 않음)"""
        if self._hwp:
            # quit()을 호출하면 HWP 창이 종료되므로 제거
            # COM 참조만 해제
            self._hwp = None

    def check_alive(self) -> bool:
        """생존 확인

        Returns:
            bool: HWP 객체가 살아있는지 여부
        """
        if self._hwp is None:
            return False

        try:
            # 강제 COM 호출로 연결 상태 확인
            if hasattr(self._hwp, "get_pos"):
                _ = self._hwp.get_pos()
            else:
                _ = self._hwp.Path
            return True
        except Exception as e:
            if self._is_disconnected_error(e):
                return False
            return False

    def validate_and_reconnect(self) -> bool:
        """COM 객체 검증 및 무효화 시 즉시 재바인딩

        Returns:
            bool: 검증 성공 또는 재바인딩 성공 여부
        """
        # 기존 객체가 살아있으면 OK
        if self.check_alive():
            return True

        # 무효화되었으면 재바인딩 시도
        print("[HwpConnector] COM 객체 무효화 감지, 재바인딩 시도", file=sys.stderr)

        # 기존 연결 정리
        self.disconnect()

        # 재연결 시도
        success = self.connect()
        if success:
            print("[HwpConnector] 재바인딩 성공", file=sys.stderr)
        else:
            print("[HwpConnector] 재바인딩 실패", file=sys.stderr)

        return success

    def _is_disconnected_error(self, error: Exception) -> bool:
        """COM 연결 끊김 에러인지 확인

        처리하는 에러 코드:
        - RPC_E_DISCONNECTED (-2147417848 / 0x80010108)
        - CO_E_OBJNOTCONNECTED (-2147220995 / 0x800401FD) - "개체가 서버에 연결되지 않았습니다"
        - E_NOINTERFACE (-2147023179 / 0x80004002) - "알 수 없는 인터페이스입니다"
        - RPC_E_FAULT (-2147023130 / 0x80010006) - "원격 프로시저 호출(RPC)에 내부 오류가 발생했습니다"
        """
        msg = str(error).upper()
        # 문자열 기반 검사
        if "RPC_E_DISCONNECTED" in msg or "RPC_E_FAULT" in msg or "RPC" in msg:
            return True
        if "OBJNOTCONNECTED" in msg or "800401FD" in msg or "-2147220995" in str(error):
            return True
        if "알 수 없는 인터페이스" in str(error) or "80004002" in msg or "-2147023179" in str(error):
            return True
        if "80010006" in msg or "-2147023130" in str(error):
            return True

        try:
            import pywintypes
            if isinstance(error, pywintypes.com_error):
                # RPC/COM 연결 에러 코드들
                if error.hresult in (-2147417848, -2147220995, -2147023179, -2147023130):
                    return True
        except Exception:
            pass
        return False

    def set_pos(self, list_pos: int, para_pos: int, char_pos: int) -> bool:
        """위치 이동

        Args:
            list_pos: 리스트 위치 (0-based)
            para_pos: 문단 위치 (0-based)
            char_pos: 문자 위치 (0-based)

        Returns:
            bool: 이동 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.set_pos(list_pos, para_pos, char_pos)
            return True
        except Exception as e:
            print(f"위치 이동 실패: {e}", file=sys.stderr)
            return False

    def get_pos(self) -> Optional[tuple]:
        """현재 커서 위치 조회

        Returns:
            Optional[tuple]: (list, para, char) 튜플 또는 None
        """
        if not self.check_alive():
            return None

        try:
            return self._hwp.get_pos()
        except Exception as e:
            print(f"위치 조회 실패: {e}", file=sys.stderr)
            return None

    def find_text(self, text: str, forward: bool = True, case_sensitive: bool = False) -> bool:
        """텍스트 검색

        pyhwpx: find() 메서드 사용

        Args:
            text: 검색할 텍스트
            forward: 전방 검색 여부 (True: 앞으로, False: 뒤로)
            case_sensitive: 대소문자 구분 여부

        Returns:
            bool: 검색 성공 여부
        """
        if not self.check_alive():
            return False

        # 메시지박스 비활성화 ("문서의 끝까지 찾았습니다" 알람 방지)
        prev_mode = None
        if hasattr(self._hwp, 'SetMessageBoxMode'):
            try:
                prev_mode = self._hwp.SetMessageBoxMode(0)
            except Exception:
                pass

        try:
            direction = "Forward" if forward else "Backward"
            # pyhwpx의 find() 메서드 사용
            found = self._hwp.find(text, direction=direction, regex=False)
            return bool(found)
        except Exception as e:
            print(f"텍스트 검색 실패: {e}", file=sys.stderr)
            return False
        finally:
            # 원래 메시지박스 모드 복원
            if prev_mode is not None and hasattr(self._hwp, 'SetMessageBoxMode'):
                try:
                    self._hwp.SetMessageBoxMode(prev_mode)
                except Exception:
                    pass

    def insert_text(self, text: str) -> bool:
        """텍스트 삽입 (2022-호환: HAction.Run("InsertText") fallback)

        Args:
            text: 삽입할 텍스트

        Returns:
            bool: 삽입 성공 여부
        """
        if not self.check_alive():
            print("insert_text: check_alive 실패", file=sys.stderr)
            return False

        try:
            # 1차: pyhwpx insert_text 시도
            result = self._hwp.insert_text(text)
            if isinstance(result, bool) and result:
                return True
            elif result is not None and not isinstance(result, bool):
                # 성공으로 간주 (예외가 발생하지 않았으므로)
                return True
        except Exception as e:
            print(f"pyhwpx insert_text 실패: {e}, HAction fallback 시도", file=sys.stderr)

        # 2차 fallback: HAction.Run("InsertText") - 구버전 호환성
        try:
            if hasattr(self._hwp, 'HAction') and hasattr(self._hwp, 'HParameterSet'):
                pset = self._hwp.HParameterSet.HInsertText
                self._hwp.HAction.GetDefault("InsertText", pset.HSet)
                pset.Text = text
                result = self._hwp.HAction.Execute("InsertText", pset.HSet)
                if result:
                    return True
        except Exception as e2:
            print(f"HAction InsertText fallback 실패: {e2}", file=sys.stderr)

        return False

    def delete_selected(self) -> bool:
        """선택 영역 삭제

        pyhwpx: DeleteBack() 메서드 사용

        Returns:
            bool: 삭제 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # pyhwpx의 DeleteBack() 메서드 사용
            self._hwp.DeleteBack()
            return True
        except Exception as e:
            print(f"선택 영역 삭제 실패: {e}", file=sys.stderr)
            return False

    def select_text(self, start_pos: tuple, end_pos: tuple) -> bool:
        """텍스트 선택

        Args:
            start_pos: 시작 위치 (list, para, char)
            end_pos: 종료 위치 (list, para, char)

        Returns:
            bool: 선택 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 방법 1: selection_mode 속성 사용 (pyhwpx 일부 버전)
            if hasattr(self._hwp, 'selection_mode'):
                # 시작 위치로 이동
                self.set_pos(*start_pos)

                # 선택 시작
                self._hwp.selection_mode = 1

                # 종료 위치로 이동
                self.set_pos(*end_pos)

                # 선택 종료
                self._hwp.selection_mode = 0

                return True

            # 방법 2: Action 기반 fallback
            # 시작 위치로 이동
            if not self.set_pos(*start_pos):
                return False

            # SelectMode 액션 시도
            if not self.run_action("SelectMode"):
                # SelectMode 실패 시 다른 방법 시도
                # MarkBegin으로 선택 시작 표시
                self.run_action("MarkBegin")

            # 종료 위치로 이동
            if not self.set_pos(*end_pos):
                return False

            # 선택 완료 (이미 선택된 상태)
            return True

        except Exception as e:
            print(f"텍스트 선택 실패: {e}", file=sys.stderr)
            return False

    def get_text(self) -> Optional[str]:
        """문서 전체 텍스트 가져오기 (scan 기반)

        pyhwpx 패턴: init_scan → get_text 반복 → release_scan

        Returns:
            Optional[str]: 문서 텍스트 또는 None
        """
        if not self.check_alive():
            return None

        try:
            # 문서 처음으로 이동
            self.set_pos(0, 0, 0)

            # scan 시작 (문서 전체 스캔)
            scan_config = {
                "option": 4,      # 스캔 옵션
                "range": 0x0017,  # 전체 범위
                "spara": 0,       # 시작 문단
                "spos": 0         # 시작 위치
            }
            self._hwp.init_scan(**scan_config)

            # 텍스트 누적
            accumulated_text = []

            while True:
                # get_text()는 (state, text) 튜플 반환
                state, text = self._hwp.get_text()

                # state <= 1: 문서 끝
                if state <= 1:
                    break

                # 텍스트 추가 (빈 문자열 아닐 경우에만)
                if text:
                    accumulated_text.append(text)

                # 다음 위치로 이동 (201: 다음 문단)
                self._hwp.move_pos(201)

            # 스캔 종료
            self._hwp.release_scan()

            # 전체 텍스트 반환
            return "".join(accumulated_text)

        except Exception as e:
            print(f"텍스트 가져오기 실패: {e}", file=sys.stderr)
            # 오류 시에도 스캔 종료
            try:
                self._hwp.release_scan()
            except:
                pass
            return None

    def open_file(self, file_path: str) -> bool:
        """파일 열기

        Args:
            file_path: 열 파일 경로

        Returns:
            bool: 열기 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.open(file_path)
            return True
        except Exception as e:
            print(f"파일 열기 실패: {e}", file=sys.stderr)
            return False

    def save_file(self, file_path: Optional[str] = None) -> bool:
        """파일 저장

        Args:
            file_path: 저장할 파일 경로 (None이면 현재 파일에 저장)

        Returns:
            bool: 저장 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            if file_path:
                self._hwp.save_as(file_path)
            else:
                self._hwp.save()
            return True
        except Exception as e:
            print(f"파일 저장 실패: {e}", file=sys.stderr)
            return False

    def get_current_paragraph_text(self) -> Optional[str]:
        """현재 커서가 위치한 문단의 텍스트 가져오기

        Returns:
            Optional[str]: 현재 문단 텍스트 또는 None (실패 시)
        """
        if not self.check_alive():
            return None

        try:
            # 현재 위치 저장
            current_pos = self.get_pos()
            if not current_pos:
                return None

            # Action 기반: 문단 전체 선택
            # ParaHead: 문단 시작으로 이동
            if not self.run_action("ParaHead"):
                return None

            # 선택 시작
            if hasattr(self._hwp, 'selection_mode'):
                self._hwp.selection_mode = 1
            else:
                self.run_action("SelectMode")

            # ParaTail: 문단 끝으로 이동 (선택 상태 유지)
            if not self.run_action("ParaTail"):
                # 선택 취소
                if hasattr(self._hwp, 'selection_mode'):
                    self._hwp.selection_mode = 0
                return None

            # 선택된 영역의 텍스트 가져오기
            try:
                if hasattr(self._hwp, 'get_selected_text'):
                    para_text = self._hwp.get_selected_text()
                elif hasattr(self._hwp, 'get_text_selected'):
                    para_text = self._hwp.get_text_selected()
                else:
                    # Action 기반: Copy → Paste로 텍스트 추출은 위험하므로
                    # 선택 해제하고 None 반환
                    para_text = None
            finally:
                # 선택 해제
                if hasattr(self._hwp, 'selection_mode'):
                    self._hwp.selection_mode = 0
                else:
                    self.run_action("Cancel")

                # 원래 위치로 복원
                self.set_pos(*current_pos)

            return para_text

        except Exception as e:
            print(f"현재 문단 텍스트 가져오기 실패: {e}", file=sys.stderr)
            return None

    def find_in_current_paragraph(self, text: str) -> bool:
        """현재 문단 내에서만 텍스트 검색

        Args:
            text: 검색할 텍스트

        Returns:
            bool: 검색 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 현재 문단 텍스트 가져오기
            para_text = self.get_current_paragraph_text()
            if not para_text:
                return False

            # 문단 내에 텍스트가 있는지 확인
            if text not in para_text:
                return False

            # find_text로 실제 검색 (현재 위치부터)
            return self.find_text(text, forward=True, case_sensitive=False)
        except Exception as e:
            print(f"문단 내 검색 실패: {e}", file=sys.stderr)
            return False

    # ============================================================
    # Action 기반 실행 (22년 이전 방식 - 모든 버전 호환)
    # ============================================================

    def run_action(self, action_id: str) -> bool:
        """HAction.Run() 실행 (ParameterSet 없는 단순 액션)

        Args:
            action_id: 액션 ID (예: "Undo", "BreakLine", "MenuExTrackChange")

        Returns:
            bool: 실행 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # HAction.Run() 호출
            if hasattr(self._hwp, 'HAction'):
                result = self._hwp.HAction.Run(action_id)
                return bool(result)
            return False
        except Exception as e:
            print(f"Action 실행 실패 ({action_id}): {e}", file=sys.stderr)
            return False

    def select_current_para(self) -> bool:
        """현재 커서 위치에서 문단 끝까지 선택

        HWP API의 MoveSelParaEnd를 사용하여 커서 위치부터 문단 끝까지 선택합니다.

        Returns:
            bool: 선택 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 현재 커서 위치부터 문단 끝까지 선택
            self._hwp.MoveSelParaEnd()
            return True
        except Exception as e:
            print(f"문단 선택 실패: {e}", file=sys.stderr)
            return False

    def cancel_selection(self) -> bool:
        """현재 선택 해제

        Returns:
            bool: 해제 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.run_action("Cancel")
            return True
        except Exception as e:
            print(f"선택 해제 실패: {e}", file=sys.stderr)
            return False

    def select_table_cell(self, table_pos: Tuple[int, int, int], row: int, col: int) -> bool:
        """표의 특정 셀을 선택

        Args:
            table_pos: 표 위치 (list, para, char)
            row: 행 번호 (0부터 시작)
            col: 열 번호 (0부터 시작)

        Returns:
            bool: 선택 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 표 위치로 이동
            if not self.set_pos(table_pos[0], table_pos[1], table_pos[2]):
                print(f"표 위치 이동 실패: {table_pos}", file=sys.stderr)
                return False

            # 표 안으로 진입
            self._hwp.FindCtrl()

            # 지정된 행으로 이동
            for _ in range(row):
                self._hwp.move_pos(501)  # 501: 다음 행

            # 지정된 열로 이동
            for _ in range(col):
                self._hwp.move_pos(502)  # 502: 다음 셀

            # 현재 셀 선택
            self._hwp.run_action("TableCellBlock")

            return True

        except Exception as e:
            print(f"표 셀 선택 실패: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return False

    def append_table_row(self, table_id: int, row: int, cells: list, start_col: int = 0) -> bool:
        """표에 행 추가

        Args:
            table_id: 표 ID
            row: 추가할 행 위치
            cells: 셀 내용 리스트
            start_col: 시작 열 (기본 0)

        Returns:
            bool: 추가 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 표 컨트롤 찾기
            ctrl = self._hwp.HeadCtrl
            table_idx = 0
            target_ctrl = None

            while ctrl:
                if hasattr(ctrl, 'UserDesc') and ctrl.UserDesc == "표":
                    table_idx += 1
                    if table_idx == table_id:
                        target_ctrl = ctrl
                        break
                ctrl = ctrl.Next if hasattr(ctrl, 'Next') else None

            if target_ctrl is None:
                print(f"표 {table_id}를 찾을 수 없습니다", file=sys.stderr)
                return False

            # 표 앵커 위치로 이동
            anchor_pos = target_ctrl.GetAnchorPos(0)
            self._hwp.set_pos_by_set(anchor_pos)

            # 표 안으로 진입
            self._hwp.FindCtrl()

            # 지정된 행으로 이동
            for _ in range(row):
                self._hwp.move_pos(501)  # 501: 다음 행

            # 행 삽입
            self._hwp.TableInsertRow()

            # 각 셀에 내용 입력
            for i, cell_content in enumerate(cells):
                if i > 0:
                    self._hwp.move_pos(502)  # 502: 다음 셀

                # 셀 내용 입력
                self._hwp.insert_text(cell_content)

            return True

        except Exception as e:
            print(f"표 행 추가 실패: {e}", file=sys.stderr)
            return False

    def execute_action(self, action_id: str, pset_id: str, params: dict) -> bool:
        """HAction.Execute() 실행 (ParameterSet 기반 액션)

        GetDefault → 파라미터 수정 → Execute 패턴

        Args:
            action_id: 액션 ID (예: "CharShape", "ParaShape", "TableCreate")
            pset_id: ParameterSet ID (예: "CharShape", "ParaShape", "TableCreation")
            params: 설정할 파라미터 딕셔너리

        Returns:
            bool: 실행 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            if not hasattr(self._hwp, 'HAction') or not hasattr(self._hwp, 'HParameterSet'):
                return False

            # ParameterSet 생성
            pset = self.create_pset(pset_id)
            if pset is None:
                return False

            # GetDefault로 기본값 로드
            self._hwp.HAction.GetDefault(action_id, pset.HSet)

            # 파라미터 수정
            for key, value in params.items():
                if hasattr(pset, key):
                    setattr(pset, key, value)

            # Execute로 실행
            result = self._hwp.HAction.Execute(action_id, pset.HSet)
            return bool(result)
        except Exception as e:
            print(f"Action 실행 실패 ({action_id}): {e}", file=sys.stderr)
            return False

    def create_pset(self, pset_id: str):
        """HParameterSet 생성

        Args:
            pset_id: ParameterSet ID (예: "CharShape", "ParaShape", "TableCreation")

        Returns:
            ParameterSet 객체 또는 None
        """
        if not self.check_alive():
            return None

        try:
            if not hasattr(self._hwp, 'HParameterSet'):
                return None

            # HParameterSet에서 해당 ID의 pset 생성
            pset_attr = f"H{pset_id}"
            if hasattr(self._hwp.HParameterSet, pset_attr):
                return getattr(self._hwp.HParameterSet, pset_attr)

            # 대안: CreateSet 메서드 사용
            if hasattr(self._hwp.HParameterSet, 'CreateSet'):
                return self._hwp.HParameterSet.CreateSet(pset_id)

            return None
        except Exception as e:
            print(f"ParameterSet 생성 실패 ({pset_id}): {e}", file=sys.stderr)
            return None

    # ============================================================
    # Track Changes (변경 내용 추적) - 2022-호환 규칙
    # ============================================================

    def start_track_changes(self) -> bool:
        """Enable Track Changes (MenuExTrackChange + ViewOptionTrackChangeFinalMemo)."""
        if not self.check_alive():
            return False

        try:
            # MemoShape 적용 (매크로 패턴)
            try:
                pset = self._hwp.HParameterSet.HSecDef
                self._hwp.HAction.GetDefault("MemoShape", pset.HSet)
                try:
                    if hasattr(self._hwp, "MiliToHwpUnit") and hasattr(pset, "MemoShape"):
                        pset.MemoShape.Width = self._hwp.MiliToHwpUnit(0.0)
                except Exception:
                    pass
                try:
                    pset.HSet.SetItem("ApplyClass", 24)
                    pset.HSet.SetItem("ApplyTo", 3)
                except Exception:
                    pass
                self._hwp.HAction.Execute("MemoShape", pset.HSet)
            except Exception:
                pass

            # TrackChangeOption 적용 제거 - HWP 기본 스타일 유지
            # 이전: GetDefault → Execute로 잘못된 디폴트 적용
            # 삽입: 밑줄 없이 밝은 녹색, 변경: 바깥쪽 테두리 빨강이 정상
            # try:
            #     pset = self._hwp.HParameterSet.HTrackChange
            #     self._hwp.HAction.GetDefault("TrackChangeOption", pset.HSet)
            #     self._hwp.HAction.Execute("TrackChangeOption", pset.HSet)
            # except Exception:
            #     pass

            # Track Change 활성화 후 표시 옵션 적용
            try:
                if hasattr(self._hwp, "lsTrackChange"):
                    self._hwp.lsTrackChange = True
            except Exception:
                pass
            result = self.run_action("MenuExTrackChange")
            self.run_action("ViewOptionTrackChangeFinalMemo")
            return bool(result)
        except Exception as e:
            print(f"Track Changes enable failed: {e}", file=sys.stderr)
            return False

    def stop_track_changes(self) -> bool:
        """Disable Track Changes (MenuExTrackChange)."""
        if not self.check_alive():
            return False

        try:
            # 상태 확인 API가 불안정하므로 MenuEx 토글을 항상 실행한다.
            result = self.run_action("MenuExTrackChange")
            return bool(result)
        except Exception as e:
            print(f"Track Changes disable failed: {e}", file=sys.stderr)
            return False

    def _normalize_track_changes_flag(self, value) -> Optional[bool]:
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

    def get_track_changes_state(self) -> Optional[bool]:
        """Track Changes 현재 상태 반환."""
        if not self.check_alive():
            return None
        try:
            current = getattr(self._hwp, 'lsTrackChange', None)
            return self._normalize_track_changes_flag(current)
        except Exception:
            return None

    def set_track_changes_flag(self, enabled: bool) -> bool:
        """Set Track Changes flag without MenuEx toggles."""
        if not self.check_alive():
            return False
        try:
            if hasattr(self._hwp, "lsTrackChange"):
                self._hwp.lsTrackChange = bool(enabled)
                return True
        except Exception:
            return False
        return False

    def accept_all_changes(self, skip_check: bool = False) -> bool:
        """모든 변경 사항 승인

        추적된 모든 변경 사항을 문서에 반영합니다.
        TrackChangeApplyAll은 변경사항이 없어도 오류 없이 처리됩니다.

        Args:
            skip_check: (미사용) 하위 호환성 유지용

        Returns:
            bool: 승인 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            try:
                self.run_action("Cancel")
            except Exception:
                pass

            # TrackChangeApplyAll 직접 실행 (변경사항 없어도 안전)
            return self.run_action("TrackChangeApplyAll")

        except Exception as e:
            print(f"변경 승인 실패: {e}", file=sys.stderr)
            return False

    def reject_all_changes(self, skip_check: bool = False) -> bool:
        """모든 변경 사항 거절 (롤백)

        추적된 모든 변경 사항을 거절하여 원래 상태로 되돌립니다.
        TrackChangeCancelAll은 변경사항이 없어도 오류 없이 처리됩니다.

        Args:
            skip_check: (미사용) 하위 호환성 유지용

        Returns:
            bool: 거절 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            try:
                self.run_action("Cancel")
            except Exception:
                pass

            # TrackChangeCancelAll 직접 실행 (변경사항 없어도 안전)
            return self.run_action("TrackChangeCancelAll")

        except Exception as e:
            print(f"변경 거절 실패: {e}", file=sys.stderr)
            return False

    # ============================================================
    def undo(self, count: int = 1) -> int:
        """Undo execution (repeat count times)."""
        if not self.check_alive():
            return 0
        try:
            count = int(count)
        except Exception:
            count = 1
        if count <= 0:
            return 0
        undone = 0
        for _ in range(count):
            try:
                if hasattr(self._hwp, "undo"):
                    result = self._hwp.undo()
                else:
                    result = self.run_action("Undo")
            except Exception:
                result = False
            if not result:
                break
            undone += 1
        return undone

    def redo(self, count: int = 1) -> int:
        """Redo execution (repeat count times)."""
        if not self.check_alive():
            return 0
        try:
            count = int(count)
        except Exception:
            count = 1
        if count <= 0:
            return 0
        redone = 0
        for _ in range(count):
            try:
                if hasattr(self._hwp, "redo"):
                    result = self._hwp.redo()
                else:
                    result = self.run_action("Redo")
            except Exception:
                result = False
            if not result:
                break
            redone += 1
        return redone

    # Legacy 고급 편집 API (2022년 이전 호환)
    # ============================================================

    def set_font(self, Bold: Optional[bool] = None, Italic: Optional[bool] = None) -> bool:
        """글자 서식 설정 (Bold/Italic)

        2022년 이전 API: CharShape ParameterSet 기반

        Args:
            Bold: 굵게 여부 (None이면 변경하지 않음)
            Italic: 이탤릭 여부 (None이면 변경하지 않음)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            pset = self._hwp.HParameterSet.HCharShape
            self._hwp.HAction.GetDefault("CharShape", pset.HSet)

            if Bold is not None:
                pset.Bold = Bold
            if Italic is not None:
                pset.Italic = Italic

            self._hwp.HAction.Execute("CharShape", pset.HSet)
            return True
        except Exception as e:
            print(f"글자 서식 설정 실패: {e}", file=sys.stderr)
            return False

    def markpen_on_selection(self, r: int = 255, g: int = 255, b: int = 0) -> bool:
        """선택 영역에 형광펜 적용

        2022년 이전 API: CharShape ParameterSet의 ShadeColor 사용

        Args:
            r: Red (0-255)
            g: Green (0-255)
            b: Blue (0-255)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            pset = self._hwp.HParameterSet.HCharShape
            self._hwp.HAction.GetDefault("CharShape", pset.HSet)

            # RGB 값을 HWP 색상 형식으로 변환 (BGR 방식)
            color_value = (b << 16) | (g << 8) | r
            pset.ShadeColor = color_value

            self._hwp.HAction.Execute("CharShape", pset.HSet)
            return True
        except Exception as e:
            print(f"형광펜 적용 실패: {e}", file=sys.stderr)
            return False

    def Cancel(self) -> bool:
        """선택 해제 (2022년 이전 API: Cancel Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("Cancel")
            return True
        except Exception as e:
            print(f"선택 해제 실패: {e}", file=sys.stderr)
            return False

    def MoveParaBegin(self) -> bool:
        """문단 처음으로 이동 (2022년 이전 API: MoveParaBegin Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("MoveParaBegin")
            return True
        except Exception as e:
            print(f"문단 처음 이동 실패: {e}", file=sys.stderr)
            return False

    def MoveParaEnd(self) -> bool:
        """문단 끝으로 이동 (2022년 이전 API: MoveParaEnd Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("MoveParaEnd")
            return True
        except Exception as e:
            print(f"문단 끝 이동 실패: {e}", file=sys.stderr)
            return False

    def MoveNextChar(self) -> bool:
        """다음 문자로 이동 (2022년 이전 API: MoveNextChar Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("MoveNextChar")
            return True
        except Exception as e:
            print(f"다음 문자 이동 실패: {e}", file=sys.stderr)
            return False

    def MoveUp(self) -> bool:
        """위로 이동 (2022년 이전 API: MoveUp Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("MoveUp")
            return True
        except Exception as e:
            print(f"위로 이동 실패: {e}", file=sys.stderr)
            return False

    def MoveSelParaEnd(self) -> bool:
        """문단 끝까지 선택 (2022년 이전 API: MoveSelParaEnd Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("MoveSelParaEnd")
            return True
        except Exception as e:
            print(f"문단 끝까지 선택 실패: {e}", file=sys.stderr)
            return False

    def ParagraphShapeIndentAtCaret(self) -> bool:
        """캐럿 기준 들여쓰기 (2022년 이전 API: ParagraphShapeIndentAtCaret Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            self._hwp.HAction.Run("ParagraphShapeIndentAtCaret")
            return True
        except Exception as e:
            print(f"들여쓰기 실패: {e}", file=sys.stderr)
            return False

    def is_cell(self) -> bool:
        """현재 커서가 표 셀 내부인지 확인

        Returns:
            bool: 셀 내부 여부
        """
        if not self.check_alive():
            return False

        try:
            # GetPos로 현재 위치 확인
            list_pos, _, _ = self.get_pos()
            # list_pos > 2이면 표/글상자 등 특수 영역 (0, 1, 2는 본문)
            return list_pos > 2
        except Exception:
            return False

    def HwpLineWidth(self, size: str) -> int:
        """선 굵기 문자열을 HWP 단위로 변환

        Args:
            size: "0.5mm", "1mm" 등

        Returns:
            int: HWP 단위 값
        """
        try:
            return self._hwp.HwpLineWidth(size)
        except Exception:
            # 기본값: 0.5mm ≈ 14 (HWP 단위)
            return 14

    @property
    def HAction(self):
        """HAction 객체 반환 (2022년 이전 API 호환)"""
        if not self.check_alive():
            return None
        return self._hwp.HAction

    @property
    def HParameterSet(self):
        """HParameterSet 객체 반환 (2022년 이전 API 호환)"""
        if not self.check_alive():
            return None
        return self._hwp.HParameterSet

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        self.disconnect()

    # ============================================================
    # 전역 상태 관리 (클래스 메서드)
    # ============================================================

    @classmethod
    def get_global_connector(cls) -> Optional['HwpConnector']:
        """전역 Connector 인스턴스 반환

        Returns:
            Optional[HwpConnector]: 전역 Connector 또는 None
        """
        return session_state.retrieve_runtime_connector()

    @classmethod
    def set_global_connector(cls, connector: 'HwpConnector') -> None:
        """전역 Connector 인스턴스 설정

        Args:
            connector: HwpConnector 인스턴스
        """
        session_state.store_runtime_connector(connector)

    @classmethod
    def ensure_global_connector(cls, **kwargs) -> 'HwpConnector':
        """전역 Connector 확보 (lazy initialization)

        전역 Connector가 없으면 생성하여 설정.
        전역 PID/HWND가 설정되어 있으면 자동으로 사용.

        Args:
            **kwargs: HwpConnector 생성 인자 (visible, new 등)

        Returns:
            HwpConnector: 전역 Connector 인스턴스
        """
        connector = cls.get_global_connector()
        if connector is None:
            # 전역 PID/HWND 활용
            pid = session_state.retrieve_runtime_target_process_id()
            hwnd = session_state.retrieve_runtime_target_window_handle()

            connector = cls(
                process_identifier=pid,
                window_handle=hwnd,
                **kwargs
            )
            connector.connect()
            cls.set_global_connector(connector)

        return connector

    # ============================================================
    # 2022-호환 규칙: Legacy 필수 메서드들
    # ============================================================

    def break_para(self) -> bool:
        """문단 분리 (2022-호환: BreakPara Action)

        새 문단(엔터) 삽입. KeyDown("{ENTER}") 대신 표준 Action 사용.

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # 1차: BreakPara Action
            if self.run_action("BreakPara"):
                return True
            # 2차 fallback: BreakLine (줄바꿈 - 문단 분리가 아님)
            return self.run_action("BreakLine")
        except Exception as e:
            print(f"BreakPara 실패: {e}", file=sys.stderr)
            return False

    def delete_back(self) -> bool:
        """역방향 삭제 (2022-호환: DeleteBack Action)

        커서 앞 문자 삭제. 

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("DeleteBack")
        except Exception as e:
            print(f"DeleteBack 실패: {e}", file=sys.stderr)
            return False

    def delete_ctrl(self, ctrl) -> bool:
        """컨트롤 삭제 (2022-호환: DeleteCtrl API)

        표/도형/누름틀/각주 등 컨트롤 단위 삭제.

        Args:
            ctrl: 삭제할 컨트롤 객체

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            if hasattr(self._hwp, 'DeleteCtrl'):
                result = self._hwp.DeleteCtrl(ctrl)
                return bool(result)
            return False
        except Exception as e:
            print(f"DeleteCtrl 실패: {e}", file=sys.stderr)
            return False

    def get_pos_by_set(self) -> Optional[Tuple[int, int, int]]:
        """위치 조회 (2022-호환: GetPosBySet API)

        GetPosBySet()가 더 안정적 - 직접 레퍼런스 기반 GetPos보다 권장.

        Returns:
            Optional[Tuple[int, int, int]]: (list, para, pos) 또는 None
        """
        if not self.check_alive():
            return None

        try:
            if hasattr(self._hwp, 'GetPosBySet'):
                pset = self._hwp.GetPosBySet()
                if pset:
                    list_id = pset.Item("List")
                    para = pset.Item("Para")
                    pos = pset.Item("Pos")
                    return (list_id, para, pos)
            # fallback: 기존 get_pos
            return self.get_pos()
        except Exception as e:
            print(f"GetPosBySet 실패: {e}", file=sys.stderr)
            return self.get_pos()

    def find_replace_all(self, find_text: str, replace_text: str, 
                         match_case: bool = False) -> int:
        """문서 전체 찾기/교체 (2022-호환: FindReplace ParameterSet)

        Args:
            find_text: 찾을 텍스트
            replace_text: 교체할 텍스트
            match_case: 대소문자 구분 여부

        Returns:
            int: 교체된 횟수 (-1이면 실패)
        """
        if not self.check_alive():
            return -1

        try:
            if hasattr(self._hwp, 'HAction') and hasattr(self._hwp, 'HParameterSet'):
                pset = self._hwp.HParameterSet.HFindReplace
                self._hwp.HAction.GetDefault("AllReplace", pset.HSet)
                
                pset.FindString = find_text
                pset.ReplaceString = replace_text
                pset.IgnoreMessage = 1  # 메시지 박스 억제
                pset.Direction = 0  # 0: 전방, 1: 후방
                pset.MatchCase = 1 if match_case else 0
                
                result = self._hwp.HAction.Execute("AllReplace", pset.HSet)
                if result:
                    # 교체 횟수 반환 (pset에서 결과 읽기)
                    try:
                        count = pset.ReplaceCount if hasattr(pset, 'ReplaceCount') else 0
                        return count
                    except:
                        return 0
            return -1
        except Exception as e:
            print(f"FindReplaceAll 실패: {e}", file=sys.stderr)
            return -1

    def select_text_range(self, start_para: int, start_pos: int, 
                          end_para: int, end_pos: int) -> bool:
        """텍스트 범위 선택 (2022-호환: SelectText API)

        epos가 가리키는 문자는 포함되지 않음 (end-exclusive).

        Args:
            start_para: 시작 문단
            start_pos: 시작 위치
            end_para: 종료 문단
            end_pos: 종료 위치 (exclusive)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            if hasattr(self._hwp, 'SelectText'):
                result = self._hwp.SelectText(start_para, start_pos, end_para, end_pos)
                return bool(result)
            # fallback: select_text 사용
            return self.select_text((0, start_para, start_pos), (0, end_para, end_pos))
        except Exception as e:
            print(f"SelectText 실패: {e}", file=sys.stderr)
            return False

    def get_text_file(self, format: str = "UNICODE", option: str = "") -> Optional[str]:
        """문서 텍스트 파일로 가져오기 (2022-호환: GetTextFile API)

        GetText() 대신 GetTextFile("UNICODE","")로 전체 문서 확인.

        Args:
            format: 포맷 ("UNICODE", "HWPML2X" 등)
            option: 옵션 ("saveblock"이면 선택 영역만)

        Returns:
            Optional[str]: 텍스트 또는 None
        """
        if not self.check_alive():
            return None

        try:
            if hasattr(self._hwp, 'GetTextFile'):
                result = self._hwp.GetTextFile(format, option)
                return result if result else None
            return None
        except Exception as e:
            print(f"GetTextFile 실패: {e}", file=sys.stderr)
            return None

    def table_append_row(self) -> bool:
        """표 행 추가 (2022-호환: TableAppendRow Action)

        TableLowerCellAppend는 구버전에 없을 수 있으므로
        TableAppendRow + TableLowerCell 조합 사용.

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            # TableAppendRow로 행 추가
            if self.run_action("TableAppendRow"):
                # 추가된 행의 첫 셀로 이동
                self.run_action("TableLowerCell")
                return True
            return False
        except Exception as e:
            print(f"TableAppendRow 실패: {e}", file=sys.stderr)
            return False

    def table_right_cell(self) -> bool:
        """표 오른쪽 셀로 이동 (2022-호환)

        Returns:
            bool: 성공 여부
        """
        return self.run_action("TableRightCell")

    def table_lower_cell(self) -> bool:
        """표 아래쪽 셀로 이동 (2022-호환)

        Returns:
            bool: 성공 여부
        """
        return self.run_action("TableLowerCell")

    # ============================================================
    # 선택 영역 관련 메서드
    # ============================================================

    def get_selected_text(self, keep_select: bool = True) -> Optional[str]:
        """선택된 텍스트 가져오기 (기존 패턴)

        Args:
            keep_select: 선택 상태 유지 여부 (True: 유지, False: 해제)

        Returns:
            Optional[str]: 선택된 텍스트 또는 None (선택 영역 없거나 실패 시)
        """
        if not self.check_alive():
            return None

        try:
            # pyhwpx의 get_selected_text 메서드 사용
            if hasattr(self._hwp, 'get_selected_text'):
                selected_text = self._hwp.get_selected_text()

                # 선택 해제 옵션이 False면 선택 취소
                if not keep_select:
                    self.Cancel()

                return selected_text if selected_text else None

            # fallback: GetTextFile with saveblock option
            if hasattr(self._hwp, 'GetTextFile'):
                result = self._hwp.GetTextFile("TEXT", "saveblock")

                if not keep_select:
                    self.Cancel()

                return result if result else None

            return None
        except Exception as e:
            print(f"선택 텍스트 가져오기 실패: {e}", file=sys.stderr)
            return None

    def has_selection(self) -> bool:
        """선택 영역이 있는지 확인

        Returns:
            bool: 선택 영역 존재 여부
        """
        if not self.check_alive():
            return False

        try:
            # 선택 텍스트를 가져와서 확인 (선택 유지)
            selected = self.get_selected_text(keep_select=True)
            return selected is not None and len(selected) > 0
        except Exception as e:
            print(f"선택 영역 확인 실패: {e}", file=sys.stderr)
            return False

    def get_selection_info(self) -> Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """선택 영역의 시작/끝 위치 정보 가져오기

        Returns:
            Optional[Tuple]: ((start_list, start_para, start_pos), (end_list, end_para, end_pos)) 또는 None
        """
        if not self.check_alive():
            return None

        try:
            # 현재 위치 저장 (선택 종료 위치)
            end_pos = self.get_pos()
            if not end_pos:
                return None

            # 선택 영역이 있는지 확인
            if not self.has_selection():
                return None

            # Cancel을 통해 선택 시작 위치로 이동
            start_pos = None
            try:
                # 현재 선택 영역의 시작 위치를 알아내기 위해
                # MoveSelectionStart 같은 액션이 있으면 사용
                # 없으면 추정 방식 사용
                if self.run_action("Cancel"):
                    start_pos = self.get_pos()
            except Exception:
                pass

            if start_pos and end_pos:
                # 시작/끝 위치가 역순일 수 있으므로 정렬
                if (start_pos[1] > end_pos[1]) or \
                   (start_pos[1] == end_pos[1] and start_pos[2] > end_pos[2]):
                    return (end_pos, start_pos)
                return (start_pos, end_pos)

            return None
        except Exception as e:
            print(f"선택 영역 정보 가져오기 실패: {e}", file=sys.stderr)
            return None

    # ============================================================
    # 문단 이동/선택 메서드 (2022-호환 규칙)
    # ============================================================

    def MoveParaBegin(self) -> bool:
        """문단 시작으로 이동 (2022-호환: MoveParaBegin Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("MoveParaBegin")
        except Exception as e:
            print(f"MoveParaBegin 실패: {e}", file=sys.stderr)
            return False

    def MoveParaEnd(self) -> bool:
        """문단 끝으로 이동 (2022-호환: MoveParaEnd Action)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("MoveParaEnd")
        except Exception as e:
            print(f"MoveParaEnd 실패: {e}", file=sys.stderr)
            return False

    def MoveSelParaEnd(self) -> bool:
        """문단 끝까지 선택하면서 이동 (2022-호환: MoveSelParaEnd Action)

        커서 위치에서 문단 끝까지 선택합니다.

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("MoveSelParaEnd")
        except Exception as e:
            print(f"MoveSelParaEnd 실패: {e}", file=sys.stderr)
            return False

    def MoveSelParaBegin(self) -> bool:
        """문단 시작까지 선택하면서 이동 (2022-호환: MoveSelParaBegin Action)

        커서 위치에서 문단 시작까지 선택합니다.

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("MoveSelParaBegin")
        except Exception as e:
            print(f"MoveSelParaBegin 실패: {e}", file=sys.stderr)
            return False

    def Cancel(self) -> bool:
        """선택 해제 (2022-호환: Cancel Action)

        현재 선택 영역을 해제합니다.
        HAction.Run("Cancel")이 Escape 키보다 더 안정적입니다.

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            return self.run_action("Cancel")
        except Exception as e:
            print(f"Cancel 실패: {e}", file=sys.stderr)
            return False

    def select_text(self, start_pos: tuple, end_pos: tuple) -> bool:
        """위치 튜플로 텍스트 범위 선택 (기존 패턴)

        Args:
            start_pos: 시작 위치 (list_pos, para_pos, char_pos)
            end_pos: 끝 위치 (list_pos, para_pos, char_pos)

        Returns:
            bool: 성공 여부
        """
        if not self.check_alive():
            return False

        try:
            if len(start_pos) < 3 or len(end_pos) < 3:
                return False

            # pyhwpx의 select_text 메서드 사용
            if hasattr(self.hwp, 'select_text'):
                return self.hwp.select_text(
                    start_pos[1], start_pos[2],  # start_para, start_pos
                    end_pos[1], end_pos[2],      # end_para, end_pos
                    start_pos[0]                  # list_pos
                )

            # 폴백: 수동으로 선택
            self.set_pos(*start_pos)
            # MoveSelLineEnd 등으로 선택 확장
            return True
        except Exception as e:
            print(f"select_text 실패: {e}", file=sys.stderr)
            return False

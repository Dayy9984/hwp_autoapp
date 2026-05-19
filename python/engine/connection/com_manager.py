"""COM 객체 관리 모듈

HWP COM 객체 관리 시스템.
다중 HWP 프로세스 환경에서 PID/HWND 기반 정확한 바인딩을 제공합니다.

핵심 기능:
1. GetActiveObject 우선 바인딩 (빠른 연결)
2. ROT (Running Object Table) 기반 HWP 객체 검색 (폴백)
3. PID/HWND 매칭을 통한 정확한 대상 지정
4. ProgID 폴백 (HncCtrl.HwpObject.Xml, HWPFrame.HwpObject 등)
5. Bitness 검증 및 오류 처리
6. 외부 자동화 권한 활성화
"""

import pythoncom
import win32com.client
from win32com.client import Dispatch
from ctypes import windll, c_ulong, byref
from ctypes.wintypes import HWND
from typing import Optional, Tuple, List
import logging
from engine.connection.security_module import activate_security_module

logger = logging.getLogger(__name__)

# 연속 실패 로그 억제
_bind_failure_count = 0
_BIND_LOG_THRESHOLD = 1


class SafeHwpBinder:
    """PID/HWND 기반 정확한 HWP 바인딩

    여러 HWP 프로세스 중 정확한 대상을 식별하고 바인딩합니다.

    Attributes:
        hwp: 바인딩된 HWP COM 객체 (성공 시)
        process_identifier: 대상 프로세스 ID
        window_handle: 대상 윈도우 핸들
    """

    # 시도할 ProgID 목록 (다양한 버전 지원)
    PROGID_CANDIDATES = [
        "HWPFrame.HwpObject",
        "HwpFrame.HwpObject",
        "HwpObject.HwpObject",
        "HwpObject.HwpObject.1",
        "HwpFrame.HwpObject.1",
        "Hanword.HwpObject",
        "HanwordFrame.HwpObject",
        "Hanword.Application",
        "HWP.Application",
        "HncCtrl.HwpObject.Xml",
        "HncCtrl.HwpObject",
    ]

    def __init__(
        self,
        process_identifier: Optional[int] = None,
        window_handle: Optional[int] = None,
        allow_fallback: bool = False,
        allow_create: bool = False,
    ):
        """SafeHwpBinder 초기화

        Args:
            process_identifier: 바인딩할 HWP 프로세스 ID (None이면 ROT에서 첫 번째 찾기)
            window_handle: 바인딩할 HWP 윈도우 핸들 (선택적)
            allow_fallback: 대상 바인딩 실패 시 다른 인스턴스 바인딩 허용 여부
            allow_create: 대상 바인딩 실패 시 새 인스턴스 생성 허용 여부
        """
        self.hwp = None
        self.process_identifier = process_identifier
        self.window_handle = window_handle
        self.allow_fallback = allow_fallback
        self.allow_create = allow_create

    def bind(self) -> bool:
        """HWP COM 객체 바인딩

        1단계: GetActiveObject로 빠른 연결 시도
        2단계: process_identifier/window_handle이 있으면 ROT에서 정확한 대상 찾기
        3단계: 없으면 ROT에서 첫 번째 HwpObject 찾기
        4단계: ROT에서 못 찾으면 ProgID로 새 인스턴스 생성

        Returns:
            bool: 바인딩 성공 여부
        """
        global _bind_failure_count

        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

        # 0단계: PID/HWND 지정 없이 GetActiveObject로 빠른 연결 시도
        if not self.process_identifier and not self.window_handle:
            if self._try_active_object_binding():
                _bind_failure_count = 0
                self._enable_automation()
                logger.info("Successfully bound via GetActiveObject (fast path)")
                return True

        # 1단계: 타겟 바인딩 시도 (PID/HWND 있으면 매칭, 없으면 첫 번째 HWP)
        if self._try_bind_to_target():
            _bind_failure_count = 0  # 성공 시 리셋
            self._enable_automation()
            logger.info(f"Successfully bound to HWP process PID={self.process_identifier}, HWND={self.window_handle}")
            return True
        else:
            # 특정 타겟 지정했는데 실패한 경우
            if self.process_identifier or self.window_handle:
                logger.warning(f"Failed to bind to target PID={self.process_identifier}, HWND={self.window_handle}")
                if not self.allow_fallback:
                    return False

        # 2단계: ROT에서 첫 번째 HwpObject 찾기
        if self.allow_fallback and self._try_bind_from_rot():
            self._enable_automation()
            logger.info("Successfully bound to HWP from ROT (no specific target)")
            return True

        # 3단계: ProgID로 새 인스턴스 생성
        if self.allow_create and self._try_bind_with_progid():
            self._enable_automation()
            logger.info("Successfully created new HWP instance via ProgID")
            return True

        _bind_failure_count += 1
        if _bind_failure_count <= _BIND_LOG_THRESHOLD:
            logger.error("All binding attempts failed")
        return False

    def _try_active_object_binding(self) -> bool:
        """GetActiveObject로 실행 중인 HWP 인스턴스에 빠른 연결

        Returns:
            bool: 바인딩 성공 여부
        """
        for prog_id in self.PROGID_CANDIDATES:
            try:
                hwp = win32com.client.gencache.EnsureDispatch(
                    win32com.client.GetActiveObject(prog_id)
                )
                if self._is_valid_hwp(hwp):
                    self.hwp = hwp
                    logger.info(f"GetActiveObject 성공: {prog_id}")
                    return True
            except pythoncom.com_error:
                continue
            except Exception as e:
                logger.debug(f"GetActiveObject {prog_id} 실패: {e}")
                continue
        return False

    def _enable_automation(self) -> None:
        """외부 자동화 실행 권한 활성화 설정"""
        if not self.hwp:
            return
        success, module_id, dll_path = activate_security_module(self.hwp)
        if success:
            if dll_path:
                logger.debug(f"외부 자동화 모듈 등록 성공: {module_id} ({dll_path})")
            else:
                logger.debug(f"외부 자동화 모듈 등록 성공: {module_id}")
        else:
            logger.warning(
                "외부 자동화 모듈 등록 실패: FilePathCheckerModule 레지스트리 매핑을 확인하세요."
            )

    def _is_valid_hwp(self, hwp) -> bool:
        """HWP 핵심 인터페이스 접근 가능 여부 확인"""
        try:
            _ = hwp.XHwpDocuments
            _ = hwp.XHwpWindows
            _ = hwp.HAction
            return True
        except Exception:
            return False

    def _try_bind_to_target(self) -> bool:
        """ROT에서 process_identifier/window_handle에 해당하는 HWP 찾기

        ROTAccessManager와 DocumentMetadataCollector를 사용하여
        WindowHandle 접근 없이 정확한 HWP 프로세스를 찾습니다.

        Returns:
            bool: 바인딩 성공 여부
        """
        try:
            # ROTAccessManager로 HWP 인스턴스 가져오기 (WindowHandle 접근 없음!)
            from engine.connection.rot_access import ROTAccessManager
            from engine.connection.document_collector import DocumentMetadataCollector

            hwp_instance = ROTAccessManager.get_hwp_instance_by_target(
                preferred_pid=self.process_identifier,
                preferred_hwnd=self.window_handle,
                strict=bool(self.process_identifier or self.window_handle),
            )
            if hwp_instance is None:
                logger.debug("No HWP instance found in ROT")
                return False

            # HWP 객체 유효성 확인
            if not ROTAccessManager.verify_hwp_instance(hwp_instance):
                logger.debug("HWP instance validation failed")
                return False

            # PID/HWND 필터링이 필요한 경우 문서 메타데이터로 매칭
            if self.process_identifier or self.window_handle:
                try:
                    # Win32 API로 창 정보 수집
                    collector = DocumentMetadataCollector()
                    window_list = collector.collect_hwp_window_info()

                    # 선호하는 창 찾기
                    # PID 일치 우선 검색
                    if self.process_identifier:
                        for window_info in window_list:
                            if window_info['pid'] == self.process_identifier:
                                # Bitness 호환성 검증
                                if not self._check_bitness_compatibility(window_info['pid']):
                                    logger.debug(f"Skipping HWP PID={window_info['pid']} due to bitness mismatch")
                                    continue

                                self.hwp = hwp_instance
                                self.process_identifier = window_info['pid']
                                self.window_handle = window_info['hwnd']
                                logger.info(f"Matched HWP by PID: PID={self.process_identifier}, HWND={self.window_handle}")
                                return True

                    # HWND 일치 검색
                    if self.window_handle:
                        for window_info in window_list:
                            if window_info['hwnd'] == self.window_handle:
                                # Bitness 호환성 검증
                                if not self._check_bitness_compatibility(window_info['pid']):
                                    logger.debug(f"Skipping HWP PID={window_info['pid']} due to bitness mismatch")
                                    continue

                                self.hwp = hwp_instance
                                self.process_identifier = window_info['pid']
                                self.window_handle = window_info['hwnd']
                                logger.info(f"Matched HWP by HWND: HWND={self.window_handle}, PID={self.process_identifier}")
                                return True

                    logger.debug("No matching PID/HWND found in window list")
                    return False

                except Exception as e:
                    logger.debug(f"Error matching PID/HWND: {e}")
                    return False
            else:
                # PID/HWND 지정 없이 첫 번째 HWP 사용
                self.hwp = hwp_instance

                # 창 정보를 가져와서 PID/HWND 설정 (get_pid_hwnd()에서 사용)
                try:
                    collector = DocumentMetadataCollector()
                    window_list = collector.collect_hwp_window_info()
                    if window_list:
                        self.process_identifier = window_list[0]['pid']
                        self.window_handle = window_list[0]['hwnd']
                        logger.info(f"Bound to first HWP instance: PID={self.process_identifier}, HWND={self.window_handle}")
                    else:
                        logger.info("Bound to first HWP instance (no window info available)")
                except Exception as e:
                    logger.debug(f"Failed to get window info: {e}")
                    logger.info("Bound to first HWP instance (no specific target)")

                return True

        except Exception as e:
            logger.error(f"ROT binding failed: {e}")
            return False

    def _try_bind_from_rot(self) -> bool:
        """ROT에서 첫 번째 HwpObject 찾기 (PID 무관)

        ROTAccessManager를 사용하여 WindowHandle 접근 없이 찾습니다.

        Returns:
            bool: 바인딩 성공 여부
        """
        try:
            # ROTAccessManager로 HWP 인스턴스 가져오기 (WindowHandle 접근 없음!)
            from engine.connection.rot_access import ROTAccessManager

            hwp_instance = ROTAccessManager.get_first_hwp_instance()
            if hwp_instance is None:
                logger.debug("No HWP instance found in ROT")
                return False

            # HWP 객체 유효성 확인
            if not ROTAccessManager.verify_hwp_instance(hwp_instance):
                logger.debug("HWP instance validation failed")
                return False

            self.hwp = hwp_instance
            logger.info("Found HWP instance in ROT (no WindowHandle access)")
            return True

        except Exception as e:
            logger.error(f"ROT enumeration failed: {e}")
            return False

    def _try_bind_with_progid(self) -> bool:
        """ProgID로 새 HWP 인스턴스 생성

        여러 ProgID를 시도하여 호환성 확보

        Returns:
            bool: 생성 성공 여부
        """
        for progid in self.PROGID_CANDIDATES:
            try:
                hwp = win32com.client.gencache.EnsureDispatch(progid)
                if not self._is_valid_hwp(hwp):
                    continue
                self.hwp = hwp
                logger.info(f"Created new HWP instance with ProgID: {progid}")
                return True
            except Exception as e:
                logger.debug(f"ProgID {progid} failed: {e}")
                continue

        return False

    def _get_pid_from_hwnd(self, hwnd: int) -> int:
        """Windows API로 HWND에서 PID 추출

        Args:
            hwnd: 윈도우 핸들

        Returns:
            int: 프로세스 ID
        """
        process_id = c_ulong()
        windll.user32.GetWindowThreadProcessId(HWND(hwnd), byref(process_id))
        return process_id.value

    def _check_bitness_compatibility(self, pid: int) -> bool:
        """프로세스 bitness 호환성 검증

        Python과 HWP 프로세스의 bitness가 일치하는지 확인
        (32bit Python은 32bit HWP만, 64bit Python은 64bit HWP만 안정적)

        Args:
            pid: 프로세스 ID

        Returns:
            bool: 호환 가능하면 True
        """
        try:
            import sys
            import platform

            # Python bitness 확인
            python_is_64bit = sys.maxsize > 2**32

            # 프로세스 핸들 열기
            PROCESS_QUERY_INFORMATION = 0x0400
            h_process = windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if not h_process:
                logger.debug(f"Failed to open process {pid} for bitness check")
                return True  # 확인 실패 시 허용 (호환성)

            # IsWow64Process로 32bit 여부 확인
            is_wow64 = c_ulong()
            windll.kernel32.IsWow64Process(h_process, byref(is_wow64))
            windll.kernel32.CloseHandle(h_process)

            # 64bit 시스템에서 32bit 프로세스는 WOW64=True
            # 64bit 프로세스는 WOW64=False
            target_is_32bit = bool(is_wow64.value)

            # HWP 2020 이하(32-bit) / 2022 이상(64-bit) 조합도 허용 (바인딩 차단 금지)
            # Bitness 일치 여부 확인
            if python_is_64bit and target_is_32bit:
                logger.warning(f"Bitness mismatch: 64bit Python with 32bit HWP (PID={pid})")
                return True
            elif not python_is_64bit and not target_is_32bit:
                logger.warning(f"Bitness mismatch: 32bit Python with 64bit HWP (PID={pid})")
                return True

            return True

        except Exception as e:
            logger.debug(f"Bitness check failed: {e}")
            return True  # 확인 실패 시 허용

    def get_hwp(self):
        """바인딩된 HWP COM 객체 반환

        Returns:
            HWP COM 객체 또는 None
        """
        return self.hwp

    def get_pid_hwnd(self) -> Tuple[Optional[int], Optional[int]]:
        """현재 바인딩된 HWP의 PID/HWND 반환

        먼저 저장된 값을 반환하고, 없으면 DocumentMetadataCollector로 조회합니다.
        WindowHandle 접근을 회피하여 HWP 2018 호환성을 보장합니다.

        Returns:
            Tuple[Optional[int], Optional[int]]: (PID, HWND) 또는 (None, None)
        """
        if not self.hwp:
            return None, None

        # 1. 저장된 PID/HWND가 있으면 반환
        if self.process_identifier and self.window_handle:
            return self.process_identifier, self.window_handle

        # 2. DocumentMetadataCollector로 Win32 API 기반 조회 (WindowHandle 접근 없음!)
        try:
            from engine.connection.document_collector import DocumentMetadataCollector

            collector = DocumentMetadataCollector()
            window_list = collector.collect_hwp_window_info()

            # 첫 번째 HWP 창의 PID/HWND 반환
            if window_list:
                self.process_identifier = window_list[0]['pid']
                self.window_handle = window_list[0]['hwnd']
                return self.process_identifier, self.window_handle

            return None, None

        except Exception as e:
            logger.error(f"Failed to get PID/HWND: {e}")
            return None, None

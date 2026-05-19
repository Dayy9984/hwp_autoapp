"""
Running Object Table (ROT) Access Manager

HWP COM 객체를 ROT에서 안전하게 가져오는 유틸리티.
XHwpWindows.WindowHandle 접근을 회피하여 HWP 2018 호환성 보장.
"""

import ctypes
from ctypes.wintypes import DWORD, HWND
from typing import List, Optional

import pythoncom
import win32com.client


class ROTAccessManager:
    """ROT 접근 관리자 - 최소한의 접근으로 HWP COM 객체 획득"""

    @staticmethod
    def _get_pid_from_hwnd(hwnd: int) -> Optional[int]:
        try:
            pid = DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(HWND(hwnd), ctypes.byref(pid))
            return int(pid.value) if pid.value else None
        except Exception:
            return None

    @staticmethod
    def _enumerate_hwp_instances() -> List[object]:
        """ROT에서 사용 가능한 HWP COM 인스턴스를 모두 열거한다."""
        instances: List[object] = []
        try:
            # COM 초기화
            pythoncom.CoInitialize()

            # Bind Context 생성
            bind_ctx = pythoncom.CreateBindCtx(0)

            # Running Object Table 가져오기
            running_table = bind_ctx.GetRunningObjectTable()

            # ROT 열거
            moniker_enum = running_table.EnumRunning()
            moniker_list = list(moniker_enum)

            # HWP 객체 찾기
            for moniker in moniker_list:
                try:
                    # Moniker 이름 가져오기
                    display_name = moniker.GetDisplayName(bind_ctx, None)

                    # HWP 객체인지 확인
                    if not display_name or not display_name.startswith("!HwpObject"):
                        continue

                    # COM 객체 가져오기
                    com_object = running_table.GetObject(moniker)

                    # IDispatch 인터페이스 획득 (타입 라이브러리 캐싱 없이 사용)
                    dispatch = com_object.QueryInterface(pythoncom.IID_IDispatch)
                    hwp_instance = win32com.client.Dispatch(dispatch)

                    instances.append(hwp_instance)

                except Exception:
                    # 이 moniker 실패 시 다음 것 시도
                    continue

        except Exception:
            pass
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

        return instances

    @staticmethod
    def get_hwp_instance_by_target(
        preferred_pid: Optional[int] = None,
        preferred_hwnd: Optional[int] = None,
        strict: bool = False,
    ) -> Optional[object]:
        """
        PID/HWND에 맞는 HWP COM 인스턴스를 ROT에서 찾는다.

        Args:
            preferred_pid: 선호 PID
            preferred_hwnd: 선호 HWND
            strict: True면 정확히 일치하지 않을 때 None 반환
        """
        instances = ROTAccessManager._enumerate_hwp_instances()
        if not instances:
            return None

        has_target = preferred_pid is not None or preferred_hwnd is not None

        # 타깃 지정이 없으면 첫 번째 유효 객체 반환
        if not has_target:
            for instance in instances:
                if ROTAccessManager.verify_hwp_instance(instance):
                    return instance
            return None

        for instance in instances:
            if not ROTAccessManager.verify_hwp_instance(instance):
                continue

            try:
                windows = instance.XHwpWindows
                window_count = int(windows.Count)
            except Exception:
                continue

            for index in range(window_count):
                try:
                    window = windows.Item(index)
                    hwnd = int(window.WindowHandle)
                except Exception:
                    continue

                if preferred_hwnd is not None and hwnd == preferred_hwnd:
                    return instance

                if preferred_pid is not None:
                    pid = ROTAccessManager._get_pid_from_hwnd(hwnd)
                    if pid == preferred_pid:
                        return instance

        if strict:
            return None

        for instance in instances:
            if ROTAccessManager.verify_hwp_instance(instance):
                return instance
        return None

    @staticmethod
    def get_first_hwp_instance() -> Optional[object]:
        """
        ROT에서 첫 번째 HWP COM 객체를 가져옵니다.

        중요: XHwpWindows.Item(0).WindowHandle에 접근하지 않으므로
        HWP 2018에서 빈 문서 생성 버그가 발생하지 않습니다.

        Returns:
            HWP COM 객체 또는 None
        """
        return ROTAccessManager.get_hwp_instance_by_target()

    @staticmethod
    def verify_hwp_instance(hwp_obj) -> bool:
        """
        HWP COM 객체가 유효한지 확인

        Args:
            hwp_obj: 검증할 HWP COM 객체

        Returns:
            bool: 유효 여부
        """
        try:
            # Version 속성 접근으로 유효성 확인
            _ = hwp_obj.Version
            return True
        except Exception:
            return False

    @staticmethod
    def get_document_count(hwp_obj) -> int:
        """
        HWP COM 객체의 문서 개수 반환

        Args:
            hwp_obj: HWP COM 객체

        Returns:
            int: 문서 개수 (실패 시 0)
        """
        try:
            return hwp_obj.XHwpDocuments.Count
        except Exception:
            return 0

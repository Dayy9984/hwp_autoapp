"""pyhwpx.Hwp.find() MessageBoxMode finally 복원값 교정 패치.

배경:
    pyhwpx 1.6.6 의 Hwp.find() 는 진입 시 SetMessageBoxMode(0x2FFF1) 으로 dialog 차단 후,
    try/finally 의 finally 에서 무조건 SetMessageBoxMode(0xFFFFF) 으로 복원한다.
    이 때 caller 가 미리 설정한 mode (예: 0 = 모든 dialog 차단) 가 0xFFFFF (permissive) 로
    덮어쓰여 후속 HAction 호출 시 다이얼로그가 노출되는 문제 발생.

패치:
    원본 find() 진입 직전에 prev_mode = SetMessageBoxMode(0x2FFF1) 으로 caller mode 캡처 후
    원본 호출. 원본 finally 가 0xFFFFF 로 복원해도 우리 finally 가 prev_mode 로 재복원.

적용 위치: hwp_com_process.py 상단 (pyhwpx import 직후, 1회 호출, idempotent).
"""
import inspect
import sys
from typing import Optional


_PATCH_MARKER = "_inserty_find_patched"
_SUPPORTED_VERSIONS = {"1.6.6"}  # 1.7.x 등 신규 버전 시그니처 변경 시 거부


def apply_find_patch() -> bool:
    """Hwp.find() 패치 적용. 중복 호출 idempotent.

    Returns:
        True: 패치 적용 또는 이미 적용된 상태.
        False: pyhwpx 미설치, 미지원 버전, 시그니처 변경 시.
    """
    try:
        import pyhwpx
    except ImportError:
        print("[pyhwpx_patch] pyhwpx 미설치 — skip", file=sys.stderr)
        return False

    version = getattr(pyhwpx, "__version__", "unknown")
    if version not in _SUPPORTED_VERSIONS:
        print(
            f"[pyhwpx_patch] 미지원 버전 {version} — 패치 skip (원본 동작 유지)",
            file=sys.stderr,
        )
        return False

    Hwp = pyhwpx.Hwp
    if getattr(Hwp.find, _PATCH_MARKER, False):
        return True

    original_find = Hwp.find

    try:
        sig = inspect.signature(original_find)
        params = list(sig.parameters.keys())
        if not (len(params) >= 2 and params[0] == "self"):
            print("[pyhwpx_patch] 예상치 못한 시그니처 — skip", file=sys.stderr)
            return False
    except (ValueError, TypeError):
        return False

    def patched_find(self, *args, **kwargs):
        """caller 의 prev_mode 를 캡처 + 복원 wrapper."""
        prev_mode: Optional[int] = None
        try:
            # SetMessageBoxMode 의 반환값 = 이전 mode. 0x2FFF1 으로 설정하면서 prev 캡처.
            prev_mode = self.SetMessageBoxMode(0x2FFF1)
        except Exception:
            prev_mode = None

        try:
            return original_find(self, *args, **kwargs)
        finally:
            # 원본 finally 가 0xFFFFF 로 덮어쓴 것을 caller prev_mode 로 재복원.
            if prev_mode is not None:
                try:
                    self.SetMessageBoxMode(prev_mode)
                except Exception:
                    pass

    setattr(patched_find, _PATCH_MARKER, True)
    Hwp.find = patched_find
    print(f"[pyhwpx_patch] Hwp.find 패치 적용 (pyhwpx {version})", file=sys.stderr)
    return True

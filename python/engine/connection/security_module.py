"""HWP FilePathCheck 보안 모듈 등록 유틸리티.

권한 승인 팝업을 방지하기 위해 FilePathCheckerModule DLL 경로를
HKCU 레지스트리에 등록하고 RegisterModule 호출을 표준화한다.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Tuple

MODULE_TYPE = "FilePathCheckDLL"
DEFAULT_MODULE_IDS: Tuple[str, ...] = (
    "FilePathCheckerModule",
    "FilePathCheckerModuleExample",
    "AutomationModule",
    "InsertyFilePathCheckerModule",
)

_REGISTRY_PATHS: Tuple[str, ...] = (
    r"Software\HNC\HwpAutomation\Modules",
    r"Software\Hnc\HwpUserAction\Modules",
    r"Software\HNC\HwpCtrl\Modules",
    r"Software\Hnc\HwpCtrl\Modules",
)
_DLL_CANDIDATES: Tuple[str, ...] = (
    "FilePathCheckerModule.dll",
    "FilePathCheckerModuleExample.dll",
)

_cached_dll_path: Optional[Path] = None
_cached_hwp_machine: Optional[int] = None


def _normalize_module_ids(module_ids: Iterable[str]) -> Tuple[str, ...]:
    normalized: list[str] = []
    for module_id in module_ids:
        module_id_text = str(module_id or "").strip()
        if not module_id_text:
            continue
        if module_id_text not in normalized:
            normalized.append(module_id_text)
    return tuple(normalized)


def _merge_module_ids(module_ids: Iterable[str]) -> Tuple[str, ...]:
    requested = _normalize_module_ids(module_ids)
    if not requested:
        return DEFAULT_MODULE_IDS
    return _normalize_module_ids((*requested, *DEFAULT_MODULE_IDS))


def _emit(log_callback: Optional[Callable[[str, str], None]], level: str, message: str) -> None:
    if not log_callback:
        return
    try:
        log_callback(level, message)
    except Exception:
        pass


def _is_pe_file(path: Path) -> bool:
    try:
        with path.open("rb") as fp:
            return fp.read(2) == b"MZ"
    except Exception:
        return False


def _read_pe_machine(path: Path) -> Optional[int]:
    try:
        with path.open("rb") as fp:
            if fp.read(2) != b"MZ":
                return None
            fp.seek(0x3C)
            pe_offset = struct.unpack("<I", fp.read(4))[0]
            fp.seek(pe_offset + 4)
            return struct.unpack("<H", fp.read(2))[0]
    except Exception:
        return None


def _iter_hwp_executable_candidates() -> Iterator[Path]:
    candidate_dirs = []
    for env_key in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_key)
        if not base:
            continue
        candidate_dirs.append(Path(base))

    seen: set[str] = set()
    for base_dir in candidate_dirs:
        if not base_dir.exists():
            continue
        for pattern in (
            "HNC/**/Hwp.exe",
            "Hancom/**/Hwp.exe",
        ):
            for exe_path in base_dir.glob(pattern):
                key = str(exe_path).lower()
                if key in seen:
                    continue
                seen.add(key)
                yield exe_path


def _detect_hwp_machine() -> Optional[int]:
    global _cached_hwp_machine
    if _cached_hwp_machine is not None:
        return _cached_hwp_machine

    newest_path: Optional[Path] = None
    newest_mtime = -1.0
    for exe_path in _iter_hwp_executable_candidates():
        try:
            if not exe_path.is_file():
                continue
            stat = exe_path.stat()
            if stat.st_mtime > newest_mtime:
                newest_mtime = stat.st_mtime
                newest_path = exe_path
        except Exception:
            continue

    if newest_path is None:
        _cached_hwp_machine = None
        return None

    _cached_hwp_machine = _read_pe_machine(newest_path)
    return _cached_hwp_machine


def _iter_dll_candidates() -> Iterator[Path]:
    appdata = os.environ.get("APPDATA")
    if appdata:
        staged_dir = Path(appdata) / "Inserty AI" / "modules"
        for dll_name in _DLL_CANDIDATES:
            yield staged_dir / dll_name

    env_path = os.environ.get("INSERTY_FILEPATHCHECKER_DLL")
    if env_path:
        env_candidate = Path(env_path).expanduser()
        if env_candidate.is_file():
            yield env_candidate
        elif env_candidate.is_dir():
            for dll_name in _DLL_CANDIDATES:
                yield env_candidate / dll_name

    try:
        from importlib.resources import files

        package_root = files("pyhwpx")
        for dll_name in _DLL_CANDIDATES:
            yield Path(str(package_root.joinpath(dll_name)))
    except Exception:
        pass

    try:
        import pyhwpx  # type: ignore

        package_dir = Path(pyhwpx.__file__).resolve().parent
        for dll_name in _DLL_CANDIDATES:
            yield package_dir / dll_name
    except Exception:
        pass

    runtime_dirs = (
        Path(sys.executable).resolve().parent,
        Path.cwd(),
        Path(__file__).resolve().parents[2],
        Path(__file__).resolve().parents[3],
    )
    for base_dir in runtime_dirs:
        for dll_name in _DLL_CANDIDATES:
            yield base_dir / dll_name


def find_security_module_dll() -> Optional[Path]:
    """FilePathCheckerModule DLL 경로를 탐색해 반환한다."""
    global _cached_dll_path

    if _cached_dll_path and _cached_dll_path.exists():
        return _cached_dll_path

    preferred_machine = _detect_hwp_machine()
    seen: set[str] = set()
    found: list[tuple[Optional[int], Path]] = []

    for candidate in _iter_dll_candidates():
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate

        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)

        try:
            if resolved.is_file():
                if not _is_pe_file(resolved):
                    continue
                found.append((_read_pe_machine(resolved), resolved))
        except Exception:
            continue

    if preferred_machine is not None:
        for machine, path in found:
            if machine == preferred_machine:
                _cached_dll_path = path
                return path

    if found:
        _cached_dll_path = found[0][1]
        return found[0][1]

    return None


def _iter_registry_targets(winreg) -> Iterator[tuple[str, int, str]]:
    view_flags = [0]
    for attr in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY"):
        flag = getattr(winreg, attr, None)
        if isinstance(flag, int):
            view_flags.append(flag)

    seen: set[tuple[str, int]] = set()
    for reg_path in _REGISTRY_PATHS:
        for view_flag in view_flags:
            key = (reg_path, view_flag)
            if key in seen:
                continue
            seen.add(key)
            if view_flag == getattr(winreg, "KEY_WOW64_32KEY", -1):
                view_name = "32"
            elif view_flag == getattr(winreg, "KEY_WOW64_64KEY", -2):
                view_name = "64"
            else:
                view_name = "default"
            yield reg_path, view_flag, view_name


def _query_registered_module_path(winreg, module_id: str) -> Optional[Path]:
    module_id_text = str(module_id or "").strip()
    if not module_id_text:
        return None

    for reg_path, view_flag, _view_name in _iter_registry_targets(winreg):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                reg_path,
                0,
                winreg.KEY_READ | view_flag,
            )
        except Exception:
            continue

        try:
            value, _reg_type = winreg.QueryValueEx(key, module_id_text)
        except Exception:
            value = None
        finally:
            winreg.CloseKey(key)

        if not value:
            continue

        candidate = Path(str(value))
        try:
            if candidate.is_file():
                return candidate.resolve()
        except Exception:
            continue

    return None


def _get_registered_module_path(module_id: str) -> Optional[Path]:
    try:
        import winreg
    except Exception:
        return None

    return _query_registered_module_path(winreg, module_id)


def _stage_security_module_dll(
    dll_path: Path,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return dll_path

    stage_dir = Path(appdata) / "Inserty AI" / "modules"
    stage_name = "FilePathCheckerModule.dll"
    staged_path = stage_dir / stage_name

    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        if (
            staged_path.exists()
            and staged_path.stat().st_size == dll_path.stat().st_size
            and staged_path.stat().st_mtime >= dll_path.stat().st_mtime
        ):
            return staged_path
        shutil.copy2(dll_path, staged_path)
        return staged_path
    except Exception as exc:
        _emit(log_callback, "debug", f"DLL stage copy 실패: {exc}")
        return dll_path


def _write_registry_via_winreg(
    winreg,
    module_ids: Tuple[str, ...],
    dll_path: Path,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    written = False
    for reg_path, view_flag, view_name in _iter_registry_targets(winreg):
        try:
            access = winreg.KEY_SET_VALUE
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                reg_path,
                0,
                access | view_flag,
            )
            try:
                for module_id in module_ids:
                    winreg.SetValueEx(
                        key,
                        module_id,
                        0,
                        winreg.REG_SZ,
                        str(dll_path),
                    )
                written = True
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            _emit(
                log_callback,
                "debug",
                f"레지스트리 등록 실패 ({reg_path}, view={view_name}): {exc}",
            )
    return written


def _write_registry_via_reg_exe(
    module_ids: Tuple[str, ...],
    dll_path: Path,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    reg_exe = shutil.which("reg")
    if not reg_exe:
        return False

    wrote = False
    for reg_path in _REGISTRY_PATHS:
        for view_arg in ("", "/reg:32", "/reg:64"):
            for module_id in module_ids:
                cmd = [
                    reg_exe,
                    "add",
                    rf"HKCU\{reg_path}",
                    "/v",
                    module_id,
                    "/t",
                    "REG_SZ",
                    "/d",
                    str(dll_path),
                    "/f",
                ]
                if view_arg:
                    cmd.append(view_arg)
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode == 0:
                    wrote = True
                    continue
                stderr_text = (proc.stderr or proc.stdout or "").strip()
                _emit(
                    log_callback,
                    "debug",
                    f"reg.exe 등록 실패 ({reg_path}, {view_arg or 'default'}, {module_id}): {stderr_text}",
                )
    return wrote


def ensure_security_module_registry(
    module_ids: Iterable[str] = DEFAULT_MODULE_IDS,
    dll_path: Optional[Path] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Path]:
    """HKCU 레지스트리에 FilePathCheckerModule 매핑을 생성/갱신한다."""
    module_id_list = _merge_module_ids(module_ids)
    resolved_dll_path = (dll_path or find_security_module_dll())
    if not resolved_dll_path:
        _emit(log_callback, "warning", "보안 모듈 DLL을 찾지 못했습니다.")
        return None

    resolved_dll_path = _stage_security_module_dll(resolved_dll_path, log_callback=log_callback)

    try:
        import winreg
    except Exception as exc:
        _emit(log_callback, "warning", f"winreg import 실패: {exc}")
        winreg = None  # type: ignore[assignment]

    written = False
    if winreg is not None:
        written = _write_registry_via_winreg(
            winreg,
            module_id_list,
            resolved_dll_path,
            log_callback=log_callback,
        )

    if not written:
        wrote_by_reg_exe = _write_registry_via_reg_exe(
            module_id_list,
            resolved_dll_path,
            log_callback=log_callback,
        )
        written = written or wrote_by_reg_exe

    # 쓰기 권한이 없는 환경을 고려해 기존 등록값을 최종 폴백으로 확인한다.
    for module_id in module_id_list:
        registered_path = _get_registered_module_path(module_id)
        if registered_path:
            return registered_path

    if written:
        return resolved_dll_path
    return None


def activate_security_module(
    hwp,
    module_ids: Iterable[str] = DEFAULT_MODULE_IDS,
    module_type: str = MODULE_TYPE,
    ensure_registry: bool = True,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """HWP 인스턴스에 FilePathCheckDLL 모듈 등록을 시도한다."""
    module_id_list = _merge_module_ids(module_ids)
    dll_path: Optional[Path] = None

    if ensure_registry:
        dll_path = ensure_security_module_registry(
            module_ids=module_id_list,
            log_callback=log_callback,
        )

    last_error: Optional[str] = None
    for module_id in module_id_list:
        registered_path = _get_registered_module_path(module_id)
        if dll_path is None and registered_path is not None:
            dll_path = registered_path

        try:
            result = hwp.RegisterModule(module_type, module_id)
            if result is False:
                # 일부 버전에서 False를 반환해도 동작하는 케이스가 있어
                # 레지스트리 매핑이 유효하면 성공으로 간주한다.
                if not registered_path:
                    continue
                if dll_path is None:
                    dll_path = registered_path
                _emit(
                    log_callback,
                    "debug",
                    f"RegisterModule False 반환 감지: {module_id} (레지스트리 매핑 확인됨)",
                )
            return True, module_id, str(dll_path) if dll_path else None
        except Exception as exc:
            last_error = str(exc)
            _emit(
                log_callback,
                "debug",
                f"RegisterModule 예외 ({module_id}): {exc}",
            )
            continue

    if last_error:
        _emit(
            log_callback,
            "warning",
            f"FilePathCheckDLL 모듈 활성화 실패: {last_error}",
        )
    return False, None, str(dll_path) if dll_path else None

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
import re
from pathlib import Path
from typing import Any, Optional, Tuple


def _sanitize_temp_stem(source_path: Path) -> str:
    raw_stem = source_path.stem or "document"
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_stem).strip("._-")
    return sanitized or "document"


def _safe_temp_copy_path(source_path: Path) -> Path:
    temp_dir = Path(tempfile.gettempdir()) / "inserty_hwp_open"
    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix or ".hwp"
    safe_stem = _sanitize_temp_stem(source_path)
    return temp_dir / f"{safe_stem}_{uuid.uuid4().hex}{suffix}"


def _stabilize_open_document(hwp: Any) -> None:
    stable_hits = 0
    for _ in range(10):
        try:
            page_count = int(getattr(hwp, "PageCount", 0) or 0)
        except Exception:
            page_count = 0
        try:
            opened_path = str(getattr(hwp, "Path", "") or "").strip()
        except Exception:
            opened_path = ""

        if page_count > 0 and opened_path:
            stable_hits += 1
            if stable_hits >= 2:
                break
        else:
            stable_hits = 0
        time.sleep(0.2)

    try:
        if hasattr(hwp, "move_pos"):
            hwp.move_pos(2)
        elif hasattr(hwp, "MovePos"):
            hwp.MovePos(2, 0, 0)
    except Exception:
        pass


def open_hwp_file_with_fallback(hwp: Any, file_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Open an HWP file, retrying through a sanitized temp copy when needed.

    Returns:
        (opened_ok, actual_opened_path, temp_copy_path)
    """
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(str(source))

    try:
        result = hwp.open(str(source))
        if result is not False:
            _stabilize_open_document(hwp)
            return True, str(source), None
    except Exception:
        pass

    temp_copy = _safe_temp_copy_path(source)
    shutil.copy2(source, temp_copy)

    try:
        result = hwp.open(str(temp_copy))
        if result is False:
            return False, None, str(temp_copy)
        _stabilize_open_document(hwp)
        return True, str(temp_copy), str(temp_copy)
    except Exception:
        return False, None, str(temp_copy)


def cleanup_temp_open_copy(temp_copy_path: Optional[str]) -> None:
    if not temp_copy_path:
        return
    try:
        Path(temp_copy_path).unlink(missing_ok=True)
    except Exception:
        pass

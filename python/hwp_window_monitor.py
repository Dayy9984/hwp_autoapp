"""
hwp_window_monitor.py - HWP 창 모니터링 (Multi-Window 지원)

HWP 창을 감지하여 PID와 HWND를 추출하고,
변경 사항을 JSON으로 stdout에 출력합니다.

개선된 기능:
- 모든 HWP 창 추적 (리스트로 관리)
- 활성 창 자동 전환 (포그라운드 기반)
- 문서 닫힘 시 다른 문서로 자동 바인딩
- 실시간 선택 영역 감지 (1초 폴링)

출력 형식:
{
  "type": "window_found" | "window_lost" | "window_switched" | "selection_changed",
  "pid": 12345,
  "hwnd": 67890,
  "title": "문서1.hwp",
  "totalWindows": 2,  # 현재 열린 HWP 창 수
  "data": {...}  # selection_changed 시 선택 영역 정보
}
"""

import sys
import time
import json
import os
import re
import threading
import queue
from typing import Optional, Tuple, Dict, Any, List
from ctypes import windll, byref, c_wchar_p, c_int, create_unicode_buffer, POINTER, WINFUNCTYPE
from ctypes.wintypes import HWND, LPARAM, BOOL, DWORD
from html.parser import HTMLParser
import win32clipboard
import win32con

# ROT 접근을 위한 import (선택 영역 감지)
try:
    import pythoncom
    import win32com.client
    from engine.connection.rot_access import ROTAccessManager
    HAS_COM_SUPPORT = True
except ImportError:
    HAS_COM_SUPPORT = False
    print("[Monitor] Warning: COM 지원 불가 (선택 영역 감지 비활성화)", file=sys.stderr)


# ====================================================================================
# DPI Awareness 설정 (고해상도 디스플레이 지원)
# ====================================================================================

def set_dpi_awareness():
    """
    DPI awareness 설정 - 노트북 고해상도 디스플레이 좌표 정확도 향상

    Windows 10 1703+: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    Windows 8.1+: PROCESS_PER_MONITOR_DPI_AWARE
    """
    try:
        # Windows 10 1703+ (가장 정확한 방법)
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        SetProcessDpiAwarenessContext = windll.user32.SetProcessDpiAwarenessContext
        SetProcessDpiAwarenessContext.argtypes = [c_int]
        SetProcessDpiAwarenessContext.restype = BOOL

        if SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except Exception:
        pass

    try:
        # Windows 8.1+ (fallback)
        PROCESS_PER_MONITOR_DPI_AWARE = 2
        SetProcessDpiAwareness = windll.shcore.SetProcessDpiAwareness
        SetProcessDpiAwareness.argtypes = [c_int]
        SetProcessDpiAwareness.restype = c_int

        SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
    except Exception:
        pass

# DPI Awareness 즉시 설정
set_dpi_awareness()


# ====================================================================================
# Win32 API 정의
# ====================================================================================

# EnumWindows 콜백 타입
WNDENUMPROC = WINFUNCTYPE(BOOL, HWND, LPARAM)

# Win32 함수 시그니처
EnumWindows = windll.user32.EnumWindows
EnumWindows.argtypes = [WNDENUMPROC, LPARAM]
EnumWindows.restype = BOOL

GetWindowThreadProcessId = windll.user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [HWND, POINTER(DWORD)]
GetWindowThreadProcessId.restype = DWORD

GetWindowTextW = windll.user32.GetWindowTextW
GetWindowTextW.argtypes = [HWND, c_wchar_p, c_int]
GetWindowTextW.restype = c_int

GetClassNameW = windll.user32.GetClassNameW
GetClassNameW.argtypes = [HWND, c_wchar_p, c_int]
GetClassNameW.restype = c_int

IsWindowVisible = windll.user32.IsWindowVisible
IsWindowVisible.argtypes = [HWND]
IsWindowVisible.restype = BOOL

# 포그라운드 창 감지 (레거시 스타일)
GetForegroundWindow = windll.user32.GetForegroundWindow
GetForegroundWindow.argtypes = []
GetForegroundWindow.restype = HWND

GetWindow = windll.user32.GetWindow
GetWindow.argtypes = [HWND, c_int]
GetWindow.restype = HWND

NON_HWP_CLASSES = {"#32770", "Chrome_WidgetWin_1", "Chrome_WidgetWin_0"}
GW_OWNER = 4


def _is_hwp_class_name(class_value: str) -> bool:
    if class_value.startswith(("HncFrame", "Hwp")):
        return True
    if class_value.startswith("HwndWrapper"):
        return "hwp.exe" in class_value.lower()
    return False

# HWP 창 찾기
# ====================================================================================

class HwpWindowFinder:
    """HWP 창을 찾는 클래스 (Multi-Window 지원)"""

    def __init__(self):
        # 모든 HWP 창 목록: {hwnd: {"pid": int, "title": str}}
        self.hwp_windows: Dict[int, Dict] = {}
        # 현재 활성 창 HWND
        self.active_hwnd: Optional[int] = None
        self._logged_windows: set = set()

    def _enum_callback_collect_all(self, hwnd: int, lparam: int) -> bool:
        """EnumWindows 콜백 - 모든 HWP 창 수집"""
        if not IsWindowVisible(hwnd):
            return True

        class_name = create_unicode_buffer(256)
        GetClassNameW(hwnd, class_name, 256)

        class_value = class_name.value

        if class_value in NON_HWP_CLASSES:
            return True

        owner_hwnd = GetWindow(hwnd, GW_OWNER)
        if owner_hwnd:
            return True

        if not _is_hwp_class_name(class_value):
            return True

        # Window title 가져오기 (로깅용)
        title = create_unicode_buffer(512)
        GetWindowTextW(hwnd, title, 512)
        title_str = title.value
        if class_value.startswith("HwndWrapper") and not title_str.strip():
            return True

        # HDML 추출 등 임시 작업용 문서 필터링 (자동 바인딩 방지)
        if title_str.startswith("빈 문서") or title_str.startswith("새 문서"):
            return True

        process_id = DWORD()
        GetWindowThreadProcessId(hwnd, byref(process_id))

        # 리스트에 추가
        self.hwp_windows[hwnd] = {
            "pid": process_id.value,
            "title": title.value,
            "class": class_name.value
        }

        # 감지된 HWP 창 로깅 (1회만)
        if hwnd not in self._logged_windows:
            print(f"[Monitor] HWP 창 감지: Class={class_name.value}, Title={title_str}, PID={process_id.value}, HWND={hwnd}", file=sys.stderr)
            self._logged_windows.add(hwnd)

        # 계속 검색 (모든 창 수집)
        return True

    def find_all_hwp_windows(self) -> Dict[int, Dict]:
        """
        모든 HWP 창 찾기

        Returns:
            {hwnd: {"pid": int, "title": str}, ...}
        """
        self.hwp_windows = {}

        try:
            callback = WNDENUMPROC(self._enum_callback_collect_all)
            EnumWindows(callback, 0)
        except Exception as e:
            print(f"[ERROR] HWP 창 검색 실패: {e}", file=sys.stderr)

        return self.hwp_windows

    def update_active_from_foreground(self) -> Optional[int]:
        """
        포그라운드 창이 HWP이면 활성 창으로 설정

        Returns:
            변경된 hwnd 또는 None (변경 없음)
        """
        try:
            fg_hwnd = GetForegroundWindow()
            if fg_hwnd and fg_hwnd in self.hwp_windows:
                pid = self.hwp_windows[fg_hwnd]["pid"]
                preferred_hwnd = self._pick_preferred_window(pid=pid, fallback_hwnd=fg_hwnd)
                if preferred_hwnd and self.active_hwnd != preferred_hwnd:
                    old_hwnd = self.active_hwnd
                    self.active_hwnd = preferred_hwnd
                    return preferred_hwnd
        except Exception:
            pass
        return None

    def get_active_window(self) -> Optional[Tuple[int, int, str]]:
        """
        현재 활성 HWP 창 정보 반환

        Returns:
            (pid, hwnd, title) 또는 None
        """
        if self.active_hwnd and self.active_hwnd in self.hwp_windows:
            info = self.hwp_windows[self.active_hwnd]
            return (info["pid"], self.active_hwnd, info["title"])
        return None

    def select_new_active(self) -> Optional[Tuple[int, int, str]]:
        """
        활성 창이 없거나 닫힌 경우 다른 HWP 창 선택

        Returns:
            (pid, hwnd, title) 또는 None (HWP 창 없음)
        """
        if not self.hwp_windows:
            self.active_hwnd = None
            return None

        # 주 창 후보 선택 (HncFrame/Hwp 우선)
        preferred_hwnd = self._pick_preferred_window()
        if preferred_hwnd:
            info = self.hwp_windows[preferred_hwnd]
            self.active_hwnd = preferred_hwnd
            return (info["pid"], preferred_hwnd, info["title"])

        return None

    def _pick_preferred_window(
        self,
        pid: Optional[int] = None,
        fallback_hwnd: Optional[int] = None
    ) -> Optional[int]:
        candidates = [
            (hwnd, info)
            for hwnd, info in self.hwp_windows.items()
            if pid is None or info["pid"] == pid
        ]
        if not candidates:
            return None

        def _rank(info: Dict) -> int:
            class_name = info.get("class", "")
            if class_name.startswith("HncFrame"):
                return 0
            if class_name.startswith("Hwp"):
                return 1
            return 2

        def _key(item: Tuple[int, Dict]) -> Tuple[int, int]:
            hwnd, info = item
            return (
                _rank(info),
                0 if (fallback_hwnd is not None and hwnd == fallback_hwnd) else 1
            )

        best_hwnd, _ = min(candidates, key=_key)
        return best_hwnd

    def find_hwp_window(self) -> Optional[Tuple[int, int, str]]:
        """
        HWP 창 찾기 (레거시 호환용 - 활성 창 반환)

        Returns:
            (pid, hwnd, title) 또는 None
        """
        # 모든 창 수집
        self.find_all_hwp_windows()

        if not self.hwp_windows:
            self.active_hwnd = None
            return None

        # 포그라운드 확인
        self.update_active_from_foreground()

        # 활성 창 반환 또는 새 창 선택
        result = self.get_active_window()
        if result:
            return result

        return self.select_new_active()

    def _check_hwp_window(self, hwnd: int) -> Optional[Tuple[int, int, str]]:
        """주어진 HWND가 HWP 창인지 확인"""
        try:
            if not IsWindowVisible(hwnd):
                return None

            class_name = create_unicode_buffer(256)
            GetClassNameW(hwnd, class_name, 256)

            if class_name.value in NON_HWP_CLASSES:
                return None

            owner_hwnd = GetWindow(hwnd, GW_OWNER)
            if owner_hwnd:
                return None

            if not _is_hwp_class_name(class_name.value):
                return None

            title = create_unicode_buffer(512)
            GetWindowTextW(hwnd, title, 512)
            if class_name.value.startswith("HwndWrapper") and not title.value.strip():
                return None

            process_id = DWORD()
            GetWindowThreadProcessId(hwnd, byref(process_id))

            return (process_id.value, hwnd, title.value)
        except Exception:
            return None


# ====================================================================================
# HTML 표 파싱 (클립보드 HTML Format)
# ====================================================================================

class TableParser(HTMLParser):
    """HTML 표 파싱 클래스"""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.current_row = []
        self.current_cell = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
        elif tag == 'tr' and self.in_table:
            self.in_tr = True
            self.current_row = []
        elif tag == 'td' and self.in_tr:
            self.in_td = True
            self.current_cell = []
        elif tag == 'br' and self.in_td:
            self.current_cell.append('\n')

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_table = False
        elif tag == 'tr':
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_tr = False
        elif tag == 'td':
            cell_text = ''.join(self.current_cell).strip()
            self.current_row.append(cell_text)
            self.in_td = False
        elif tag == 'p' and self.in_td:
            if self.current_cell and self.current_cell[-1] != '\n':
                self.current_cell.append('\n')

    def handle_data(self, data):
        if self.in_td:
            text = re.sub(r'\s+', ' ', data)
            if text.strip():
                self.current_cell.append(text)


def extract_html_fragment(html_text: str) -> str:
    """HTML Format에서 Fragment 구간 추출"""
    start_marker = "<!--StartFragment-->"
    end_marker = "<!--EndFragment-->"

    start_idx = html_text.find(start_marker)
    end_idx = html_text.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        return html_text[start_idx + len(start_marker):end_idx]
    return html_text


def parse_html_table(html_text: str) -> str:
    """HTML 표를 셀 마커 형식 텍스트로 변환"""
    try:
        fragment = extract_html_fragment(html_text)

        if "<table" not in fragment.lower():
            return ""

        parser = TableParser()
        parser.feed(fragment)

        if not parser.rows:
            return ""

        # 셀 마커 형식으로 변환
        lines = []
        cell_index = 0

        for row_idx, row in enumerate(parser.rows):
            lines.append(f"[행 {row_idx + 1}]")
            for cell in row:
                cell_index += 1
                lines.append(f"[셀 {cell_index}]")
                lines.append(cell)
                lines.append("")  # 셀 구분

        return '\n'.join(lines)

    except Exception as e:
        print(f"[Selection] HTML 파싱 실패: {e}", file=sys.stderr)
        return ""


def _open_clipboard_with_retry(max_retries=3, delay=0.05) -> bool:
    """클립보드 열기 (재시도 포함) - 다른 프로세스가 점유 중일 때 대비"""
    for i in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            if i < max_retries - 1:
                time.sleep(delay)
    return False


def backup_clipboard() -> Dict[str, Any]:
    """클립보드 백업"""
    backup = {}
    clipboard_opened = False
    try:
        clipboard_opened = _open_clipboard_with_retry()
        if not clipboard_opened:
            return backup

        # Unicode Text 백업
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            try:
                backup['unicode'] = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            except:
                pass

        # Text 백업
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
            try:
                backup['text'] = win32clipboard.GetClipboardData(win32con.CF_TEXT)
            except:
                pass

        win32clipboard.CloseClipboard()
        clipboard_opened = False
    except Exception as e:
        print(f"[Selection] 클립보드 백업 실패: {e}", file=sys.stderr)
        if clipboard_opened:
            try:
                win32clipboard.CloseClipboard()
            except:
                pass

    return backup


def restore_clipboard(backup: Dict[str, Any]):
    """클립보드 복원"""
    clipboard_opened = False
    try:
        clipboard_opened = _open_clipboard_with_retry()
        if not clipboard_opened:
            return

        win32clipboard.EmptyClipboard()

        # Unicode Text 복원
        if 'unicode' in backup:
            try:
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, backup['unicode'])
            except:
                pass

        # Text 복원
        if 'text' in backup:
            try:
                win32clipboard.SetClipboardData(win32con.CF_TEXT, backup['text'])
            except:
                pass

        win32clipboard.CloseClipboard()
        clipboard_opened = False
    except Exception as e:
        print(f"[Selection] 클립보드 복원 실패: {e}", file=sys.stderr)
        if clipboard_opened:
            try:
                win32clipboard.CloseClipboard()
            except:
                pass


def get_clipboard_html() -> Optional[str]:
    """클립보드에서 HTML Format 읽기"""
    clipboard_opened = False
    try:
        clipboard_opened = _open_clipboard_with_retry()
        if not clipboard_opened:
            return None

        html_format = win32clipboard.RegisterClipboardFormat("HTML Format")
        if not win32clipboard.IsClipboardFormatAvailable(html_format):
            win32clipboard.CloseClipboard()
            clipboard_opened = False
            return None

        html_data = win32clipboard.GetClipboardData(html_format)
        win32clipboard.CloseClipboard()
        clipboard_opened = False

        # bytes → str 변환
        if isinstance(html_data, bytes):
            return html_data.decode('utf-8', errors='ignore')
        else:
            return str(html_data)

    except Exception as e:
        print(f"[Selection] HTML Format 읽기 실패: {e}", file=sys.stderr)
        if clipboard_opened:
            try:
                win32clipboard.CloseClipboard()
            except:
                pass
        return None


# ====================================================================================
# 선택 영역 변경 감지
# ====================================================================================
# ====================================================================================

def get_selection_info_from_hwp(hwp_obj) -> Optional[Dict[str, Any]]:
    """
    HWP COM 객체에서 선택 영역 정보 추출

    표 선택 (mode=3): Copy + HTML Format 파싱
    - Run("Copy") → 클립보드 HTML Format 추출
    - <table> → <tr> → <td> 구조로 셀 구분 명확
    - 클립보드 백업/복원으로 사용자 클립보드 보호

    드래그 선택 (mode=1,2): InitScan + GetText
    - option=0x07 (char+inline+ctrl 모두 포함)
    - range=0xff (선택 영역만)
    - state=1일 때만 종료

    Returns:
        dict: {
            "hasSelection": bool,
            "selectedText": str,
            "selectedTextFull": str,
            "selectionType": "cursor" | "text" | "table" | "control",
            "isTableSelection": bool,
            "position": ((list, para, pos), (list, para, pos)) | None,
            "filename": str | None
        }
        None: COM 지원 불가 또는 오류
    """
    if not HAS_COM_SUPPORT or not hwp_obj:
        return None

    try:
        raw_hwp = getattr(hwp_obj, '_raw', None) or getattr(hwp_obj, 'hwp', None) or hwp_obj

        # 1. 파일명 추출 (XHwpDocuments.Active_XHwpDocument.FullName)
        filename = None
        try:
            if hasattr(raw_hwp, 'XHwpDocuments'):
                xdocs = raw_hwp.XHwpDocuments
                if xdocs:
                    active_doc = xdocs.Active_XHwpDocument
                    if active_doc and hasattr(active_doc, 'FullName'):
                        active_path = active_doc.FullName
                        if active_path:
                            filename = os.path.basename(active_path)
                        else:
                            # 저장되지 않은 문서
                            filename = "새 문서"
        except Exception:
            pass

        # 2. SelectionMode 확인
        selection_mode = 0
        if hasattr(raw_hwp, 'SelectionMode'):
            selection_mode = (raw_hwp.SelectionMode or 0) & 0x0F

        has_selection = (selection_mode != 0)
        is_text = (selection_mode in (1, 2))
        is_table = (selection_mode == 3)
        is_ctrl = (selection_mode == 4)

        # 3. 선택 범위 좌표 (빠름, InitScan 없음)
        position = None
        if hasattr(hwp_obj, 'CreateSet') and hasattr(hwp_obj, 'GetSelectedPosBySet'):
            try:
                sset = hwp_obj.CreateSet("ListParaPos")
                eset = hwp_obj.CreateSet("ListParaPos")
                if hwp_obj.GetSelectedPosBySet(sset, eset):
                    position = (
                        (sset.Item("List"), sset.Item("Para"), sset.Item("Pos")),
                        (eset.Item("List"), eset.Item("Para"), eset.Item("Pos"))
                    )
            except Exception:
                pass

        # 4. 선택 텍스트 추출
        selected_text = ""
        if has_selection and not is_ctrl:
            # 표 선택 (mode=3): Copy + HTML 파싱
            if is_table and hasattr(hwp_obj, 'Run'):
                try:
                    # 1. 클립보드 백업
                    clipboard_backup = backup_clipboard()

                    # 2. 복사 실행
                    hwp_obj.Run("Copy")

                    # HWP의 HTML Format 클립보드 쓰기 완료 대기
                    time.sleep(0.15)

                    # 3. HTML Format 파싱
                    html_text = get_clipboard_html()
                    if html_text:
                        selected_text = parse_html_table(html_text)

                    # 4. 클립보드 복원
                    restore_clipboard(clipboard_backup)

                except Exception as e:
                    print(f"[Selection] Copy + HTML 파싱 실패: {e}", file=sys.stderr)
                    # 클립보드 복원 시도
                    try:
                        restore_clipboard(clipboard_backup)
                    except:
                        pass

            # 드래그 선택 (mode=1,2) 또는 표 Copy 실패: Copy + 클립보드 텍스트
            if not selected_text and (is_text or is_table):
                try:
                    # 1. 클립보드 백업
                    clipboard_backup = backup_clipboard()

                    # 2. 복사 실행
                    hwp_obj.Run("Copy")

                    # HWP의 클립보드 쓰기 완료 대기
                    time.sleep(0.15)

                    # 3. 클립보드에서 일반 텍스트 읽기
                    clipboard_text_opened = False
                    try:
                        clipboard_text_opened = _open_clipboard_with_retry()
                        if clipboard_text_opened:
                            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                                selected_text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                            win32clipboard.CloseClipboard()
                            clipboard_text_opened = False
                    except Exception as e:
                        print(f"[Selection] 클립보드 텍스트 읽기 실패: {e}", file=sys.stderr)
                        if clipboard_text_opened:
                            try:
                                win32clipboard.CloseClipboard()
                            except:
                                pass

                    # 4. 클립보드 복원
                    restore_clipboard(clipboard_backup)

                except Exception as e:
                    print(f"[Selection] Copy + 클립보드 읽기 실패: {e}", file=sys.stderr)
                    # 클립보드 복원 시도
                    try:
                        restore_clipboard(clipboard_backup)
                    except:
                        pass

            # Fallback 메시지
            if not selected_text and is_table:
                selected_text = "(셀 블록 선택됨)"

        # 5. 선택 유형 결정
        if is_table:
            sel_type = "table"
        elif is_text:
            sel_type = "text"
        elif is_ctrl:
            sel_type = "control"
            selected_text = "(개체 선택됨)"
        else:
            sel_type = "cursor"

        result = {
            "hasSelection": has_selection,
            "selectedText": selected_text[:200] if selected_text else "",
            "selectedTextFull": selected_text,
            "selectionType": sel_type,
            "isTableSelection": is_table,
            "position": position,
            "filename": filename
        }

        return result

    except Exception as e:
        print(f"[Monitor] 선택 영역 감지 오류: {e}", file=sys.stderr)
        return None


# ====================================================================================
# stdin 명령어 처리 (pause/resume)
# ====================================================================================

def stdin_reader(command_queue: queue.Queue):
    """
    stdin에서 명령어를 읽어 큐에 추가하는 백그라운드 스레드

    명령어 형식: {"type": "pause_selection"} 또는 {"type": "resume_selection"}
    """
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            command_queue.put(line.strip())
        except Exception as e:
            print(f"[Monitor] stdin reader error: {e}", file=sys.stderr)
            break


# ====================================================================================
# 메인 모니터링 루프
# ====================================================================================

def main():
    """HWP 창 모니터링 메인 루프 (Multi-Window + 선택 영역 감지)"""
    print("[Monitor] HWP Window Monitor started (Multi-Window + Selection)", file=sys.stderr)

    # stdin 명령어 처리를 위한 백그라운드 스레드 시작
    command_queue: queue.Queue = queue.Queue()
    stdin_thread = threading.Thread(target=stdin_reader, args=(command_queue,), daemon=True)
    stdin_thread.start()

    finder = HwpWindowFinder()
    last_pid: Optional[int] = None
    last_hwnd: Optional[int] = None
    last_window_count: int = 0
    previous_hwnds: set = set()  # 이전 루프의 HWND 세트

    # 선택 영역 감지용
    hwp_com_obj = None
    last_selection: Optional[Dict[str, Any]] = None
    selection_paused = False  # 선택 영역 감지 일시 중단 플래그

    loop_count = 0

    while True:
        try:
            loop_count += 1

            # stdin 명령어 처리 (non-blocking)
            while not command_queue.empty():
                try:
                    line = command_queue.get_nowait()
                    command = json.loads(line)
                    cmd_type = command.get('type')

                    if cmd_type == 'pause_selection':
                        selection_paused = True
                        print("[Monitor] Selection detection paused", file=sys.stderr)
                    elif cmd_type == 'resume_selection':
                        selection_paused = False
                        print("[Monitor] Selection detection resumed", file=sys.stderr)
                except queue.Empty:
                    break
                except Exception as e:
                    print(f"[Monitor] Command parsing error: {e}", file=sys.stderr)

            # 모든 HWP 창 수집
            finder.find_all_hwp_windows()
            current_window_count = len(finder.hwp_windows)
            current_hwnds = set(finder.hwp_windows.keys())

            # 활성 창이 여전히 존재하는지 확인
            active_still_exists = (
                finder.active_hwnd is not None and
                finder.active_hwnd in finder.hwp_windows
            )

            # 포그라운드 창이 HWP이면 활성 창 갱신
            switched_hwnd = finder.update_active_from_foreground()

            if switched_hwnd:
                # 사용자가 다른 HWP 창 클릭 -> 전환 이벤트
                info = finder.hwp_windows[switched_hwnd]
                output = {
                    "type": "window_switched",
                    "pid": info["pid"],
                    "hwnd": switched_hwnd,
                    "title": info["title"],
                    "totalWindows": current_window_count,
                    "reason": "foreground_changed"
                }
                print(json.dumps(output), flush=True)
                print(f"[Monitor] HWP 창 전환: PID={info['pid']}, HWND={switched_hwnd}, Title={info['title']}", file=sys.stderr)

                last_pid = info["pid"]
                last_hwnd = switched_hwnd

            elif not active_still_exists and finder.hwp_windows:
                # 이전 활성 창이 닫힘 -> 다른 창으로 자동 전환
                result = finder.select_new_active()
                if result:
                    pid, hwnd, title = result
                    output = {
                        "type": "window_switched",
                        "pid": pid,
                        "hwnd": hwnd,
                        "title": title,
                        "totalWindows": current_window_count,
                        "reason": "previous_closed"
                    }
                    print(json.dumps(output), flush=True)
                    print(f"[Monitor] 자동 전환 (이전 창 닫힘): PID={pid}, HWND={hwnd}", file=sys.stderr)

                    last_pid = pid
                    last_hwnd = hwnd
                    hwp_com_obj = None
                    last_selection = None

            elif finder.hwp_windows and last_hwnd is None:
                # 처음 HWP 창 발견
                result = finder.select_new_active()
                if result:
                    pid, hwnd, title = result
                    output = {
                        "type": "window_found",
                        "pid": pid,
                        "hwnd": hwnd,
                        "title": title,
                        "totalWindows": current_window_count
                    }
                    print(json.dumps(output), flush=True)
                    print(f"[Monitor] HWP 창 발견: PID={pid}, HWND={hwnd}, Title={title}", file=sys.stderr)

                    last_pid = pid
                    last_hwnd = hwnd

            elif not finder.hwp_windows and last_hwnd is not None:
                # 모든 HWP 창이 닫힘
                output = {
                    "type": "window_lost",
                    "totalWindows": 0
                }
                print(json.dumps(output), flush=True)
                print("[Monitor] 모든 HWP 창 사라짐", file=sys.stderr)

                last_pid = None
                last_hwnd = None
                finder.active_hwnd = None
                hwp_com_obj = None
                last_selection = None

            # 창 수 변경 로깅
            if current_window_count != last_window_count:
                print(f"[Monitor] HWP 창 수 변경: {last_window_count} -> {current_window_count}", file=sys.stderr)

                last_window_count = current_window_count

            # 다음 루프를 위해 현재 HWND 세트 저장
            previous_hwnds = current_hwnds

            # 선택 영역 감지 (1초마다 체크, paused가 아닐 때만)
            if HAS_COM_SUPPORT and finder.active_hwnd and not selection_paused:
                # HWP COM 객체 얻기 (ROT에서)
                if hwp_com_obj is None:
                    try:
                        hwp_com_obj = ROTAccessManager.get_hwp_instance_by_target(
                            preferred_hwnd=finder.active_hwnd,
                            strict=False,
                        )
                        if hwp_com_obj and not ROTAccessManager.verify_hwp_instance(hwp_com_obj):
                            hwp_com_obj = None
                    except Exception as e:
                        print(f"[Monitor] Selection COM bind failed: {e}", file=sys.stderr)
                        hwp_com_obj = None

                # 선택 영역 확인
                if hwp_com_obj:
                    try:
                        current_selection = get_selection_info_from_hwp(hwp_com_obj)

                        # 선택 변경 감지 (선택이 실제로 있을 때만)
                        if current_selection and current_selection.get("hasSelection"):
                            if current_selection != last_selection:
                                output = {
                                    "type": "selection_changed",
                                    "data": current_selection
                                }
                                print(json.dumps(output, ensure_ascii=False), flush=True)
                                last_selection = current_selection
                        elif last_selection and last_selection.get("hasSelection"):
                            # 선택이 해제된 경우 (이전에 선택이 있었으나 지금은 없음)
                            empty_selection = {
                                "hasSelection": False,
                                "selectedText": "",
                                "selectedTextFull": "",
                                "selectionType": "cursor",
                                "isTableSelection": False,
                                "position": None,
                                "filename": None
                            }
                            output = {
                                "type": "selection_changed",
                                "data": empty_selection
                            }
                            print(json.dumps(output, ensure_ascii=False), flush=True)
                            last_selection = None

                    except Exception as e:
                        # COM 객체 무효화 시 재바인딩
                        print(f"[Monitor] Selection check error: {e}", file=sys.stderr)
                        if "RPC" in str(e) or "disconnect" in str(e).lower():
                            hwp_com_obj = None
                            last_selection = None

        except KeyboardInterrupt:
            print("[Monitor] 종료", file=sys.stderr)
            break
        except Exception as e:
            print(f"[Monitor] 오류: {e}", file=sys.stderr)

        # 1초 대기
        time.sleep(1)


if __name__ == "__main__":
    main()

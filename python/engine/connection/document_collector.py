"""
Document Metadata Collector for HWP Windows

Win32 API를 사용하여 HWP 창 정보를 수집하고 문서 메타데이터를 추출합니다.
HWP COM 객체의 XHwpWindows 접근을 최소화하여 HWP 2018 호환성을 보장합니다.
"""

import os
import re
import ctypes
from ctypes import windll, byref
from ctypes.wintypes import HWND, DWORD, BOOL, LPARAM
from typing import List, Dict, Optional


class DocumentMetadataCollector:
    """HWP 문서 메타데이터 수집기 (Win32 API 기반)"""

    def __init__(self):
        self._cached_windows: List[Dict] = []

    def collect_hwp_window_info(self) -> List[Dict]:
        """
        Win32 API를 사용하여 열린 HWP 창 정보 수집

        Returns:
            List[Dict]: 각 창의 HWND, PID, 제목, 클래스명 정보
        """
        windows = []

        def callback(hwnd: int, lparam: int) -> bool:
            # 보이지 않는 창 제외
            if not windll.user32.IsWindowVisible(hwnd):
                return True

            # 창 제목 가져오기
            title_length = windll.user32.GetWindowTextLengthW(hwnd)
            if title_length == 0:
                return True

            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            windll.user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
            window_title = title_buffer.value

            # 클래스명 가져오기
            class_buffer = ctypes.create_unicode_buffer(256)
            windll.user32.GetClassNameW(hwnd, class_buffer, 256)
            class_name = class_buffer.value

            # HWP 창 필터링
            if self._is_hwp_window(window_title, class_name):
                # PID 추출
                process_id = DWORD()
                windll.user32.GetWindowThreadProcessId(hwnd, byref(process_id))

                windows.append({
                    'hwnd': hwnd,
                    'pid': process_id.value,
                    'title': window_title,
                    'class_name': class_name
                })

            return True

        # EnumWindows 콜백 타입 정의
        callback_type = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
        enum_callback = callback_type(callback)

        # 모든 창 열거
        windll.user32.EnumWindows(enum_callback, 0)

        self._cached_windows = windows
        return windows

    def _is_hwp_window(self, title: str, class_name: str) -> bool:
        """
        HWP 창인지 판별

        Args:
            title: 창 제목
            class_name: 창 클래스명

        Returns:
            bool: HWP 창 여부
        """
        # Dialog 창 제외
        if class_name == "#32770":
            return False

        # HWP 클래스명 패턴
        hwp_class_patterns = ["Hwp", "HncFrame"]
        for pattern in hwp_class_patterns:
            if class_name.startswith(pattern):
                return True

        # 제목 기반 감지 (클래스명이 일치하지 않는 경우)
        title_lower = title.lower()
        if ".hwp" in title_lower or "한글" in title:
            return True

        return False

    def parse_document_name(self, window_title: str) -> Optional[str]:
        """
        창 제목에서 문서 파일명 추출

        Args:
            window_title: 창 제목 (예: "[별첨-1] 사업계획서.hwp [C:\\Users\\...] - 한글")

        Returns:
            str: 파일명 (예: "[별첨-1] 사업계획서.hwp") 또는 None
        """
        if not window_title:
            return None

        # " - 한글" 제거
        clean_title = window_title
        if " - 한글" in clean_title:
            clean_title = clean_title.split(" - 한글")[0].strip()

        # 경로 대괄호만 제거 (예: "[C:\Users\...]", "[D:\...]", "[\\server\...]")
        # 파일명에 포함된 대괄호는 유지 (예: "[별첨-1]")
        path_bracket_pattern = r'\s*\[(?:[A-Za-z]:|\\\\)[^\]]*\]'
        clean_title = re.sub(path_bracket_pattern, '', clean_title).strip()

        # .hwp 확장자가 없으면 전체 제목 반환 (빈 문서, 새 문서 등)
        return clean_title if clean_title else None

    def build_document_metadata(self, window_info_list: List[Dict]) -> List[Dict]:
        """
        창 정보 목록에서 문서 메타데이터 생성

        Args:
            window_info_list: collect_hwp_window_info()의 결과

        Returns:
            List[Dict]: 문서 메타데이터 목록 (hwnd, pid, filename 포함)
        """
        metadata_list = []

        for info in window_info_list:
            filename = self.parse_document_name(info['title'])

            metadata_list.append({
                'hwnd': info['hwnd'],
                'pid': info['pid'],
                'filename': filename,
                'raw_title': info['title'],
                'class_name': info['class_name']
            })

        return metadata_list

    def match_com_documents(self, hwp_com_obj, metadata_list: List[Dict]) -> List[Dict]:
        """
        HWP COM 객체의 문서 목록과 Win32 창 정보를 매칭

        Args:
            hwp_com_obj: HWP COM 객체 (pyhwpx Hwp 인스턴스)
            metadata_list: build_document_metadata()의 결과

        Returns:
            List[Dict]: COM 문서 인덱스와 창 정보가 매칭된 목록
        """
        matched_documents = []

        try:
            doc_count = hwp_com_obj.XHwpDocuments.Count
        except Exception:
            return matched_documents

        # COM 문서 목록 순회
        for doc_index in range(doc_count):
            try:
                doc = hwp_com_obj.XHwpDocuments.Item(doc_index)
                full_path = doc.FullName
                doc_basename = os.path.basename(full_path)

                # 메타데이터와 매칭
                matched_meta = None
                for meta in metadata_list:
                    if meta['filename'] == doc_basename:
                        matched_meta = meta
                        break

                # 매칭된 정보 구성
                doc_info = {
                    'com_index': doc_index,
                    'full_path': full_path,
                    'filename': doc_basename,
                    'hwnd': matched_meta['hwnd'] if matched_meta else None,
                    'pid': matched_meta['pid'] if matched_meta else None,
                }

                matched_documents.append(doc_info)

            except Exception:
                # 문서 접근 실패 시 건너뛰기
                continue

        return matched_documents

    def get_active_document_hwnd(self, hwp_com_obj) -> Optional[int]:
        """
        현재 활성 문서의 HWND 반환

        Args:
            hwp_com_obj: HWP COM 객체

        Returns:
            int: 활성 문서의 HWND 또는 None
        """
        try:
            active_doc = hwp_com_obj.XHwpDocuments.Active_XHwpDocument
            if not active_doc:
                return None

            active_fullname = active_doc.FullName
            active_basename = os.path.basename(active_fullname)

            # 캐시된 창 정보에서 찾기
            for window_info in self._cached_windows:
                parsed_name = self.parse_document_name(window_info['title'])
                if parsed_name == active_basename:
                    return window_info['hwnd']

        except Exception:
            pass

        return None

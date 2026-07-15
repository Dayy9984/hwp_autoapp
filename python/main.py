"""
Inserty Python Processor

Electron Main Process와 stdin/stdout으로 JSON-RPC 통신하며
HWP 문서 편집 작업을 수행합니다.

사용법:
    python main.py

통신 프로토콜:
    - stdin으로 JSON 명령 수신
    - stdout으로 JSON 응답 전송
    - 각 메시지는 줄바꿈으로 구분
"""

import sys
import os
import json
import asyncio
import traceback
from typing import Optional, Any
from pathlib import Path

# Windows COM 초기화
try:
    import pythoncom
    pythoncom.CoInitialize()
except ImportError:
    pass  # pythoncom이 없는 환경에서는 스킵

# 현재 구조 기반 import
from engine.connection.document_connector import HwpConnector
from utilities.error_handler import ErrorHandler
from engine.state import session_state
from processing.structure.view_generator import DocumentViewGenerator
from processing.conversion.markup_converter import MarkupConverter as DocumentExtractor
from llm.client import get_llm_client
from llm.streaming_client import get_streaming_client, StreamingCommand
from readers.txt_reader import TxtReader
from readers.excel_reader import ExcelReader


class DocumentProcessor:
    """문서 처리기 - HWP 문서 편집 작업 수행

    SafeHwp 패턴 지원: Window Monitor로부터 전달받은 PID/HWND로 정확한 HWP 바인딩
    """

    def __init__(self):
        # HWP 연결 관리
        self._connector: Optional[HwpConnector] = None
        self._current_file: Optional[str] = None

        # Window Monitor 연동 (SafeHwp 패턴)
        self._process_identifier: Optional[int] = None
        self._window_handle: Optional[int] = None

        # Undo/Diff 관련 상태
        self._diff_mode_enabled: bool = False
        self._edit_history: list = []  # [{id, chatId, editCount, timestamp}, ...]
        self._current_chat_id: Optional[str] = None

        # 문서 뷰 생성기
        self._view_generator: Optional[DocumentViewGenerator] = None

        # HDML 관련 (chat 메서드에서 초기화)
        self._hdml_adapter = None
        self._hdml_parser = None
        self._doc_hdml = None
        self._allowed_block_ids = None
        self._table_anchors = None

    def _ensure_connector(self) -> HwpConnector:
        """HWP Connector 초기화 (lazy)

        process_identifier/window_handle이 설정되어 있으면 SafeHwp 패턴으로 바인딩
        """
        if self._connector is None:
            self._connector = HwpConnector(
                visible=True,
                new=False,
                process_identifier=self._process_identifier,
                window_handle=self._window_handle
            )
            if not self._connector.connect():
                raise RuntimeError("HWP 연결 실패")
        return self._connector

    def _ensure_adapter(self) -> HwpConnector:
        """_ensure_connector의 alias (하위 호환성)"""
        return self._ensure_connector()

    def _get_pid_from_hwnd(self, hwnd: int) -> Optional[int]:
        try:
            import ctypes
            from ctypes.wintypes import DWORD, HWND

            pid = DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(HWND(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return None

    def _get_hwp_from_rot(
        self,
        preferred_pid: Optional[int] = None,
        preferred_hwnd: Optional[int] = None,
    ):
        try:
            import win32com.client
            import pythoncom

            pythoncom.CoInitialize()
            rot = pythoncom.GetRunningObjectTable()
            monikers = rot.EnumRunning()

            candidates: list[tuple[Any, Optional[int], Optional[int]]] = []

            while True:
                try:
                    moniker = monikers.Next(1)
                    if not moniker:
                        break
                    moniker = moniker[0]
                    ctx = pythoncom.CreateBindCtx(0)
                    name = moniker.GetDisplayName(ctx, None)

                    if 'HwpObject' not in name:
                        continue

                    hwp_obj = rot.GetObject(moniker)

                    # Late binding (gen_py 비활성화로 순수 동적 호출)
                    hwp = win32com.client.dynamic.Dispatch(
                        hwp_obj.QueryInterface(pythoncom.IID_IDispatch)
                    )

                    pid = None
                    hwnd = None
                    # HWP 2018 (Version "10,...") = WindowHandle 호출 시 active document
                    # toggle 부수효과. 호출 자체 회피 + pid/hwnd=None 으로 등록.
                    is_hwp_legacy = False
                    try:
                        v = str(getattr(hwp, 'Version', '')).strip()
                        is_hwp_legacy = v.startswith("10,")
                    except Exception:
                        pass

                    if not is_hwp_legacy:
                        try:
                            # WindowHandle 속성 사용 (32/64비트 호환)
                            hwnd = hwp.XHwpWindows.Item(0).WindowHandle
                            pid = self._get_pid_from_hwnd(hwnd)
                        except Exception:
                            pass

                    candidates.append((hwp, pid, hwnd))
                except StopIteration:
                    break
                except Exception:
                    continue

            if preferred_pid is not None:
                for hwp, pid, _ in candidates:
                    if pid == preferred_pid:
                        return hwp

            if preferred_hwnd is not None:
                for hwp, _, hwnd in candidates:
                    if hwnd == preferred_hwnd:
                        return hwp

            if candidates:
                return candidates[0][0]
        except Exception:
            return None

        return None

    def get_open_documents(self) -> dict:
        """열린 문서 목록 조회 (HWP, Word, Excel)"""
        documents = []

        # HWP 문서 조회
        try:
            hwp_docs = self._get_hwp_documents()
            documents.extend(hwp_docs)
        except Exception as e:
            print(f"[Python] HWP 문서 조회 오류: {e}", file=sys.stderr)

        # Word 문서 조회
        try:
            word_docs = self._get_word_documents()
            documents.extend(word_docs)
        except Exception as e:
            print(f"[Python] Word 문서 조회 오류: {e}", file=sys.stderr)

        # Excel 문서 조회
        try:
            excel_docs = self._get_excel_documents()
            documents.extend(excel_docs)
        except Exception as e:
            print(f"[Python] Excel 문서 조회 오류: {e}", file=sys.stderr)

        return {"success": True, "documents": documents}

    def _get_hwp_documents(self) -> list:
        """ROT에서 직접 HWP 객체를 가져와 문서 목록 조회 (한글 활성화 없이)"""
        documents = []
        import os

        try:
            preferred_pid = session_state.retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = session_state.retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                # 한글이 실행 중이 아님
                return documents

            # XHwpDocuments 컬렉션으로 열린 문서 목록 가져오기
            try:
                xdocs = hwp.XHwpDocuments
                doc_count = xdocs.Count

                for i in range(doc_count):
                    doc = xdocs.Item(i)
                    if doc:
                        # FullName에서 전체 경로 가져오기
                        try:
                            path = doc.FullName if hasattr(doc, 'FullName') else ''
                        except:
                            path = ''

                        # 파일명 추출
                        if path:
                            name = os.path.basename(path)
                        else:
                            # 저장 안 된 문서
                            name = f"새 문서 {i + 1}"

                        doc_id = None
                        try:
                            doc_id = int(doc.DocumentID)
                        except Exception:
                            doc_id = None

                        doc_entry = {
                            "id": f"hwp-{doc_id}" if doc_id is not None else f"hwp-{i}",
                            "name": name,
                            "path": path,
                            "type": "hwp",
                            "index": i,
                        }
                        if doc_id is not None:
                            doc_entry["documentId"] = doc_id

                        documents.append(doc_entry)
            except Exception as e:
                print(f"[Python] XHwpDocuments 접근 실패: {e}", file=sys.stderr)

        except Exception as e:
            print(f"[Python] HWP ROT 접근 실패: {e}", file=sys.stderr)

        return documents

    def _get_word_documents(self) -> list:
        """win32com으로 열린 Word 문서 목록 조회"""
        documents = []

        try:
            import win32com.client

            word = win32com.client.GetActiveObject("Word.Application")
            for i, doc in enumerate(word.Documents):
                name = doc.Name
                path = doc.FullName if doc.Path else ""

                documents.append({
                    "id": f"word-{i}",
                    "name": name,
                    "path": path,
                    "type": "word",
                    "index": i,
                })

        except Exception:
            # Word가 실행 중이 아님
            pass

        return documents

    def _get_excel_documents(self) -> list:
        """win32com으로 열린 Excel 문서 목록 조회"""
        documents = []

        try:
            import win32com.client

            excel = win32com.client.GetActiveObject("Excel.Application")
            for i, wb in enumerate(excel.Workbooks):
                name = wb.Name
                path = wb.FullName if wb.Path else ""

                documents.append({
                    "id": f"excel-{i}",
                    "name": name,
                    "path": path,
                    "type": "excel",
                    "index": i,
                })

        except Exception:
            # Excel이 실행 중이 아님
            pass

        return documents

    def select_document(self, doc_type: str, index: int) -> dict:
        """특정 문서 선택 (편집 대상으로 지정)"""
        try:
            if doc_type == "hwp":
                return self._select_hwp_document(index)
            elif doc_type == "word":
                return self._select_word_document(index)
            elif doc_type == "excel":
                return self._select_excel_document(index)
            else:
                return {"success": False, "error": f"Unknown document type: {doc_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _bring_window_to_front(self, keywords: list) -> bool:
        """특정 키워드가 포함된 창을 앞으로 가져오기"""
        import win32gui
        import win32con
        import win32process
        import win32api
        import ctypes

        def find_window(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                class_name = win32gui.GetClassName(hwnd)
                for kw in keywords:
                    if kw.lower() in title.lower() or kw.lower() in class_name.lower():
                        windows.append((hwnd, title))
                        break
            return True

        windows = []
        win32gui.EnumWindows(find_window, windows)

        if not windows:
            return False

        hwnd = windows[0][0]
        try:
            # 최소화된 경우 복원
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # AttachThreadInput 트릭으로 SetForegroundWindow 제한 우회
            current_thread = win32api.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

            if current_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)

            # 창을 앞으로 가져오기
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

            if current_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)

            return True
        except Exception as e:
            print(f"[Python] 창 활성화 실패: {e}", file=sys.stderr)
            # 최후의 수단: Alt 키 시뮬레이션
            try:
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
                win32gui.SetForegroundWindow(hwnd)
                return True
            except:
                pass
        return False

    def _select_hwp_document(self, index: int) -> dict:
        """HWP 문서 선택 및 창 활성화 (pyhwpx 사용)"""
        try:
            import pythoncom
            pythoncom.CoInitialize()

            from pyhwpx import Hwp

            # 기존 한글 인스턴스에 연결
            hwp = Hwp(new=False, visible=True)

            # 해당 문서 활성화
            xdocs = hwp.XHwpDocuments
            if index >= xdocs.Count:
                return {"success": False, "error": f"문서 인덱스 {index}가 범위를 벗어남"}

            doc = xdocs.Item(index)
            if doc:
                # SetActive_XHwpDocument로 문서 활성화
                doc.SetActive_XHwpDocument()
                self._current_file = doc.FullName if hasattr(doc, 'FullName') else None

            # 한글 창을 앞으로 가져오기
            self._bring_window_to_front(['Hwp', '한글'])

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            print(f"[Python] HWP 문서 선택 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def _select_word_document(self, index: int) -> dict:
        """Word 문서 선택 및 창 활성화"""
        import win32com.client

        try:
            word = win32com.client.GetActiveObject("Word.Application")

            # 1-based index
            doc = word.Documents.Item(index + 1)
            if doc:
                doc.Activate()
                self._current_file = doc.FullName

            # Word 창을 앞으로
            word.Visible = True
            word.Activate()
            self._bring_window_to_front(['Word', 'WINWORD'])

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _select_excel_document(self, index: int) -> dict:
        """Excel 문서 선택 및 창 활성화"""
        import win32com.client

        try:
            excel = win32com.client.GetActiveObject("Excel.Application")

            # 1-based index
            wb = excel.Workbooks.Item(index + 1)
            if wb:
                wb.Activate()
                self._current_file = wb.FullName

            # Excel 창을 앞으로
            excel.Visible = True
            self._bring_window_to_front(['Excel', 'XLMAIN'])

            return {"success": True, "index": index, "path": self._current_file}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_document(self, file_path: str) -> dict:
        """문서 열기"""
        try:
            adapter = self._ensure_adapter()

            if not adapter.open_file(file_path):
                return {"success": False, "error": "파일 열기 실패"}

            self._current_file = file_path
            return {"success": True, "file": file_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extract_html(self, format: str = "api") -> dict:
        """현재 문서에서 HTML 추출

        Args:
            format: 추출 방식
                - "api" (기본값): HWP API 직접 사용 (DocumentViewGenerator)
                - "hwpml": HWPML → HTML 직접 변환 (DocumentExtractor)

        Returns:
            dict: {"success": bool, "html": str, "element_count": int}
        """
        try:
            if format == "hwpml":
                # HWPML → HTML 직접 변환 방식 (Phase 2)
                return self._extract_html_from_hwpml()
            else:
                # HWP API 직접 사용 방식 (기존)
                return self._extract_html_from_api()

        except Exception as e:
            return {"success": False, "error": str(e), "trace": traceback.format_exc()}

    def _extract_html_from_api(self) -> dict:
        """HWP API를 사용한 HTML 추출 (기존 방식)"""
        connector = self._ensure_connector()

        # DocumentViewGenerator 초기화
        if self._view_generator is None:
            self._view_generator = DocumentViewGenerator(connector)

        # DocumentView 생성
        doc_view = self._view_generator.generate_view()
        if not doc_view:
            return {"success": False, "error": "DocumentView 생성 실패"}

        # HTML 형식 마크다운 추출
        html_content = doc_view.html

        return {
            "success": True,
            "html": html_content,
            "element_count": len(doc_view.elements) if doc_view.elements else 0,
            "format": "api"
        }

    def _extract_html_from_hwpml(self) -> dict:
        """HWPML → HTML 직접 변환 (Phase 2 방식)"""
        import tempfile
        import uuid

        try:
            # HWP 연결
            from pyhwpx import Hwp
            hwp = Hwp(new=False, visible=True)

            # HWPML 추출
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"hwpml_{uuid.uuid4()}.xml")

            # HWPML 형식으로 저장
            hwp.save_as(temp_path, format="HWPML2X")

            # DocumentExtractor로 변환
            extractor = DocumentExtractor()
            result = extractor.extract_from_hwpml_file(Path(temp_path))

            # 임시 파일 삭제
            try:
                os.remove(temp_path)
            except:
                pass

            if not result:
                return {"success": False, "error": "HWPML 변환 실패"}

            return {
                "success": True,
                "html": result.html_content,
                "element_count": result.element_count,
                "outline_mapping": result.outline_mapping,
                "format": "hwpml"
            }

        except Exception as e:
            print(f"[Python] HWPML 변환 실패: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"success": False, "error": str(e), "trace": traceback.format_exc()}

    def apply_commands(self, commands: list, mappings_data: dict = None) -> dict:
        """편집 명령 적용 (스트리밍 방식으로 대체됨)"""
        # NOTE: 실제 편집은 streaming_client의 on_command 콜백에서 실시간으로 수행됨
        # 이 메서드는 레거시 호환성을 위해 유지
        return {
            "success": True,
            "message": "스트리밍 방식으로 실시간 편집이 수행됩니다"
        }

    def save_document(self) -> dict:
        """문서 저장"""
        try:
            connector = self._ensure_connector()
            connector.save()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def close_document(self) -> dict:
        """문서 닫기"""
        try:
            if self._connector:
                self._connector.disconnect()
                self._connector = None
                self._current_file = None
                self._view_generator = None
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_current_page_info(self) -> dict:
        """현재 커서 위치의 페이지 정보 조회 (창 활성화 없이 ROT 방식)"""
        try:
            preferred_pid = session_state.retrieve_runtime_target_process_id() or self._process_identifier
            preferred_hwnd = session_state.retrieve_runtime_target_window_handle() or self._window_handle
            hwp = self._get_hwp_from_rot(preferred_pid, preferred_hwnd)

            if hwp is None:
                return {
                    "success": False,
                    "error": "열린 HWP 문서가 없습니다."
                }

            # 페이지 정보 조회
            total_pages = hwp.PageCount
            # current_page는 0-based로 반환되므로 +1
            current_page = hwp.XHwpDocuments.Active_XHwpDocument.XHwpDocumentInfo.CurrentPage + 1

            # 읽기 범위 계산 (현재 페이지부터 5페이지)
            MAX_PAGES = 5
            start_page = current_page
            end_page = min(current_page + MAX_PAGES - 1, total_pages)

            active_document = None
            try:
                xdocs = hwp.XHwpDocuments
                active_doc = xdocs.Active_XHwpDocument
                active_path = ""
                active_doc_id = None
                if active_doc:
                    try:
                        active_doc_id = active_doc.DocumentID
                    except Exception:
                        active_doc_id = None
                    try:
                        active_path = active_doc.FullName if hasattr(active_doc, "FullName") else ""
                    except Exception:
                        active_path = ""

                active_index = None
                if active_doc_id is not None or active_path:
                    for i in range(xdocs.Count):
                        doc = xdocs.Item(i)
                        if active_doc_id is not None:
                            try:
                                if doc.DocumentID == active_doc_id:
                                    active_index = i
                                    break
                            except Exception:
                                pass
                        if active_path:
                            try:
                                if doc.FullName == active_path:
                                    active_index = i
                                    break
                            except Exception:
                                pass

                if active_index is not None:
                    if active_path:
                        active_name = os.path.basename(active_path)
                    else:
                        active_name = f"새 문서 {active_index + 1}"
                    doc_uid = f"hwp-{active_doc_id}" if active_doc_id is not None else f"hwp-{active_index}"
                    active_document = {
                        "id": doc_uid,
                        "name": active_name,
                        "path": active_path or "",
                        "type": "hwp",
                        "index": active_index,
                    }
                    if active_doc_id is not None:
                        try:
                            active_document["documentId"] = int(active_doc_id)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[Python] 활성 문서 정보 수집 실패: {e}", file=sys.stderr)

            return {
                "success": True,
                "currentPage": current_page,
                "totalPages": total_pages,
                "startPage": start_page,
                "endPage": end_page,
                "activeDocument": active_document
            }

        except Exception as e:
            print(f"[Python] 페이지 정보 조회 실패: {e}", file=sys.stderr)
            return {
                "success": False,
                "error": str(e)
            }

    def _send_progress(self, event_type: str, data: dict):
        """진행 상황 이벤트를 stdout으로 전송 (Electron에서 수신)"""
        progress_event = {
            "type": "progress",
            "event": event_type,
            "data": data
        }
        print(json.dumps(progress_event, ensure_ascii=False))
        sys.stdout.flush()


    def _map_block_pages(self, hwp, doc_hdml):
        """
        HDML 블록의 페이지 번호를 매핑

        - 표 셀 블록: 표의 앵커 페이지 기준
        - 일반 문단 블록: 문단 순회로 페이지 매핑
        - 커서 위치: 함수 시작 시 저장, 종료 시 복원
        """
        # P1: 현재 커서 위치 저장
        try:
            original_pos = hwp.get_pos_by_set()
            print(f"[Python] 커서 위치 저장: {original_pos}", file=sys.stderr)
        except Exception as e:
            original_pos = None
            print(f"[Python] 커서 위치 저장 실패: {e}", file=sys.stderr)

        # 표별 페이지 매핑 (표 시작 위치 기준)
        table_pages = {}

        try:
            # HeadCtrl로 모든 표 컨트롤 순회
            ctrl = hwp.HeadCtrl
            table_idx = 0
            while ctrl:
                try:
                    if hasattr(ctrl, 'UserDesc') and ctrl.UserDesc == "표":
                        table_idx += 1
                        # 표의 앵커 위치로 이동
                        anchor_pos = ctrl.GetAnchorPos(0)
                        hwp.set_pos_by_set(anchor_pos)
                        page = hwp.current_page
                        table_pages[table_idx] = page
                        print(f"[Python] 표 {table_idx} -> 페이지 {page}", file=sys.stderr)
                    ctrl = ctrl.Next
                except Exception as e:
                    print(f"[Python] 표 페이지 매핑 오류: {e}", file=sys.stderr)
                    if hasattr(ctrl, 'Next'):
                        ctrl = ctrl.Next
                    else:
                        break

            # 각 블록에 페이지 번호 설정
            for block in doc_hdml.blocks.values():
                cell_id = block.owner.get("cell_id")
                if cell_id is not None:
                    # 표 셀 블록 - 소속 표의 페이지
                    cell = doc_hdml.get_cell(cell_id)
                    if cell:
                        block.page = table_pages.get(cell.table_id, 1)
                else:
                    # 일반 문단 블록 - 페이지 1로 기본 설정 (문단 순회 필요시 구현)
                    block.page = 1

            print(f"[Python] {len(doc_hdml.blocks)}개 블록 페이지 매핑 완료", file=sys.stderr)

        finally:
            # P1: 원래 커서 위치 복원
            if original_pos is not None:
                try:
                    hwp.set_pos_by_set(original_pos)
                    print(f"[Python] 커서 위치 복원 완료", file=sys.stderr)
                except Exception as e:
                    print(f"[Python] 커서 위치 복원 실패: {e}", file=sys.stderr)

    def _filter_blocks_by_page(
        self,
        doc_hdml,
        start_page: int,
        end_page: int
    ) -> tuple:
        """
        지정된 페이지 범위 내의 블록만 필터링하여 JSONL 생성

        Returns:
            (filtered_jsonl, allowed_block_ids): 필터링된 JSONL과 허용된 블록 ID 집합
        """
        allowed_block_ids = set()
        filtered_blocks = []

        # 각 블록 순회하면서 페이지 범위 체크
        for block_id, block in doc_hdml.blocks.items():
            if hasattr(block, 'page') and start_page <= block.page <= end_page:
                allowed_block_ids.add(block_id)
                filtered_blocks.append(block)

        # 필터링된 블록으로 압축 JSONL 생성
        # 압축 포맷: block_id → id 필드로 축약 ("cell-80-para-0" → "80-0")

        # 압축 JSONL 생성
        full_jsonl = self._hdml_parser.to_compact_jsonl(doc_hdml)

        # 필터링된 블록 ID만 포함하도록 JSONL 필터링
        import json
        jsonl_lines = full_jsonl.strip().split('\n')
        filtered_lines = []

        # allowed_block_ids를 압축 형식으로 변환
        allowed_compact_ids = set()
        for block_id in allowed_block_ids:
            # "cell-80-para-0" → "80-0"
            compact_id = block_id.replace("cell-", "").replace("-para-", "-")
            allowed_compact_ids.add(compact_id)

        for line in jsonl_lines:
            try:
                obj = json.loads(line)
                obj_type = obj.get("T")  # 압축 포맷: type → T

                if obj_type in ("tbl", "ref"):
                    # 표, 중첩 참조는 모두 포함
                    filtered_lines.append(line)
                elif "id" in obj:
                    # 블록은 "id" 필드 사용 (압축 포맷)
                    compact_id = obj.get("id")
                    if compact_id in allowed_compact_ids:
                        filtered_lines.append(line)
                else:
                    # 기타는 포함
                    filtered_lines.append(line)
            except:
                # 파싱 실패한 줄은 포함
                filtered_lines.append(line)

        filtered_jsonl = '\n'.join(filtered_lines)

        return filtered_jsonl, allowed_block_ids


    def chat(
        self,
        prompt: str,
        doc_type: str = None,
        doc_index: int = None,
        reference_content: str = None,
        reference_file_name: str = None,
        start_page: int = None,
        end_page: int = None,
        openai_api_key: str = None,
    ) -> dict:
        """
        채팅 기반 문서 편집 (스트리밍 + 실시간 편집)

        Args:
            prompt: 사용자 편집 요청
            doc_type: 문서 타입 (hwp, word, excel) - None이면 현재 활성 문서
            doc_index: 문서 인덱스 - None이면 현재 활성 문서
            reference_content: 참조 자료 내용 (txt/excel 파일 - 서버 분리 대비)
            reference_file_name: 참조 자료 파일명
            start_page: 시작 페이지 (None이면 현재 커서 위치)
            end_page: 끝 페이지 (None이면 start_page + 4)
            openai_api_key: OpenAI API 키 (필수)

        Returns:
            {
                "success": bool,
                "message": str,  # AI 응답 메시지
                "edits": int,    # 편집 횟수
                "error": str     # 에러 시
            }
        """
        try:
            import pythoncom
            pythoncom.CoInitialize()

            # 진행 상황 알림: 시작
            self._send_progress("start", {"stage": "initializing"})
            if not openai_api_key:
                return {"success": False, "error": "OPENAI_API_KEY_REQUIRED"}

            # 1. 문서 타입 확인 (현재는 HWP만 지원)
            if doc_type and doc_type != "hwp":
                return {
                    "success": False,
                    "error": f"현재 HWP 문서만 지원합니다. (요청: {doc_type})"
                }

            # 2. HWP 인스턴스 연결
            from pyhwpx import Hwp
            hwp = Hwp(new=False, visible=True)

            # 3. 활성 문서 확인
            xdocs = hwp.XHwpDocuments
            if xdocs.Count == 0:
                return {
                    "success": False,
                    "error": "열린 HWP 문서가 없습니다."
                }

            # 특정 문서 선택 (인덱스 지정된 경우)
            if doc_index is not None:
                if doc_index >= xdocs.Count:
                    return {
                        "success": False,
                        "error": f"문서 인덱스 {doc_index}가 범위를 벗어남 (총 {xdocs.Count}개)"
                    }
                doc = xdocs.Item(doc_index)
                doc.SetActive_XHwpDocument()

            # 3.5. 페이지 범위 결정
            current_page = hwp.current_page
            total_pages = hwp.PageCount
            MAX_PAGES = 5

            # 사용자 지정 페이지 범위가 있으면 사용, 없으면 현재 커서 위치 기준
            if start_page is not None:
                # 사용자 지정 시작 페이지
                actual_start = max(1, min(start_page, total_pages))
                if end_page is not None:
                    # 사용자 지정 끝 페이지
                    actual_end = max(actual_start, min(end_page, total_pages))
                else:
                    # 끝 페이지 미지정 시 5페이지 제한
                    actual_end = min(actual_start + MAX_PAGES - 1, total_pages)
            else:
                # 기본값: 현재 커서 위치부터 5페이지
                actual_start = current_page
                actual_end = min(current_page + MAX_PAGES - 1, total_pages)

            print(f"[Python] 페이지 범위: {actual_start}~{actual_end} (전체 {total_pages}페이지, 현재 커서 {current_page}페이지)", file=sys.stderr)
            self._send_progress("stage", {"stage": "page_info", "message": f"페이지 {actual_start}~{actual_end} 읽는 중..."})

            # 4. HWPML 추출
            self._send_progress("stage", {"stage": "reading", "message": "문서 읽는 중..."})
            print(f"[Python] HWPML 추출 중...", file=sys.stderr)
            hwpml = self._extract_hwpml_from_active(hwp)
            if not hwpml:
                return {
                    "success": False,
                    "error": "문서 내용을 읽을 수 없습니다."
                }

            # 5. HDML로 파싱 (디폴트)
            self._send_progress("stage", {"stage": "parsing", "message": "문서 분석 중..."})
            print(f"[Python] HDML 파싱 중...", file=sys.stderr)

            # HDML 파서로 파싱
            from pathlib import Path
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.hwpml', delete=False, encoding='utf-8') as f:
                f.write(hwpml)
                temp_path = Path(f.name)

            try:
                self._doc_hdml = self._hdml_parser.parse_hwpml(temp_path)
                print(f"[Python] HDML 파싱 완료: {len(self._doc_hdml.blocks)}개 블록, {len(self._doc_hdml.cells)}개 셀", file=sys.stderr)
            finally:
                temp_path.unlink()

            # 5.5. 각 블록의 페이지 번호 매핑
            self._send_progress("stage", {"stage": "mapping_pages", "message": "페이지 매핑 중..."})
            self._map_block_pages(hwp, self._doc_hdml)

            # 5.6. 페이지 범위 내 블록만 필터링하여 JSONL 생성
            filtered_jsonl, allowed_block_ids = self._filter_blocks_by_page(
                self._doc_hdml, actual_start, actual_end
            )

            print(f"[Python] 원본 블록 수: {len(self._doc_hdml.blocks)}, 필터링 후: {len(allowed_block_ids)}개 블록 허용", file=sys.stderr)
            print(f"[Python] 필터링 JSONL 길이: {len(filtered_jsonl)}", file=sys.stderr)

            # 편집 허용 블록 ID 저장 (편집 시 범위 체크용)
            self._allowed_block_ids = allowed_block_ids

            # JSONL 병합 (필터링된 버전 사용)
            merged_jsonl = filtered_jsonl
            print(f"[Python] JSONL 길이: {len(merged_jsonl)}, 블록 수: {len(self._doc_hdml.blocks)}", file=sys.stderr)

            # HDML 어댑터 초기화 (hwp 객체 업데이트 포함)
            if self._hdml_adapter is None:
                self._hdml_adapter = HDMLAdapter(hwp)
            else:
                # hwp 객체가 변경될 수 있으므로 업데이트 (테이블 캐시 무효화)
                self._hdml_adapter.hwp = hwp
                self._hdml_adapter._invalidate_table_cache()
            self._hdml_adapter.set_doc_hdml(self._doc_hdml)
            self._hdml_adapter.set_parser(self._hdml_parser)

            # 디버그: HWPML, JSONL 저장
            from utils.logger import log_llm_interaction, log_token_usage, LOG_DIR
            from datetime import datetime
            debug_ts = datetime.now().strftime('%Y%m%d_%H%M%S')

            # HWPML 원본 저장
            hwpml_file = LOG_DIR / f"debug_hwpml_{debug_ts}.xml"
            with open(hwpml_file, 'w', encoding='utf-8') as f:
                f.write(hwpml)
            print(f"[Python] HWPML 저장: {hwpml_file}", file=sys.stderr)

            # JSONL 저장
            jsonl_file = LOG_DIR / f"debug_jsonl_{debug_ts}.jsonl"
            with open(jsonl_file, 'w', encoding='utf-8') as f:
                f.write(merged_jsonl)
            print(f"[Python] JSONL 저장: {jsonl_file}", file=sys.stderr)

            log_llm_interaction(
                prompt=prompt,
                html=merged_jsonl,
                response_raw={"stage": "before_llm"},
                commands=[],
                success=True
            )

            # 6. 참조 자료가 있으면 프롬프트에 포함 (서버 분리 대비)
            final_prompt = prompt
            if reference_content:
                reference_section = f"""
[참조 자료: {reference_file_name or '첨부 파일'}]
---
{reference_content}
---

위 참조 자료를 바탕으로 아래 요청을 수행해주세요:
"""
                final_prompt = reference_section + prompt
                print(f"[Python] 참조 자료 포함: {reference_file_name} ({len(reference_content)} chars)", file=sys.stderr)

            # 7. LLM 스트리밍 호출
            self._send_progress("stage", {"stage": "thinking", "message": "AI가 분석 중..."})
            print(f"[Python] LLM 스트리밍 호출 중...", file=sys.stderr)

            # PyhwpxAdapter 사용 (실시간 편집용) - self._adapter와 동일 인스턴스 사용
            # ✅ 핵심: 새 adapter 만들지 않고 통일된 인스턴스 사용
            adapter = self._ensure_adapter()

            # anchor 정보 설정 (표 진입 안정성 향상)
            if self._table_anchors:
                adapter.set_table_anchors(self._table_anchors)

            # Diff 모드 상태 재적용 (COM 컨텍스트 일치 보장)
            if self._diff_mode_enabled:
                adapter.start_track_changes()
            else:
                adapter.stop_track_changes()

            # HDML 어댑터에도 Diff 모드 동기화 (P0: 누락 수정)
            if self._hdml_adapter is not None:
                self._hdml_adapter.set_diff_mode(self._diff_mode_enabled)

            # 스트리밍 클라이언트
            streaming_client = get_streaming_client(openai_api_key)

            # 편집 카운터 및 메시지 수집
            edits_count = 0
            messages_collected = []

            def on_command(cmd: StreamingCommand):
                """명령이 파싱될 때마다 호출되는 콜백 (HDML 기반)"""
                nonlocal edits_count

                if cmd.action == "message":
                    # AI 메시지
                    messages_collected.append(cmd.message)
                    self._send_progress("message", {"text": cmd.message})

                elif cmd.action == "edit_document":
                    # HDML 편집 - 즉시 실행
                    # cmd.id는 int(셀 ID) 또는 str(블록 ID, 예: "cell-80-para-0")

                    if isinstance(cmd.id, int):
                        # Cell ID → edit_cell() 직접 사용
                        cell = self._doc_hdml.get_cell(cmd.id)
                        if cell is None:
                            print(f"[Python] 알 수 없는 셀 ID: {cmd.id}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": cmd.id,
                                "reason": "알 수 없는 셀 ID"
                            })
                            return

                        # 페이지 범위 체크 (셀의 첫 번째 블록 기준)
                        if cell.blocks and self._allowed_block_ids:
                            first_block_id = cell.blocks[0]
                            if first_block_id not in self._allowed_block_ids:
                                block = self._doc_hdml.get_block(first_block_id)
                                page = getattr(block, 'page', 'unknown') if block else 'unknown'
                                print(f"[Python] 페이지 범위 외 셀 무시: {cmd.id} (page: {page})", file=sys.stderr)
                                self._send_progress("edit_skipped", {
                                    "id": cmd.id,
                                    "reason": "페이지 범위 외",
                                    "page": page
                                })
                                return

                        # HDML adapter로 셀 편집
                        success = self._hdml_adapter.edit_cell(cmd.id, cmd.content)
                        edit_mode = "track" if self._diff_mode_enabled else "normal"

                        if success:
                            edits_count += 1
                            self._send_progress("edit", {
                                "type": "cell",
                                "id": cmd.id,
                                "mode": edit_mode,
                                "content": cmd.content[:50] + "..." if len(cmd.content) > 50 else cmd.content
                            })
                    else:
                        # Block ID (str) → edit_block() 사용
                        block_id = str(cmd.id)

                        # 블록 조회
                        block = self._doc_hdml.get_block(block_id)
                        if block is None:
                            print(f"[Python] 알 수 없는 블록 ID: {block_id}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": block_id,
                                "reason": "알 수 없는 블록 ID"
                            })
                            return

                        # 페이지 범위 체크 - 허용된 블록 ID만 편집
                        if self._allowed_block_ids and block_id not in self._allowed_block_ids:
                            page = getattr(block, 'page', 'unknown')
                            print(f"[Python] 페이지 범위 외 블록 무시: {block_id} (page: {page})", file=sys.stderr)
                            self._send_progress("edit_skipped", {
                                "id": block_id,
                                "reason": "페이지 범위 외",
                                "page": page
                            })
                            return

                        # HDML adapter로 블록 편집
                        success = self._hdml_adapter.edit_block(block_id, cmd.content)
                        edit_mode = "track" if self._diff_mode_enabled else "normal"

                        if success:
                            edits_count += 1
                            self._send_progress("edit", {
                                "type": "block",
                                "id": block_id,
                                "mode": edit_mode,
                                "content": cmd.content[:50] + "..." if len(cmd.content) > 50 else cmd.content
                            })

                elif cmd.action == "append_table_row":
                    # 행 추가 - 즉시 실행 (HDML 기반)
                    # cmd.id는 셀 ID (int) 또는 블록 ID (str)

                    if isinstance(cmd.id, int):
                        # Cell ID → 직접 셀 조회
                        cell_id = cmd.id
                        cell = self._doc_hdml.get_cell(cell_id)
                        if cell is None:
                            print(f"[Python] 행 추가 실패: 알 수 없는 셀 ID {cell_id}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": cell_id,
                                "reason": "알 수 없는 셀 ID"
                            })
                            return

                        # 페이지 범위 체크 (셀의 첫 번째 블록 기준)
                        block_id_for_check = cell.blocks[0] if cell.blocks else None
                        if block_id_for_check and self._allowed_block_ids:
                            if block_id_for_check not in self._allowed_block_ids:
                                block = self._doc_hdml.get_block(block_id_for_check)
                                page = getattr(block, 'page', 'unknown') if block else 'unknown'
                                print(f"[Python] 페이지 범위 외 셀 무시 (행추가): {cell_id}", file=sys.stderr)
                                self._send_progress("edit_skipped", {
                                    "id": cell_id,
                                    "reason": "페이지 범위 외",
                                    "page": page
                                })
                                return
                    else:
                        # Block ID (str) → 블록에서 셀 정보 추출
                        block_id = str(cmd.id)
                        block = self._doc_hdml.get_block(block_id)
                        if block is None:
                            print(f"[Python] 행 추가 실패: 잘못된 블록 ID {block_id}", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": block_id,
                                "reason": "잘못된 블록 ID"
                            })
                            return

                        # 소속 셀 정보 추출
                        cell_id = block.owner.get("cell_id")
                        if cell_id is None:
                            print(f"[Python] 행 추가 실패: 블록 {block_id}의 소속 셀 없음", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": block_id,
                                "reason": "소속 셀 없음"
                            })
                            return

                        cell = self._doc_hdml.get_cell(cell_id)
                        if cell is None:
                            print(f"[Python] 행 추가 실패: 셀 ID {cell_id} 찾을 수 없음", file=sys.stderr)
                            self._send_progress("edit_failed", {
                                "id": block_id,
                                "reason": "셀 찾을 수 없음"
                            })
                            return

                        # 페이지 범위 체크 - 허용된 블록 ID만 편집
                        if self._allowed_block_ids and block_id not in self._allowed_block_ids:
                            page = getattr(block, 'page', 'unknown')
                            print(f"[Python] 페이지 범위 외 블록 무시 (행추가): {block_id}", file=sys.stderr)
                            self._send_progress("edit_skipped", {
                                "id": block_id,
                                "reason": "페이지 범위 외",
                                "page": page
                            })
                            return

                    # 행 추가 (기존 adapter 사용 - append_table_row는 HDML adapter에 없음)
                    current_row = cell.r0
                    for row_str in cmd.rows:
                        row_cells = row_str.split("|")

                        if adapter.append_table_row(cell.table_id, current_row, row_cells, cell.c0):
                            edits_count += 1
                            current_row += 1
                            self._send_progress("edit", {
                                "type": "append_row",
                                "id": cell_id,
                                "table": cell.table_id,
                                "row": current_row,
                                "cells": len(row_cells)
                            })

            # 스트리밍 실행 (압축 HDML JSONL 전달)
            result = streaming_client.generate_commands_streaming(
                html=merged_jsonl,
                prompt=final_prompt,
                on_command=on_command,
                compact_mode=True  # 압축 포맷 사용 (토큰 ~60% 절감)
            )

            # 토큰 사용량 로깅 (터미널 + 파일)
            log_token_usage(
                token_usage=result.token_usage,
                prompt=prompt,
                context_info=f"JSONL: {len(merged_jsonl):,} chars, edits: {edits_count}"
            )

            print(f"[Python] 스트리밍 완료: {edits_count}개 편집", file=sys.stderr)

            # 편집 이력 기록 (TASK-004)
            if edits_count > 0:
                import uuid
                chat_id = str(uuid.uuid4())[:8]  # 짧은 고유 ID
                self._record_edit(chat_id, edits_count)
                print(f"[Python] 편집 이력 기록: chatId={chat_id}, count={edits_count}", file=sys.stderr)

            # 완료 알림
            self._send_progress("complete", {
                "edits": edits_count,
                "messages": messages_collected,
                "editHistory": self.get_edit_history()
            })

            # 최종 메시지 구성
            if messages_collected:
                final_message = "\n".join(messages_collected)
            elif edits_count > 0:
                final_message = f"문서를 수정했습니다. ({edits_count}개 항목 편집)"
            else:
                final_message = "수정할 내용이 없습니다."

            # 세션 토큰 통계 가져오기
            from utils.logger import get_session_token_stats
            session_stats = get_session_token_stats()

            return {
                "success": True,
                "message": final_message,
                "edits": edits_count,
                "token_usage": result.token_usage,
                "session_tokens": session_stats  # 세션 누적 토큰 정보
            }

        except Exception as e:
            print(f"[Python] chat 오류: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self._send_progress("error", {"error": str(e)})
            return {
                "success": False,
                "error": str(e),
                "trace": traceback.format_exc()
            }

    def _extract_hwpml_from_active(self, hwp) -> str:
        """활성 HWP 문서에서 HWPML 추출"""
        import tempfile
        import uuid

        try:
            # 임시 파일로 HWPML 저장 후 읽기
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"hwpml_{uuid.uuid4()}.xml")

            # HWPML 형식으로 저장
            hwp.save_as(temp_path, format="HWPML2X")

            # 파일 읽기
            with open(temp_path, 'r', encoding='utf-8') as f:
                hwpml = f.read()

            # 임시 파일 삭제
            try:
                os.remove(temp_path)
            except:
                pass

            return hwpml

        except Exception as e:
            print(f"[Python] HWPML 추출 실패: {e}", file=sys.stderr)
            return ""

    # ============================================================
    # Undo/Redo 및 Diff 모드 API (TASK-004)
    # ============================================================

    def undo(self, count: int = 1) -> dict:
        """
        Undo 실행

        Args:
            count: 실행할 Undo 횟수 (기본 1)

        Returns:
            {"success": bool, "undone": int, "remaining": int}
        """
        try:
            adapter = self._ensure_adapter()
            undone = adapter.undo(count)

            # 편집 이력 업데이트
            if undone > 0 and self._edit_history:
                # 가장 최근 편집의 카운트 감소
                for i in range(len(self._edit_history) - 1, -1, -1):
                    if self._edit_history[i]["editCount"] > 0:
                        self._edit_history[i]["editCount"] -= undone
                        if self._edit_history[i]["editCount"] <= 0:
                            self._edit_history.pop(i)
                        break

            total_remaining = sum(h["editCount"] for h in self._edit_history)

            return {
                "success": True,
                "undone": undone,
                "remaining": total_remaining
            }
        except Exception as e:
            print(f"[Python] Undo 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def redo(self, count: int = 1) -> dict:
        """
        Redo 실행

        Args:
            count: 실행할 Redo 횟수 (기본 1)

        Returns:
            {"success": bool, "redone": int}
        """
        try:
            adapter = self._ensure_adapter()
            redone = adapter.redo(count)
            return {"success": True, "redone": redone}
        except Exception as e:
            print(f"[Python] Redo 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def set_diff_mode(self, enabled: bool) -> dict:
        """
        Diff 모드 설정 - Track Changes 기능 연동

        Args:
            enabled: True면 Diff 모드 활성화 (Track Changes ON)

        Returns:
            {"success": bool, "diffMode": bool}
        """
        try:
            connector = self._ensure_connector()
            if enabled:
                # Track Changes 켜기
                connector.start_track_changes()
            else:
                # Track Changes 끄기
                connector.stop_track_changes()

            # HDML 어댑터에도 Diff 모드 설정 (P0: 누락 수정)
            if self._hdml_adapter is not None:
                self._hdml_adapter.set_diff_mode(enabled)
                print(f"[Python] HDML 어댑터 Diff 모드: {'ON' if enabled else 'OFF'}", file=sys.stderr)

            self._diff_mode_enabled = enabled
            print(f"[Python] Diff 모드: {'ON' if enabled else 'OFF'} (Track Changes)", file=sys.stderr)
            return {"success": True, "diffMode": self._diff_mode_enabled}
        except Exception as e:
            print(f"[Python] Diff 모드 설정 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def get_diff_mode(self) -> dict:
        """
        현재 Diff 모드 상태 조회

        Returns:
            {"success": bool, "diffMode": bool}
        """
        return {"success": True, "diffMode": self._diff_mode_enabled}

    def apply_changes(self) -> dict:
        """
        Diff 변경사항 승인 - 스냅샷 방식 (현재 상태 유지)

        Track Changes API가 동작하지 않으므로 스냅샷 방식 사용:
        - 현재 편집된 상태를 그대로 유지 (이미 문서에 반영됨)
        - Diff 이력만 정리

        Returns:
            {"success": bool}
        """
        try:
            adapter = self._ensure_adapter()
            # 스냅샷 방식: 현재 상태를 최종 상태로 확정
            success = adapter.apply_diff_changes()
            if success:
                # Track Changes 끄기 (켜져있다면)
                adapter.stop_track_changes()
                self._diff_mode_enabled = False
            print(f"[Python] 변경사항 승인: {'성공' if success else '실패'} (스냅샷 방식)", file=sys.stderr)
            return {"success": success}
        except Exception as e:
            print(f"[Python] 승인 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def reject_changes(self) -> dict:
        """
        Diff 변경사항 거절 - Track Changes + 스냅샷 방식 병행

        1) Track Changes 기반 거절 먼저 시도 (HWP 내장 기능)
        2) 실패 시 스냅샷 복원 fallback

        Returns:
            {"success": bool}
        """
        try:
            adapter = self._ensure_adapter()
            success = False

            # 1) Track Changes 기반 거절 먼저 시도
            track_reject = adapter.reject_all_changes()
            print(f"[Python] Track Changes 거절 시도: {track_reject}", file=sys.stderr)

            # 2) 스냅샷 복원 fallback (Track Changes가 없거나 실패한 경우)
            snapshot_restore = adapter.restore_checkpoint()
            print(f"[Python] 스냅샷 복원 시도: {snapshot_restore}", file=sys.stderr)

            # 둘 중 하나라도 성공하면 OK
            success = track_reject or snapshot_restore

            # Track Changes 끄기
            adapter.stop_track_changes()
            self._diff_mode_enabled = False

            print(f"[Python] 변경사항 거절 최종: {'성공' if success else '실패'}", file=sys.stderr)
            return {"success": success}
        except Exception as e:
            print(f"[Python] 거절 실패: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def get_edit_history(self) -> dict:
        """
        편집 이력 조회

        Returns:
            {"success": bool, "history": list, "totalEdits": int}
        """
        total_edits = sum(h["editCount"] for h in self._edit_history)
        return {
            "success": True,
            "history": self._edit_history,
            "totalEdits": total_edits
        }

    def clear_edit_history(self) -> dict:
        """
        편집 이력 초기화

        Returns:
            {"success": bool}
        """
        self._edit_history = []
        self._current_chat_id = None
        print("[Python] 편집 이력 초기화됨", file=sys.stderr)
        return {"success": True}

    def _record_edit(self, chat_id: str, edit_count: int = 1):
        """
        편집 이력 기록 (내부용)

        Args:
            chat_id: 채팅 세션 ID
            edit_count: 편집 횟수
        """
        from datetime import datetime

        # 같은 채팅 세션이면 카운트 증가
        if self._edit_history and self._edit_history[-1]["chatId"] == chat_id:
            self._edit_history[-1]["editCount"] += edit_count
        else:
            # 새 세션 기록
            self._edit_history.append({
                "id": len(self._edit_history) + 1,
                "chatId": chat_id,
                "editCount": edit_count,
                "timestamp": datetime.now().isoformat()
            })

        self._current_chat_id = chat_id

    def quit(self):
        """종료"""
        if self._connector:
            self._connector.disconnect()
            self._connector = None


def handle_request(processor: DocumentProcessor, request: dict) -> dict:
    """요청 처리"""
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")

    result = {"id": request_id}

    try:
        if method == "ping":
            result["result"] = {"pong": True}

        elif method == "open":
            result["result"] = processor.open_document(params.get("file"))

        elif method == "extract":
            result["result"] = processor.extract_html(
                params.get("format", "api")  # "api" (기본값) 또는 "hwpml"
            )

        elif method == "apply":
            result["result"] = processor.apply_commands(
                params.get("commands", []),
                params.get("mappings")
            )

        elif method == "save":
            result["result"] = processor.save_document()

        elif method == "close":
            result["result"] = processor.close_document()

        elif method == "quit":
            processor.quit()
            result["result"] = {"success": True}

        elif method == "getOpenDocuments":
            result["result"] = processor.get_open_documents()

        elif method == "getCurrentPageInfo":
            result["result"] = processor.get_current_page_info()

        elif method == "selectDocument":
            result["result"] = processor.select_document(
                params.get("type"),
                params.get("index", 0)
            )

        elif method == "chat":
            result["result"] = processor.chat(
                params.get("prompt", ""),
                params.get("docType"),
                params.get("docIndex"),
                params.get("referenceContent"),
                params.get("referenceFileName"),
                params.get("startPage"),
                params.get("endPage"),
                params.get("openaiApiKey")
            )

        # 파일 읽기 (서버 분리 대비 - 로컬에서 읽고 문자열 반환)
        elif method == "readTxtFile":
            result["result"] = TxtReader.read(
                params.get("filePath", ""),
                params.get("maxChars")
            )

        elif method == "readExcelFile":
            result["result"] = ExcelReader.read(
                params.get("filePath", ""),
                params.get("sheetName")
            )

        elif method == "getExcelSheets":
            result["result"] = ExcelReader.get_sheet_names(
                params.get("filePath", "")
            )

        # Undo/Redo 및 Diff 모드 API (TASK-004)
        elif method == "undo":
            result["result"] = processor.undo(params.get("count", 1))

        elif method == "redo":
            result["result"] = processor.redo(params.get("count", 1))

        elif method == "setDiffMode":
            result["result"] = processor.set_diff_mode(params.get("enabled", False))

        elif method == "getDiffMode":
            result["result"] = processor.get_diff_mode()

        elif method == "getEditHistory":
            result["result"] = processor.get_edit_history()

        elif method == "clearEditHistory":
            result["result"] = processor.clear_edit_history()

        # Diff 변경사항 승인/거절 API
        elif method == "applyChanges":
            result["result"] = processor.apply_changes()

        elif method == "rejectChanges":
            result["result"] = processor.reject_changes()

        # Window Binding API (Window Monitor 연동)
        elif method == "window:bind":
            pid = params.get("pid")
            hwnd = params.get("hwnd")
            print(f"[main.py] Binding to HWP window: PID={pid}, HWND={hwnd}", file=sys.stderr)
            success = session_state.bind_global_target_window(pid, hwnd)
            # 기존 connector가 있으면 초기화 (새 PID로 재연결)
            from engine.state.session_state import retrieve_runtime_connector, store_runtime_connector
            old_connector = retrieve_runtime_connector()
            if old_connector:
                try:
                    old_connector.disconnect()
                except Exception:
                    pass
                store_runtime_connector(None)
                # processor의 _connector도 초기화
                processor._connector = None
            print(f"[main.py] Bind result: {success}, old connector cleared", file=sys.stderr)
            result["result"] = {
                "success": success,
                "pid": pid,
                "hwnd": hwnd
            }

        elif method == "window:unbind":
            print("[main.py] Unbinding HWP window", file=sys.stderr)
            session_state.store_runtime_target_process_id(None)
            session_state.store_runtime_target_window_handle(None)
            # connector 초기화
            from engine.state.session_state import retrieve_runtime_connector, store_runtime_connector
            connector = retrieve_runtime_connector()
            if connector:
                try:
                    connector.disconnect()
                except Exception:
                    pass
                store_runtime_connector(None)
            processor._connector = None
            result["result"] = {"success": True}

        else:
            result["error"] = f"Unknown method: {method}"

    except Exception as e:
        error_msg = str(e)
        result["error"] = error_msg
        result["trace"] = traceback.format_exc()

        # RPC 오류 감지 시 자동 PID 초기화
        if "RPC" in error_msg or "-2147417851" in error_msg or "-2147023174" in error_msg or "-2147220995" in error_msg:
            print(f"[main.py] RPC 오류 감지, PID/HWND 초기화: {error_msg}", file=sys.stderr)
            session_state.store_runtime_target_process_id(None)
            session_state.store_runtime_target_window_handle(None)
            # connector 초기화
            from engine.state.session_state import retrieve_runtime_connector, store_runtime_connector
            connector = retrieve_runtime_connector()
            if connector:
                try:
                    connector.disconnect()
                except Exception:
                    pass
                store_runtime_connector(None)
            processor._connector = None
            # 에러 메시지에 재연결 안내 추가
            result["error"] = f"{error_msg}\n\n한글 문서가 닫혔거나 응답하지 않습니다. Window Monitor가 자동으로 재연결을 시도합니다."

    return result


def main():
    """메인 루프 - stdin에서 JSON 명령을 읽고 처리"""
    processor = DocumentProcessor()

    # stderr로 시작 메시지 출력 (디버깅용)
    print("Inserty Python Processor started", file=sys.stderr)
    sys.stderr.flush()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = handle_request(processor, request)

                # stdout으로 JSON 응답 전송
                print(json.dumps(response, ensure_ascii=False))
                sys.stdout.flush()

                # quit 명령이면 종료
                if request.get("method") == "quit":
                    break

            except json.JSONDecodeError as e:
                error_response = {"error": f"JSON parse error: {e}"}
                print(json.dumps(error_response))
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        processor.quit()


if __name__ == "__main__":
    main()

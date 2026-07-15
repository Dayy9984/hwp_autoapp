# -*- coding: utf-8 -*-
"""
CVD Service
CVD 추출 래퍼 서비스
"""

import json
import os
import sys
from typing import Dict, Any, Optional, Callable

# CVD Extractor 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.connection.hwp_file_opener import cleanup_temp_open_copy
from engine.connection.hwp_file_opener import open_hwp_file_with_fallback
from engine.connection.security_module import activate_security_module
from .config import config


class CVDService:
    """CVD 추출 서비스"""

    def __init__(
        self,
        log_callback: Optional[Callable[[str, str], None]] = None,
        allow_existing_instance: bool = True
    ):
        self.log_callback = log_callback
        self.allow_existing_instance = allow_existing_instance
    
    def log(self, level: str, message: str) -> None:
        """로그 출력"""
        if self.log_callback:
            self.log_callback(level, message)
        else:
            print(f"[CVDService] [{level}] {message}")

    @staticmethod
    def _is_hwp_window_class(class_name: str) -> bool:
        if class_name.startswith(("HncFrame", "Hwp")):
            return True
        if class_name.startswith("HwndWrapper"):
            return "hwp.exe" in class_name.lower()
        return False

    def _list_running_hwp_pids(self) -> set[int]:
        """
        현재 보이는 HWP 메인 윈도우의 PID 목록을 수집한다.
        격리 인스턴스 검증용으로 사용된다.
        """
        pids: set[int] = set()
        try:
            import win32gui
            import win32process
        except Exception as e:
            self.log("warning", f"HWP PID 수집 불가 (win32 모듈 없음): {e}")
            return pids

        def enum_callback(hwnd: int, _lparam: int) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True

                class_name = win32gui.GetClassName(hwnd)
                if not self._is_hwp_window_class(class_name):
                    return True

                # GW_OWNER (4): owner가 있으면 서브 창으로 판단하여 제외
                owner_hwnd = win32gui.GetWindow(hwnd, 4)
                if owner_hwnd:
                    return True

                title = win32gui.GetWindowText(hwnd) or ""
                if class_name.startswith("HwndWrapper") and not title.strip():
                    return True

                _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    pids.add(int(pid))
            except Exception:
                # 일부 창 조회 실패는 무시하고 계속 탐색
                pass
            return True

        try:
            win32gui.EnumWindows(enum_callback, 0)
        except Exception as e:
            self.log("warning", f"HWP PID 열거 실패: {e}")
        return pids

    def _get_hwp_pid(self, hwp: Any) -> Optional[int]:
        """pyhwpx Hwp 인스턴스의 활성 윈도우 PID를 추출한다.

        HWP 2018 (Version "10,...") = Active_XHwpWindow.WindowHandle 호출 자체 가
        active document 를 빈 문서 로 toggle 시키는 buggy 동작 → 호출 자체 회피.
        대신 None 반환 → 호출자 가 fallback (예: 첫 번째 hwp.exe PID).
        """
        try:
            v = str(getattr(hwp, 'Version', '')).strip()
            if v.startswith("10,"):
                return None  # HWP 2018: WindowHandle 호출 skip
        except Exception:
            pass

        try:
            import win32process
            hwnd = int(hwp.XHwpWindows.Active_XHwpWindow.WindowHandle)
            if hwnd <= 0:
                return None
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            return int(pid) if pid else None
        except Exception:
            return None

    def _register_automation_module(self, hwp: Any, base_name: str) -> None:
        """
        HWP 파일 접근 보안 팝업 완화를 위해 자동화 모듈 등록을 시도한다.
        """
        success, module_id, dll_path = activate_security_module(
            hwp,
            log_callback=self.log,
        )
        if success:
            suffix = f" ({dll_path})" if dll_path else ""
            self.log("info", f"[{base_name}] 보안 모듈 등록 성공: {module_id}{suffix}")
            return
        raise RuntimeError(
            "HWP_SECURITY_MODULE_UNAVAILABLE: FilePathCheckerModule 등록 실패로 "
            "권한 승인 팝업 없이 자동 실행할 수 없습니다."
        )

    @staticmethod
    def _looks_like_permission_error(message: str) -> bool:
        normalized = (message or "").lower()
        keywords = (
            "filepathcheckdll",
            "automationmodule",
            "권한",
            "접근",
            "허용",
            "permission",
            "access",
            "denied",
            "security",
        )
        return any(token in normalized for token in keywords)
    
    def extract_single_cvd(
        self, 
        file_path: str, 
        output_dir: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        start_pct: float = 0.0,
        end_pct: float = 1.0
    ) -> Dict[str, Any]:
        """
        단일 파일 CVD 추출 (HWPML 파싱 방식)

        Args:
            file_path: HWP/HWPX 파일 경로
            output_dir: CVD 출력 디렉토리
            progress_callback: 진행률 콜백
            start_pct: 시작 진행률 (0.0 ~ 1.0)
            end_pct: 종료 진행률 (0.0 ~ 1.0)
        """
        import tempfile
        import uuid
        from pathlib import Path

        def report(pct_in_stage: float, msg: str):
            if progress_callback:
                # 전체 범위 내에서 현재 단계 비율 계산
                total_pct = start_pct + (end_pct - start_pct) * pct_in_stage
                progress_callback(total_pct, msg)

        hwp = None
        owns_hwp_instance = False
        temp_hwpml_path: Optional[str] = None
        temp_open_copy_path: Optional[str] = None

        try:
            from pyhwpx import Hwp
            from processing.extraction.cvd_extractor import CVDExtractor

            base_name = os.path.splitext(os.path.basename(file_path))[0]
            self.log("info", f"[{base_name}] CVD 추출 시작")
            report(0.05, f"[{base_name}] 파일 준비 중...")

            # HWP 열기
            report(0.1, f"[{base_name}] HWP 실행 중...")
            self.log("info", f"[{base_name}] HWP 파일 열기 중...")
            before_pids = self._list_running_hwp_pids()
            hwp = Hwp(new=True, visible=False)
            owns_hwp_instance = True
            self._register_automation_module(hwp, base_name)
            hwp_pid = self._get_hwp_pid(hwp)
            is_isolated = hwp_pid is not None and hwp_pid not in before_pids

            if not is_isolated:
                reason = (
                    f"격리 인스턴스 검증 실패 (pid={hwp_pid}, "
                    f"existing_pids={sorted(before_pids)})"
                )
                self.log("error", f"[{base_name}] {reason}")
                if not self.allow_existing_instance:
                    raise RuntimeError(f"HWP_ISOLATION_FAILED: {reason}")

            try:
                opened_ok, actual_opened_path, temp_open_copy_path = open_hwp_file_with_fallback(
                    hwp, file_path
                )
                if not opened_ok:
                    raise RuntimeError("HWP_OPEN_FAILED")
                if actual_opened_path and actual_opened_path != file_path:
                    self.log(
                        "info",
                        f"[{base_name}] 원본 경로 열기 실패로 임시 복사본 경로 사용: {actual_opened_path}",
                    )
            except Exception as open_error:
                open_message = str(open_error)
                if self._looks_like_permission_error(open_message):
                    raise RuntimeError(
                        "HWP_PERMISSION_DENIED: 한글 파일 접근 권한이 거부되었거나 "
                        "보안 승인 대기 상태입니다."
                    ) from open_error
                raise

            # CVD 추출
            report(0.6, f"[{base_name}] 구조 분석 중...")
            self.log("info", f"[{base_name}] CVD 추출기 실행 중...")
            extractor = CVDExtractor(hwp)

            # 빌드 환경(Nuitka)에서 COM 초기화가 느려 PageCount=0일 수 있음 → 추가 대기
            import time as _time
            page_count = int(getattr(hwp, "PageCount", 0) or 0)
            if page_count <= 0:
                for _ in range(15):  # 최대 3초 추가 대기
                    _time.sleep(0.2)
                    page_count = int(getattr(hwp, "PageCount", 0) or 0)
                    if page_count > 0:
                        break

            if page_count <= 0:
                raise RuntimeError("HWP_PAGE_COUNT_UNAVAILABLE")
            extracted = extractor.extract_cvd(
                {"start": 1, "end": page_count, "current_page": 1}
            )
            if extracted is None:
                raise ValueError("CVD extraction returned None")
            html_content, _id_to_pos = extracted

            # 저장
            report(0.9, f"[{base_name}] 결과 저장 중...")
            self.log("info", f"[{base_name}] HTML 파일 저장 중...")
            os.makedirs(output_dir, exist_ok=True)
            cvd_path = os.path.join(output_dir, f"{base_name}.cvd.md")
            meta_path = os.path.join(output_dir, f"{base_name}.cvd.meta.json")

            with open(cvd_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            meta = {
                "html_length": len(html_content),
                "element_count": len(getattr(extractor, "extracted_elements", []) or []),
                "page_count": page_count,
                "created_at": self._get_timestamp()
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            self.log("info", f"[{base_name}] CVD 추출 완료 ({len(html_content):,} chars)")
            report(1.0, f"[{base_name}] 완료")

            return {
                "success": True,
                "cvd_path": cvd_path,
                "meta_path": meta_path
            }

        except Exception as e:
            self.log("error", f"CVD 추출 실패: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            # 생성한 인스턴스는 예외 경로를 포함해 항상 종료한다.
            if hwp is not None and owns_hwp_instance:
                try:
                    hwp.quit()
                except Exception as quit_error:
                    self.log("warning", f"HWP 종료 중 경고: {quit_error}")

            if temp_hwpml_path and os.path.exists(temp_hwpml_path):
                try:
                    os.remove(temp_hwpml_path)
                except Exception:
                    pass
            cleanup_temp_open_copy(temp_open_copy_path)
    
    def extract_pair_cvd(
        self,
        project_id: str,
        pair_id: str,
        template_path: str,
        filled_path: str,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Template Pair CVD 추출
        """
        try:
            output_dir = config.get_template_pair_path(project_id, pair_id)
            os.makedirs(output_dir, exist_ok=True)
            
            # 템플릿 CVD 추출 (0% ~ 50%)
            template_result = self.extract_single_cvd(
                template_path, 
                output_dir,
                progress_callback=progress_callback,
                start_pct=0.0,
                end_pct=0.5
            )
            if not template_result.get("success"):
                return template_result
            
            # 작성본 CVD 추출 (50% ~ 100%)
            filled_result = self.extract_single_cvd(
                filled_path, 
                output_dir,
                progress_callback=progress_callback,
                start_pct=0.5,
                end_pct=1.0
            )
            if not filled_result.get("success"):
                return filled_result
            
            # 결과 경로 정리 (이제 .md 파일)
            template_cvd_path = os.path.join(output_dir, "template.cvd.md")
            filled_cvd_path = os.path.join(output_dir, "filled.cvd.md")

            # 파일명 변경
            if os.path.exists(template_result["cvd_path"]):
                os.rename(template_result["cvd_path"], template_cvd_path)
            if os.path.exists(template_result["meta_path"]):
                os.rename(template_result["meta_path"], os.path.join(output_dir, "template.cvd.meta.json"))
            if os.path.exists(filled_result["cvd_path"]):
                os.rename(filled_result["cvd_path"], filled_cvd_path)
            if os.path.exists(filled_result["meta_path"]):
                os.rename(filled_result["meta_path"], os.path.join(output_dir, "filled.cvd.meta.json"))
            
            if progress_callback:
                progress_callback(1.0, "CVD 추출 완료")
            
            return {
                "success": True,
                "template_cvd_path": template_cvd_path,
                "filled_cvd_path": filled_cvd_path
            }
            
        except Exception as e:
            self.log("error", f"Pair CVD 추출 실패: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _get_timestamp(self) -> int:
        """밀리초 타임스탬프"""
        from datetime import datetime
        return int(datetime.now().timestamp() * 1000)
    
    def extract_pdf(self, file_path: str) -> Dict[str, Any]:
        """
        PDF 텍스트 추출
        ⭐ 규칙 S3: 스캔 PDF는 OCR fallback
        """
        try:
            import PyPDF2
            
            reader = PyPDF2.PdfReader(file_path)
            text = ""
            page_count = len(reader.pages)
            
            for page in reader.pages:
                # ⭐ None 방지
                page_text = page.extract_text() or ""
                text += page_text
            
            if not text.strip():
                # 스캔 PDF → OCR fallback (실패 시 명시적으로 실패 반환)
                text = self.extract_pdf_with_ocr(file_path)
            
            return {
                "success": True,
                "text": text,
                "pages": page_count,
                "page_count": page_count
            }
            
        except Exception as e:
            self.log("error", f"PDF 추출 실패: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def extract_pdf_with_ocr(self, file_path: str) -> str:
        """
        스캔 PDF OCR 추출
        ⭐ PDF OCR 지원
        """
        try:
            from services.ocr_service import OCRService

            ocr_service = OCRService()
            ocr_result = ocr_service.extract_text_from_pdf(
                pdf_path=file_path,
                lang='kor+eng',
                dpi=300
            )
            if not ocr_result.get("success"):
                raise ValueError(ocr_result.get("error", "OCR failed"))
            return str(ocr_result.get("text", "")).strip()
        except Exception as e:
            raise ValueError(f"OCR failed: {e}")

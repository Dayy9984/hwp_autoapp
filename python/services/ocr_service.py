# -*- coding: utf-8 -*-
"""
OCR Service
PDF 및 이미지 OCR 서비스 (Tesseract 기반)
"""

import os
from typing import Dict, Any, List, Tuple


class OCRService:
    """OCR 서비스"""
    
    def __init__(self):
        self._tesseract_available = None
        self._setup_tesseract()
    
    def _setup_tesseract(self) -> None:
        """Tesseract 경로 설정 (Windows)"""
        if os.name == 'nt':
            candidate_paths = [
                os.getenv("TESSERACT_CMD", "").strip(),
                r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            ]
            for tesseract_path in candidate_paths:
                if not tesseract_path:
                    continue
                if os.path.exists(tesseract_path):
                    try:
                        import pytesseract
                        pytesseract.pytesseract.tesseract_cmd = tesseract_path
                        break
                    except ImportError:
                        pass
    
    def is_tesseract_available(self) -> bool:
        """Tesseract 설치 여부 확인"""
        if self._tesseract_available is not None:
            return self._tesseract_available
        
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._tesseract_available = True
        except Exception:
            self._tesseract_available = False
        
        return self._tesseract_available
    
    def extract_text_from_image(
        self,
        image_path: str,
        lang: str = 'kor+eng'
    ) -> Dict[str, Any]:
        """
        이미지에서 텍스트 추출
        
        Args:
            image_path: 이미지 파일 경로
            lang: OCR 언어 (기본: 한글+영어)
        
        Returns:
            {success: bool, text: str}
        """
        try:
            if not self.is_tesseract_available():
                return {
                    "success": False,
                    "error": "Tesseract not installed"
                }
            
            import pytesseract
            from PIL import Image
            
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, lang=lang)
            
            return {
                "success": True,
                "text": text.strip()
            }
            
        except ImportError as e:
            return {
                "success": False,
                "error": f"Required packages not installed: {e}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def extract_text_from_pdf(
        self,
        pdf_path: str,
        lang: str = 'kor+eng',
        dpi: int = 300
    ) -> Dict[str, Any]:
        """
        스캔 PDF에서 텍스트 추출
        
        Args:
            pdf_path: PDF 파일 경로
            lang: OCR 언어
            dpi: 이미지 변환 DPI
        
        Returns:
            {success: bool, text: str, pages: int}
        """
        try:
            if not self.is_tesseract_available():
                return {
                    "success": False,
                        "error": "Tesseract not installed. Please install Tesseract-OCR."
                }
            
            import pytesseract
            images, backend = self._load_pdf_images(pdf_path, dpi=dpi)
            
            text = ""
            for i, image in enumerate(images):
                page_text = pytesseract.image_to_string(image, lang=lang)
                text += f"\n--- Page {i + 1} ---\n{page_text}"

            extracted = text.strip()
            if not extracted:
                return {
                    "success": False,
                    "error": f"OCR extracted no text (backend={backend})"
                }
            
            return {
                "success": True,
                "text": extracted,
                "pages": len(images),
                "backend": backend
            }
            
        except ImportError as e:
            return {
                "success": False,
                "error": f"Required packages not installed: {e}. "
                         "Run: pip install pdf2image pytesseract"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _load_pdf_images(self, pdf_path: str, dpi: int = 300) -> Tuple[List[Any], str]:
        """
        PDF를 OCR 가능한 이미지 목록으로 변환
        1순위: pdf2image (poppler 기반)
        2순위: pypdfium2 (poppler 불필요)
        """
        errors: List[str] = []

        try:
            from pdf2image import convert_from_path
            images = convert_from_path(pdf_path, dpi=dpi)
            if images:
                return images, "pdf2image"
            errors.append("pdf2image returned no pages")
        except Exception as e:
            errors.append(f"pdf2image failed: {e}")

        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(pdf_path)
            try:
                page_count = len(pdf)
                if page_count == 0:
                    errors.append("pypdfium2 found zero pages")
                else:
                    scale = max(1.0, float(dpi) / 72.0)
                    images: List[Any] = []
                    for page_index in range(page_count):
                        page = pdf[page_index]
                        try:
                            pil_image = page.render(scale=scale).to_pil()
                            images.append(pil_image)
                        finally:
                            close_fn = getattr(page, "close", None)
                            if callable(close_fn):
                                close_fn()
                    if images:
                        return images, "pypdfium2"
                    errors.append("pypdfium2 rendered no images")
            finally:
                close_pdf = getattr(pdf, "close", None)
                if callable(close_pdf):
                    close_pdf()
        except Exception as e:
            errors.append(f"pypdfium2 failed: {e}")

        raise RuntimeError("PDF 이미지 변환 실패: " + " | ".join(errors))
    
    def get_installation_guide(self) -> Dict[str, str]:
        """설치 가이드 반환"""
        return {
            "tesseract": {
                "windows": (
                    "1. https://github.com/UB-Mannheim/tesseract/wiki 에서 다운로드\n"
                    "2. 설치 시 'Korean' 언어 팩 포함\n"
                    "3. 환경변수 PATH에 Tesseract 경로 추가"
                ),
                "mac": "brew install tesseract tesseract-lang",
                "linux": "sudo apt-get install tesseract-ocr tesseract-ocr-kor"
            },
            "poppler": {
                "windows": (
                    "1. https://github.com/oschwartz10612/poppler-windows/releases 에서 다운로드\n"
                    "2. 압축 해제 후 bin 폴더를 PATH에 추가"
                ),
                "mac": "brew install poppler",
                "linux": "sudo apt-get install poppler-utils"
            },
            "python_packages": "pip install pdf2image pytesseract pillow"
        }

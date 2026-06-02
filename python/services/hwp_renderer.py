# -*- coding: utf-8 -*-
"""HWP 렌더러 — 편집된 HWP 문서를 PDF 로 저장 후 페이지별 PNG bytes 로 변환.

검증 파이프라인(verification_service)에서 비전 모델에 넘길 이미지를 만들기 위해 사용.

설계 제약(스펙 §5.3, 결정 4):
  - 전체 문서 렌더(after 만).
  - 절대 raise 하지 않는다. 어떤 실패도 빈 리스트([]) 로 degrade — 본 편집/UI 에 0 영향.
  - 임시 PDF 는 항상 정리.
"""

import io
import os
import sys
import tempfile


def _load_pdf_images_for_render(pdf_path, dpi):
    """OCRService._load_pdf_images 를 감싸 PIL 이미지 리스트만 반환.

    _load_pdf_images 는 (images, backend) 튜플을 반환하므로 [0] 으로 이미지만 취한다.
    분리된 함수로 둔 이유: 테스트에서 HWP/PDF 백엔드 없이 monkeypatch 하기 위함.
    """
    from services.ocr_service import OCRService
    return OCRService()._load_pdf_images(pdf_path, dpi=dpi)[0]


def render_doc_to_pngs(hwp, dpi: int = 200) -> "list[bytes]":
    """편집된 HWP 문서를 PDF→PNG[] 로 렌더한다.

    Args:
        hwp: pyhwpx HWP COM 객체(또는 save_as 를 가진 호환 객체).
        dpi: PDF→이미지 변환 DPI (기본 200, 스펙 §5.3).

    Returns:
        페이지별 PNG bytes 리스트. 실패 시 [] (절대 raise 안 함).
    """
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        hwp.save_as(tmp, format="PDF")
        images = _load_pdf_images_for_render(tmp, dpi)
        out: list[bytes] = []
        for img in images or []:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    except Exception as e:
        print(f"[hwp_renderer] failed: {e}", file=sys.stderr)
        return []
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

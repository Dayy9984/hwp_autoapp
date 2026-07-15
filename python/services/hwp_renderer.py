# -*- coding: utf-8 -*-
"""HWP 렌더러 — 편집된 HWP 문서를 페이지별 PNG bytes 로 변환.

평가 파이프라인(eval.auto_eval)에서 비전 모델에 넘길 이미지를 만들기 위해 사용.

설계 제약(스펙 §5.3, 결정 4):
  - 절대 raise 하지 않는다. 어떤 실패도 빈 리스트([]) 로 degrade — 본 편집/UI 에 0 영향.
  - 임시 파일은 항상 정리.

렌더 모드(스펙 + 340a07/1f379b 헤비 문서 hang 회피):
  - ``max_pages`` 가 주어지면: **첫 ``max_pages`` 페이지만** 페이지 단위로 직접 렌더한다
    (raw COM ``CreatePageImage(pgno=i)``). 이 경로는 전체 문서 PDF 익스포트를
    하지 않으므로, 전체 PDF save_as 가 무한 hang 하는 헤비 문서(340a07: 편집 없이도
    full-PDF-export 가 RPC-die / hang; 1f379b: 47p) 에서도 페이지 단위로 빠르게 끝난다.
  - ``max_pages`` 가 None 이면(기존 동작): 전체 문서를 PDF 로 save_as → 페이지별 PNG.
    (전체 렌더가 필요한 호출 경로 호환용. 헤비 문서에는 max_pages 를 넘길 것.)
"""

import io
import os
import sys
import tempfile
import time


def _load_pdf_images_for_render(pdf_path, dpi):
    """OCRService._load_pdf_images 를 감싸 PIL 이미지 리스트만 반환.

    _load_pdf_images 는 (images, backend) 튜플을 반환하므로 [0] 으로 이미지만 취한다.
    분리된 함수로 둔 이유: 테스트에서 HWP/PDF 백엔드 없이 monkeypatch 하기 위함.
    """
    from services.ocr_service import OCRService
    return OCRService()._load_pdf_images(pdf_path, dpi=dpi)[0]


def _raw_com(hwp):
    """렌더 핸들에서 ``CreatePageImage`` / ``PageCount`` 를 노출하는 raw COM 을 추출.

    렌더러가 받는 핸들은 경로마다 다르다:
      - pyhwpx ``Hwp``        → 실제 COM 은 ``.hwp`` 속성.
      - ``_PdfSaveAdapter``   → raw COM 은 ``._com`` 속성(auto_eval).
      - raw COM 객체 자체      → 그대로 사용.
    ``CreatePageImage`` 를 직접 가진 첫 후보를 반환. 없으면 None.
    """
    for cand in (
        getattr(hwp, "_com", None),   # _PdfSaveAdapter
        getattr(hwp, "hwp", None),    # pyhwpx Hwp wrapper
        hwp,                          # raw COM
    ):
        if cand is not None and hasattr(cand, "CreatePageImage"):
            return cand
    return None


def _page_count(com, hwp) -> "int | None":
    """문서 총 페이지 수. raw COM 우선, pyhwpx wrapper 의 ``PageCount`` 도 시도."""
    for obj in (com, hwp):
        if obj is None:
            continue
        try:
            n = int(getattr(obj, "PageCount"))
            if n > 0:
                return n
        except Exception:
            continue
    return None


def _render_first_pages(hwp, dpi: int, max_pages: int) -> "list[bytes]":
    """첫 ``max_pages`` 페이지를 페이지 단위로 직접 렌더 → PNG bytes 리스트.

    raw COM ``CreatePageImage(Path, pgno, resolution, depth, Format)`` 를 페이지마다
    호출한다. pgno 는 0-index(pyhwpx core.create_page_image 와 동일: pgno=i-1).
    전체 문서 PDF 익스포트가 없어 헤비 문서에서도 hang 하지 않는다(대화상자 없음).

    PNG 직접 출력 시도 → 일부 HWP 빌드는 png 미지원이므로 실패하면 bmp 로 저장 후
    PIL 로 PNG 재인코딩한다. 절대 raise 안 함(페이지 실패는 skip).
    """
    com = _raw_com(hwp)
    if com is None:
        raise RuntimeError("handle exposes no CreatePageImage (raw COM not found)")

    total = _page_count(com, hwp)
    n = max_pages if total is None else min(max_pages, total)
    if n <= 0:
        return []

    from PIL import Image

    out: list[bytes] = []
    for i in range(n):
        tmp = None
        try:
            # 먼저 PNG 직접 출력 시도.
            for fmt, suffix in (("png", ".png"), ("bmp", ".bmp")):
                fd, tmp = tempfile.mkstemp(suffix=suffix)
                os.close(fd)
                try:
                    ok = com.CreatePageImage(
                        Path=tmp, pgno=i, resolution=dpi, depth=24, Format=fmt
                    )
                except Exception as e:
                    ok = False
                    print(f"[hwp_renderer] CreatePageImage page {i + 1} fmt={fmt} "
                          f"failed: {e}", file=sys.stderr)
                # CreatePageImage 는 출력파일을 새로 쓴다; 파일 존재+비어있지않음으로 성공 판정.
                if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                    with Image.open(tmp) as img:
                        buf = io.BytesIO()
                        img.convert("RGB").save(buf, format="PNG")
                        out.append(buf.getvalue())
                    break  # 이 페이지 렌더 성공
                # 이 포맷 실패 → 임시파일 정리 후 다음 포맷 시도.
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    tmp = None
        except Exception as e:
            print(f"[hwp_renderer] page {i + 1} render failed: {e}", file=sys.stderr)
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return out


def render_doc_to_pngs(hwp, dpi: int = 200, attempts: int = 3,
                       max_pages: "int | None" = None) -> "list[bytes]":
    """편집된 HWP 문서를 PNG[] 로 렌더한다.

    ``max_pages`` 가 주어지면 **첫 ``max_pages`` 페이지만** 페이지 단위로 직접 렌더
    (raw COM ``CreatePageImage``) — 전체 문서 PDF 익스포트를 하지 않으므로 헤비 문서
    (340a07: 전체 PDF save_as 가 hang/ RPC-die; 1f379b: 47p) 에서도 hang 하지 않는다.
    None 이면 기존대로 전체 문서를 PDF 로 save_as 후 페이지별 변환.

    save_as / CreatePageImage 가 HWP COM RPC 변덕(예: -2147023174 'RPC 서버 사용 불가',
    -2147023170 '원격 프로시저 호출 못함')으로 실패하는 경우가 있어 backoff 재시도한다.
    transient 한 RPC 글리치는 재시도로 복구되고, COM 이 완전히 죽은 경우엔 모두 실패 →
    빈 리스트 반환(상위 form-level 재시도가 새 프로세스로 복구).

    Args:
        hwp: pyhwpx HWP COM 객체(또는 save_as/CreatePageImage 를 가진 호환 객체).
        dpi: 이미지 변환 DPI (기본 200, 스펙 §5.3).
        attempts: 렌더 재시도 횟수(기본 3).
        max_pages: 렌더할 첫 페이지 수. None 이면 전체 문서.

    Returns:
        페이지별 PNG bytes 리스트. 실패 시 [] (절대 raise 안 함).
    """
    last_err = None
    for attempt in range(1, attempts + 1):
        tmp = None
        try:
            if max_pages and max_pages > 0:
                # 페이지 단위 직접 렌더 — 전체 PDF 익스포트 없음(헤비 문서 hang 회피).
                out = _render_first_pages(hwp, dpi, max_pages)
                if out:
                    return out
                last_err = "no pages rendered"
            else:
                # 전체 문서 PDF 익스포트 (기존 동작).
                fd, tmp = tempfile.mkstemp(suffix=".pdf")
                os.close(fd)
                hwp.save_as(tmp, format="PDF")
                images = _load_pdf_images_for_render(tmp, dpi)
                out = []
                for img in images or []:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    out.append(buf.getvalue())
                if out:
                    return out
                last_err = "no images rendered"
        except Exception as e:
            last_err = e
            print(f"[hwp_renderer] attempt {attempt}/{attempts} failed: {e}", file=sys.stderr)
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if attempt < attempts:
            time.sleep(1.5 * attempt)  # backoff
    print(f"[hwp_renderer] all {attempts} attempts failed: {last_err}", file=sys.stderr)
    return []

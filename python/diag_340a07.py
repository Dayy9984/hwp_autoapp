# 340a07 render 진단 — 편집 없이 open + PageCount + save_as PDF 만.
# RPC 죽으면 = 47페이지 PDF 내보내기 자체가 범인(편집 무관).
# 사용: uv run python diag_340a07.py <hwp_path>
import sys, os, time, tempfile

def main():
    path = sys.argv[1]
    hwp = None
    try:
        from pyhwpx import Hwp
    except Exception as e:
        print("IMPORT_FAIL", e); return 2
    try:
        hwp = Hwp(new=True, visible=False)
        try:
            from engine.connection.security_module import activate_security_module
            activate_security_module(hwp, log_callback=None)
        except Exception as e:
            print("sec warn:", e)
        from engine.connection.hwp_file_opener import open_hwp_file_with_fallback
        ok, _, _ = open_hwp_file_with_fallback(hwp, path)
        print("opened:", ok)
        try:
            print("PageCount:", hwp.PageCount)
        except Exception as e:
            print("PageCount err:", e)

        # (A) 편집 없이 전체 save_as PDF
        fd, pdf = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
        t = time.time()
        try:
            hwp.save_as(pdf, format="PDF")
            dt = time.time() - t
            sz = os.path.getsize(pdf) if os.path.exists(pdf) else -1
            print(f"NOEDIT save_as PDF: OK in {dt:.1f}s size={sz}")
            # PDF 페이지 수
            try:
                from services.ocr_service import OCRService
                imgs = OCRService()._load_pdf_images(pdf, dpi=120)[0]
                print(f"  rendered PDF pages: {len(imgs)}")
            except Exception as e:
                print(f"  pdf load err: {e}")
        except Exception as e:
            print(f"NOEDIT save_as PDF: FAIL in {time.time()-t:.1f}s: {e}")
        finally:
            try: os.remove(pdf)
            except OSError: pass
        return 0
    except Exception as e:
        print("FAIL", type(e).__name__, str(e)[:160]); return 1
    finally:
        try:
            if hwp is not None: hwp.quit()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())

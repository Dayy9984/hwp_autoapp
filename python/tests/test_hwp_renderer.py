import os
import sys

# Match repo test convention: make python/ importable so `services.*` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_render_doc_to_pngs(monkeypatch, tmp_path):
    from PIL import Image
    import services.hwp_renderer as r

    class FakeHwp:
        def save_as(self, path, format=None):
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 fake")

    # _load_pdf_images_for_render returns 2 PIL images (HWP/PDF backend mocked away).
    monkeypatch.setattr(
        r, "_load_pdf_images_for_render",
        lambda path, dpi: [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))],
    )
    out = r.render_doc_to_pngs(FakeHwp(), dpi=150)
    assert len(out) == 2
    assert all(b[:4] == b"\x89PNG" for b in out)


def test_render_never_raises(monkeypatch):
    import services.hwp_renderer as r

    class BadHwp:
        def save_as(self, *a, **k):
            raise RuntimeError("no hwp")

    assert r.render_doc_to_pngs(BadHwp()) == []


def test_render_empty_when_no_images(monkeypatch):
    import services.hwp_renderer as r

    class FakeHwp:
        def save_as(self, path, format=None):
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 fake")

    monkeypatch.setattr(r, "_load_pdf_images_for_render", lambda path, dpi: [])
    assert r.render_doc_to_pngs(FakeHwp()) == []


def test_render_max_pages_uses_create_page_image_not_save_as():
    """max_pages 경로: 페이지 단위 CreatePageImage 만 호출, save_as(전체 PDF) 금지."""
    from PIL import Image
    import services.hwp_renderer as r

    class FakeCom:
        PageCount = 47  # 헤비 문서 흉내

        def __init__(self):
            self.create_calls = []

        def save_as(self, *a, **k):  # 호출되면 안 됨 (전체 PDF 익스포트 hang 유발)
            raise AssertionError("save_as must NOT be called when max_pages is set")

        def CreatePageImage(self, Path, pgno, resolution, depth, Format):
            self.create_calls.append((pgno, Format))
            Image.new("RGB", (12, 12)).save(Path, format=Format.upper())
            return True

    com = FakeCom()
    out = r.render_doc_to_pngs(com, max_pages=1)
    assert len(out) == 1
    assert out[0][:4] == b"\x89PNG"
    # 첫 페이지(pgno=0)만 렌더, 전체 47p 가 아님.
    assert [c[0] for c in com.create_calls] == [0]


def test_render_max_pages_caps_at_page_count():
    """max_pages > PageCount 이면 PageCount 만큼만 렌더."""
    from PIL import Image
    import services.hwp_renderer as r

    class FakeCom:
        PageCount = 2

        def __init__(self):
            self.create_calls = []

        def CreatePageImage(self, Path, pgno, resolution, depth, Format):
            self.create_calls.append(pgno)
            Image.new("RGB", (12, 12)).save(Path, format=Format.upper())
            return True

    com = FakeCom()
    out = r.render_doc_to_pngs(com, max_pages=5)
    assert len(out) == 2
    assert com.create_calls == [0, 1]


def test_render_max_pages_never_raises():
    """CreatePageImage 가 모두 실패해도 [] 반환(절대 raise 안 함)."""
    import services.hwp_renderer as r

    class FakeCom:
        PageCount = 3

        def CreatePageImage(self, **k):
            raise RuntimeError("COM dead")

    assert r.render_doc_to_pngs(FakeCom(), max_pages=1, attempts=1) == []

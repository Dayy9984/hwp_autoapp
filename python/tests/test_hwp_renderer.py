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

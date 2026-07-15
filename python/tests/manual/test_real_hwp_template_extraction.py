import os
import re
import shutil
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HWP_TEMPLATE_TEST") != "1",
    reason="manual integration test (set RUN_REAL_HWP_TEMPLATE_TEST=1)",
)


DEFAULT_FILE = Path(
    r"C:\Users\dlgkr\Downloads\[별첨 1] 2026년 예비창업패키지 사업계획서 양식.hwp"
)
DEFAULT_PDF_FILE = Path(
    r"C:\Users\dlgkr\Downloads\[별첨 1] 2026년 예비창업패키지 사업계획서 양식.pdf"
)


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _copy_to_safe_temp(source: Path) -> Path:
    safe_dir = Path(tempfile.gettempdir()) / "inserty_manual_compare"
    safe_dir.mkdir(parents=True, exist_ok=True)
    target = safe_dir / f"{source.stem.encode('ascii', 'ignore').decode() or 'doc'}_{int(time.time() * 1000)}{source.suffix}"
    shutil.copy2(source, target)
    return target


def _extract_page_texts_from_hdml(extractor) -> dict[int, str]:
    page_map: dict[int, list[str]] = {}
    for item in extractor.extracted_elements:
        page = item.get("page")
        if not isinstance(page, int):
            continue
        text = _normalize_text(item.get("text", ""))
        if text == "":
            continue
        page_map.setdefault(page, []).append(text)
    return {page: " ".join(parts) for page, parts in page_map.items()}


class _HDMLPageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.page_stack: list[int | None] = []
        self.page_map: dict[int, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        page = self.page_stack[-1] if self.page_stack else None
        page_attr = attrs_dict.get("data-page-no")
        if page_attr and page_attr.isdigit():
            page = int(page_attr)
        elif tag == "page":
            page_num = attrs_dict.get("num")
            if page_num and page_num.isdigit():
                page = int(page_num)
        self.page_stack.append(page)

    def handle_endtag(self, tag: str) -> None:
        if self.page_stack:
            self.page_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.page_stack:
            return
        page = self.page_stack[-1]
        if page is None:
            return
        text = _normalize_text(data)
        if not text:
            return
        self.page_map.setdefault(page, []).append(text)


def _extract_page_texts_from_hdml_markup(hdml_text: str) -> dict[int, str]:
    parser = _HDMLPageTextParser()
    parser.feed(hdml_text or "")
    return {page: " ".join(parts) for page, parts in parser.page_map.items()}


def _extract_text_sequence_from_hdml_markup(hdml_text: str) -> list[str]:
    texts: list[str] = []
    for text in re.findall(r"<p\b[^>]*>([^<]*)</p>", hdml_text or ""):
        normalized = _normalize_text(text)
        if normalized:
            texts.append(normalized)
    return texts


def _extract_fixture_hdml_with_retry(
    source_file: Path,
    start_page: int = 2,
    end_page: int = 6,
    attempts: int = 3,
) -> tuple[str, list[str], dict[int, str]]:
    from services.hdml_service import HDMLService

    last_error: Exception | None = None
    for attempt in range(attempts):
        output_dir = Path(tempfile.gettempdir()) / "inserty_real_template_service_test"
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            service = HDMLService()
            result = service.extract_single_hdml(str(source_file), str(output_dir))
            assert result.get("success"), result.get("error", "unknown hdml extraction error")
            hdml_path = Path(result["hdml_path"])
            with hdml_path.open("r", encoding="utf-8") as f:
                hdml_text = f.read()
            texts = _extract_text_sequence_from_hdml_markup(hdml_text)
            page_texts = _extract_page_texts_from_hdml_markup(hdml_text)
            assert all(page in page_texts for page in range(start_page, end_page + 1))
            return hdml_text, texts, page_texts
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
            time.sleep(0.8)
        finally:
            for artifact in output_dir.glob("*.hdml.*"):
                try:
                    artifact.unlink()
                except Exception:
                    pass

    raise AssertionError(f"fixture extraction failed after retries: {last_error}")


def test_real_hwp_template_extraction_preserves_summary_and_solution_bullets():
    if not DEFAULT_FILE.exists():
        pytest.skip(f"fixture file not found: {DEFAULT_FILE}")
    hdml_text, _texts, _page_texts = _extract_fixture_hdml_with_retry(DEFAULT_FILE)

    assert "□ 창업 아이템 개요(요약)" in hdml_text
    assert "< 사업추진 일정(협약기간 내) >" in hdml_text
    assert "2. 실현 가능성(Solution)_창업 아이템의 개발 계획" in hdml_text
    assert (
        "※ 아이디어를 제품·서비스로 개발 또는 구체화 하고자 하는 계획(사업기간 내 일정 등)"
        "개발 창업 아이템의 기능·성능의 차별성 및 경쟁력 확보 전략정부지원사업비 집행 계획 기재"
        in hdml_text
    )

    solution_start = hdml_text.index("2. 실현 가능성(Solution)_창업 아이템의 개발 계획")
    schedule_start = hdml_text.index("< 사업추진 일정(협약기간 내) >")
    solution_block = hdml_text[solution_start:schedule_start]
    assert "◦" in solution_block
    assert solution_block.count("-") >= 3
    assert solution_block.count("◦") >= 2


def test_real_hwp_and_pdf_match_across_pages_2_to_6():
    if not DEFAULT_FILE.exists():
        pytest.skip(f"fixture file not found: {DEFAULT_FILE}")
    if not DEFAULT_PDF_FILE.exists():
        pytest.skip(f"fixture file not found: {DEFAULT_PDF_FILE}")

    safe_pdf = _copy_to_safe_temp(DEFAULT_PDF_FILE)
    hdml_text, _texts, page_texts = _extract_fixture_hdml_with_retry(DEFAULT_FILE)

    pypdf = pytest.importorskip("pypdf")
    try:
        reader = pypdf.PdfReader(str(safe_pdf))
        pdf_page_texts = {
            2: _normalize_text(reader.pages[1].extract_text() or ""),
            3: _normalize_text(reader.pages[2].extract_text() or ""),
            4: _normalize_text(reader.pages[3].extract_text() or ""),
            5: _normalize_text(reader.pages[4].extract_text() or ""),
            6: _normalize_text(reader.pages[5].extract_text() or ""),
        }
    finally:
        try:
            safe_pdf.unlink(missing_ok=True)
        except Exception:
            pass

    expected_page_anchors = {
        2: [
            "예비창업패키지 예비창업자 사업계획서",
            "□ 일반현황",
            "창업아이템명",
            "산출물",
            "직업",
            "기업(예정)명",
            "(예비)창업팀 구성 현황(대표자 본인 제외)",
            "순번",
            "직위",
            "담당 업무",
            "보유역량(경력 및 학력 등)",
            "구성 상태",
        ],
        3: [
            "□ 창업 아이템 개요(요약)",
            "명 칭",
            "범 주",
            "아이템 개요",
            "문제 인식(Problem)",
            "실현 가능성(Solution)",
            "성장전략(Scale-up)",
            "팀 구성(Team)",
            "이미지",
            "< 사진(이미지) 또는 설계도 제목 >",
        ],
        4: [
            "1. 문제 인식(Problem)_창업 아이템의 필요성",
            "문제 해결을 위한 창업 아이템의 개발 필요성 등 기재_개발 아이템 소개",
        ],
        5: [
            "2. 실현 가능성(Solution)_창업 아이템의 개발 계획",
            "정부지원사업비 집행 계획 기재",
            "< 사업추진 일정(협약기간 내) >",
            "구분",
            "추진 내용",
            "추진 기간",
            "세부 내용",
            "필수 개발 인력 채용",
            "제품 패키지 디자인",
            "홍보용 웹사이트 제작",
            "시제품 완성",
        ],
        6: [
            "< 1단계 정부지원사업비 집행계획 >",
            "※ 1단계 정부지원사업비는 20백만원 내외로 작성",
            "비 목",
            "산출 근거",
            "정부지원사업비(원)",
            "재료비",
            "외주용역비",
            "지급수수료",
            "합 계",
            "< 2단계 정부지원사업비 집행계획 >",
            "※ 2단계 정부지원사업비는 20백만원 내외로 작성",
        ],
    }

    for page_no, anchors in expected_page_anchors.items():
        pdf_text = pdf_page_texts[page_no]
        hdml_page_text = page_texts.get(page_no, "")
        assert pdf_text, f"empty pdf text on page {page_no}"
        assert hdml_page_text, f"empty hdml text on page {page_no}"

        for anchor in anchors:
            pdf_idx = pdf_text.find(anchor)
            hdml_idx = hdml_page_text.find(anchor)
            assert pdf_idx != -1, f"missing anchor in pdf page {page_no}: {anchor}"
            assert hdml_idx != -1, f"missing anchor in hdml page {page_no}: {anchor}"

    assert pdf_page_texts[4].count("◦") >= 2
    assert pdf_page_texts[4].count("-") >= 4
    assert page_texts[4].count("◦") >= 2
    assert page_texts[4].count("-") >= 4

    assert pdf_page_texts[5].count("◦") >= 2
    assert pdf_page_texts[5].count("-") >= 3
    assert page_texts[5].count("◦") >= 2
    assert page_texts[5].count("-") >= 3

    cross_page_anchors = [
        "□ 창업 아이템 개요(요약)",
        "1. 문제 인식(Problem)_창업 아이템의 필요성",
        "2. 실현 가능성(Solution)_창업 아이템의 개발 계획",
        "< 사업추진 일정(협약기간 내) >",
        "< 1단계 정부지원사업비 집행계획 >",
        "< 2단계 정부지원사업비 집행계획 >",
    ]
    last_idx = -1
    for anchor in cross_page_anchors:
        idx = hdml_text.find(anchor)
        assert idx != -1, f"missing cross-page anchor in hdml: {anchor}"
        assert idx >= last_idx, f"cross-page anchor order broke at {anchor}"
        last_idx = idx

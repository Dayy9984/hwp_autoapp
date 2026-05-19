"""
CVD 타입 태깅 유틸리티

LLM이 셀/문단/리스트 ID를 혼동하지 않도록 CVD 텍스트를 명시적 타입 태그로 변환합니다.
원본 CVD는 보존하고, LLM 입력용 문자열만 가공합니다.
"""

from __future__ import annotations

import re
from typing import Tuple

_TD_TAG_PATTERN = re.compile(r"<td\s+(\d+)([^>]*)>")
_LIST_PATTERN = re.compile(r"<list([^>]*)><(\d+)>")
_PARA_LINE_PATTERN = re.compile(r"^<(\d+)>(.*)$")
_TEXTBOX_PATTERN = re.compile(r"^<textbox\s+(\d+)[^>]*><(\d+)>(.*)$")
_FOOTNOTE_PATTERN = re.compile(r"^<각주(\d+)\s+(\d+)><(\d+)>(.*)$")
_IMAGE_PATTERN = re.compile(r"^<image\s+(\d+)>\s*$")
_TRAILING_TAG_PATTERN = re.compile(
    r"(</(?:td|list|table|textbox|footnote)>\s*)$", re.IGNORECASE
)


def _ensure_newline_after_td(match: re.Match) -> str:
    """<td ...> 뒤에 < 가 바로 오는 경우 줄바꿈을 보장한다."""
    end = match.end()
    if end < len(match.string) and match.string[end] == "\n":
        return match.group(1)
    return match.group(1) + "\n"


def _normalize_table_boundaries(cvd_text: str) -> str:
    """표 셀 경계를 줄바꿈으로 분리해 line-based 태깅 안정화."""
    if not cvd_text:
        return cvd_text

    # <td> 직후에 태그가 이어지면 줄바꿈 삽입
    cvd_text = re.sub(r"(<td[^>]*>)(?=\s*<)", _ensure_newline_after_td, cvd_text)
    # </td>, </list>는 항상 독립 라인으로 분리
    cvd_text = cvd_text.replace("</td>", "\n</td>")
    cvd_text = cvd_text.replace("</list>", "\n</list>")
    return cvd_text


def _split_trailing_tags(text: str) -> Tuple[str, str]:
    """문단 텍스트 뒤에 붙은 닫는 태그들을 분리한다."""
    if not text:
        return text, ""

    trailing = ""
    while True:
        match = _TRAILING_TAG_PATTERN.search(text)
        if not match:
            break
        trailing = match.group(1) + trailing
        text = text[: match.start()]
    return text, trailing


def _rewrite_td_tag(match: re.Match) -> str:
    td_id = match.group(1)
    attrs = match.group(2) or ""
    # 이미 id/data-id가 있으면 그대로 둔다
    if re.search(r"\b(?:id|data-id)\s*=", attrs):
        return f"<td{attrs}>"
    return f'<td id="{td_id}" data-type="td"{attrs}>'


def _rewrite_list_line(line: str) -> Tuple[str, bool]:
    match = _LIST_PATTERN.search(line)
    if not match:
        return line, False
    label = match.group(1).strip()
    list_id = match.group(2)
    label_attr = f' label="{label}"' if label else ""
    replacement = f'<list id="{list_id}" data-type="list"{label_attr}>'
    line = line[: match.start()] + replacement + line[match.end() :]
    line, trailing = _split_trailing_tags(line)
    if "</list>" not in line:
        line = line + "</list>"
    return line + trailing, True


def _rewrite_paragraph_line(line: str) -> Tuple[str, bool]:
    match = _PARA_LINE_PATTERN.match(line)
    if not match:
        return line, False
    para_id = match.group(1)
    text = match.group(2)
    text, trailing = _split_trailing_tags(text)
    return f'<p id="{para_id}" data-type="paragraph">{text}</p>{trailing}', True


def _rewrite_textbox_line(line: str) -> Tuple[str, bool]:
    match = _TEXTBOX_PATTERN.match(line)
    if not match:
        return line, False
    textbox_id = match.group(1)
    para_id = match.group(2)
    text = match.group(3)
    return (
        f'<textbox id="{textbox_id}" data-type="textbox">'
        f'<p id="{para_id}" data-type="paragraph">{text}</p></textbox>'
    ), True


def _rewrite_footnote_line(line: str) -> Tuple[str, bool]:
    match = _FOOTNOTE_PATTERN.match(line)
    if not match:
        return line, False
    index = match.group(1)
    anchor_id = match.group(2)
    para_id = match.group(3)
    text = match.group(4)
    return (
        f'<footnote index="{index}" anchor_id="{anchor_id}" data-type="footnote">'
        f'<p id="{para_id}" data-type="footnote_content">{text}</p></footnote>'
    ), True


def _rewrite_image_line(line: str) -> Tuple[str, bool]:
    match = _IMAGE_PATTERN.match(line)
    if not match:
        return line, False
    image_id = match.group(1)
    return f'<image id="{image_id}" data-type="image" />', True


def tag_cvd_for_llm(cvd_text: str) -> str:
    """LLM 입력용으로 CVD 텍스트의 ID 타입을 명시적으로 태깅한다."""
    if not cvd_text:
        return cvd_text

    cvd_text = _normalize_table_boundaries(cvd_text)
    output_lines = []
    for line in cvd_text.splitlines():
        updated = line

        # 1) td 태그 ID 명시
        updated = _TD_TAG_PATTERN.sub(_rewrite_td_tag, updated)

        # 2) 리스트 라인 변환
        updated, rewritten = _rewrite_list_line(updated)
        if rewritten:
            output_lines.append(updated)
            continue

        # 3) 텍스트박스 라인 변환
        updated, rewritten = _rewrite_textbox_line(updated)
        if rewritten:
            output_lines.append(updated)
            continue

        # 4) 각주 라인 변환
        updated, rewritten = _rewrite_footnote_line(updated)
        if rewritten:
            output_lines.append(updated)
            continue

        # 5) 이미지 라인 변환
        updated, rewritten = _rewrite_image_line(updated)
        if rewritten:
            output_lines.append(updated)
            continue

        # 6) 일반 문단 라인 변환
        updated, rewritten = _rewrite_paragraph_line(updated)
        if rewritten:
            output_lines.append(updated)
            continue

        output_lines.append(updated)

    return "\n".join(output_lines)

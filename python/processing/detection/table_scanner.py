"""
테이블 구조 스캔 및 HWPML 변환 유틸리티

HWP 문서의 테이블을 추출하고 HTML 형식으로 변환합니다.
레거시 convert_hwpml_to_html 및 clean_table_html 로직을 통합합니다.
"""

import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple


def extract_table_from_hwpml(hwpml_text: str) -> str:
    """
    HWPML XML에서 TABLE 구조를 최소 HTML로 변환
    (convert_hwpml_to_html → extract_table_from_hwpml, 내부 로직 유지하되 함수명 변경)

    Args:
        hwpml_text: HWPML2X 형식의 XML 문자열

    Returns:
        변환된 HTML 문자열
    """
    if not isinstance(hwpml_text, str) or not hwpml_text.strip():
        return ""

    try:
        root = ET.fromstring(hwpml_text)
    except Exception:
        return hwpml_text

    # 최상위 테이블만 추출
    all_tables: List[ET.Element] = list(root.iter("TABLE"))
    nested: set[ET.Element] = set()
    for t in all_tables:
        for child in t.findall(".//TABLE"):
            nested.add(child)
    top_level_tables: List[ET.Element] = [t for t in all_tables if t not in nested]

    def construct_table_html(table_elem: ET.Element) -> str:
        """재귀적으로 테이블 HTML 생성 (build_html_table → construct_table_html)"""
        parts: List[str] = ["<table>\n"]

        for row in table_elem.findall("ROW"):
            parts.append("  <tr>\n")
            for cell in row.findall("CELL"):
                attr_parts: List[str] = []
                col_span = cell.get("ColSpan")
                row_span = cell.get("RowSpan")
                if col_span and col_span.isdigit() and int(col_span) > 1:
                    attr_parts.append(f' colspan="{col_span}"')
                if row_span and row_span.isdigit() and int(row_span) > 1:
                    attr_parts.append(f' rowspan="{row_span}"')

                cell_contents: List[str] = []

                for para_list in cell.findall("PARALIST"):
                    for p in para_list.findall("P"):
                        for text in p.findall("TEXT"):
                            char_buf: List[str] = []
                            for child in list(text):
                                tag_name = child.tag
                                if tag_name == "CHAR":
                                    if child.text:
                                        char_buf.append(child.text)
                                elif tag_name == "TABLE":
                                    # 누적 텍스트 플러시
                                    p_text = "".join(char_buf).strip()
                                    if p_text:
                                        cell_contents.append(
                                            f'<p class="HStyle0">{p_text}</p>'
                                        )
                                    char_buf = []
                                    # 중첩 테이블 재귀 변환
                                    cell_contents.append(construct_table_html(child))
                            # TEXT 종료 시 잔여 텍스트 플러시
                            p_text = "".join(char_buf).strip()
                            if p_text:
                                cell_contents.append(f'<p class="HStyle0">{p_text}</p>')

                inner = "\n".join(cell_contents)
                parts.append(f"    <td{''.join(attr_parts)}>{inner}</td>\n")

            parts.append("  </tr>\n")

        parts.append("</table>")
        return "".join(parts)

    if not top_level_tables:
        return ""

    html_parts: List[str] = []
    for tbl in top_level_tables:
        html_parts.append(construct_table_html(tbl))

    return "".join(html_parts)


class TableStructureParser(HTMLParser):
    """
    HTML 테이블 구조 파싱
    (TableHTMLParser → TableStructureParser, 내부 로직은 유지)
    """

    def __init__(self):
        super().__init__()
        self.result = []
        self.table_stack = []
        self.row_stack = []
        self.cell_stack = []
        self.text_stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            new_table = {"tag": "table", "children": []}
            self.table_stack.append(new_table)

        elif tag == "tr" and self.table_stack:
            new_row = {"tag": "tr", "children": []}
            self.row_stack.append(new_row)

        elif tag == "td" and self.row_stack:
            attrs_dict = dict(attrs)
            cell_attrs = {}
            if "colspan" in attrs_dict:
                cell_attrs["colspan"] = attrs_dict["colspan"]
            if "rowspan" in attrs_dict:
                cell_attrs["rowspan"] = attrs_dict["rowspan"]
            new_cell = {
                "tag": "td",
                "attrs": cell_attrs,
                "text": "",
                "nested_tables": [],
            }
            self.cell_stack.append(new_cell)
            self.text_stack.append("")

        elif tag == "p" and self.cell_stack and self.text_stack:
            if self.text_stack[-1].strip():
                self.text_stack[-1] += "\n"

    def handle_endtag(self, tag):
        if tag == "table" and self.table_stack:
            completed_table = self.table_stack.pop()
            if not self.table_stack:
                self.result.append(completed_table)
            else:
                if self.cell_stack:
                    self.cell_stack[-1]["nested_tables"].append(completed_table)

        elif tag == "tr" and self.row_stack:
            completed_row = self.row_stack.pop()
            if self.table_stack:
                self.table_stack[-1]["children"].append(completed_row)

        elif tag == "td" and self.cell_stack:
            completed_cell = self.cell_stack.pop()
            if self.text_stack:
                completed_cell["text"] = self.text_stack.pop().strip()
            if self.row_stack:
                self.row_stack[-1]["children"].append(completed_cell)

    def handle_data(self, data):
        if self.cell_stack and self.text_stack:
            clean_data = data.strip()
            if clean_data:
                self.text_stack[-1] += clean_data


def refine_table_html(
    html_content: str,
    exclude_sections: List[int],
    between_elements: List[Dict[str, Any]],
    nested_table_items: List[Tuple[int, Tuple[int, int, int]]],
) -> Tuple[str, Tuple[int, int, int], List[Dict[str, Any]]]:
    """
    HTML 테이블을 정제하고 id 부여
    (clean_table_html → refine_table_html, 로직은 유지하되 함수명과 변수명 변경)

    Args:
        html_content: 원본 HTML
        exclude_sections: 제외할 section_idx 리스트
        between_elements: 요소 메타데이터
        nested_table_items: 중첩 테이블 정보

    Returns:
        (정제된 HTML, 마지막 셀 좌표, td 메타데이터 리스트)
    """
    parser = TableStructureParser()
    parser.feed(html_content)
    nested_coords_list = [coords for _, coords in nested_table_items]

    filtered_elements = [
        elem
        for elem in between_elements
        if elem.get("pos")[0] not in exclude_sections
    ]

    if len(filtered_elements) < 2:
        return html_content, (0, 0, 0), []

    start_section_id = filtered_elements[1].get("pos")[0]

    def build_refined_table(
        table_data: Dict[str, Any],
        td_metas: List[Dict[str, Any]],
        elements_data: List[Dict[str, Any]],
    ) -> Tuple[str]:
        """재귀적으로 정제된 테이블 HTML 생성"""
        nonlocal start_section_id
        result_parts: List[str] = ["<table>\n"]

        for row_num, row in enumerate(table_data.get("children", [])):
            result_parts.append("  <tr>\n")

            for cell_num, cell in enumerate(row.get("children", [])):
                attrs_str = ""
                if "colspan" in cell.get("attrs", {}):
                    attrs_str += f' colspan="{cell["attrs"]["colspan"]}"'
                if "rowspan" in cell.get("attrs", {}):
                    attrs_str += f' rowspan="{cell["attrs"]["rowspan"]}"'

                # start_section_id와 일치하는 요소들 찾기
                matching_elements = [
                    element
                    for element in elements_data
                    if element.get("pos")[0] == start_section_id
                ]

                start_section_id += 1
                while start_section_id in exclude_sections:
                    start_section_id += 1

                elements_in_cell = []
                if matching_elements:
                    if matching_elements:
                        first_idx = elements_data.index(matching_elements[0])
                        if len(matching_elements) >= 2:
                            last_idx = elements_data.index(matching_elements[-1])
                            elements_in_cell = elements_data[first_idx : last_idx + 1]
                        else:
                            elements_in_cell = [matching_elements[0]]

                    # 중첩 테이블 처리 (로직은 유지)
                    for nested_coords in nested_coords_list:
                        nested_section, nested_para, _ = nested_coords

                        matching_nested_indices = [
                            i
                            for i, element in enumerate(elements_in_cell)
                            if (
                                element.get("type", None) != "nested_table_elements"
                                and element.get("pos")[0] == nested_section
                                and element.get("pos")[1] == nested_para
                            )
                        ]

                        if matching_nested_indices:
                            if nested_coords in nested_coords_list:
                                nested_coords_list.remove(nested_coords)

                            first_idx = matching_nested_indices[0]
                            last_idx = matching_nested_indices[-1]

                            between_nested = elements_in_cell[first_idx + 1 : last_idx]

                            # 중첩 테이블 재귀 처리
                            nested_html = ""
                            for nested_tbl in cell.get("nested_tables", []):
                                nested_result, _, _ = build_refined_table(
                                    nested_tbl, td_metas, between_nested
                                )
                                nested_html += nested_result

                            # 중첩 테이블을 nested_table_elements로 표시
                            nested_marker = {
                                "type": "nested_table_elements",
                                "html": nested_html,
                            }
                            elements_in_cell = (
                                elements_in_cell[:first_idx]
                                + [nested_marker]
                                + elements_in_cell[last_idx + 1 :]
                            )

                # 셀 내용 생성
                cell_html_parts = []
                for elem in elements_in_cell:
                    if elem.get("type") == "nested_table_elements":
                        cell_html_parts.append(elem.get("html", ""))
                    else:
                        elem_id = elem.get("id")
                        elem_text = elem.get("text", "")
                        if elem_id:
                            cell_html_parts.append(f'<p id="{elem_id}">{elem_text}</p>')

                cell_inner = "\n".join(cell_html_parts)
                result_parts.append(f"    <td{attrs_str}>{cell_inner}</td>\n")

            result_parts.append("  </tr>\n")

        result_parts.append("</table>")
        return "".join(result_parts)

    if not parser.result:
        return "", (0, 0, 0), []

    td_metas: List[Dict[str, Any]] = []
    refined_html = build_refined_table(parser.result[0], td_metas, filtered_elements)

    # 마지막 셀 좌표 계산
    last_coords = (start_section_id - 1, 0, 0)

    return refined_html, last_coords, td_metas


def add_ids_to_table_cells(html_content: str, id_generator) -> str:
    """
    테이블 HTML의 td 태그에만 ID 부여
    (레거시 패턴: td에만 ID 부여, p 태그에는 ID 부여 안 함)

    Args:
        html_content: 기본 테이블 HTML
        id_generator: ID 생성 함수

    Returns:
        ID가 부여된 HTML
    """
    if not html_content or not html_content.strip():
        return html_content

    import re

    result = html_content

    # td 태그에만 ID 부여 (레거시 패턴)
    def replace_td(match):
        attrs = match.group(1)  # colspan, rowspan 등 기존 속성
        content = match.group(2)  # td 내부 내용

        # 새 ID 생성
        td_id = id_generator()

        # 속성이 있으면 공백 추가
        attrs_str = f" {attrs}" if attrs else ""
        # td에만 ID 부여, 내부 p 태그는 그대로 유지
        return f'<td id="{td_id}"{attrs_str}>{content}</td>'

    # 모든 td 태그에 ID 부여
    result = re.sub(
        r'<td([^>]*)>(.*?)</td>', replace_td, result, flags=re.DOTALL
    )

    return result


def scan_table_with_markup(hwp, id_generator, log_callback=None) -> Tuple[str, Tuple[int, int, int]]:
    """
    레거시 테이블 선택 + HWPML 추출 + ID 부여 패턴 통합
    
    **2022 이전 호환 규칙 적용:**
    - SetScreenUpdate 사용 금지
    - GetText() 사용 금지 → GetTextFile("UNICODE","") 사용
    - Cancel() 대신 HAction.Run("Cancel") 사용
    - 표 이동은 HAction.Run("TableColBegin") 등 사용

    Args:
        hwp: HWP COM 객체
        id_generator: ID 생성 함수
        log_callback: 로그 콜백 함수

    Returns:
        (ID가 부여된 HTML, 첫 셀 좌표)
    """
    if log_callback is None:
        log_callback = lambda msg, level=None: print(msg, file=sys.stderr)

    def _save_block_hwpml() -> Optional[str]:
        """SaveBlockAction으로 선택 블록을 HWPML2X로 저장 후 읽기."""
        tmp_path = None
        try:
            if not hasattr(hwp, "HAction") or not hasattr(hwp, "HParameterSet"):
                return None

            pset = hwp.HParameterSet.HFileSaveBlock
            hwp.HAction.GetDefault("SaveBlockAction", pset.HSet)

            fd, tmp_path = tempfile.mkstemp(suffix=".tmp")
            os.close(fd)

            pset.FileName = tmp_path
            pset.Format = "HWPML2X"
            if hasattr(pset, "Argument"):
                pset.Argument = "code:unicode;lock:false;"

            result = hwp.HAction.Execute("SaveBlockAction", pset.HSet)
            if not result:
                log_callback("SaveBlockAction 실패: result=False", "WARNING")
                return None

            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            log_callback(f"SaveBlockAction 실패: {e}", "WARNING")
            return None
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    try:
        # 1. 표 안에서 셀 블록 시작 (HAction 기반 - 2022 이전 호환)
        try:
            hwp.HAction.Run("TableCellBlock")
        except Exception as e:
            log_callback(f"HAction TableCellBlock 실패: {e}", "WARNING")
            # pyhwpx 폴백
            try:
                hwp.TableCellBlock()
            except:
                pass

        # 2. 표 첫 셀로 이동 (HAction 기반)
        try:
            hwp.HAction.Run("TableColBegin")
            hwp.HAction.Run("TableColPageUp")
        except Exception as e:
            log_callback(f"HAction TableColBegin 실패: {e}", "WARNING")
            # pyhwpx 폴백
            try:
                hwp.TableColBegin()
                hwp.TableColPageUp()
            except:
                pass

        # 3. 첫 셀 좌표 저장
        first_coords = (0, 0, 0)
        try:
            first_coords = hwp.get_pos()
        except:
            try:
                pos_set = hwp.GetPosBySet()
                if pos_set:
                    first_coords = (
                        pos_set.Item("List"),
                        pos_set.Item("Para"),
                        pos_set.Item("Pos")
                    )
            except:
                pass

        # 4. 표 전체 선택 (끝까지 확장)
        try:
            hwp.HAction.Run("TableColEnd")
            hwp.HAction.Run("TableColPageDown")
            hwp.HAction.Run("TableCellBlockExtend")
        except Exception as e:
            log_callback(f"표 전체 선택 실패: {e}", "WARNING")
            # pyhwpx 폴백
            try:
                hwp.TableColEnd()
                hwp.TableColPageDown()
                hwp.TableCellBlockExtend()
            except:
                pass

        # 5. HWPML2X로 선택 블록 추출 (SaveBlockAction 우선)
        xml_text = _save_block_hwpml()
        if xml_text is None:
            try:
                xml_text = hwp.GetTextFile("HWPML2X", option="saveblock")
            except Exception as e:
                log_callback(f"GetTextFile 호출 실패: {e}", "ERROR")

        # 6. 선택 해제 (HAction 기반 - 2022 이전 호환)
        try:
            hwp.HAction.Run("Cancel")
        except:
            pass

        if xml_text is None or (isinstance(xml_text, str) and xml_text.strip() == ""):
            log_callback("HWPML 추출 실패: xml_text가 비어있음", "WARNING")
            
            # 폴백: GetTextFile("UNICODE","")로 표 텍스트만 추출
            try:
                # 다시 표 선택 시도
                hwp.HAction.Run("TableCellBlock")
                hwp.HAction.Run("TableColBegin")
                hwp.HAction.Run("TableColPageUp")
                hwp.HAction.Run("TableColEnd")
                hwp.HAction.Run("TableColPageDown")
                hwp.HAction.Run("TableCellBlockExtend")
                
                # UNICODE로 텍스트 추출 (2022 이전 호환)
                text_content = hwp.GetTextFile("UNICODE", option="saveblock")
                
                # 선택 해제
                hwp.HAction.Run("Cancel")
                
                if text_content and text_content.strip():
                    cell_id = id_generator()
                    return f'<table><tr><td id="{cell_id}">{text_content.strip()}</td></tr></table>', first_coords
            except Exception as e:
                log_callback(f"폴백 텍스트 추출 실패: {e}", "ERROR")
            
            return "", (0, 0, 0)

        # 7. HWPML을 HTML로 변환
        html_from_xml = extract_table_from_hwpml(xml_text)

        # 8. td 태그에 ID 부여 (레거시 clean_table_html 로직)
        html_with_ids = add_ids_to_table_cells(html_from_xml, id_generator)

        return html_with_ids, first_coords

    except Exception as e:
        log_callback(f"테이블 스캔 실패: {e}", "ERROR")
        # 선택 해제 시도 (HAction 기반)
        try:
            hwp.HAction.Run("Cancel")
        except:
            pass
        return "", (0, 0, 0)

# -*- coding: utf-8 -*-
"""
CVD (Claude View Document) extractor for HWP documents.
"""

import os
import re
import sys
import json
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

from .hwp_raw_wrapper import HwpRawWrapper


class Element:
    id: int 
    type: str
    text: str
    pos: tuple[int, int, int]
    outline_level: Optional[str]


class OutlineStyleExtractor:
    """CSS 스타일 블록에서 개요 레벨(1-10) 매핑 추출기"""

    def __init__(self, html_content: str):
        self._html = html_content
        self._mappings = {}

    def extract_outline_classes(self) -> Dict[str, str]:
        """개요 레벨별 CSS 클래스 매핑 추출 (개요 1~10)"""
        if not self._html or not isinstance(self._html, str):
            return {}

        css_block = self._extract_css_block()
        if not css_block:
            return {}

        self._mappings = self._scan_css_for_outline_mappings(css_block)
        return self._mappings

    def _extract_css_block(self) -> str:
        """HTML에서 <style> 블록 추출"""
        match = re.search(r"<style[^>]*>(.*?)</style>", self._html, re.DOTALL | re.IGNORECASE)
        return match.group(1) if match else ""

    def _scan_css_for_outline_mappings(self, css_text: str) -> Dict[str, str]:
        """CSS 텍스트에서 '개요 N' → 클래스명 매핑 스캔"""
        mappings = {}
        for level in range(1, 11):
            outline_label = f"개요 {level}"
            class_name = self._find_class_for_outline(css_text, outline_label)
            if class_name:
                mappings[outline_label] = class_name
        return mappings

    def _find_class_for_outline(self, css_text: str, outline_label: str) -> Optional[str]:
        """특정 개요 레이블에 대한 CSS 클래스명 검색"""
        escaped_label = re.escape(outline_label)
        regex = rf'\.(\w+)\s*\{{[^}}]*?style-name:\s*["\']?{escaped_label}["\']?\s*;[^}}]*?\}}'
        match = re.search(regex, css_text, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else None


class HStyleTextExtractor(HTMLParser):
    """개요 스타일 클래스별 <p> 태그 텍스트 파싱"""

    def __init__(self, outline_classes: Dict[str, str]):
        super().__init__()
        self._outline_map = outline_classes
        self._target_class_set = set(outline_classes.values())
        self.result = {}
        self._parse_state = {"active_p_class": None, "buffer": "", "inside_p": False, "ignore_nested": False}

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            class_attr = dict(attrs).get("class", "")
            if class_attr in self._target_class_set:
                self._parse_state["active_p_class"] = class_attr
                self._parse_state["buffer"] = ""
                self._parse_state["inside_p"] = True
        elif self._parse_state["inside_p"]:
            self._parse_state["ignore_nested"] = True

    def handle_endtag(self, tag):
        if tag == "p" and self._parse_state["inside_p"]:
            text_content = self._parse_state["buffer"].strip()
            if text_content:
                level = self._reverse_lookup_outline_level(self._parse_state["active_p_class"])
                if level:
                    self.result[text_content] = level
            self._reset_parse_state()
        elif tag != "p":
            self._parse_state["ignore_nested"] = False

    def handle_data(self, data):
        if self._parse_state["inside_p"] and not self._parse_state["ignore_nested"]:
            stripped = data.strip()
            if stripped:
                self._parse_state["buffer"] += stripped

    def _reverse_lookup_outline_level(self, class_name: str) -> Optional[str]:
        """CSS 클래스명에서 개요 레벨 역조회"""
        for level, cls in self._outline_map.items():
            if cls == class_name:
                return level
        return None

    def _reset_parse_state(self):
        """파싱 상태 초기화"""
        self._parse_state = {"active_p_class": None, "buffer": "", "inside_p": False, "ignore_nested": False}


def extract_hstyle_text_mapping(html_content: str) -> Dict[str, str]:
    """개요 레벨(1-10)별 텍스트 매핑 추출 - Builder 패턴"""
    if not html_content or not isinstance(html_content, str):
        return {}

    css_mapper = OutlineStyleExtractor(html_content)
    level_to_class = css_mapper.extract_outline_classes()

    if not level_to_class:
        return {}

    parser = HStyleTextExtractor(level_to_class)
    parser.feed(html_content)

    return parser.result


class TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self._tables = []
        self._rows = []
        self._cells = []
        self._text_buffers = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            tbl = {"tag": "table", "children": []}
            self._tables.append(tbl)
        elif tag == "tr":
            if self._tables:
                row = {"tag": "tr", "children": []}
                self._rows.append(row)
        elif tag == "td":
            if self._rows:
                attr_map = dict(attrs)
                span_attrs = {}
                for span_type in ["colspan", "rowspan"]:
                    if span_type in attr_map:
                        span_attrs[span_type] = attr_map[span_type]
                cell = {"tag": "td", "attrs": span_attrs, "text": "", "nested_tables": []}
                self._cells.append(cell)
                self._text_buffers.append("")
        elif tag == "p":
            if self._cells and self._text_buffers:
                if self._text_buffers[-1].strip():
                    self._text_buffers[-1] += "\n"

    def handle_endtag(self, tag):
        if tag == "table":
            if self._tables:
                completed_tbl = self._tables.pop()
                target = self.result if not self._tables else (self._cells[-1]["nested_tables"] if self._cells else [])
                target.append(completed_tbl)
        elif tag == "tr":
            if self._rows:
                completed_row = self._rows.pop()
                if self._tables:
                    self._tables[-1]["children"].append(completed_row)
        elif tag == "td":
            if self._cells:
                completed_cell = self._cells.pop()
                if self._text_buffers:
                    completed_cell["text"] = self._text_buffers.pop().strip()
                if self._rows:
                    self._rows[-1]["children"].append(completed_cell)

    def handle_data(self, data):
        if self._cells and self._text_buffers:
            stripped = data.strip()
            if stripped:
                self._text_buffers[-1] += stripped


def convert_hwpml_to_html(hwpml_text: str) -> str:
    """HWPML → HTML 변환 (Strategy + Template Method + Filter 패턴)"""
    from typing import Optional as Opt, List as ListType
    from abc import ABC, abstractmethod

    class InputValidator(ABC):
        """입력 검증기 (추상)"""
        @abstractmethod
        def validate(self, text: str) -> Opt[str]:
            """검증 실행, 실패 시 None 또는 기본값 반환"""
            pass

    class TypeValidator(InputValidator):
        """타입 검증"""
        def validate(self, text: str) -> Opt[str]:
            if not isinstance(text, str):
                return ""
            return text

    class EmptyValidator(InputValidator):
        """공백 검증"""
        def validate(self, text: str) -> Opt[str]:
            if not text.strip():
                return ""
            return text

    class XMLParser:
        """XML 파서 (Strategy)"""
        @staticmethod
        def parse(xml_text: str) -> Opt:
            """XML 파싱, 실패 시 None 반환 (fallback 처리는 호출자에서)"""
            try:
                return ET.fromstring(xml_text)
            except Exception:
                return None

    class TopLevelTableFilter:
        """최상위 테이블 필터"""
        @staticmethod
        def filter_top_level(root) -> ListType:
            """중첩이 아닌 최상위 테이블만 선택"""
            all_tables = list(root.iter("TABLE"))
            nested = set()

            for table in all_tables:
                for child in table.findall(".//TABLE"):
                    nested.add(child)

            return [t for t in all_tables if t not in nested]

    class ConversionPipeline:
        """변환 파이프라인 (Template Method)"""
        def __init__(self):
            self.validators = [TypeValidator(), EmptyValidator()]
            self.parser = XMLParser()
            self.filter = TopLevelTableFilter()

        def execute(self, hwpml_text: str, builder_func) -> str:
            """파이프라인 실행 """
            vd = self._validate(hwpml_text)
            if vd is not None: return vd
            rt = self.parser.parse(hwpml_text)
            if rt is None: return hwpml_text
            tls = self.filter.filter_top_level(rt)
            return "" if not tls else self._convert_tables(tls, builder_func)

        def _validate(self, text: str) -> Opt[str]:
            """검증 체인 실행"""
            for validator in self.validators:
                result = validator.validate(text)
                if result is not None and result == "":
                    return ""
            return None  # 검증 통과

        @staticmethod
        def _convert_tables(tables: ListType, builder_func) -> str:
            """테이블 리스트를 HTML로 변환"""
            html_parts = [builder_func(tbl) for tbl in tables]
            return "".join(html_parts)

    def build_html_table(table_elem: ET.Element) -> str:
        """HTML 테이블 빌드 (Builder + Strategy + State 패턴)"""
        from dataclasses import dataclass, field
        from typing import List as ListType, Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class TextAccumulator:
            """텍스트 축적기 (State 패턴)"""
            buffer: ListType[str] = field(default_factory=list)

            def append(self, text: str) -> None:
                self.buffer.append(text)

            def flush(self) -> Opt[str]:
                result = "".join(self.buffer).strip()
                self.buffer.clear()
                return result if result else None

        class AttributeBuilder:
            """셀 속성 빌더"""
            @staticmethod
            def build_span_attrs(cell_elem: ET.Element) -> str:
                attrs = []
                col_span = cell_elem.get("ColSpan")
                row_span = cell_elem.get("RowSpan")

                if col_span and col_span.isdigit() and int(col_span) > 1:
                    attrs.append(f' colspan="{col_span}"')
                if row_span and row_span.isdigit() and int(row_span) > 1:
                    attrs.append(f' rowspan="{row_span}"')

                return "".join(attrs)

        class ContentProcessor(ABC):
            """콘텐츠 처리기 (추상)"""
            @abstractmethod
            def can_process(self, element: ET.Element) -> bool:
                pass

            @abstractmethod
            def process(self, element: ET.Element, accumulator: TextAccumulator) -> Opt[str]:
                pass

        class CharProcessor(ContentProcessor):
            """CHAR 태그 처리"""
            def can_process(self, element: ET.Element) -> bool:
                return element.tag == "CHAR"

            def process(self, element: ET.Element, accumulator: TextAccumulator) -> Opt[str]:
                if element.text:
                    accumulator.append(element.text)
                return None

        class TableProcessor(ContentProcessor):
            """TABLE 태그 처리 (재귀)"""
            def can_process(self, element: ET.Element) -> bool:
                return element.tag == "TABLE"

            def process(self, element: ET.Element, accumulator: TextAccumulator) -> Opt[str]:
                # 누적 텍스트 flush
                flushed = accumulator.flush()
                result = []
                if flushed:
                    result.append(f'<p class="HStyle0">{flushed}</p>')
                # 중첩 테이블 재귀 빌드
                result.append(build_html_table(element))
                return "\n".join(result) if result else None

        class CellContentBuilder:
            """셀 콘텐츠 빌더"""
            def __init__(self):
                self.processors = [CharProcessor(), TableProcessor()]

            def build(self, cell_elem: ET.Element) -> str:
                """셀 빌드 """
                cts = []
                for pl in cell_elem.findall("PARALIST"):
                    for p in pl.findall("P"):
                        for tx in p.findall("TEXT"):
                            acc = TextAccumulator()
                            for ch in list(tx):
                                for prc in self.processors:
                                    if prc.can_process(ch):
                                        (res := prc.process(ch, acc)) and cts.append(res)
                                        break
                            (fl := acc.flush()) and cts.append(f'<p class="HStyle0">{fl}</p>')
                return "\n".join(cts)

        class CellBuilder:
            """셀 빌더"""
            def __init__(self):
                self.content_builder = CellContentBuilder()
                self.attr_builder = AttributeBuilder()

            def build(self, cell_elem: ET.Element) -> str:
                attrs = self.attr_builder.build_span_attrs(cell_elem)
                inner = self.content_builder.build(cell_elem)
                return f"    <td{attrs}>{inner}</td>\n"

        class RowBuilder:
            """행 빌더"""
            def __init__(self):
                self.cell_builder = CellBuilder()

            def build(self, row_elem: ET.Element) -> str:
                cells = [self.cell_builder.build(cell) for cell in row_elem.findall("CELL")]
                return "  <tr>\n" + "".join(cells) + "  </tr>\n"

        class TableBuilder:
            """테이블 빌더 (Template Method)"""
            def __init__(self):
                self.row_builder = RowBuilder()

            def build(self, table_elem: ET.Element) -> str:
                rows = [self.row_builder.build(row) for row in table_elem.findall("ROW")]
                return "<table>\n" + "".join(rows) + "</table>"

        # Main execution
        builder = TableBuilder()
        return builder.build(table_elem)

    # 메인 실행 - 파이프라인 사용
    pipeline = ConversionPipeline()
    return pipeline.execute(hwpml_text, build_html_table)


def clean_table_html(
    html_content: str,
    should_exclude_list_pos: List[int],
    between_elements: List[Dict[str, Any]],
    nested_table_items: List[Tuple[int, Tuple[int, int, int]]],
) -> Tuple[str, Tuple[int, int, int], List[Dict[str, Any]]]:
    """HTML 정제 (Builder + State + Strategy 패턴 리팩토링)"""
    from dataclasses import dataclass, field
    from copy import copy

    # === Dataclasses ===

    @dataclass
    class BuildState:
        """빌드 상태 (nonlocal 제거)"""
        current_list_id: int
        exclude_list_pos: List[int]
        nested_table_positions: List[Tuple[int, int, int]]  # 복사본

        def advance_list_id(self):
            """list_id 전진 (exclude 건너뛰기)"""
            self.current_list_id += 1
            while self.current_list_id in self.exclude_list_pos:
                self.current_list_id += 1

        def try_remove_nested_pos(self, pos: Tuple[int, int, int]) -> bool:
            """중첩 테이블 위치 제거 시도 (존재하면 제거 후 True 반환)"""
            if pos in self.nested_table_positions:
                self.nested_table_positions.remove(pos)
                return True
            return False

    @dataclass
    class CellElementsContext:
        """셀 요소 컨텍스트"""
        matching_elements: List[Dict]
        between_elements_data: List[Dict]

        def extract_elements_in_cell(self) -> List[Dict]:
            """셀 내부 요소 추출"""
            if not self.matching_elements:
                return []

            first_idx = self.between_elements_data.index(self.matching_elements[0])
            if len(self.matching_elements) >= 2:
                last_idx = self.between_elements_data.index(self.matching_elements[-1])
                return self.between_elements_data[first_idx:last_idx + 1]
            return [self.matching_elements[0]]

    # === Strategy Pattern: Element Meta Builders ===

    class ElementMetaBuilder:
        """요소 메타 빌더 (추상 역할)"""
        @staticmethod
        def build_paragraph_meta(element: Dict) -> Dict:
            """문단 메타"""
            return {
                "type": "paragraph",
                "id": None,
                "pos": element.get("pos"),
                "text": element.get("text"),
            }

        @staticmethod
        def build_outline_meta(element: Dict) -> Dict:
            """개요 메타"""
            return {
                "type": "outline",
                "id": None,
                "pos": element.get("pos"),
                "text": element.get("text"),
                "outline_level": element.get("outline_level"),
            }

        @staticmethod
        def build_list_meta(element: Dict) -> Dict:
            """리스트 메타"""
            return {
                "type": "list",
                "id": None,
                "pos": element.get("pos"),
                "text": element.get("text"),
                "heading_text": element.get("heading_text"),
            }

        @staticmethod
        def build_nested_table_meta(pos: Tuple, html: str) -> Dict:
            """중첩 테이블 메타"""
            return {
                "type": "nested_table",
                "id": None,
                "pos": pos,
                "html": html,
            }

    # === Nested Table Processor ===

    class NestedTableProcessor:
        """중첩 테이블 처리기 (in-place 수정)"""
        def __init__(self, state: BuildState):
            self.state = state

        def process_elements(self, elements_in_cell: List[Dict]) -> List[Dict]:
            """중첩 테이블 요소 처리 (백업 버전 로직)"""
            for nested_pos in copy(self.state.nested_table_positions):
                nested_list_id, nested_para_id, _ = nested_pos

                # 같은 list_id와 para_id를 가진 요소 찾기
                matching_indices = [
                    i
                    for i, element in enumerate(elements_in_cell)
                    if (
                        element.get("type") != "nested_table_elements"
                        and element.get("pos", [None, None])[0] == nested_list_id
                        and element.get("pos", [None, None, None])[1] == nested_para_id
                    )
                ]

                if matching_indices:
                    self.state.try_remove_nested_pos(nested_pos)

                    first_idx = matching_indices[0]
                    last_idx = matching_indices[-1]

                    # first와 last 사이의 요소 추출 (first, last 제외)
                    between_nested = elements_in_cell[first_idx + 1:last_idx]

                    # 중첩 테이블 요소 생성
                    nested_elem = {
                        "type": "nested_table_elements",
                        "first_element": elements_in_cell[first_idx],
                        "last_element": elements_in_cell[last_idx],
                        "nested_pos": nested_pos,
                        "between_elements": between_nested,
                    }

                    # first_idx부터 last_idx까지 삭제하고 새 요소 삽입
                    del elements_in_cell[first_idx:last_idx + 1]
                    elements_in_cell.insert(first_idx, nested_elem)

            return elements_in_cell

    # === Cell Builder ===

    class CellBuilder:
        """셀 빌더"""
        def __init__(self, state: BuildState, meta_builder: ElementMetaBuilder):
            self.state = state
            self.meta_builder = meta_builder
            self.nested_processor = NestedTableProcessor(state)

        def build_cell(
            self,
            cell: Dict,
            between_elements_data: List[Dict],
            row_num: int,
            cell_num: int,
            table_path: Tuple[int, ...],
            td_metas: List[Dict],
            recursion_func: callable,
        ) -> str:
            """셀 HTML 구축"""
            attrs = self._build_attributes(cell)

            # 요소 수집
            matching_elements = [
                el for el in between_elements_data
                if el.get("pos", [None])[0] == self.state.current_list_id
            ]
            self.state.advance_list_id()

            # 셀 내부 요소 추출
            ctx = CellElementsContext(matching_elements, between_elements_data)
            elements_in_cell = ctx.extract_elements_in_cell()

            # 중첩 테이블 처리
            elements_in_cell = self.nested_processor.process_elements(elements_in_cell)

            # 메타 생성
            inner_metas = self._build_inner_metas(
                elements_in_cell,
                cell.get("nested_tables", []),
                td_metas,
                recursion_func,
                table_path,
                row_num,
                cell_num,
            )

            # TD 메타 추가
            if inner_metas:
                td_metas.append({
                    "type": "td",
                    "id": None,
                    "pos": inner_metas[0].get("pos"),
                    "inner_metas": inner_metas,
                    "row": row_num,
                    "col": cell_num,
                    "rowspan": int(cell.get("attrs", {}).get("rowspan") or 1),
                    "colspan": int(cell.get("attrs", {}).get("colspan") or 1),
                    "table_path": table_path,
                })

            # 셀 텍스트 생성
            cell_text = self._build_cell_text(inner_metas)

            return f"    <td{attrs}>{cell_text}</td>\n"

        def _build_attributes(self, cell: Dict) -> str:
            """셀 속성 구축"""
            attrs = []
            cell_attrs = cell.get("attrs", {})
            if "colspan" in cell_attrs:
                attrs.append(f'colspan="{cell_attrs["colspan"]}"')
            if "rowspan" in cell_attrs:
                attrs.append(f'rowspan="{cell_attrs["rowspan"]}"')
            return " " + " ".join(attrs) if attrs else ""

        def _build_inner_metas(
            self,
            elements: List[Dict],
            nested_tables: List,
            td_metas: List,
            recursion_func: callable,
            table_path: Tuple[int, ...],
            row_num: int,
            cell_num: int,
        ) -> List[Dict]:
            """내부 메타 구축 """
            ims, ni = [], 0
            for el in elements:
                if (et := el.get("type")) == "nested_table_elements":
                    (fe := el.get("first_element")) and fe.get("text", "") and ims.append(self.meta_builder.build_paragraph_meta(fe))
                    if ni < len(nested_tables):
                        nested_table_path = table_path + (row_num, cell_num, ni)
                        nested_html = recursion_func(
                            nested_tables[ni],
                            td_metas,
                            el.get("between_elements", []),
                            nested_table_path,
                        )
                        nested_meta = self.meta_builder.build_nested_table_meta(
                            el.get("nested_pos"),
                            nested_html,
                        )
                        nested_meta["table_path"] = nested_table_path
                        ims.append(nested_meta)
                    (le := el.get("last_element")) and le.get("text", "") and ims.append(self.meta_builder.build_paragraph_meta(le))
                    ni += 1
                elif et == "outline": ims.append(self.meta_builder.build_outline_meta(el))
                elif et == "list": ims.append(self.meta_builder.build_list_meta(el))
                else: ims.append(self.meta_builder.build_paragraph_meta(el))
            return ims

        def _build_cell_text(self, inner_metas: List[Dict]) -> str:
            """셀 텍스트 구축"""
            contents = []
            for meta in inner_metas:
                if meta.get("type") == "paragraph":
                    contents.append(meta.get("text", ""))
                elif meta.get("type") == "nested_table":
                    contents.append(meta.get("html", ""))
            return "\n".join(contents)

    # === Table Builder (재귀) ===

    class TableBuilder:
        """테이블 빌더 (재귀 처리)"""
        def __init__(self, state: BuildState):
            self.state = state
            self.meta_builder = ElementMetaBuilder()
            self.cell_builder = CellBuilder(state, self.meta_builder)

        def build(
            self,
            table_data: Dict,
            td_metas: List[Dict],
            between_elements_data: List[Dict],
            table_path: Tuple[int, ...],
        ) -> str:
            """테이블 HTML 재귀 빌드 """
            return (
                "<table>\n"
                + "".join(
                    f"  <tr>\n{''.join(self.cell_builder.build_cell(c, between_elements_data, rn, cn, table_path, td_metas, self.build) for cn, c in enumerate(r.get('children', [])))}  </tr>\n"
                    for rn, r in enumerate(table_data.get("children", []))
                )
                + "</table>"
            )

    # === Main execution ===

    parser = TableHTMLParser()
    parser.feed(html_content)

    # Filter elements
    filtered_between_elements = [
        el for el in between_elements
        if el.get("pos", [None])[0] not in should_exclude_list_pos
    ]

    if len(filtered_between_elements) < 2:
        return html_content, (0, 0, 0), []

    # Initialize state
    start_list_id = filtered_between_elements[1].get("pos")[0]
    nested_table_pos_list = [pos for _, pos in nested_table_items]

    state = BuildState(
        current_list_id=start_list_id,
        exclude_list_pos=should_exclude_list_pos,
        nested_table_positions=nested_table_pos_list,
    )

    # Build tables
    td_metas: List[Dict[str, Any]] = []
    table_builder = TableBuilder(state)
    html_parts = [
        table_builder.build(table, td_metas, filtered_between_elements, (table_index,))
        for table_index, table in enumerate(parser.result)
    ]

    html = "".join(html_parts)
    last_pos = filtered_between_elements[-2].get("pos")
    return html, last_pos, td_metas


class CVDExtractor:
    """CVD 문서 추출기 - 구조 유지 추출 (표/텍스트박스/일반텍스트)"""

    def __init__(self, hwp: HwpRawWrapper, log_to_main: Callable[[str, str], None] = None):
        self.hwp = hwp if hasattr(hwp, "init_scan") else (hwp.hwp if hasattr(hwp, "hwp") and hasattr(hwp.hwp, "init_scan") else hwp)
        self.global_id = 1
        _dl = lambda lv, msg: print(f"[{lv}] {msg}", file=sys.stderr)
        if not log_to_main: self.log_to_main = lambda msg, lv="INFO": _dl(lv, msg)
        else:
            def _lg(msg: str, lv: str = "INFO"):
                try: return log_to_main(lv, msg)
                except TypeError: return log_to_main(msg, lv)
            self.log_to_main = _lg
        self.extracted_elements, self.id_to_pos, self.pos_to_shape, self.cell_info_map, self.cell_style_map = [], {}, {}, {}, {}
        self.pos_to_page: Dict[Tuple[int, int, int], int] = {}

    def _save_cvd_markdown(self, cvd_text: str) -> None:
        """CVD 마크다운 저장 """
        try:
            ed = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "experiments")); os.path.exists(ed) or os.makedirs(ed)
            fp = os.path.join(ed, "document_cvd.md"); mps = ["# Document CVD", f"\n**Timestamp**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "\n## CVD (Raw)\n", "```\n" + (cvd_text or "") + "\n```"]
            open(fp, "w", encoding="utf-8").write("\n".join(mps)); self.log_to_main(f"CVD markdown saved: {fp}")
        except Exception as e: self.log_to_main(f"Failed to save CVD markdown: {e}", "ERROR")

    def _map_id_to_pos(self, pos: Tuple[int, int, int]) -> int | None:
        """ID 매핑 """
        if self._validate_pos(pos):
            mid = self.global_id
            self.global_id += 1
            self.id_to_pos[mid] = pos
            return mid
        return None

    def _get_style_attrs_from_pos(self, pos: Tuple[int, int, int]) -> str:
        """스타일 속성 문자열 생성 """
        # pos_to_shape는 tuple key로 저장되어 있으므로 tuple로 변환하여 조회
        pos_key = tuple(pos) if isinstance(pos, list) else pos
        ats = []
        page_no = self.pos_to_page.get(pos_key)
        if isinstance(page_no, int) and page_no > 0:
            ats.append(f'data-page="{page_no}"')
            ats.append(f'data-page-no="{page_no}"')
        sh = self.pos_to_shape.get(pos_key)
        cs = sh.get("char_shape") if isinstance(sh, dict) else None
        try: tc = cs.Item("TextColor") if cs else 0; tc and ats.append(f'color="#{tc&0xFF:02x}{(tc>>8)&0xFF:02x}{(tc>>16)&0xFF:02x}"')
        except: pass
        try: h = cs.Item("Height") if cs else 1000; sv = h / 100; ats.append(f'font-size="{int(sv) if sv.is_integer() else sv}pt"')
        except: pass
        return (" " + " ".join(ats)) if ats else ""

    def _style_snapshot_from_pos(self, pos: Tuple[int, int, int]) -> Dict[str, Any]:
        """시그니처 생성을 위한 최소 스타일 스냅샷 추출"""
        pos_key = tuple(pos) if isinstance(pos, list) else pos
        shape = self.pos_to_shape.get(pos_key)
        if not shape:
            return {}

        char_shape = shape.get("char_shape")
        para_shape = shape.get("para_shape")
        snapshot: Dict[str, Any] = {}

        try:
            if char_shape:
                snapshot["text_color"] = int(char_shape.Item("TextColor"))
        except Exception:
            pass

        try:
            if char_shape:
                snapshot["font_height"] = int(char_shape.Item("Height"))
        except Exception:
            pass

        try:
            if char_shape:
                snapshot["bold"] = int(char_shape.Item("Bold"))
        except Exception:
            pass

        try:
            if char_shape:
                snapshot["italic"] = int(char_shape.Item("Italic"))
        except Exception:
            pass

        try:
            if para_shape:
                snapshot["align"] = int(para_shape.Item("AlignType"))
        except Exception:
            pass

        return snapshot

    def _stable_signature(self, payload: Dict[str, Any], prefix: str) -> str:
        """안정적인 짧은 시그니처 생성"""
        compact = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(compact.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}:{digest}"

    def _build_td_signature(self, table_index: int, meta: Dict[str, Any]) -> str:
        """셀 단위 구조+스타일 시그니처 생성"""
        row = meta.get("row")
        col = meta.get("col")
        pos = meta.get("pos")
        span = {
            "rowspan": int(meta.get("rowspan") or 1),
            "colspan": int(meta.get("colspan") or 1),
        }
        cell_style = {}
        if row is not None and col is not None:
            cell_style = self.cell_style_map.get((table_index, row, col), {}) or {}

        payload = {
            "table": int(table_index),
            "row": int(row) if row is not None else None,
            "col": int(col) if col is not None else None,
            "span": span,
            "cell_style": {
                "bgcolor": cell_style.get("bgcolor"),
                "border_sides": ",".join(cell_style.get("border_sides") or []),
                "border_complete": bool(cell_style.get("border_complete")),
                "diagonal": bool(cell_style.get("diagonal")),
                "diagonal_flags": ",".join(cell_style.get("diagonal_flags") or []),
            },
            "style": self._style_snapshot_from_pos(pos) if self._validate_pos(pos) else {},
        }
        return self._stable_signature(payload, "tdv1")

    def _build_paragraph_signature(self, pos: Tuple[int, int, int], text: str) -> str:
        """문단 단위 글자스타일+텍스트 패턴 시그니처 생성"""
        text = text or ""
        normalized = " ".join(text.split())
        payload = {
            "style": self._style_snapshot_from_pos(pos) if self._validate_pos(pos) else {},
            "len": len(normalized),
            "starts_bullet": bool(re.match(r"^[-•·○□■◆▪▫▶➤]\s+", normalized)),
            "has_colon": ":" in normalized,
            "prefix": normalized[:24],
        }
        return self._stable_signature(payload, "pv1")

    def _go_to_page(self, hwp: HwpRawWrapper, page: int):
        """HWP 문서 특정 페이지로 이동"""
        try:
            hwp.goto_page(page)
        except Exception as err:
            self.log_to_main(f"Page navigation failed: page={page}, error={err}", "ERROR")

    def _move_to_page_start(self, hwp: HwpRawWrapper, page: int) -> Optional[Tuple[int, int, int]]:
        """페이지 시작으로 이동 """
        try: hwp.goto_page(page)
        except Exception as e: self.log_to_main(f"Failed to go to page: {page}, {e}", "ERROR"); return None
        try: (hwp.MovePageBegin() if hasattr(hwp, "MovePageBegin") else hwp.HAction.Run("MovePageBegin") if hasattr(hwp, "HAction") else None)
        except: pass
        try: cp = hwp.current_page
        except: cp = None
        if cp == page:
            lp, sg = hwp.get_pos(), 0
            while sg < 2000:
                sg += 1
                try: hwp.move_pos(22)
                except: break
                try:
                    if hwp.current_page != page: (hwp.set_pos(*lp) if lp and hasattr(hwp, "set_pos") else hwp.move_pos(23)); break
                    lp = hwp.get_pos()
                except: break
        try: hwp.HAction.Run("MoveParaBegin") if hasattr(hwp, "HAction") else None
        except: pass
        return hwp.get_pos()

    def _page_down(self, hwp: HwpRawWrapper, start_page: int, end_page: int):
        """페이지 다운 """
        tp = hwp.PageCount
        ep = min(end_page, tp)
        pdcl = ep - start_page + 1
        if pdcl < 1:
            self.log_to_main(f"page_down_count_left: {pdcl}", "ERROR")
            return
        sg = 0
        while pdcl > 0:
            if hwp.MoveSelPageDown(): pdcl -= 1
            else:
                hwp.MoveSelNextParaBegin()
                cp = hwp.current_page
                if cp == tp: break
                pdcl = ep - cp + 1
                if pdcl < 1: break
            sg += 1
            if sg > tp * 10: self.log_to_main("page down safety break", "ERROR"); break
        if hwp.current_page == tp:
            for _ in range(100):
                if not hwp.MoveSelLineDown():
                    for _ in range(100):
                        if not hwp.MoveSelRight(): break
                    break

    def _validate_pos(self, pos: Tuple[int, int, int]) -> bool:
        """위치 검증"""
        return isinstance(pos, tuple) and len(pos) >= 3
 
    def _strip_ns(self, tag: str) -> str:
        """네임스페이스 제거"""
        return (tag.split("}", 1)[-1] if "}" in tag else tag) if isinstance(tag, str) else ""

    def _get_attr_case_insensitive(self, elem: ET.Element, keys: List[str]) -> Optional[str]:
        """대소문자 무관 속성 추출"""
        if elem is None or not keys: return None
        al = {k.lower(): v for k, v in elem.attrib.items()}
        for k in keys:
            v = al.get(k.lower())
            if v is not None: return v
        return None

    def _parse_color_value(self, value: str) -> Optional[str]:
        """색상 값 파싱 """
        if not value: return None
        rw, lw = str(value).strip(), str(value).strip().lower()
        if not rw or lw in ("none", "null", "transparent"): return None
        if lw.startswith("#"):
            hs = lw[1:]
            return ("#" + "".join(c * 2 for c in hs)) if len(hs) == 3 and all(c in "0123456789abcdef" for c in hs) else (f"#{hs}" if len(hs) == 6 and all(c in "0123456789abcdef" for c in hs) else None)
        if "," in rw:
            pts = [p.strip() for p in rw.split(",")]
            if len(pts) == 3 and all(p.isdigit() for p in pts): return f"#{int(pts[0]):02x}{int(pts[1]):02x}{int(pts[2]):02x}"
        try: nm = int(rw, 0) & 0xFFFFFF; return f"#{nm&0xFF:02x}{(nm>>8)&0xFF:02x}{(nm>>16)&0xFF:02x}"
        except: return None

    def _extract_color_from_elem(self, elem: ET.Element) -> Optional[str]:
        if elem is None:
            return None
        for key, val in elem.attrib.items():
            key_lower = key.lower()
            if key_lower in (
                "fillcolor",
                "backcolor",
                "color",
                "colorref",
                "rgb",
                "argb",
            ):
                parsed = self._parse_color_value(val)
                if parsed:
                    return parsed
        tag_lower = self._strip_ns(elem.tag).lower()
        if tag_lower in ("color", "colorref", "fillcolor", "backcolor") and elem.text:
            parsed = self._parse_color_value(elem.text)
            if parsed:
                return parsed
        return None

    def _extract_fill_color_from_borderfill(self, borderfill_elem: ET.Element) -> Optional[str]:
        """채우기 색상 추출 """
        if borderfill_elem is None: return None
        for em in borderfill_elem.iter():
            if ("fillbrush" in (tl := self._strip_ns(em.tag).lower()) or tl.startswith("fill")) and (cl := self._extract_color_from_elem(em)): return cl
        for em in borderfill_elem.iter():
            if (("background" in (tl := self._strip_ns(em.tag).lower()) or tl in ("bgcolor", "backcolor")) and (cl := self._extract_color_from_elem(em))): return cl
        return None

    def _is_truthy_diagonal_attr(self, value: Any) -> bool:
        """대각선 관련 속성의 truthy 여부를 보수적으로 판정."""
        if value is None:
            return False
        text = str(value).strip().lower()
        if not text:
            return False
        if text in {"0", "false", "none", "null", "off", "no"}:
            return False
        try:
            return float(text) > 0
        except Exception:
            return text in {"1", "true", "on", "yes", "t"}

    def _extract_diagonal_flags_from_borderfill(self, borderfill_elem: ET.Element) -> List[str]:
        """대각선/사선 계열 플래그 추출 (구버전 HWPML 호환)."""
        if borderfill_elem is None:
            return []

        token_map = {
            "counterslash": "counter_slash",
            "counterbackslash": "counter_backslash",
            "backslash": "backslash",
            "slash": "slash",
            "diagonal": "diagonal",
            "crookedslash": "crooked_slash",
        }

        flags: set[str] = set()
        for em in borderfill_elem.iter():
            tag_lower = self._strip_ns(em.tag).lower()
            attr_map = {str(k).lower(): str(v) for k, v in getattr(em, "attrib", {}).items()}

            for token, flag_name in token_map.items():
                if token in tag_lower and (
                    self._border_elem_has_line(em)
                    or any(self._is_truthy_diagonal_attr(v) for v in attr_map.values())
                ):
                    flags.add(flag_name)

            for key, value in attr_map.items():
                for token, flag_name in token_map.items():
                    if token in key and self._is_truthy_diagonal_attr(value):
                        flags.add(flag_name)

        return sorted(flags)

    def _border_elem_has_line(self, elem: ET.Element) -> bool:
        """테두리 라인 존재 확인 """
        if elem is None: return False
        ats = {k.lower(): v for k, v in elem.attrib.items()}
        for k, v in ats.items():
            if "width" in k or "thick" in k:
                try:
                    if float(str(v)) <= 0: return False
                except ValueError: pass
            if "type" in k or "style" in k:
                if str(v).strip().lower() in ("0", "none", "null", "false"): return False
        return True

    def _extract_border_sides_from_borderfill(self, borderfill_elem: ET.Element) -> Tuple[List[str], bool]:
        """테두리 측면 추출 """
        if borderfill_elem is None: return [], False
        sd = {"left": None, "right": None, "top": None, "bottom": None}
        for em in borderfill_elem.iter():
            if "border" not in (tl := self._strip_ns(em.tag).lower()) and "line" not in tl: continue
            (s := next((k for k in sd.keys() if k in tl), None)) and sd[s] is None and (sd.__setitem__(s, self._border_elem_has_line(em)) or True)
        return [s for s in ("left", "right", "top", "bottom") if sd[s]], all(v is not None for v in sd.values())

    def _extract_borderfill_map(self, root: ET.Element) -> Dict[str, Dict[str, Any]]:
        """테두리채우기 맵 추출 """
        if root is None: return {}
        bfs = {}
        for em in root.iter():
            if self._strip_ns(em.tag).lower() != "borderfill" or not (bid := self._get_attr_case_insensitive(em, ["id", "borderfill", "borderfillid", "borderfill_id"])): continue
            fc = self._extract_fill_color_from_borderfill(em); fc = None if fc and fc.lower() in ("#ffffff", "#fff") else fc
            bs, bc = self._extract_border_sides_from_borderfill(em)
            diagonal_flags = self._extract_diagonal_flags_from_borderfill(em)
            bfs[str(bid)] = {
                "bgcolor": fc,
                "border_sides": bs,
                "border_complete": bc,
                "diagonal": bool(diagonal_flags),
                "diagonal_flags": diagonal_flags,
            }
        return bfs

    def _get_cell_borderfill_id(self, cell_elem: ET.Element) -> Optional[str]:
        """셀 테두리채우기 ID 추출 """
        if cell_elem is None: return None
        bid = self._get_attr_case_insensitive(cell_elem, ["borderfill", "borderfillid", "borderfill_id", "borderfillref", "borderfillidref"])
        if bid: return bid
        for ch in list(cell_elem):
            tl = self._strip_ns(ch.tag).lower()
            if tl in ("cellborderfill", "borderfill"):
                bid = self._get_attr_case_insensitive(ch, ["id", "refid", "borderfill", "borderfillid", "borderfill_id"])
                if bid: return bid
        return None

    def _get_cell_style_attrs(self, table_index: int, row: int, col: int) -> str:
        """셀 스타일 속성 """
        inf = self.cell_style_map.get((table_index, row, col))
        if not inf: return ""
        ats = []
        bg = inf.get("bgcolor")
        if bg: ats.append(f'bgcolor="{bg}"')
        bs = inf.get("border_sides") or []
        if inf.get("border_complete") and bs and len(bs) < 4: ats.append(f'border="{",".join(bs)}"')
        if inf.get("diagonal"):
            ats.append('data-diagonal="1"')
            flags = inf.get("diagonal_flags") or []
            if flags:
                ats.append(f'data-diagonal-flags="{",".join(flags)}"')
        return (" " + " ".join(ats)) if ats else ""

    def _extract_cell_info_from_hwpml(self):
        """HWPML에서 셀 크기 정보 추출 (Strategy + Builder + Dataclass 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Dict as DictType, Tuple as TupleType
        from abc import ABC, abstractmethod

        @dataclass(frozen=True)
        class CellKey:
            """셀 키 (불변)"""
            table_idx: int
            row: int
            col: int

            @classmethod
            def from_coords(cls, table_idx: int, row: str, col: str) -> Opt['CellKey']:
                """좌표로부터 생성"""
                try:
                    return cls(table_idx, int(row), int(col))
                except (ValueError, TypeError):
                    return None

        @dataclass
        class CellInfo:
            """셀 정보"""
            width: int
            height: int

            def to_dict(self) -> DictType[str, int]:
                return {'width': self.width, 'height': self.height}

        class AttributeExtractor(ABC):
            """속성 추출기 (추상)"""
            @abstractmethod
            def extract(self, element) -> Opt[str]:
                """요소에서 속성 추출"""
                pass

        class FallbackExtractor(AttributeExtractor):
            """Fallback 속성 추출기"""
            def __init__(self, *attr_names: str):
                self.attr_names = attr_names

            def extract(self, element) -> Opt[str]:
                """여러 속성명 시도"""
                for name in self.attr_names:
                    value = element.get(name)
                    if value:
                        return value
                return None

        class CellInfoBuilder:
            """셀 정보 빌더"""
            def __init__(self):
                self.width_extractor = FallbackExtractor('Width', 'width')
                self.height_extractor = FallbackExtractor('Height', 'height')

            def build(self, cell_elem) -> Opt[CellInfo]:
                """셀 요소로부터 CellInfo 생성"""
                width = self.width_extractor.extract(cell_elem)
                height = self.height_extractor.extract(cell_elem)

                if width and height:
                    try:
                        return CellInfo(int(width), int(height))
                    except ValueError:
                        return None
                return None

        class CellCoordinateExtractor:
            """셀 좌표 추출기"""
            def __init__(self):
                self.row_extractor = FallbackExtractor('RowAddr', 'rowaddr', 'Row', 'row')
                self.col_extractor = FallbackExtractor('ColAddr', 'coladdr', 'Col', 'col')

            def extract(self, cell_elem) -> Opt[TupleType[str, str]]:
                """행/열 좌표 추출"""
                row = self.row_extractor.extract(cell_elem)
                col = self.col_extractor.extract(cell_elem)

                if row is not None and col is not None:
                    return (row, col)
                return None

        class TableProcessor:
            """테이블 처리기"""
            def __init__(self, borderfill_map: DictType, cell_info_map: DictType, cell_style_map: DictType, borderfill_getter):
                self.borderfill_map, self.cell_info_map, self.cell_style_map, self.borderfill_getter = borderfill_map, cell_info_map, cell_style_map, borderfill_getter
                self.info_builder, self.coord_extractor = CellInfoBuilder(), CellCoordinateExtractor()

            def process_cell(self, cell_elem, table_idx: int) -> None:
                """셀 처리"""
                # 좌표 추출
                coords = self.coord_extractor.extract(cell_elem)
                if not coords:
                    return

                row_str, col_str = coords
                key = CellKey.from_coords(table_idx, row_str, col_str)
                if not key:
                    return

                # 크기 정보 추출 및 저장
                info = self.info_builder.build(cell_elem)
                if info:
                    self.cell_info_map[(key.table_idx, key.row, key.col)] = info.to_dict()

                # 스타일 정보 추출 및 저장
                bf_id = self.borderfill_getter(cell_elem)
                if bf_id:
                    style = self.borderfill_map.get(str(bf_id))
                    if style:
                        self.cell_style_map[(key.table_idx, key.row, key.col)] = style

            def process_table(self, table_elem, table_idx: int) -> None:
                """테이블 처리"""
                for cell in table_elem.iter('CELL'):
                    self.process_cell(cell, table_idx)

        class HWPMLProcessor:
            """HWPML 처리기 (Template Method)"""
            def __init__(self, hwp, logger, borderfill_extractor, borderfill_getter):
                self.hwp = hwp
                self.logger = logger
                self.borderfill_extractor = borderfill_extractor
                self.borderfill_getter = borderfill_getter

            def extract_and_parse(self) -> Opt:
                """HWPML 추출 및 파싱"""
                import xml.etree.ElementTree as ET

                hwpml_str = self.hwp.GetTextFile("HWPML2X", "")
                if not hwpml_str:
                    self.logger("HWPML 추출 실패 (빈 문자열)", "WARN")
                    return None

                return ET.fromstring(hwpml_str)

            def process(self, cell_info_map: DictType, cell_style_map: DictType) -> bool:
                """HWPML 처리 """
                if not (rt := self.extract_and_parse()): return False
                bfm = self.borderfill_extractor(rt)
                prc = TableProcessor(bfm, cell_info_map, cell_style_map, self.borderfill_getter)
                for ti, te in enumerate(rt.iter('TABLE')): prc.process_table(te, ti)
                return True

        # 메인 실행
        try:
            processor = HWPMLProcessor(
                self.hwp,
                self.log_to_main,
                self._extract_borderfill_map,
                self._get_cell_borderfill_id
            )

            success = processor.process(self.cell_info_map, self.cell_style_map)

            if success:
                self.log_to_main(f"셀 정보 추출 완료: {len(self.cell_info_map)}개 셀")
                if self.cell_style_map:
                    self.log_to_main(f"셀 스타일 추출 완료: {len(self.cell_style_map)}개 셀")

        except Exception as e:
            self.log_to_main(f"HWPML 셀 정보 추출 중 오류: {e}", "WARN")

    def _excute_scan_in_page_range(self, hwp: HwpRawWrapper, start_page: int, end_page: int):
        """스캔 범위 실행 """
        from dataclasses import dataclass
        from typing import Optional as Opt

        @dataclass
        class SC:
            """스캔 컨텍스트"""
            ssp: Opt[tuple] = None
            ep: Opt[tuple] = None
            ilp: bool = False
            pp: Opt[tuple] = None
            sl: bool = False

        sc = SC()
        try:
            tp = hwp.PageCount
            self.log_to_main(f"total_page: {tp}")
            self.log_to_main(f"start_page: {start_page}")
            self.log_to_main(f"end_page: {end_page}")
            hwp.goto_page(start_page)
            sp = hwp.get_pos()
            if not sp:
                self.log_to_main("start_pos is None", "ERROR")
                return
            self.log_to_main(f"start_pos: {sp}")
            sp[0] != 0 and (hwp.move_pos(25) or True) and (sp := hwp.get_pos())
            sc.ssp, sc.ilp = sp, end_page >= tp
            current_scan_page = start_page
            self.log_to_main(f"end_page: {end_page}"); self.log_to_main(f"is_last_page_in_range: {sc.ilp}")
            self._page_down(hwp, start_page, end_page); ep = hwp.get_pos()
            ep[0] != 0 and (hwp.move_pos(25) or True) and (ep := hwp.get_pos())
            self.log_to_main(f"end_pos: {ep}"); sc.ep, (el, epa, ech) = ep, ep
            hwp.init_scan(option=4, range=0x0017, spara=sp[1], spos=sp[2])
            while not sc.sl:
                st, tx = hwp.get_text(); hwp.move_pos(201); ps, ht = hwp.get_pos(), hwp.get_heading_string()
                try:
                    cp = hwp.current_page
                    if isinstance(cp, int) and cp > 0:
                        current_scan_page = cp
                except Exception:
                    pass
                try: cs, prs = self.hwp.CharShape, self.hwp.ParaShape
                except Exception as e: self.log_to_main(f"❌ shape 추출 실패: {e}", "ERROR"); cs, prs = None, None
                self.log_to_main(f"state: {st}, text: {tx}, pos: {ps}, heading_text: {ht}")
                if st <= 1: break
                self.pos_to_shape[ps], (lid, pid, cps) = {"char_shape": cs, "para_shape": prs}, ps
                self.pos_to_page[ps] = current_scan_page
                if not sc.ilp and lid == 0 and el == 0:
                    if pid == epa + 1: self.log_to_main("end_pos 다음 para_id에 도달 break"); break
                    if pid == epa and cps == ech: self.log_to_main("end_pos와 정확히 동일한 pos에 도달 break"); sc.sl = True
                ct = tx.replace("\r\n", "")
                if sc.pp == ps and ct.strip() == "": self.log_to_main(f"같은 pos에 도달하면 중복 추가 방지 pos: {ps} clean_text: {ct}"); continue
                sc.pp = ps
                self.extracted_elements.append(
                    {"id": None, "type": "list", "pos": ps, "text": ct, "heading_text": ht, "page": current_scan_page}
                    if ht else
                    (
                        {"id": None, "type": "paragraph", "pos": ps, "text": ct, "page": current_scan_page}
                        if lid == 0 else
                        {"id": None, "type": None, "pos": ps, "text": ct, "page": current_scan_page}
                    )
                )
        finally:
            try: hwp.release_scan()
            except Exception as e: self.log_to_main(f"release_scan failed: {e}", "WARN")
            try:
                if sc.ssp and hasattr(hwp, "set_pos"): hwp.set_pos(*sc.ssp)
                else: hwp.goto_page(start_page); ps = hwp.get_pos(); ps and ps[0] != 0 and hwp.move_pos(25)
            except Exception as e: self.log_to_main(f"Failed to restore scan start page: {e}", "WARN")

    def extract_elements(self, start_page: int, end_page: int):
        """요소 추출 (Strategy + Command + Collector 패턴, 축약형)"""
        from dataclasses import dataclass, field
        from typing import List as LT, Dict as DT, Set as ST, Tuple as TT, Any, Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class EX:
            """추출 컨텍스트"""
            elems: LT[DT] = field(default_factory=list)
            celems: LT[DT] = field(default_factory=list)
            rps: LT[int] = field(default_factory=list)

        class CP(ABC):
            """컨트롤 처리기"""
            @abstractmethod
            def can(self, c) -> bool: pass
            @abstractmethod
            def proc(self, c, h, cx: EX) -> Opt[DT]: pass

        class PP(CP):
            """페이지 처리기"""
            def can(self, c): return getattr(c, "CtrlID", None) == "pgnp"
            def proc(self, c, h, cx):
                return {"type": "pgnp", "anchor_pos": h.get_ctrl_pos(c)}

        class FP(CP):
            """각주/미주 처리기"""
            def __init__(self): self.cnt, self.cs = 0, []
            def can(self, c): return getattr(c, "CtrlID", None) in ("fn", "en", "atno")
            def proc(self, c, h, cx):
                self.cs.append(c)
                return None
            def _extract_note_text(self, c) -> str:
                """각주/미주 컨트롤에서 텍스트 추출"""
                try:
                    # 방법 1: 컨트롤의 Text 속성 직접 접근
                    if hasattr(c, "Text"):
                        return str(c.Text).strip()
                    # 방법 2: GetText 메서드 (일부 컨트롤)
                    if hasattr(c, "GetText"):
                        return str(c.GetText()).strip()
                    # 방법 3: 각주/미주 영역 속성 (HWP 특유)
                    if hasattr(c, "Properties"):
                        props = c.Properties
                        if hasattr(props, "Item"):
                            try:
                                return str(props.Item("Text")).strip()
                            except: pass
                except Exception:
                    pass
                return ""
            def fin(self, h, cx) -> LT[DT]:
                rs, fc, ec = [], 0, 0
                for i, c in enumerate(self.cs):
                    if c.CtrlID == "fn":
                        fc += 1
                        ap = h.get_ctrl_pos(c)
                        if ap[0] != 0: continue
                        atp = h.get_ctrl_pos(self.cs[i+1]) if i+1 < len(self.cs) and self.cs[i+1].CtrlID == "atno" else None
                        note_text = self._extract_note_text(c)
                        d = {"type": "fn", "id": None, "anchor_pos": ap, "index": fc, "text": note_text}
                        if atp: d["atno_pos"] = atp
                        rs.append(d)
                    elif c.CtrlID == "en":
                        ec += 1
                        ap = h.get_ctrl_pos(c)
                        if ap[0] != 0: continue
                        atp = h.get_ctrl_pos(self.cs[i+1]) if i+1 < len(self.cs) and self.cs[i+1].CtrlID == "atno" else None
                        note_text = self._extract_note_text(c)
                        d = {"type": "en", "id": None, "anchor_pos": ap, "index": ec, "text": note_text}
                        if atp: d["atno_pos"] = atp
                        rs.append(d)
                return rs

        # 초기화
        self.global_id, self.extracted_elements, self.pos_to_shape, self.pos_to_page = 1, [], {}, {}
        h, cx = self.hwp, EX()

        try:
            self._excute_scan_in_page_range(h, start_page, end_page)
            if not self.extracted_elements:
                self.log_to_main("extracted_elements is empty", "ERROR")
                return

            # 루트 para
            for e in self.extracted_elements:
                if e.get("type") == "paragraph" and self._validate_pos(e.get("pos")):
                    cx.rps.append(e["pos"][1])
            self.log_to_main(f"root_para_pos_list: {cx.rps}")

            # 컨트롤 처리
            ps = [PP(), FP()]
            cl = h.ctrl_list
            for c in cl:
                for p in ps:
                    if p.can(c):
                        r = p.proc(c, h, cx)
                        if r: cx.celems.append(r)
                        break
            for p in ps:
                if isinstance(p, FP): cx.celems.extend(p.fin(h, cx))

            # 테이블/이미지
            tc, rex, elp = [], [], []
            for c in cl:
                if c.CtrlID in ["foot", "head", "fn", "en", "tbl"]:
                    if c.CtrlID == "tbl": tc.append(c)
                    else:
                        p = h.get_ctrl_pos(c)
                        self.log_to_main(f" ctrl_id: {c.CtrlID}, ctrl_pos: {p}")
                        (elp.append(p[0] + 1) if p[0] != 0 else rex.append({"ctrl_id": c.CtrlID, "ctrl_pos": p}))

            # 이미지
            ies, sip, sirp = [], set(), set()
            for c in cl:
                try:
                    if getattr(c, "CtrlID", None) != "gso": continue
                    ud = getattr(c, "UserDesc", None)
                    nd = str(ud).replace(" ", "").lower() if ud else ""
                    if "사각형" in nd or not any(k in nd for k in ("그림", "사진", "image", "picture")): continue
                    gp = h.get_ctrl_pos(c)
                    if not self._validate_pos(gp) or gp in sip: continue
                    sip.add(gp)
                    gl, gpa, _ = gp
                    if gl != 0 or gpa not in cx.rps or gpa in sirp: continue
                    sirp.add(gpa)
                    ies.append({"type": "image", "pos": gp, "user_desc": ud})
                except: continue

            # 테이블 범위
            tir, am = [], False
            for ti, tc_item in enumerate(tc):
                tp = h.get_ctrl_pos(tc_item)
                tl, tpa, tch = tp
                self.log_to_main(f"table_pos: {tl, tpa, tch}")
                if tl == 0:
                    if am:
                        if tpa in cx.rps: tir.append((ti, tp))
                        else: break
                    else:
                        if tpa in cx.rps: am = True; tir.append((ti, tp))
                else:
                    if am: tir.append((ti, tp))

            # 테이블 그룹화
            tgs, csg, crg = [], None, None
            self.log_to_main(f"table_items_in_range: {tir}")
            for ti, tp in tir:
                tl, tpa, tch = tp
                if tl == 0 and (csg is None or tpa != csg["pid"]):
                    crg = {"rt": (ti, tp), "ns": []}
                    _meis = []
                    for i, el in enumerate(self.extracted_elements):
                        if isinstance(el, dict) and self._validate_pos(el.get("pos")) and el["pos"][0] == 0 and el["pos"][1] == tpa:
                            _meis.append((i, el))
                    csg = {
                        "pid": tpa,
                        "grps": [crg],
                        "meis": _meis
                    }
                    tgs.append(csg)
                elif tl == 0 and tpa == csg["pid"]:
                    crg = {"rt": (ti, tp), "ns": []}
                    csg["grps"].append(crg)
                else:
                    if crg: crg["ns"].append((ti, tp))
            self.log_to_main(f"table_same_para_groups: {tgs}")

            # 테이블 요소 생성
            for tg in tgs:
                sptgs, meis, pid = tg.get("grps", []), tg.get("meis", []), tg.get("pid", None)
                _extra = []
                for c in rex:
                    if c.get("ctrl_pos") and self._validate_pos(c.get("ctrl_pos")) and c.get("ctrl_pos")[1] == pid:
                        _extra.append(c)
                spcgs = sptgs + _extra
                spcgs = sorted(spcgs, key=lambda g: g.get("rt")[1][2] if "rt" in g and isinstance(g.get("rt"), tuple) and len(g.get("rt")) > 1 and self._validate_pos(g.get("rt")[1]) else (g.get("ctrl_pos")[2] if self._validate_pos(g.get("ctrl_pos")) else -float("inf")))
                spgl = len(spcgs)
                for tgi, tgrp in enumerate(spcgs):
                    if "rt" not in tgrp: continue
                    ri, rp = tgrp["rt"]
                    rl, rpa, rch = rp
                    self.log_to_main(f"root_index: {ri}, root_pos: {rp}")
                    bes = []
                    if meis:
                        si, ei, mel = None, None, len(meis)
                        if mel == spgl + 1:
                            si, ei = meis[tgi][0], meis[tgi + 1][0]
                        else:
                            for idx, (i, el) in enumerate(meis):
                                icp = el["pos"][2]
                                if icp <= rch: si = i
                                elif icp > rch and ei is None: ei = i; break
                        if ei is None: ei = meis[-1][0]
                        self.log_to_main(f"start_idx: {si}, end_idx: {ei}")
                        bes = self.extracted_elements[si:ei+1] if si is not None and ei is not None else []
                    nti = tgrp.get("ns", [])
                    if len(bes) > 2:
                        h.get_into_nth_table(ri)
                        rc, suc = 0, False
                        while rc < 5:
                            suc = h.TableCellBlock()
                            if suc: break
                            h.MoveDown()
                            rc += 1
                        h.TableColBegin()
                        h.TableColPageUp()
                        fp = h.get_pos()
                        self.log_to_main(f"root_index: {ri}")
                        xt = h.GetTextFile("HWPML2X", option="saveblock")
                        if xt is None:
                            self.log_to_main("xml is None", "ERROR")
                            continue
                        hfx = convert_hwpml_to_html(xt)
                        filtered_bes = []
                        for be in bes:
                            if be.get("pos")[0] not in elp:
                                filtered_bes.append(be)
                        ch, lp, tdm = clean_table_html(hfx, elp, filtered_bes, nti)
                        self.log_to_main(f"html: {ch}")
                        cx.celems.append({"type": "table", "index": ri, "table_pos": rp, "first_cell_pos": fp, "last_cell_pos": lp, "html": ch, "td_metas": tdm})

            # 컨트롤 병합
            for ce in cx.celems:
                if ce.get("type") == "pgnp":
                    pp = ce.get("anchor_pos")
                    for idx, el in enumerate(self.extracted_elements):
                        if self._validate_pos(el.get("pos")) and el.get("pos") == pp:
                            del self.extracted_elements[idx]
                if ce.get("type") == "fn":
                    ap = ce.get("atno_pos", None)
                    if ap is not None:
                        al, apa, ach = ap
                        ec = len(self.extracted_elements) if self.extracted_elements else 0
                        for idx in range(ec - 1, -1, -1):
                            if self._validate_pos(self.extracted_elements[idx].get("pos")) and self.extracted_elements[idx].get("pos") == ap:
                                del self.extracted_elements[idx]
                        sli, sle = [], []
                        for idx, el in enumerate(self.extracted_elements):
                            if self._validate_pos(el.get("pos")) and el.get("pos")[0] == al:
                                sli.append(idx)
                                sle.append(el)
                        if sli:
                            fps = []
                            for el in sle:
                                fps.append({"type": "paragraph", "id": None, "pos": el.get("pos"), "text": el.get("text", "")})
                            ce["paragraphs"] = fps
                            ia = min(sli)
                            for ridx in reversed(sli): del self.extracted_elements[ridx]
                            self.extracted_elements.insert(ia, ce)
                if ce.get("type") == "table":
                    fl, ll = ce.get("first_cell_pos", (0, 0, 0))[0], ce.get("last_cell_pos", (0, 0, 0))[0]
                    tl, tpa, _ = ce.get("table_pos", (0, 0, 0))
                    rs, re = min(fl, ll), max(fl, ll)
                    ris = []
                    for idx, el in enumerate(self.extracted_elements):
                        ps = el.get("pos") if isinstance(el, dict) else None
                        tx = el.get("text") if isinstance(el, dict) else None
                        if self._validate_pos(ps):
                            lv, pv, _ = ps
                            if rs <= lv <= re or (tl == lv and tpa == pv and tx.strip() == ""):
                                ris.append(idx)
                    ii = ris[0] if ris else next((i for i, el in enumerate(self.extracted_elements) if isinstance(el, dict) and self._validate_pos(el.get("pos")) and el.get("pos")[0] > re), len(self.extracted_elements))
                    for ridx in reversed(ris): del self.extracted_elements[ridx]
                    self.extracted_elements.insert(ii, ce)
            for ie in ies: self.extracted_elements.append(ie)

        except Exception as e:
            self.log_to_main(f"❌ 오류: {e}", "ERROR")
            self.hwp.release_scan()
        finally:
            # 정규화
            nes = []
            for el in self.extracted_elements:
                if isinstance(el, dict) and el.get("type", None) is None:
                    ps = el.get("pos")
                    if self._validate_pos(ps):
                        ne = dict(el)
                        try: lid = ps[0]
                        except: lid = 0
                        ne["type"] = "paragraph" if lid == 0 else "textbox"
                        nes.append(ne)
                        continue
                nes.append(el)
            _filtered = []
            for el in nes:
                if el.get("type", None) is not None:
                    _filtered.append(el)
            self.extracted_elements = _filtered
            self.extracted_elements = self._merge_consecutive_root_paragraphs(self.extracted_elements)
            self.extracted_elements = self._deduplicate_root_paragraphs(self.extracted_elements)
            self._extract_cell_info_from_hwpml()

    def _merge_consecutive_root_paragraphs(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """연속 루트 문단 병합 """
        mg, i = [], 0
        while i < len(elements):
            el = elements[i]
            if isinstance(el, dict) and el.get("type") == "paragraph" and self._validate_pos(el.get("pos")) and el["pos"][0] == 0:
                bp, txs, pr = el["pos"][1], [el.get("text", "")], el["pos"]
                j = i + 1
                while j < len(elements) and isinstance((nx := elements[j]), dict) and nx.get("type") == "paragraph" and self._validate_pos(nx.get("pos")) and nx["pos"][0] == 0 and nx["pos"][1] == bp:
                    txs.append(nx.get("text", "")); j += 1
                mg.append({"id": None, "type": "paragraph", "pos": pr, "text": "".join(txs), "page": el.get("page")}); i = j
            else: mg.append(el); i += 1
        return mg

    def _deduplicate_root_paragraphs(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """루트 문단 중 동일 텍스트 중복 제거.

        HWP get_text()가 같은 텍스트를 서로 다른 charshape/위치로
        중복 반환하는 경우를 처리한다. 표 셀(type=None, list_pos≠0)이
        아닌 루트 문단(paragraph/list, list_pos==0)만 대상으로 한다.
        """
        def _is_symbol_only_text(value: str) -> bool:
            normalized = (value or "").strip()
            if not normalized:
                return False
            return bool(re.fullmatch(r"[\-•·○□■◆▪▫▶➤◦…\.]+", normalized))

        seen_texts: set = set()
        deduped: List[Dict[str, Any]] = []
        for el in elements:
            if isinstance(el, dict) and el.get("type") in ("paragraph", "list") and self._validate_pos(el.get("pos")) and el["pos"][0] == 0:
                normalized = (el.get("text") or "").strip()
                if _is_symbol_only_text(normalized):
                    deduped.append(el)
                    continue
                if normalized and normalized in seen_texts:
                    self.log_to_main(f"중복 루트 문단 제거: pos={el['pos']} text={normalized[:40]}")
                    continue
                if normalized:
                    seen_texts.add(normalized)
            deduped.append(el)
        return deduped

    def _has_id_in_td_text(self, td_text: str) -> bool:
        """셀 텍스트 내 식별자 속성 존재 여부 검사 (id 또는 data-id 패턴)"""
        # 패턴: 공백 + (id|data-id) + = + 따옴표로 감싼 숫자
        identifier_pattern = r'\s(?:id|data-id)\s*=\s*["\'](\d+)["\']'
        match_result = re.search(identifier_pattern, td_text)
        return match_result is not None

    def _extract_content_and_id_to_pos_from_extracted_elements(
        self,
    ) -> Tuple[str, Dict[int, Tuple[int, int, int]]] | None:
        """CVD 추출 (Command + Strategy + Builder + Parser 패턴)"""
        from dataclasses import dataclass, field
        from typing import Optional as Opt, List as ListType, Dict as DictType, Iterator, Callable
        from abc import ABC, abstractmethod

        # 설명: 이 메서드는 매우 복잡하므로 여러 패턴을 조합하여 리팩토링합니다
        # 원본은 330줄의 거대한 메서드였지만, Command + Strategy + Builder로 분리

        @dataclass
        class CTX:
            """컨텍스트 """
            id_map: DictType[int, tuple] = field(default_factory=dict)
            tbl_idx: int = 0

        class HP:
            """HTML 파서 """
            @staticmethod
            def match(h: str, sp: int, ot: str, ct: str) -> int:
                """매칭 태그 찾기"""
                pos, d, ol, cl = sp, 1, len(ot), len(ct)
                while d > 0:
                    no, nc = h.find(ot, pos), h.find(ct, pos)
                    if nc == -1: return len(h)
                    if no != -1 and no < nc: d += 1; pos = no + ol
                    else: d -= 1; pos = nc + cl
                return pos

        class AB:
            """속성 빌더 """
            def __init__(self, ex, ctx):
                self.ex, self.ctx = ex, ctx

            def bld(self, m: dict) -> Opt[str]:
                """속성 빌드 """
                if not (i := self.ex._map_id_to_pos(p := m.get("pos"))): return None
                a = self.ex._get_style_attrs_from_pos(p)
                if (r := m.get("row")) is not None and (c := m.get("col")) is not None:
                    if ci := self.ex.cell_info_map.get((self.ctx.tbl_idx, r, c)):
                        (w := ci.get("width")) and (a := a + f' width="{w}"'); (h := ci.get("height")) and (a := a + f' height="{h}"')
                    a += self.ex._get_cell_style_attrs(self.ctx.tbl_idx, r, c)
                td_sig = self.ex._build_td_signature(self.ctx.tbl_idx, m)
                attrs = [f'id="{i}"', f'data-td-sig="{td_sig}"']

                if r is not None:
                    attrs.append(f'data-row="{int(r)}"')
                if c is not None:
                    attrs.append(f'data-col="{int(c)}"')
                attrs.append(f'data-rowspan="{int(m.get("rowspan") or 1)}"')
                attrs.append(f'data-colspan="{int(m.get("colspan") or 1)}"')

                raw_table_path = m.get("table_path")
                if isinstance(raw_table_path, (list, tuple)):
                    table_path = "/".join(str(v) for v in raw_table_path)
                elif raw_table_path is None:
                    table_path = ""
                else:
                    table_path = str(raw_table_path)
                if table_path:
                    attrs.append(f'data-table-path="{table_path}"')

                return " " + " ".join(attrs) + a

        class ICB:
            """내부 콘텐츠 빌더 """
            def __init__(self, ex, pf):
                self.ex, self.pf = ex, pf

            def bld(self, m: dict, ih: str) -> str:
                """빌드"""
                nt = self._ext(ih)
                return self._cmb(m, nt)

            def _ext(self, ih: str) -> ListType[str]:
                """중첩 테이블 추출"""
                hp, ts, si = HP(), [], 0
                while True:
                    to = ih.find("<table", si)
                    if to == -1: break
                    te = ih.find(">", to)
                    if te == -1: break
                    ts_pos = hp.match(ih, te + 1, "<table", "</table>")
                    if ts_pos <= len(ih): ts.append(ih[to:ts_pos])
                    si = ts_pos
                return ts

            def _cmb(self, m: dict, nt: ListType[str]) -> str:
                """조합 """
                ls, ni = [], 0
                for im in (m.get("inner_metas", []) if isinstance(m, dict) else []):
                    if (mt := im.get("type")) == "nested_table":
                        if ni < len(nt):
                            nested_path = tuple(im.get("table_path") or ())
                            ls.append(self.pf(nt[ni], nested_path))
                        else:
                            ls.append("")
                        ni += 1
                    else: ls.append(self._ol(im) if mt == "outline" else (self._ls(im) if mt == "list" else self._pa(im)))
                return "\n".join(ls)

            def _ol(self, m: dict) -> str:
                """개요"""
                p, i = m.get("pos"), self.ex._map_id_to_pos(m.get("pos"))
                if not i: return ""
                t, lr = m.get("text") or "", m.get("outline_level") or ""
                olt = str(lr).replace("개요 ", "outline")
                sa = self.ex._get_style_attrs_from_pos(p)
                p_sig = self.ex._build_paragraph_signature(p, t)
                return f'<{olt}><p id="{i}" data-p-sig="{p_sig}"{sa}>{t}</p>'

            def _ls(self, m: dict) -> str:
                """리스트"""
                p, i = m.get("pos"), self.ex._map_id_to_pos(m.get("pos"))
                if not i: return ""
                t, ht = m.get("text") or "", m.get("heading_text") or ""
                sa = self.ex._get_style_attrs_from_pos(p)
                p_sig = self.ex._build_paragraph_signature(p, t)
                return f'<list {ht}><p id="{i}" data-p-sig="{p_sig}"{sa}>{t}</p></list>'

            def _pa(self, m: dict) -> str:
                """문단"""
                p, i = m.get("pos"), self.ex._map_id_to_pos(m.get("pos"))
                if not i: return ""
                sa = self.ex._get_style_attrs_from_pos(p)
                text = m.get("text", "")
                p_sig = self.ex._build_paragraph_signature(p, text)
                return f'<p id="{i}" data-p-sig="{p_sig}"{sa}>{text}</p>'

        class SP:
            """세그먼트 처리기 """
            def __init__(self, ex, ctx, metas: ListType[DictType[str, Any]], table_path: Tuple[int, ...]):
                self.ex, self.ctx = ex, ctx
                self.table_path = tuple(table_path or ())
                self._metas = list(metas or [])
                self._used_meta_indices = set()
                self._meta_key_to_indices: DictType[Tuple[Tuple[int, ...], int, int], ListType[int]] = {}
                self._meta_row_col_to_indices: DictType[Tuple[int, int], ListType[int]] = {}
                for idx, meta in enumerate(self._metas):
                    row, col = meta.get("row"), meta.get("col")
                    if row is None or col is None:
                        continue
                    try:
                        key_row_col = (int(row), int(col))
                    except Exception:
                        continue
                    meta_path = tuple(meta.get("table_path") or self.table_path)
                    self._meta_key_to_indices.setdefault((meta_path, key_row_col[0], key_row_col[1]), []).append(idx)
                    self._meta_row_col_to_indices.setdefault(key_row_col, []).append(idx)
                self.ab = AB(ex, ctx)
                self.icb = ICB(ex, self.proc)

            def _consume_unused_index(self, indices: ListType[int]) -> int:
                for idx in indices:
                    if idx in self._used_meta_indices:
                        continue
                    self._used_meta_indices.add(idx)
                    return idx
                return -1

            def _match_meta(self, slot: DictType[str, Any]) -> Opt[DictType[str, Any]]:
                row = slot.get("row")
                col = slot.get("col")
                slot_path = tuple(slot.get("table_path") or self.table_path)
                if row is not None and col is not None:
                    try:
                        row_col = (int(row), int(col))
                    except Exception:
                        row_col = None
                    if row_col is not None:
                        idx = self._consume_unused_index(self._meta_key_to_indices.get((slot_path, row_col[0], row_col[1]), []))
                        if idx != -1:
                            return self._metas[idx]
                        idx = self._consume_unused_index(self._meta_row_col_to_indices.get(row_col, []))
                        if idx != -1:
                            return self._metas[idx]

                rowspan = int(slot.get("rowspan") or 1)
                colspan = int(slot.get("colspan") or 1)
                for idx, meta in enumerate(self._metas):
                    if idx in self._used_meta_indices:
                        continue
                    if tuple(meta.get("table_path") or self.table_path) != slot_path:
                        continue
                    try:
                        mr = int(meta.get("rowspan") or 1)
                        mc = int(meta.get("colspan") or 1)
                    except Exception:
                        mr, mc = 1, 1
                    if mr == rowspan and mc == colspan:
                        self._used_meta_indices.add(idx)
                        return self._metas[idx]

                for idx, meta in enumerate(self._metas):
                    if idx in self._used_meta_indices:
                        continue
                    if tuple(meta.get("table_path") or self.table_path) != slot_path:
                        continue
                    self._used_meta_indices.add(idx)
                    return meta

                for idx, meta in enumerate(self._metas):
                    if idx in self._used_meta_indices:
                        continue
                    self._used_meta_indices.add(idx)
                    return meta
                return None

            def _extract_td_slots(self, seg: str) -> ListType[DictType[str, Any]]:
                class _TDSlotParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.table_depth = 0
                        self.top_table_index = -1
                        self.top_table_count = 0
                        self.current_row = None
                        self.current_col = 0
                        self.row_occupancy: DictType[int, set[int]] = {}
                        self.slots: ListType[DictType[str, Any]] = []

                    @staticmethod
                    def _parse_span(attrs_map: DictType[str, Any], key: str) -> int:
                        value = attrs_map.get(key)
                        if value is None:
                            return 1
                        try:
                            iv = int(value)
                            return iv if iv > 0 else 1
                        except Exception:
                            return 1

                    def handle_starttag(self, tag: str, attrs):
                        if tag == "table":
                            self.table_depth += 1
                            if self.table_depth == 1:
                                self.top_table_index += 1
                                self.top_table_count += 1
                                self.current_row = None
                                self.current_col = 0
                                self.row_occupancy = {}
                            return

                        if self.table_depth != 1:
                            return

                        if tag == "tr":
                            self.current_row = 0 if self.current_row is None else self.current_row + 1
                            self.current_col = 0
                            self.row_occupancy.setdefault(self.current_row, set())
                            return

                        if tag != "td" or self.current_row is None:
                            return

                        attrs_map = dict(attrs)
                        rowspan = self._parse_span(attrs_map, "rowspan")
                        colspan = self._parse_span(attrs_map, "colspan")

                        occupied = self.row_occupancy.setdefault(self.current_row, set())
                        while self.current_col in occupied:
                            self.current_col += 1

                        row = self.current_row
                        col = self.current_col
                        self.slots.append({
                            "row": row,
                            "col": col,
                            "rowspan": rowspan,
                            "colspan": colspan,
                            "top_table_index": self.top_table_index,
                        })

                        for row_offset in range(rowspan):
                            target_row = row + row_offset
                            target_occ = self.row_occupancy.setdefault(target_row, set())
                            for col_offset in range(colspan):
                                target_occ.add(col + col_offset)

                        self.current_col = col + colspan

                    def handle_endtag(self, tag: str):
                        if tag == "table":
                            if self.table_depth == 1:
                                self.current_row = None
                                self.current_col = 0
                            self.table_depth = max(0, self.table_depth - 1)

                parser = _TDSlotParser()
                parser.feed(seg)
                multi_top = parser.top_table_count > 1
                slots: ListType[DictType[str, Any]] = []
                for slot in parser.slots:
                    top_idx = slot.pop("top_table_index", None)
                    if multi_top and top_idx is not None:
                        slot["table_path"] = self.table_path + (int(top_idx),)
                    else:
                        slot["table_path"] = self.table_path
                    slots.append(slot)
                return slots

            def proc(self, seg: str, table_path: Opt[Tuple[int, ...]] = None) -> str:
                """처리"""
                if table_path is not None and tuple(table_path) != self.table_path:
                    nested = SP(self.ex, self.ctx, self._metas, tuple(table_path))
                    nested._used_meta_indices = self._used_meta_indices
                    nested._meta_key_to_indices = self._meta_key_to_indices
                    nested._meta_row_col_to_indices = self._meta_row_col_to_indices
                    return nested.proc(seg)

                td_slots = self._extract_td_slots(seg)
                slot_idx = 0
                hp, ops, cur = HP(), [], 0
                while True:
                    oi = seg.find("<td", cur)
                    if oi == -1: ops.append(seg[cur:]); break
                    ops.append(seg[cur:oi])
                    ste = seg.find(">", oi)
                    if ste == -1: ops.append(seg[oi:]); break
                    st = seg[oi:ste+1]
                    sp = hp.match(seg, ste + 1, "<td", "</td>")
                    ih = seg[ste+1:sp-5]
                    slot = td_slots[slot_idx] if slot_idx < len(td_slots) else None
                    slot_idx += 1
                    m = self._match_meta(slot or {})
                    if not m: ops.append(seg[oi:sp]); cur = sp; continue
                    if self.ex._has_id_in_td_text(st): swi = st
                    else:
                        a = self.ab.bld(m)
                        swi = st.replace("<td", f"<td{a}", 1) if a else st
                    ic = self.icb.bld(m, ih)
                    ops.append(swi + ic + "</td>")
                    cur = sp
                return "".join(ops)

        class EP(ABC):
            """요소 처리기 (추상, 축약형)"""
            @abstractmethod
            def can(self, e: dict) -> bool: pass
            @abstractmethod
            def proc(self, e: dict, ctx) -> str: pass

        class TP(EP):
            """테이블 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "table"
            def proc(self, e, ctx):
                h, tms = e.get("html") or "", e.get("td_metas") or []
                tms = sorted(tms, key=lambda m: m.get("pos")[0] if self.ex._validate_pos(m.get("pos")) else 0)
                sp = SP(self.ex, ctx, tms, (0,))
                h = sp.proc(h, (0,))
                ctx.tbl_idx += 1
                return f"{h}\n"

        class FP(EP):
            """각주 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "fn"
            def proc(self, e, ctx):
                ap, idx = e.get("anchor_pos"), e.get("index")
                fai = self.ex._map_id_to_pos(ap)
                note_text = e.get("text", "")
                # paragraphs 방식 fallback (레거시 호환)
                if not note_text:
                    ps = e.get("paragraphs", [])
                    pts = []
                    for p in ps:
                        pi = self.ex._map_id_to_pos(p.get("pos"))
                        if pi: pts.append(f"<{pi}>{p.get('text', '')}")
                    note_text = ''.join(pts)
                return f"<각주{idx} {fai}>{note_text}\n"

        class ENP(EP):
            """미주 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "en"
            def proc(self, e, ctx):
                ap, idx = e.get("anchor_pos"), e.get("index")
                fai = self.ex._map_id_to_pos(ap)
                note_text = e.get("text", "")
                # paragraphs 방식 fallback (레거시 호환)
                if not note_text:
                    ps = e.get("paragraphs", [])
                    pts = []
                    for p in ps:
                        pi = self.ex._map_id_to_pos(p.get("pos"))
                        if pi: pts.append(f"<{pi}>{p.get('text', '')}")
                    note_text = ''.join(pts)
                return f"<미주{idx} {fai}>{note_text}\n"

        class TBP(EP):
            """글상자 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "textbox"
            def proc(self, e, ctx):
                p = e.get("pos")
                tci, tconti = self.ex._map_id_to_pos(p), self.ex._map_id_to_pos(p)
                if not tci or not tconti: return ""
                t, sa = e.get("text") or "", self.ex._get_style_attrs_from_pos(p)
                p_sig = self.ex._build_paragraph_signature(p, t)
                return f'<textbox id="{tci}"><p id="{tconti}" data-p-sig="{p_sig}"{sa}>{t}</p></textbox>\n'

        class IP(EP):
            """이미지 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "image"
            def proc(self, e, ctx):
                i = self.ex._map_id_to_pos(e.get("pos"))
                return f"<image {i}>\n" if i else ""

        class LP(EP):
            """리스트 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return e.get("type") == "list"
            def proc(self, e, ctx):
                p, i = e.get("pos"), self.ex._map_id_to_pos(e.get("pos"))
                if not i: return ""
                t, ht = e.get("text"), e.get("heading_text")
                sa = self.ex._get_style_attrs_from_pos(p)
                p_sig = self.ex._build_paragraph_signature(p, t)
                return f'<list {ht}><p id="{i}" data-p-sig="{p_sig}"{sa}>{t}</p></list>\n'

        class PP(EP):
            """문단 처리기 """
            def __init__(self, ex): self.ex = ex
            def can(self, e): return True
            def proc(self, e, ctx):
                p, i = e.get("pos"), self.ex._map_id_to_pos(e.get("pos"))
                if not i: return ""
                t, sa = e.get("text"), self.ex._get_style_attrs_from_pos(p)
                p_sig = self.ex._build_paragraph_signature(p, t)
                return f'<p id="{i}" data-p-sig="{p_sig}"{sa}>{t}</p>\n'

        # 메인 실행
        self.id_to_pos = {}
        ctx = CTX(id_map=self.id_to_pos)
        procs = [TP(self), FP(self), ENP(self), TBP(self), IP(self), LP(self), PP(self)]
        cvd = ""

        try:
            for element in self.extracted_elements:
                for proc in procs:
                    if proc.can(element):
                        cvd += proc.proc(element, ctx)
                        break

        except Exception as e:
            self.log_to_main(f"❌ 오류: {e}", "ERROR")

        if not cvd or not ctx.id_map:
            return None
        return cvd, ctx.id_map

    def _create_page_range_prefix(self, start_page: int, end_page: int) -> str:
        """CVD 맨 앞줄에 들어갈 페이지 범위 정보 텍스트 생성"""
        return (f"<scanned_page_range>\n{start_page} ~ {end_page} 페이지\n</scanned_page_range>\n\n" if start_page != end_page else f"<scanned_page_range>\n{start_page} 페이지\n</scanned_page_range>\n\n")

    def _create_style_sidecar_from_mapping_data(self) -> str:
        """스타일 사이드카 생성 (Factory + Strategy + Dataclass 패턴)"""
        from dataclasses import dataclass, field
        from typing import List as ListType, Dict as DictType, Tuple as TupleType, Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class StyleRange:
            """스타일 범위"""
            start_id: int
            end_id: int
            value: str

            def to_text(self) -> str:
                return str(self.start_id) if self.start_id == self.end_id else f"{self.start_id} ~ {self.end_id}"

            def count(self) -> int:
                return self.end_id - self.start_id + 1

        @dataclass
        class GroupedStyle:
            """그룹화된 스타일"""
            value_to_ranges: DictType[str, ListType[str]] = field(default_factory=dict)
            value_counts: DictType[str, int] = field(default_factory=dict)

            def add_range(self, range_obj: StyleRange) -> None:
                v = range_obj.value
                if v not in self.value_to_ranges:
                    self.value_to_ranges[v] = []
                    self.value_counts[v] = 0
                self.value_to_ranges[v].append(range_obj.to_text())
                self.value_counts[v] += range_obj.count()

            def get_dominant_value(self) -> Opt[str]:
                return max(self.value_counts.items(), key=lambda x: x[1])[0] if self.value_counts else None

        class StyleExtractor(ABC):
            """스타일 추출기 (추상)"""
            @abstractmethod
            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                pass

        class FontSizeExtractor(StyleExtractor):
            """폰트 크기 추출"""
            DEFAULT_HEIGHT = 1000

            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                try:
                    char_shape = shape.get("char_shape")
                    height = char_shape.Item("Height") if char_shape else self.DEFAULT_HEIGHT
                except Exception as e:
                    logger(f"❌ font size 추출 실패: {e}", "ERROR")
                    height = self.DEFAULT_HEIGHT

                size_val = height / 100
                size_pt = f"{int(size_val)}pt" if size_val.is_integer() else f"{size_val}pt"
                return (_id, size_pt)

        class FontFamilyExtractor(StyleExtractor):
            """폰트 패밀리 추출"""
            DEFAULT_FAMILY = "함초롬바탕"

            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                try:
                    char_shape = shape.get("char_shape")
                    family = char_shape.Item("FaceNameHangul") if char_shape else self.DEFAULT_FAMILY
                except Exception as e:
                    logger(f"❌ font family 추출 실패: {e}", "ERROR")
                    family = self.DEFAULT_FAMILY
                return (_id, family)

        class AlignExtractor(StyleExtractor):
            """정렬 추출"""
            ALIGN_MAP = {0: "Justify", 1: "Left", 2: "Right", 3: "Center", 4: "Distribute", 5: "DistributeSpace"}

            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                try:
                    para_shape = shape.get("para_shape")
                    align = para_shape.Item("AlignType") if para_shape else 0
                except Exception as e:
                    logger(f"❌ align type 추출 실패: {e}", "ERROR")
                    align = 0
                align_str = self.ALIGN_MAP.get(align, "Justify")
                return (_id, align_str)

        class ItalicExtractor(StyleExtractor):
            """이탤릭 추출"""
            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                try:
                    char_shape = shape.get("char_shape")
                    italic = char_shape.Item("Italic") if char_shape else 0
                    is_italic = bool(italic)
                except Exception:
                    is_italic = False
                return (_id, "Italic") if is_italic else None

        class TextColorExtractor(StyleExtractor):
            """텍스트 색상 추출"""
            DEFAULT_COLOR = 0

            def extract(self, _id: int, shape: DictType, logger) -> Opt[TupleType[int, str]]:
                try: tc = (cs := shape.get("char_shape")).Item("TextColor") if cs else 0
                except: tc = 0
                if tc == self.DEFAULT_COLOR: return None
                return (_id, f"#{tc&0xFF:02x}{(tc>>8)&0xFF:02x}{(tc>>16)&0xFF:02x}")

        class RangeGrouper:
            """범위 그룹화"""
            @staticmethod
            def group_consecutive_ranges(data: ListType[TupleType[int, str]]) -> ListType[StyleRange]:
                if not data: return []
                from functools import reduce
                def agg(acc, item):
                    rngs, sid, pid, cv = acc
                    cid, v = item
                    if v == cv and cid == pid + 1: return (rngs, sid, cid, cv)
                    rngs.append(StyleRange(sid, pid, cv)); return (rngs, cid, cid, v)
                sid, cv = data[0]; rngs, _, pid, fv = reduce(agg, data[1:], ([], sid, sid, cv))
                rngs.append(StyleRange(sid, pid, fv)); return rngs

        class GroupingStrategy(ABC):
            """그룹핑 전략 (추상)"""
            @abstractmethod
            def format(self, grouped: GroupedStyle, title: str) -> str:
                pass

        class StandardGrouping(GroupingStrategy):
            """표준 그룹핑"""
            def format(self, grouped: GroupedStyle, title: str) -> str:
                result_lines = [title]
                for val, r_list in grouped.value_to_ranges.items():
                    result_lines.append(f"{val}: {', '.join(r_list)}\n")
                return "\n".join(result_lines).strip()

        class DominantGrouping(GroupingStrategy):
            """지배적 값 제외 그룹핑"""
            def format(self, grouped: GroupedStyle, title: str) -> str:
                dv = grouped.get_dominant_value()
                rls = [title] + [f"{v}: {', '.join(rl)}\n" for v, rl in grouped.value_to_ranges.items() if v != dv]
                dv is not None and rls.append(f'\nThe rest blocks are "{dv}".\n')
                return "\n".join(rls).strip()

        class SectionBuilder:
            """섹션 빌더"""
            def __init__(self, title: str, strategy: GroupingStrategy, tag: str, suffix: str = ""):
                self.title = title
                self.strategy = strategy
                self.tag = tag
                self.suffix = suffix

            def build(self, data: ListType[TupleType[int, str]]) -> Opt[str]:
                if not data:
                    return None

                ranges = RangeGrouper.group_consecutive_ranges(data)
                grouped = GroupedStyle()
                for r in ranges:
                    grouped.add_range(r)

                content = self.strategy.format(grouped, self.title)
                return content + self.suffix + f"\n{self.tag}"

        # Main execution
        if not self.pos_to_shape or not self.id_to_pos:
            return ""

        sorted_ids = sorted(self.id_to_pos.keys())

        # Factory: 추출기 생성
        extractors = {
            'font_size': FontSizeExtractor(),
            'font_family': FontFamilyExtractor(),
            'align': AlignExtractor(),
            'italic': ItalicExtractor(),
            'text_color': TextColorExtractor(),
        }

        # 데이터 수집
        collected_data = {key: [] for key in extractors.keys()}

        for _id in sorted_ids:
            pos = self.id_to_pos[_id]
            # pos_to_shape는 tuple key로 저장되어 있으므로 tuple로 변환하여 조회
            pos_key = tuple(pos) if isinstance(pos, list) else pos
            shape = self.pos_to_shape.get(pos_key)
            if not shape:
                continue

            for key, extractor in extractors.items():
                result = extractor.extract(_id, shape, self.log_to_main)
                if result is not None:
                    collected_data[key].append(result)

        # 섹션 빌더 구성
        sections = []
        builders = [
            SectionBuilder("<font_size_info>", StandardGrouping(), "</font_size_info>"),
            SectionBuilder("<font_family_info>", DominantGrouping(), "</font_family_info>"),
            SectionBuilder("<para_align_info>", DominantGrouping(), "</para_align_info>"),
            SectionBuilder("<italic_info>", StandardGrouping(), "</italic_info>",
                           "\nThese italic blocks are likely form instructions - delete and replace with actual content.\n"),
            SectionBuilder("<text_color_info>", StandardGrouping(), "</text_color_info>",
                           "\nThese colored text blocks are likely form instructions - delete and replace with actual content.\n"),
        ]

        data_keys = ['font_size', 'font_family', 'align', 'italic', 'text_color']
        for builder, data_key in zip(builders, data_keys):
            section = builder.build(collected_data[data_key])
            if section:
                sections.append(section)

        start_text = "<style_info>\nThe below are style information scanned from the document that the user paired to the assistant.\n\n"
        end_text = "</style_info>\n\n"

        return start_text + "\n".join(sections) + "\n" + end_text if sections else start_text + end_text

    def extract_cvd(
        self, page_range: Dict[str, int]
    ) -> Tuple[str, Dict[int, Tuple[int, int, int]]] | None:
        """CVD 추출 (Strategy + Chain of Responsibility + Builder + Template Method 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Dict as DictType, Tuple as TupleType
        from abc import ABC, abstractmethod

        @dataclass
        class PageRange:
            """페이지 범위 (불변)"""
            start: int
            end: int
            current: Opt[int] = None

            def is_valid(self) -> bool:
                """범위 유효성 검사"""
                return (
                    self.start >= 1
                    and self.end >= 1
                    and self.start <= self.end
                )

        @dataclass
        class ExtractionResult:
            """추출 결과"""
            cvd_text: str
            id_to_pos: DictType[int, TupleType[int, int, int]]

            def to_tuple(self) -> TupleType[str, DictType[int, TupleType[int, int, int]]]:
                return (self.cvd_text, self.id_to_pos)

        class PageRangeExtractor:
            """페이지 범위 추출기 (Strategy)"""
            START_KEYS = ("start_page", "start", "startPage")
            END_KEYS = ("end_page", "end", "endPage")

            @classmethod
            def extract(cls, raw_range: DictType) -> Opt[TupleType[int, int]]:
                """딕셔너리에서 start/end 추출"""
                start = cls._extract_value(raw_range, cls.START_KEYS)
                end = cls._extract_value(raw_range, cls.END_KEYS)

                if start is None or end is None:
                    return None

                return (start, end)

            @staticmethod
            def _extract_value(data: DictType, keys: TupleType[str, ...]) -> Opt[int]:
                """여러 키 시도"""
                for key in keys:
                    value = data.get(key)
                    if value is not None:
                        return value
                return None

        class Validator(ABC):
            """검증기 (추상, Chain of Responsibility)"""
            def __init__(self):
                self.next_validator: Opt[Validator] = None

            def set_next(self, validator: 'Validator') -> 'Validator':
                """다음 검증기 설정"""
                self.next_validator = validator
                return validator

            def validate(self, raw_range: DictType, logger) -> Opt[PageRange]:
                """검증 실행"""
                result = self._do_validate(raw_range, logger)
                if result is None:
                    return None
                if self.next_validator:
                    return self.next_validator.validate(raw_range, logger)
                return result

            @abstractmethod
            def _do_validate(self, raw_range: DictType, logger) -> Opt[PageRange]:
                pass

        class NoneValidator(Validator):
            """None 검증"""
            def _do_validate(self, raw_range: DictType, logger) -> Opt[PageRange]:
                extracted = PageRangeExtractor.extract(raw_range)
                if extracted is None:
                    logger(f"start_page or end_page is None {raw_range}", "ERROR")
                    return None
                start, end = extracted
                current = raw_range.get("current_page")
                return PageRange(start, end, current)

        class TypeValidator(Validator):
            """타입 검증"""
            def _do_validate(self, raw_range: DictType, logger) -> Opt[PageRange]:
                extracted = PageRangeExtractor.extract(raw_range)
                if extracted is None:
                    return None
                start, end = extracted
                if not isinstance(start, int) or not isinstance(end, int):
                    logger(f"start_page or end_page is not int {raw_range}", "ERROR")
                    return None
                current = raw_range.get("current_page")
                return PageRange(start, end, current)

        class RangeValidator(Validator):
            """범위 검증"""
            def _do_validate(self, raw_range: DictType, logger) -> Opt[PageRange]:
                extracted = PageRangeExtractor.extract(raw_range)
                if extracted is None:
                    return None
                start, end = extracted
                current = raw_range.get("current_page")
                page_range_obj = PageRange(start, end, current)

                if not page_range_obj.is_valid():
                    logger(f"start_page or end_page is invalid {raw_range}", "ERROR")
                    return None
                return page_range_obj

        class CVDBuilder:
            """CVD 빌더"""
            @staticmethod
            def build(prefix: str, body: str) -> str:
                """CVD 조합"""
                return prefix + "<main_content>\n" + body + "</main_content>\n"

        class CVDExtractionPipeline:
            """CVD 추출 파이프라인 (Template Method)"""
            def __init__(self, extractor, logger, hwp):
                self.extractor = extractor
                self.logger = logger
                self.hwp = hwp

            def execute(self, page_range: PageRange) -> Opt[ExtractionResult]:
                """파이프라인 실행"""
                # 1. 요소 추출
                self.extractor.extract_elements(page_range.start, page_range.end)
                if not self.extractor.extracted_elements:
                    return None

                # 2. prefix 생성
                prefix = self.extractor._create_page_range_prefix(
                    page_range.start, page_range.end
                )

                # 3. 콘텐츠 추출
                extracted = self.extractor._extract_content_and_id_to_pos_from_extracted_elements()
                if not extracted:
                    self.logger("❌ cvd 추출 결과 None", "ERROR")
                    return None

                body, id_to_pos = extracted

                # 4. CVD 빌드
                cvd = CVDBuilder.build(prefix, body)

                # 5. 페이지 이동 (optional)
                if page_range.current is not None:
                    self.extractor._go_to_page(self.hwp, page_range.current)

                # 6. Dev 모드 저장 (optional)
                self._save_if_dev(cvd)

                return ExtractionResult(cvd, id_to_pos)

            def _save_if_dev(self, cvd: str) -> None:
                """개발 모드에서 CVD 저장"""
                import os
                try:
                    is_dev = os.environ.get("NODE_ENV") == "development"
                    if is_dev:
                        self.extractor._save_cvd_markdown(cvd)
                except Exception as e:
                    self.logger(f"CVD 저장 중 오류: {e}", "ERROR")

        # 메인 실행
        # 1. 검증 체인 구성
        none_validator = NoneValidator()
        type_validator = TypeValidator()
        range_validator = RangeValidator()

        none_validator.set_next(type_validator).set_next(range_validator)

        # 2. 검증 실행
        validated_range = none_validator.validate(page_range, self.log_to_main)
        if validated_range is None:
            return None

        self.log_to_main(f"extract_cvd page_range: {page_range}")

        # 3. 파이프라인 실행
        pipeline = CVDExtractionPipeline(self, self.log_to_main, self.hwp)
        result = pipeline.execute(validated_range)

        if result is None:
            return None

        return result.to_tuple()

    def reset_cache(self) -> None:
        """Clear cached extraction data to release memory."""
        self.extracted_elements = []
        self.id_to_pos = {}
        self.pos_to_shape = {}
        self.pos_to_page = {}
        self.global_id = 1


class DocumentExtractor(CVDExtractor):
    """Backward-compatible alias."""

    pass

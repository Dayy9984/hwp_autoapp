"""
Markup Conversion Engine - Direct markup to HTML transformation.

Converts structured markup formats to HTML with support for 이전 버전 호환.
Designed to work with older format versions (pre-2022).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class MarkupConversionResult:
    """Markup → HTML conversion result"""
    html_content: str
    heading_mapping: Dict[str, str]  # style → text
    element_count: int
    outline_mapping: Dict[str, str] = field(default_factory=dict)  # 개요 레벨 매핑 (호환성)


class HeadingStyleMapper:
    """Heading style extractor (h1~h10)

    Extracts heading styles from markup STYLE elements and
    maps them to text content.
    """

    def __init__(self):
        self.heading_style_map: Dict[str, str] = {}

    def map_heading_styles(self, markup_root: ET.Element) -> Dict[str, str]:
        """Extract STYLE → text mapping

        Args:
            markup_root: Markup root element

        Returns:
            Dict[str, str]: style ID → text mapping
        """
        self.heading_style_map = {}

        try:
            # Find HSTYLE elements
            for hstyle in markup_root.iter("HSTYLE"):
                style_id = hstyle.get("id", "")
                style_type = hstyle.get("type", "")

                # Extract heading styles only (h1~h10)
                if style_type in ("h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8", "h9", "h10"):
                    # Extract style text (TEXT/CHAR within HSTYLE)
                    text_parts = []
                    for text_elem in hstyle.iter("TEXT"):
                        # Extract text from CHAR elements within TEXT
                        for char_elem in text_elem.findall("CHAR"):
                            if char_elem.text:
                                text_parts.append(char_elem.text)

                    if text_parts:
                        self.heading_style_map[style_id] = "".join(text_parts)

        except Exception as e:
            print(f"[HeadingStyleMapper] Heading style extraction failed: {e}", file=sys.stderr)

        return self.heading_style_map


class TableMarkupConverter:
    """Markup TABLE → HTML <table> conversion parser

    Handles nested tables, colspan/rowspan attributes.
    Parses Merge attributes safely for 이전 버전 호환.
    """

    def __init__(self, element_id_generator=None):
        """Initialize

        Args:
            element_id_generator: Element ID generation function (uses internal counter if not provided)
        """
        self._id_counter = 0
        self._id_generator = element_id_generator or self._default_id_generator

    def _default_id_generator(self) -> int:
        """Default ID generator"""
        self._id_counter += 1
        return self._id_counter

    def convert_table_structure(self, table_elem: ET.Element, depth: int = 0) -> str:
        """Convert TABLE element to HTML <table>

        Args:
            table_elem: Markup TABLE element
            depth: Nesting depth (for debugging)

        Returns:
            str: HTML <table> string
        """
        table_id = self._id_generator()
        html_parts = [f'<table id="tbl-{table_id}">']

        try:
            # Iterate ROW
            for row_elem in table_elem.findall("ROW"):
                html_parts.append("<tr>")

                # Iterate CELL
                for cell_elem in row_elem.findall("CELL"):
                    cell_html = self._convert_cell_element(cell_elem, depth)
                    html_parts.append(cell_html)

                html_parts.append("</tr>")

        except Exception as e:
            print(f"[TableMarkupConverter] Table parsing failed (depth={depth}): {e}", file=sys.stderr)
            return f'<table id="tbl-{table_id}"><tr><td>[Parsing failed]</td></tr></table>'

        html_parts.append("</table>")
        return "\n".join(html_parts)

    def _convert_cell_element(self, cell_elem: ET.Element, depth: int) -> str:
        """Convert CELL element to HTML <td>

        Args:
            cell_elem: Markup CELL element
            depth: Nesting depth

        Returns:
            str: HTML <td> string
        """
        cell_id = self._id_generator()

        # Extract colspan/rowspan
        colspan, rowspan = self._retrieve_merge_attributes(cell_elem)

        # Generate attributes
        attrs = [f'id="cell-{cell_id}"']
        if colspan > 1:
            attrs.append(f'colspan="{colspan}"')
        if rowspan > 1:
            attrs.append(f'rowspan="{rowspan}"')

        # Extract cell content
        cell_content = self._retrieve_cell_content(cell_elem, depth)

        return f'<td {" ".join(attrs)}>{cell_content}</td>'

    def _retrieve_merge_attributes(self, cell_elem: ET.Element) -> Tuple[int, int]:
        """Extract colspan/rowspan from CELL Merge attribute

        Returns default values (1, 1) if attribute doesn't exist for 이전 버전 호환

        Args:
            cell_elem: Markup CELL element

        Returns:
            Tuple[int, int]: (colspan, rowspan)
        """
        colspan, rowspan = 1, 1

        try:
            # Merge attribute (may not exist in 이전 버전)
            merge_attr = cell_elem.get("Merge")
            if merge_attr:
                # Stored as Merge="2,3" format (colspan, rowspan)
                parts = merge_attr.split(",")
                if len(parts) >= 1:
                    colspan = int(parts[0]) if parts[0].strip() else 1
                if len(parts) >= 2:
                    rowspan = int(parts[1]) if parts[1].strip() else 1

        except Exception as e:
            print(f"[TableMarkupConverter] Merge attribute parsing failed: {e}, using defaults", file=sys.stderr)

        return colspan, rowspan

    def _retrieve_cell_content(self, cell_elem: ET.Element, depth: int) -> str:
        """Extract cell internal text and nested tables

        Args:
            cell_elem: Markup CELL element
            depth: Nesting depth

        Returns:
            str: Cell content (HTML)
        """
        content_parts = []

        try:
            # Iterate PARALIST
            for para_list in cell_elem.findall("PARALIST"):
                # Check for nested tables
                nested_tables = para_list.findall(".//TABLE")
                if nested_tables:
                    # Process nested tables
                    for nested_table in nested_tables:
                        nested_html = self.convert_table_structure(nested_table, depth + 1)
                        content_parts.append(nested_html)

                # Extract regular text
                for p_elem in para_list.findall("P"):
                    para_text = self._retrieve_paragraph_text(p_elem)
                    if para_text.strip():
                        content_parts.append(para_text)

        except Exception as e:
            print(f"[TableMarkupConverter] Cell content extraction failed (depth={depth}): {e}", file=sys.stderr)
            return "[Extraction failed]"

        # Return empty string if no content
        if not content_parts:
            return ""

        # Wrap in <p> tags (exclude nested tables)
        result = []
        for part in content_parts:
            if part.startswith("<table"):
                result.append(part)
            else:
                result.append(f"<p>{part}</p>")

        return "".join(result)

    def _retrieve_paragraph_text(self, p_elem: ET.Element) -> str:
        """Extract text from P element

        Args:
            p_elem: Markup P element

        Returns:
            str: Paragraph text
        """
        text_parts = []

        try:
            # Iterate TEXT/CHAR
            for text_elem in p_elem.findall("TEXT"):
                for char_elem in text_elem.findall("CHAR"):
                    if char_elem.text:
                        text_parts.append(char_elem.text)

        except Exception as e:
            print(f"[TableMarkupConverter] Paragraph text extraction failed: {e}", file=sys.stderr)

        return "".join(text_parts)


class MarkupConverter:
    """Markup → HTML direct conversion engine

    Parses structured markup and converts to HTML.
    Performs safe parsing for 이전 버전(pre-2022) compatibility.
    """

    def __init__(self):
        self._id_counter = 0
        self.heading_mapper = HeadingStyleMapper()
        self.table_converter = TableMarkupConverter(self._generate_id)

    def _generate_id(self) -> int:
        """Generate element ID"""
        self._id_counter += 1
        return self._id_counter

    def convert_from_markup_file(self, markup_path: Path) -> Optional[MarkupConversionResult]:
        """Extract HTML from markup file

        Args:
            markup_path: Markup file path

        Returns:
            Optional[MarkupConversionResult]: Conversion result or None (on failure)
        """
        try:
            with open(markup_path, 'r', encoding='utf-8') as f:
                markup_text = f.read()

            return self.convert_from_markup_text(markup_text)

        except Exception as e:
            print(f"[MarkupConverter] Markup file read failed: {e}", file=sys.stderr)
            return None

    def convert_from_markup_text(self, markup_text: str) -> Optional[MarkupConversionResult]:
        """Extract HTML from markup text

        Args:
            markup_text: Markup string

        Returns:
            Optional[MarkupConversionResult]: Conversion result or None (on failure)
        """
        try:
            # Parse XML
            root = ET.fromstring(markup_text)

            # Extract heading styles
            heading_mapping = self.heading_mapper.map_heading_styles(root)

            # Convert to HTML
            html_content = self.convert_markup_to_html(markup_text)

            return MarkupConversionResult(
                html_content=html_content,
                heading_mapping=heading_mapping,
                element_count=self._id_counter,
                outline_mapping=heading_mapping.copy()  # 호환성: outline_mapping = heading_mapping
            )

        except Exception as e:
            print(f"[MarkupConverter] Markup extraction failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def extract_from_hwpml_file(self, markup_path: Path) -> Optional[MarkupConversionResult]:
        """HWPML 파일에서 HTML 추출 (hdml_service.py, main.py 호환용)

        Args:
            markup_path: HWPML 파일 경로

        Returns:
            Optional[MarkupConversionResult]: 변환 결과 (outline_mapping 포함)
        """
        return self.convert_from_markup_file(markup_path)

    def convert_markup_to_html(self, markup_text: str) -> str:
        """Convert markup text to HTML

        Args:
            markup_text: Markup string

        Returns:
            str: HTML string
        """
        html_parts = []

        try:
            root = ET.fromstring(markup_text)

            # BODY → SECTION iteration
            body = root.find("BODY")
            if body is None:
                print(f"[MarkupConverter] BODY element not found", file=sys.stderr)
                return "<p>[BODY missing]</p>"

            for section in body.findall("SECTION"):
                section_html = self._convert_section_element(section)
                if section_html:
                    html_parts.append(section_html)

        except Exception as e:
            print(f"[MarkupConverter] HTML conversion failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return "<p>[Conversion failed]</p>"

        return "\n".join(html_parts)

    def _convert_section_element(self, section_elem: ET.Element) -> str:
        """Convert SECTION element to HTML

        Actual markup structure: SECTION → P (direct children)

        Args:
            section_elem: Markup SECTION element

        Returns:
            str: HTML string
        """
        html_parts = []

        try:
            # Iterate P elements directly (direct children of SECTION)
            for p_elem in section_elem.findall("P"):
                para_html = self._convert_paragraph_element(p_elem)
                if para_html and para_html.strip():
                    html_parts.append(para_html)

        except Exception as e:
            print(f"[MarkupConverter] SECTION conversion failed: {e}", file=sys.stderr)

        return "\n".join(html_parts)


    def _convert_paragraph_element(self, p_elem: ET.Element) -> str:
        """Convert P element to HTML

        P may contain TABLE, so process both TEXT and TABLE.

        Args:
            p_elem: Markup P element

        Returns:
            str: HTML string
        """
        para_id = self._generate_id()
        html_parts = []

        try:
            # Iterate TEXT (includes both TABLE and regular text)
            for text_elem in p_elem.findall("TEXT"):
                # Process TABLE within TEXT
                tables = text_elem.findall("TABLE")
                for table_elem in tables:
                    table_html = self.table_converter.convert_table_structure(table_elem)
                    html_parts.append(table_html)

                # CHAR within TEXT (regular text)
                text_parts = []
                for char_elem in text_elem.findall("CHAR"):
                    if char_elem.text:
                        text_parts.append(char_elem.text)

                if text_parts:
                    para_text = "".join(text_parts)
                    html_parts.append(f'<p id="para-{para_id}">{para_text}</p>')

        except Exception as e:
            print(f"[MarkupConverter] P conversion failed: {e}", file=sys.stderr)

        return "\n".join(html_parts)

    def retrieve_heading_style_mapping(self, markup_text: str) -> Dict[str, str]:
        """Extract heading style (h1~h10) mapping (for external API)

        Args:
            markup_text: Markup string

        Returns:
            Dict[str, str]: style ID → text mapping
        """
        try:
            root = ET.fromstring(markup_text)
            return self.heading_mapper.map_heading_styles(root)

        except Exception as e:
            print(f"[MarkupConverter] Heading style extraction failed: {e}", file=sys.stderr)
            return {}

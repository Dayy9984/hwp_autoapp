# -*- coding: utf-8 -*-
"""
HWP Parser for RAG-Anything
커스텀 파서: pyhwpx 기반 한글 문서 파싱
"""

import os
from typing import Dict, List, Any, Optional
from pathlib import Path


class HWPParser:
    """
    RAG-Anything용 HWP 파서
    .hwp, .hwpx 파일 지원
    """

    def __init__(self):
        self.supported_formats = ['.hwp', '.hwpx']
        self.name = "HWPParser"

    def can_parse(self, file_path: str) -> bool:
        """파일 확장자로 파싱 가능 여부 확인"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_formats

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        HWP 파일 파싱

        Returns:
            {
                'text': str,           # 전체 텍스트
                'tables': List[Dict],  # 표 데이터
                'fields': Dict,        # 필드 데이터
                'metadata': Dict       # 메타데이터
            }
        """
        if not self.can_parse(file_path):
            raise ValueError(f"Unsupported file format: {file_path}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            # pyhwpx import
            from pyhwpx import Hwp

            # HWP 열기 (백그라운드)
            hwp = Hwp(visible=False)
            hwp.Open(file_path)

            # 1. 전체 텍스트 추출
            text = self._extract_text(hwp)

            # 2. 표 추출
            tables = self._extract_tables(hwp)

            # 3. 필드 추출
            fields = self._extract_fields(hwp)

            # 4. 메타데이터
            metadata = self._extract_metadata(hwp, file_path)

            # HWP 닫기
            hwp.Quit()

            return {
                'text': text,
                'tables': tables,
                'fields': fields,
                'metadata': metadata
            }

        except ImportError:
            raise ImportError("pyhwpx not installed. Run: uv pip install pyhwpx")
        except Exception as e:
            raise RuntimeError(f"HWP parsing failed: {str(e)}")

    def _extract_text(self, hwp) -> str:
        """전체 텍스트 추출"""
        try:
            hwp.Run("SelectAll")
            hwp.Run("Copy")
            text = hwp.GetText()
            hwp.Run("Cancel")
            return text
        except Exception:
            return ""

    def _extract_tables(self, hwp) -> List[Dict[str, Any]]:
        """표 추출"""
        tables = []
        try:
            ctrl = hwp.HeadCtrl
            while ctrl:
                if ctrl.UserDesc == "표":
                    table_data = self._parse_table(ctrl)
                    if table_data:
                        tables.append(table_data)
                ctrl = ctrl.Next
        except Exception:
            pass
        return tables

    def _parse_table(self, ctrl) -> Optional[Dict[str, Any]]:
        """개별 표 파싱"""
        try:
            table = ctrl.Table
            rows = table.Rows
            cols = table.Cols

            data = []
            for r in range(rows):
                row_data = []
                for c in range(cols):
                    cell = table.Cell(r, c)
                    cell_text = cell.Text if hasattr(cell, 'Text') else ""
                    row_data.append(cell_text)
                data.append(row_data)

            return {
                'rows': rows,
                'cols': cols,
                'data': data
            }
        except Exception:
            return None

    def _extract_fields(self, hwp) -> Dict[str, str]:
        """필드 추출 (누름틀 등)"""
        fields = {}
        try:
            ctrl = hwp.HeadCtrl
            while ctrl:
                if hasattr(ctrl, 'Name') and hasattr(ctrl, 'Text'):
                    field_name = ctrl.Name
                    field_value = ctrl.Text
                    if field_name:
                        fields[field_name] = field_value
                ctrl = ctrl.Next
        except Exception:
            pass
        return fields

    def _extract_metadata(self, hwp, file_path: str) -> Dict[str, Any]:
        """메타데이터 추출"""
        metadata = {
            'file_name': os.path.basename(file_path),
            'file_path': file_path,
            'file_size': os.path.getsize(file_path),
            'parser': self.name
        }

        try:
            # 문서 정보
            metadata['title'] = hwp.SummaryInfo.Title
            metadata['author'] = hwp.SummaryInfo.Author
            metadata['subject'] = hwp.SummaryInfo.Subject
        except Exception:
            pass

        return metadata

    def parse_to_markdown(self, file_path: str) -> str:
        """
        HWP → Markdown 변환
        RAG-Anything 통합용
        """
        parsed = self.parse(file_path)

        markdown_parts = []

        # 메타데이터
        if parsed['metadata'].get('title'):
            markdown_parts.append(f"# {parsed['metadata']['title']}\n")

        # 본문 텍스트
        if parsed['text']:
            markdown_parts.append(parsed['text'])

        # 표
        for i, table in enumerate(parsed['tables'], 1):
            markdown_parts.append(f"\n## 표 {i}\n")
            markdown_parts.append(self._table_to_markdown(table))

        # 필드
        if parsed['fields']:
            markdown_parts.append("\n## 필드 정보\n")
            for name, value in parsed['fields'].items():
                markdown_parts.append(f"- **{name}**: {value}\n")

        return "\n".join(markdown_parts)

    def _table_to_markdown(self, table: Dict[str, Any]) -> str:
        """표 → Markdown 표 변환"""
        if not table or not table.get('data'):
            return ""

        data = table['data']
        if len(data) == 0:
            return ""

        # 헤더 행
        header = "| " + " | ".join(data[0]) + " |"
        separator = "|" + "|".join(["---"] * len(data[0])) + "|"

        # 데이터 행
        rows = [header, separator]
        for row in data[1:]:
            rows.append("| " + " | ".join(row) + " |")

        return "\n".join(rows)


# RAG-Anything 통합용 래퍼
def get_hwp_parser():
    """RAG-Anything에서 사용할 파서 인스턴스 반환"""
    return HWPParser()

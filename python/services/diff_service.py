# -*- coding: utf-8 -*-
"""
Diff Service
Template과 Filled HDML 비교 서비스

HDML 포맷 (HTML-like):
- <td ID [colspan="N"] [rowspan="N"]><PARA_ID>텍스트</td>
- <ID>텍스트  (독립 paragraph)
- <textbox ID><PARA_ID>텍스트
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from .config import config


class HDMLParser:
    """HDML HTML-like 포맷 파서

    지원 포맷:
    1. document_extractor.py 생성 포맷:
       - <td id="cell-N"> ... </td>
       - <p id="para-N">text</p>

    2. 레거시 HDML 포맷:
       - <td N><PARA_ID>text</td>
       - <N>text
    """

    # 정규표현식 패턴 - document_extractor.py 포맷
    # <td id="cell-N" [attrs]>
    TD_CELL_PATTERN = re.compile(
        r'<td\s+id="cell-(\d+)"([^>]*)>',
        re.IGNORECASE
    )
    # <p id="para-N">text</p>
    PARA_ID_PATTERN = re.compile(
        r'<p\s+id="para-(\d+)"[^>]*>([^<]*)</p>',
        re.IGNORECASE
    )
    # <p>text</p> (id 없는 경우)
    PARA_SIMPLE_PATTERN = re.compile(
        r'<p>([^<]*)</p>',
        re.IGNORECASE
    )

    # 정규표현식 패턴 - 레거시 HDML 포맷
    # <td ID [attrs]> 또는 <td ID attrs>
    TD_PATTERN = re.compile(
        r'<td\s+(\d+)(?:\s+[^>]*)?>',
        re.IGNORECASE
    )
    # <ID>text 형태의 paragraph (td 내부 또는 독립)
    PARA_PATTERN = re.compile(r'<(\d+)>([^<]*)')

    # 공통 패턴
    # </td> 닫는 태그
    TD_CLOSE_PATTERN = re.compile(r'</td>', re.IGNORECASE)
    # <textbox ID>
    TEXTBOX_PATTERN = re.compile(r'<textbox\s+(\d+)>', re.IGNORECASE)
    # <table> 태그
    TABLE_PATTERN = re.compile(r'</?table[^>]*>', re.IGNORECASE)
    # <tr> 태그
    TR_PATTERN = re.compile(r'</?tr[^>]*>', re.IGNORECASE)
    # <main_content> 섹션
    MAIN_CONTENT_PATTERN = re.compile(
        r'<main_content>(.*?)</main_content>',
        re.DOTALL | re.IGNORECASE
    )

    def parse(self, hdml_text: str) -> List[Dict[str, Any]]:
        """
        HDML 텍스트를 파싱하여 블록 리스트 반환

        Args:
            hdml_text: HDML 파일 내용

        Returns:
            List[Dict]: [
                {
                    'id': int,
                    'type': 'td' | 'paragraph' | 'textbox',
                    'text': str,
                    'parent_id': Optional[int],  # td 내부 paragraph의 경우 td ID
                    'attrs': Dict  # colspan, rowspan 등
                },
                ...
            ]
        """
        # 포맷 자동 감지
        if 'id="cell-' in hdml_text or 'id="para-' in hdml_text:
            return self._parse_extractor_format(hdml_text)
        else:
            return self._parse_legacy_format(hdml_text)

    def _parse_extractor_format(self, hdml_text: str) -> List[Dict[str, Any]]:
        """
        document_extractor.py 생성 포맷 파싱
        - <td id="cell-N" [attrs]> ... </td>
        - <p id="para-N">text</p>
        - <p>text</p>
        """
        blocks = []
        para_counter = 10000  # id 없는 p 태그용 카운터

        # main_content 섹션 추출
        main_match = self.MAIN_CONTENT_PATTERN.search(hdml_text)
        content = main_match.group(1) if main_match else hdml_text

        # 라인별 파싱
        current_td_id = None
        current_td_text_parts = []

        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue

            # table, tr 태그는 스킵
            if self.TABLE_PATTERN.match(line) or self.TR_PATTERN.match(line):
                continue

            # td 시작 태그 처리: <td id="cell-N" ...>
            td_match = self.TD_CELL_PATTERN.search(line)
            if td_match:
                # 이전 td 플러시
                if current_td_id is not None:
                    self._flush_td(blocks, current_td_id, current_td_text_parts)

                current_td_id = int(td_match.group(1))
                current_td_text_parts = []
                attrs = self._extract_td_attrs(line)

                blocks.append({
                    'id': current_td_id,
                    'type': 'td',
                    'text': '',
                    'parent_id': None,
                    'attrs': attrs
                })

                # td 내부 paragraph 추출: <p id="para-N">text</p>
                for pm in self.PARA_ID_PATTERN.finditer(line):
                    para_id = int(pm.group(1))
                    para_text = pm.group(2).strip()
                    blocks.append({
                        'id': para_id,
                        'type': 'paragraph',
                        'text': para_text,
                        'parent_id': current_td_id,
                        'attrs': {}
                    })
                    current_td_text_parts.append(para_text)

                # id 없는 <p>text</p>도 처리
                for pm in self.PARA_SIMPLE_PATTERN.finditer(line):
                    para_text = pm.group(1).strip()
                    if para_text:
                        para_counter += 1
                        blocks.append({
                            'id': para_counter,
                            'type': 'paragraph',
                            'text': para_text,
                            'parent_id': current_td_id,
                            'attrs': {}
                        })
                        current_td_text_parts.append(para_text)

                # </td>로 끝나면 td 종료
                if self.TD_CLOSE_PATTERN.search(line):
                    self._flush_td(blocks, current_td_id, current_td_text_parts)
                    current_td_id = None
                    current_td_text_parts = []
                continue

            # </td> 닫는 태그
            if self.TD_CLOSE_PATTERN.search(line):
                if current_td_id is not None:
                    # 닫기 전 paragraph 추출
                    for pm in self.PARA_ID_PATTERN.finditer(line):
                        para_id = int(pm.group(1))
                        para_text = pm.group(2).strip()
                        blocks.append({
                            'id': para_id,
                            'type': 'paragraph',
                            'text': para_text,
                            'parent_id': current_td_id,
                            'attrs': {}
                        })
                        current_td_text_parts.append(para_text)

                    for pm in self.PARA_SIMPLE_PATTERN.finditer(line):
                        para_text = pm.group(1).strip()
                        if para_text:
                            para_counter += 1
                            blocks.append({
                                'id': para_counter,
                                'type': 'paragraph',
                                'text': para_text,
                                'parent_id': current_td_id,
                                'attrs': {}
                            })
                            current_td_text_parts.append(para_text)

                    self._flush_td(blocks, current_td_id, current_td_text_parts)
                    current_td_id = None
                    current_td_text_parts = []
                continue

            # td 내부 멀티라인
            if current_td_id is not None:
                for pm in self.PARA_ID_PATTERN.finditer(line):
                    para_id = int(pm.group(1))
                    para_text = pm.group(2).strip()
                    blocks.append({
                        'id': para_id,
                        'type': 'paragraph',
                        'text': para_text,
                        'parent_id': current_td_id,
                        'attrs': {}
                    })
                    current_td_text_parts.append(para_text)

                for pm in self.PARA_SIMPLE_PATTERN.finditer(line):
                    para_text = pm.group(1).strip()
                    if para_text:
                        para_counter += 1
                        blocks.append({
                            'id': para_counter,
                            'type': 'paragraph',
                            'text': para_text,
                            'parent_id': current_td_id,
                            'attrs': {}
                        })
                        current_td_text_parts.append(para_text)
                continue

            # 독립 paragraph: <p id="para-N">text</p>
            for pm in self.PARA_ID_PATTERN.finditer(line):
                para_id = int(pm.group(1))
                para_text = pm.group(2).strip()
                blocks.append({
                    'id': para_id,
                    'type': 'paragraph',
                    'text': para_text,
                    'parent_id': None,
                    'attrs': {}
                })

        # 마지막 td 플러시
        if current_td_id is not None:
            self._flush_td(blocks, current_td_id, current_td_text_parts)

        return blocks

    def _parse_legacy_format(self, hdml_text: str) -> List[Dict[str, Any]]:
        """
        레거시 HDML 포맷 파싱
        - <td N><PARA_ID>text</td>
        - <N>text
        """
        blocks = []

        # main_content 섹션 추출
        main_match = self.MAIN_CONTENT_PATTERN.search(hdml_text)
        content = main_match.group(1) if main_match else hdml_text

        # 라인별 파싱
        current_td_id = None
        current_td_text_parts = []

        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue

            # table, tr 태그는 스킵
            if self.TABLE_PATTERN.match(line) or self.TR_PATTERN.match(line):
                continue

            # td 시작 태그 처리
            td_match = self.TD_PATTERN.search(line)
            if td_match:
                # 이전 td가 있으면 저장
                if current_td_id is not None:
                    self._flush_td(blocks, current_td_id, current_td_text_parts)

                current_td_id = int(td_match.group(1))
                current_td_text_parts = []

                # attrs 추출 (colspan, rowspan)
                attrs = self._extract_td_attrs(line)
                blocks.append({
                    'id': current_td_id,
                    'type': 'td',
                    'text': '',  # 나중에 업데이트
                    'parent_id': None,
                    'attrs': attrs
                })

                # td 내부의 paragraph 추출
                remaining = line[td_match.end():]
                para_matches = list(self.PARA_PATTERN.finditer(remaining))
                for pm in para_matches:
                    para_id = int(pm.group(1))
                    para_text = pm.group(2).strip()
                    blocks.append({
                        'id': para_id,
                        'type': 'paragraph',
                        'text': para_text,
                        'parent_id': current_td_id,
                        'attrs': {}
                    })
                    current_td_text_parts.append(para_text)

                # </td>로 끝나면 td 종료
                if self.TD_CLOSE_PATTERN.search(line):
                    self._flush_td(blocks, current_td_id, current_td_text_parts)
                    current_td_id = None
                    current_td_text_parts = []
                continue

            # </td> 닫는 태그
            if self.TD_CLOSE_PATTERN.search(line):
                if current_td_id is not None:
                    # 닫기 전 라인의 paragraph 추출
                    para_matches = list(self.PARA_PATTERN.finditer(line))
                    for pm in para_matches:
                        para_id = int(pm.group(1))
                        para_text = pm.group(2).strip()
                        blocks.append({
                            'id': para_id,
                            'type': 'paragraph',
                            'text': para_text,
                            'parent_id': current_td_id,
                            'attrs': {}
                        })
                        current_td_text_parts.append(para_text)

                    self._flush_td(blocks, current_td_id, current_td_text_parts)
                    current_td_id = None
                    current_td_text_parts = []
                continue

            # textbox 처리
            textbox_match = self.TEXTBOX_PATTERN.search(line)
            if textbox_match:
                textbox_id = int(textbox_match.group(1))
                blocks.append({
                    'id': textbox_id,
                    'type': 'textbox',
                    'text': '',
                    'parent_id': None,
                    'attrs': {}
                })
                # textbox 내부 paragraph
                remaining = line[textbox_match.end():]
                para_matches = list(self.PARA_PATTERN.finditer(remaining))
                for pm in para_matches:
                    para_id = int(pm.group(1))
                    para_text = pm.group(2).strip()
                    blocks.append({
                        'id': para_id,
                        'type': 'paragraph',
                        'text': para_text,
                        'parent_id': textbox_id,
                        'attrs': {}
                    })
                continue

            # td 내부에 있는 경우 (멀티라인 td)
            if current_td_id is not None:
                para_matches = list(self.PARA_PATTERN.finditer(line))
                for pm in para_matches:
                    para_id = int(pm.group(1))
                    para_text = pm.group(2).strip()
                    blocks.append({
                        'id': para_id,
                        'type': 'paragraph',
                        'text': para_text,
                        'parent_id': current_td_id,
                        'attrs': {}
                    })
                    current_td_text_parts.append(para_text)
                continue

            # 독립 paragraph (<ID>text 형태)
            para_matches = list(self.PARA_PATTERN.finditer(line))
            for pm in para_matches:
                para_id = int(pm.group(1))
                para_text = pm.group(2).strip()
                blocks.append({
                    'id': para_id,
                    'type': 'paragraph',
                    'text': para_text,
                    'parent_id': None,
                    'attrs': {}
                })

        # 마지막 td 처리
        if current_td_id is not None:
            self._flush_td(blocks, current_td_id, current_td_text_parts)

        return blocks

    def _extract_td_attrs(self, line: str) -> Dict[str, Any]:
        """td 태그에서 colspan, rowspan 등 속성 추출"""
        attrs = {}

        colspan_match = re.search(r'colspan\s*=?\s*["\']?(\d+)["\']?', line, re.IGNORECASE)
        if colspan_match:
            attrs['colspan'] = int(colspan_match.group(1))

        rowspan_match = re.search(r'rowspan\s*=?\s*["\']?(\d+)["\']?', line, re.IGNORECASE)
        if rowspan_match:
            attrs['rowspan'] = int(rowspan_match.group(1))

        return attrs

    def _flush_td(self, blocks: List[Dict], td_id: int, text_parts: List[str]) -> None:
        """td 블록의 텍스트를 업데이트"""
        for block in blocks:
            if block['id'] == td_id and block['type'] == 'td':
                block['text'] = '\n'.join(text_parts)
                break


class DiffService:
    """Diff 생성 서비스 (위치 기반 매칭)"""

    def _extract_table_cells(self, hdml_text: str) -> List[Dict]:
        """
        HDML에서 표 셀 추출 (위치 기반)

        Returns:
            List[Dict]: [{
                'table_idx': int,
                'row': int,
                'col': int,
                'colspan': int,
                'rowspan': int,
                'td_id': Optional[int],
                'text': str,
                'color': Optional[str],
                'font_size': Optional[str],
                'width': Optional[int],
                'height': Optional[int]
            }, ...]
        """
        tables = re.findall(r'<table>(.*?)</table>', hdml_text, re.DOTALL | re.IGNORECASE)

        all_cells = []

        for table_idx, table_content in enumerate(tables):
            rows = re.findall(r'<tr>(.*?)</tr>', table_content, re.DOTALL | re.IGNORECASE)

            for row_idx, row_content in enumerate(rows):
                td_pattern = re.compile(r'<td([^>]*)>(.*?)</td>', re.DOTALL | re.IGNORECASE)

                col_idx = 0
                for td_match in td_pattern.finditer(row_content):
                    attrs_str = td_match.group(1)
                    td_inner = td_match.group(2)

                    # ID 추출
                    id_match = re.search(r'id="(\d+)"', attrs_str)
                    td_id = int(id_match.group(1)) if id_match else None

                    # colspan, rowspan 추출
                    colspan_match = re.search(r'colspan="(\d+)"', attrs_str)
                    rowspan_match = re.search(r'rowspan="(\d+)"', attrs_str)
                    colspan = int(colspan_match.group(1)) if colspan_match else 1
                    rowspan = int(rowspan_match.group(1)) if rowspan_match else 1

                    # 스타일 속성 추출
                    color_match = re.search(r'color="([^"]*)"', attrs_str)
                    font_size_match = re.search(r'font-size="([^"]*)"', attrs_str)
                    width_match = re.search(r'width="(\d+)"', attrs_str)
                    height_match = re.search(r'height="(\d+)"', attrs_str)

                    color = color_match.group(1) if color_match else None
                    font_size = font_size_match.group(1) if font_size_match else None
                    width = int(width_match.group(1)) if width_match else None
                    height = int(height_match.group(1)) if height_match else None

                    # 셀 내 모든 텍스트 추출
                    p_texts = re.findall(r'<p[^>]*>([^<]*)</p>', td_inner)
                    text = '\n'.join(p_texts).strip()

                    all_cells.append({
                        'table_idx': table_idx,
                        'row': row_idx,
                        'col': col_idx,
                        'colspan': colspan,
                        'rowspan': rowspan,
                        'td_id': td_id,
                        'text': text,
                        'color': color,
                        'font_size': font_size,
                        'width': width,
                        'height': height
                    })

                    col_idx += 1

        return all_cells

    def generate_diff_v2_position_based(
        self,
        template_hdml_path: str,
        filled_hdml_path: str,
        output_path: str,
        pair_id: str
    ) -> Dict[str, Any]:
        """
        위치 기반 diff 생성 (v2)

        표 구조가 동일한 경우 ID가 아닌 위치(table, row, col)로 매칭
        paragraph ID 불일치 문제 해결

        Args:
            template_hdml_path: 템플릿 HDML 경로
            filled_hdml_path: 작성본 HDML 경로
            output_path: diff.json 출력 경로
            pair_id: Pair ID

        Returns:
            DiffResult 객체
        """
        # 1. HDML 로드
        with open(template_hdml_path, 'r', encoding='utf-8') as f:
            template_hdml_text = f.read()

        with open(filled_hdml_path, 'r', encoding='utf-8') as f:
            filled_hdml_text = f.read()

        # 2. 표 셀 추출 (위치 기반)
        template_cells = self._extract_table_cells(template_hdml_text)
        filled_cells = self._extract_table_cells(filled_hdml_text)

        # 3. 위치 기반 매칭 및 변경 탐지
        changes = []

        for t_cell in template_cells:
            # 같은 위치의 filled 셀 찾기
            matching_filled = [
                f_cell for f_cell in filled_cells
                if (f_cell['table_idx'] == t_cell['table_idx'] and
                    f_cell['row'] == t_cell['row'] and
                    f_cell['col'] == t_cell['col'] and
                    f_cell['colspan'] == t_cell['colspan'] and
                    f_cell['rowspan'] == t_cell['rowspan'])
            ]

            if not matching_filled:
                # 매칭 실패 (표 구조 변경)
                continue

            f_cell = matching_filled[0]

            # 텍스트 비교
            if t_cell['text'] != f_cell['text']:
                # 위치 키 생성
                position_key = f"t{t_cell['table_idx']}_r{t_cell['row']}_c{t_cell['col']}"

                # 변경 타입 결정
                if not t_cell['text'] and f_cell['text']:
                    change_type = 'added'
                elif t_cell['text'] and not f_cell['text']:
                    change_type = 'deleted'
                else:
                    change_type = 'modified'

                changes.append({
                    'positionKey': position_key,
                    'tableIndex': t_cell['table_idx'],
                    'row': t_cell['row'],
                    'col': t_cell['col'],
                    'colspan': t_cell['colspan'],
                    'rowspan': t_cell['rowspan'],
                    'templateTdId': t_cell['td_id'],
                    'filledTdId': f_cell['td_id'],
                    'templateValue': t_cell['text'],
                    'filledValue': f_cell['text'],
                    'changeType': change_type,
                    # 스타일 속성
                    'templateColor': t_cell['color'],
                    'filledColor': f_cell['color'],
                    'templateFontSize': t_cell['font_size'],
                    'filledFontSize': f_cell['font_size'],
                    'width': t_cell['width'] or f_cell['width'],
                    'height': t_cell['height'] or f_cell['height']
                })

        # 4. diff.json 생성
        diff_result = {
            'version': '2.0',
            'algorithm': 'position_based',
            'pairId': pair_id,
            'templateHdmlPath': template_hdml_path,
            'filledHdmlPath': filled_hdml_path,
            'createdAt': int(datetime.now().timestamp() * 1000),
            'changes': changes,
            'stats': {
                'totalCells': len(filled_cells),
                'changedCells': len(changes),
                'unchangedCells': len(filled_cells) - len(changes),
                'addedCells': len([c for c in changes if c['changeType'] == 'added']),
                'modifiedCells': len([c for c in changes if c['changeType'] == 'modified']),
                'deletedCells': len([c for c in changes if c['changeType'] == 'deleted'])
            }
        }

        # 5. 저장
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(diff_result, f, ensure_ascii=False, indent=2)

        return diff_result

    def generate_diff(
        self,
        template_hdml_path: str,
        filled_hdml_path: str,
        output_path: str,
        pair_id: str
    ) -> Dict[str, Any]:
        """
        위치 기반 diff 생성

        표 구조가 동일한 경우 ID가 아닌 위치(table, row, col)로 매칭
        기존 ID 기반 방식의 문제(ID 이동, paragraph 추가) 해결

        Args:
            template_hdml_path: 템플릿 HDML 경로
            filled_hdml_path: 작성본 HDML 경로
            output_path: diff.json 출력 경로
            pair_id: Pair ID

        Returns:
            DiffResult 객체
        """
        # v2 메서드 호출
        return self.generate_diff_v2_position_based(
            template_hdml_path,
            filled_hdml_path,
            output_path,
            pair_id
        )

    def generate_diff_for_pair(
        self,
        project_id: str,
        pair_id: str
    ) -> Dict[str, Any]:
        """
        주어진 Pair에 대해 diff 생성

        Args:
            project_id: 프로젝트 ID
            pair_id: Pair ID

        Returns:
            {
                success: bool,
                diff_path: str,
                changes_count: int,
                error?: str
            }
        """
        try:
            pair_path = config.get_template_pair_path(project_id, pair_id)

            template_hdml_path = os.path.join(pair_path, "template.hdml.md")
            filled_hdml_path = os.path.join(pair_path, "filled.hdml.md")
            output_path = os.path.join(pair_path, "diff.json")

            # HDML 존재 확인
            if not os.path.exists(template_hdml_path):
                return {
                    "success": False,
                    "error": f"Template HDML not found: {template_hdml_path}"
                }

            if not os.path.exists(filled_hdml_path):
                return {
                    "success": False,
                    "error": f"Filled HDML not found: {filled_hdml_path}"
                }

            # Diff 생성
            diff_result = self.generate_diff(
                template_hdml_path,
                filled_hdml_path,
                output_path,
                pair_id
            )

            return {
                "success": True,
                "diff_path": output_path,
                "changes_count": len(diff_result['changes']),
                "stats": diff_result['stats']
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

"""
DocumentView 생성기

HWP 문서를 구조화된 DocumentView 표현으로 변환하는 엔진
실제 HWP API를 사용하여 문서 요소 추출
"""

import sys
from typing import List, Dict, Tuple, Optional
from processing.structure.view_structures import Element, DocumentView
from engine.connection.document_connector import HwpConnector

from processing.detection.table_scanner import scan_table_with_markup


class DocumentViewGenerator:
    """HWP 문서 → DocumentView 변환 엔진

    실제 HWP API 메서드를 사용하여 문서 요소를 추출하고
    HTML 기반 구조화 표현으로 변환합니다.
    """

    def __init__(self, connector: HwpConnector):
        """초기화

        Args:
            connector: HWP COM 연결 관리 객체
        """
        self.connector = connector
        self.hwp = connector.hwp
        self._element_id_counter = 0

    def _generate_element_id(self) -> int:
        """고유 element ID 생성"""
        self._element_id_counter += 1
        return self._element_id_counter

    def generate_view(self) -> Optional[DocumentView]:
        """메인 생성 메서드

        HWP 문서 전체를 DocumentView로 변환

        Returns:
            Optional[DocumentView]: 구조화된 문서 표현 또는 None (실패 시)
        """
        if not self.connector.check_alive():
            print("HWP connection is not alive", file=sys.stderr)
            return None

        # element ID 카운터 초기화
        self._element_id_counter = 0

        try:
            # 1. 문서 요소 추출
            elements = self._extract_elements()

            # 요소가 없으면 None 반환
            if not elements:
                print("No elements extracted", file=sys.stderr)
                return None

            # 2. HTML 생성
            html_content = self._generate_html(elements)

            # 3. 위치 매핑 구축
            position_map = self._build_position_map(elements)

            # 4. 페이지 정보 수집
            page_info = self._get_page_info()

            return DocumentView(
                html_content=html_content,
                elements=elements,
                position_map=position_map,
                page_info=page_info
            )

        except Exception as e:
            print(f"DocumentView 생성 실패: {e}", file=sys.stderr)
            return None

    def _extract_elements(self) -> List[Element]:
        """HWP 문서 요소 추출

        pyhwpx init_scan 패턴 및 CtrlID 기반 컨트롤 처리

        Returns:
            List[Element]: 추출된 요소 리스트
        """
        elements = []

        try:
            # 문서가 비어있는지 확인 (구버전 호환성)
            # GetText()는 스캔용이므로 GetTextFile로 "문서 전체" 확인
            try:
                # GetTextFile("TEXT") 또는 GetTextFile("UNICODE") 사용
                text_content = self.hwp.GetTextFile("UNICODE", "")
                if text_content is None or len(text_content.strip()) == 0:
                    print("[ViewGenerator] 문서가 비어있습니다. 기본 텍스트를 삽입합니다.", file=sys.stderr)
                    # 기본 텍스트 삽입
                    self.hwp.HAction.Run("MoveDocBegin")  # 문서 처음으로
                    self.hwp.HAction.Run("InsertText", "NewSet:아직 내용이 없습니다.\n")
            except Exception as e:
                print(f"[ViewGenerator] 문서 상태 확인 실패: {e}", file=sys.stderr)
                # GetTextFile 실패 시 스캔으로 폴백
                try:
                    self.hwp.init_scan()
                    text_content = ""
                    while True:
                        state, text = self.hwp.get_text()
                        if state <= 1:
                            break
                        if text and str(text).strip():
                            text_content += str(text)
                    self.hwp.release_scan()
                    if len(text_content.strip()) == 0:
                        print("[ViewGenerator] 문서가 비어있습니다. 기본 텍스트를 삽입합니다.", file=sys.stderr)
                        self.hwp.HAction.Run("MoveDocBegin")
                        self.hwp.HAction.Run("InsertText", "NewSet:아직 내용이 없습니다.\n")
                except Exception as e2:
                    print(f"[ViewGenerator] 스캔 폴백도 실패: {e2}", file=sys.stderr)

            # 문서 처음으로 이동
            if not self.connector.set_pos(0, 0, 0):
                print("[ViewGenerator] 위치 이동 실패, MoveDocBegin으로 재시도", file=sys.stderr)
                try:
                    self.hwp.HAction.Run("MoveDocBegin")
                except:
                    return elements

            # 1단계: init_scan으로 문서 텍스트 요소 추출
            text_elements = self._scan_text_elements()

            # 2단계: ctrl_list로 컨트롤 요소 추출 (CtrlID 기반)
            control_elements = self._extract_control_elements()

            # 3단계: 통합 및 정렬 (position 기준)
            all_elements = text_elements + control_elements
            all_elements.sort(key=lambda e: e.position)

            elements = all_elements

        except Exception as e:
            print(f"요소 추출 중 에러: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

        return elements

    def _scan_text_elements(self) -> List[Element]:
        """init_scan 패턴으로 텍스트 요소 스캔

        Returns:
            List[Element]: 텍스트 paragraph 요소 리스트
        """
        elements = []

        try:
            # scan 설정
            scan_config = {
                "option": 4,
                "range": 0x0017,  # 전체 범위
                "spara": 0,
                "spos": 0
            }

            # init_scan 시작
            self.hwp.init_scan(**scan_config)

            while True:
                # get_text()는 (state, text) 튜플 반환
                state, text = self.hwp.get_text()

                if state <= 1:  # 문서 끝
                    break

                # move_pos(201): 다음 문단으로 이동
                self.hwp.move_pos(201)
                pos = self.hwp.get_pos()

                # 텍스트가 있는 경우에만 Element 생성
                if text:
                    # 안전하게 문자열로 변환
                    text_str = str(text) if not isinstance(text, str) else text
                    if text_str.strip():
                        element = Element(
                            element_id=self._generate_element_id(),
                            element_type="paragraph",
                            content=text_str.strip(),
                            position=pos,
                            attributes={}
                        )
                        elements.append(element)

            # release_scan 종료
            self.hwp.release_scan()

        except Exception as e:
            print(f"텍스트 스캔 중 에러: {e}", file=sys.stderr)
            # 오류 시에도 release_scan 시도
            try:
                self.hwp.release_scan()
            except:
                pass

        return elements

    def _extract_control_elements(self) -> List[Element]:
        """CtrlID 기반 컨트롤 요소 추출

        추출 과정:
        - ctrl_list 가져오기
        - ctrl.CtrlID로 타입 식별
        - get_ctrl_pos()로 위치 가져오기

        처리 컨트롤:
        - "tbl": 표
        - "fn": 각주
        - "gso": 그림/이미지 (UserDesc 확인)

        Returns:
            List[Element]: 컨트롤 Element 리스트
        """
        elements = []

        try:
            # ctrl_list 가져오기
            ctrl_list = self.hwp.ctrl_list

            for ctrl in ctrl_list:
                # CtrlID 가져오기
                ctrl_id = getattr(ctrl, "CtrlID", None)
                if not ctrl_id:
                    continue

                # get_ctrl_pos() 사용
                ctrl_pos = self.hwp.get_ctrl_pos(ctrl)
                if not ctrl_pos or len(ctrl_pos) < 3:
                    continue

                # CtrlID 기반 분기
                if ctrl_id == "tbl":
                    # 표 요소
                    element = self._create_table_element(ctrl, ctrl_pos)
                    if element:
                        elements.append(element)
                        # 개별 셀도 Element로 등록 (LLM이 셀별 편집 가능하도록)
                        cell_elements = self._extract_table_cell_elements(element)
                        elements.extend(cell_elements)

                elif ctrl_id == "fn":
                    # 각주 요소
                    element = self._create_footnote_element(ctrl, ctrl_pos)
                    if element:
                        elements.append(element)

                elif ctrl_id == "gso":
                    # 그림/이미지 요소 (UserDesc 확인)
                    element = self._create_image_element(ctrl, ctrl_pos)
                    if element:
                        elements.append(element)

        except Exception as e:
            print(f"컨트롤 추출 중 에러: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

        return elements

    def _create_table_element(self, ctrl, ctrl_pos: Tuple[int, int, int]) -> Optional[Element]:
        """표 컨트롤을 Element로 변환 - 기존 HWPML 방식

        기존 패턴:
        1. 표 전체 선택 (TableCellBlock)
        2. HWPML 추출 (GetTextFile("HWPML2X", "saveblock"))
        3. HWPML → 셀 데이터 파싱
        4. 각 셀에 ID 부여

        Args:
            ctrl: 표 컨트롤 객체
            ctrl_pos: 표 위치 (list, para, char)

        Returns:
            Optional[Element]: 표 Element (셀 데이터 포함) 또는 None
        """
        try:
            table_id = self._generate_element_id()

            # 표 위치로 이동
            if not self.connector.set_pos(ctrl_pos[0], ctrl_pos[1], ctrl_pos[2]):
                print(f"표 위치 이동 실패: {ctrl_pos}", file=sys.stderr)
                return Element(
                    element_id=table_id,
                    element_type="table",
                    content="[표]",
                    position=ctrl_pos,
                    attributes={"ctrl_id": "tbl"}
                )

            # 표 안으로 진입
            try:
                self.hwp.FindCtrl()
            except Exception as e:
                print(f"FindCtrl 실패: {e}", file=sys.stderr)

            # 기존 통합 패턴: 테이블 선택 + HWPML 추출 + HTML 변환 + ID 부여
            try:
                table_html, first_coords = scan_table_with_markup(
                    self.hwp,
                    self._generate_element_id,
                    log_callback=lambda msg, level=None: print(msg, file=sys.stderr)
                )

                # 선택 해제 (구버전 호환: HAction.Run 사용)
                try:
                    self.hwp.HAction.Run("Cancel")
                except:
                    pass

                # table_html이 None이거나 빈 문자열인지 확인
                if not table_html:
                    print("테이블 스캔 실패: 빈 결과", file=sys.stderr)
                    return Element(
                        element_id=table_id,
                        element_type="table",
                        content="",
                        position=first_coords,
                        attributes={}
                    )
                else:
                    # 안전하게 문자열로 변환
                    table_str = str(table_html) if not isinstance(table_html, str) else table_html
                    if table_str.strip() == "":
                        print("테이블 스캔 실패: 빈 결과", file=sys.stderr)
                        return Element(
                            element_id=table_id,
                            element_type="table",
                            content="",
                            position=first_coords,
                            attributes={}
                        )
                    # ID가 부여된 HTML 형식으로 테이블 반환
                    return Element(
                        element_id=table_id,
                        element_type="table",
                        content=table_html,  # ID가 부여된 HTML
                        position=ctrl_pos,
                        attributes={
                            "ctrl_id": "tbl",
                            "html": True,  # HTML 형식 표시
                            "first_coords": first_coords  # 첫 셀 좌표 저장
                        }
                    )

            except Exception as e:
                print(f"HWPML 처리 실패: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                # 선택 해제 시도 (구버전 호환: HAction.Run 사용)
                try:
                    self.hwp.HAction.Run("Cancel")
                except:
                    pass
                return Element(
                    element_id=table_id,
                    element_type="table",
                    content="[표]",
                    position=ctrl_pos,
                    attributes={"ctrl_id": "tbl"}
                )

        except Exception as e:
            print(f"표 Element 생성 실패: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def _parse_table_hwpml(self, hwpml_text: str) -> List[List[Dict]]:
        """HWPML 텍스트에서 표 구조 파싱 - 기존 패턴

        기존 방식:
        - TABLE/ROW/CELL 구조 파싱
        - PARALIST/P/TEXT/CHAR에서 텍스트 추출
        - 빈 셀도 명시적으로 처리

        Args:
            hwpml_text: HWPML 형식 문자열

        Returns:
            List[List[Dict]]: 행별 셀 데이터 리스트
        """
        import xml.etree.ElementTree as ET

        rows = []
        try:
            root = ET.fromstring(hwpml_text)

            # TABLE 요소 찾기 (기존 findall 사용)
            all_tables = list(root.iter("TABLE"))

            if not all_tables:
                print("HWPML에서 TABLE 요소를 찾을 수 없습니다", file=sys.stderr)
                return []

            # 첫 번째 TABLE 사용 (중첩 테이블은 나중에 처리)
            table = all_tables[0]

            # ROW 순회 (기존 패턴)
            for row_elem in table.findall("ROW"):
                cells = []

                # CELL 순회
                for cell_elem in row_elem.findall("CELL"):
                    cell_text_parts = []

                    # PARALIST/P/TEXT/CHAR 순회하며 텍스트 추출 (기존 패턴)
                    for para_list in cell_elem.findall("PARALIST"):
                        for p in para_list.findall("P"):
                            for text in p.findall("TEXT"):
                                for child in list(text):
                                    if child.tag == "CHAR" and child.text:
                                        cell_text_parts.append(child.text)

                    # 셀 텍스트 결합 (빈 셀도 유지)
                    cell_text = "".join(str(part) for part in cell_text_parts).strip()

                    cells.append({
                        "id": self._generate_element_id(),
                        "text": cell_text,  # 빈 문자열도 유지
                        "row": len(rows),
                        "col": len(cells)
                    })

                # 빈 행도 포함 (셀이 있으면 추가)
                if cells:
                    rows.append(cells)

            if not rows:
                print("HWPML에서 ROW/CELL을 찾을 수 없습니다", file=sys.stderr)
                return []

        except Exception as e:
            print(f"HWPML 파싱 실패: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return []

        return rows

    def _extract_table_cell_elements(self, table_element: Element) -> List[Element]:
        """표 Element로부터 개별 셀 Element 리스트 생성

        LLM이 개별 셀을 편집할 수 있도록 각 셀을 독립적인 Element로 등록합니다.

        Args:
            table_element: 표 Element (content에 셀 데이터 포함)

        Returns:
            List[Element]: 개별 셀 Element 리스트
        """
        cell_elements = []

        # content가 리스트(셀 데이터)인지 확인
        if not isinstance(table_element.content, list):
            return cell_elements

        table_id = table_element.element_id
        table_pos = table_element.position

        # 각 셀을 Element로 변환
        for row_data in table_element.content:
            for cell_data in row_data:
                cell_id = cell_data.get("id")
                cell_text = cell_data.get("text", "")
                cell_row = cell_data.get("row", 0)
                cell_col = cell_data.get("col", 0)

                # 셀 Element 생성
                cell_element = Element(
                    element_id=cell_id,
                    element_type="table_cell",
                    content=cell_text,
                    position=table_pos,  # 표와 같은 위치 사용
                    attributes={
                        "table_id": table_id,
                        "row": cell_row,
                        "col": cell_col
                    }
                )
                cell_elements.append(cell_element)

        return cell_elements

    def _create_footnote_element(self, ctrl, ctrl_pos: Tuple[int, int, int]) -> Optional[Element]:
        """각주 컨트롤을 Element로 변환

        Args:
            ctrl: 각주 컨트롤 객체
            ctrl_pos: 각주 위치 (list, para, char)

        Returns:
            Optional[Element]: 각주 Element 또는 None
        """
        try:
            element = Element(
                element_id=self._generate_element_id(),
                element_type="footnote",
                content="[각주]",  # 임시 표시
                position=ctrl_pos,
                attributes={
                    "ctrl_id": "fn"
                }
            )
            return element

        except Exception as e:
            print(f"각주 Element 생성 실패: {e}", file=sys.stderr)
            return None

    def _create_image_element(self, ctrl, ctrl_pos: Tuple[int, int, int]) -> Optional[Element]:
        """그림/이미지 컨트롤을 Element로 변환

        UserDesc가 "그림", "사진", "image", "picture"인 경우만 처리

        Args:
            ctrl: gso 컨트롤 객체
            ctrl_pos: 이미지 위치 (list, para, char)

        Returns:
            Optional[Element]: 이미지 Element 또는 None
        """
        try:
            # UserDesc 확인
            user_desc = getattr(ctrl, "UserDesc", None)
            if not user_desc:
                return None

            # 정규화
            normalized_desc = str(user_desc).replace(" ", "").lower()

            # 텍스트 박스("사각형")는 제외
            if "사각형" in normalized_desc:
                return None

            # 그림/이미지만 포함
            if not any(key in normalized_desc for key in ("그림", "사진", "image", "picture")):
                return None

            element = Element(
                element_id=self._generate_element_id(),
                element_type="image",
                content="[이미지]",  # 임시 표시
                position=ctrl_pos,
                attributes={
                    "ctrl_id": "gso",
                    "user_desc": user_desc
                }
            )
            return element

        except Exception as e:
            print(f"이미지 Element 생성 실패: {e}", file=sys.stderr)
            return None

    def _generate_html(self, elements: List[Element]) -> str:
        """HTML 형식 마크다운 생성

        Args:
            elements: 문서 요소 리스트

        Returns:
            str: HTML 형식 문자열
        """
        parts = []

        for elem in elements:
            if elem.element_type == "heading":
                level = elem.attributes.get("level", 1)
                parts.append(f'<h{level} id="{elem.element_id}">{elem.content}</h{level}>\n\n')

            elif elem.element_type == "paragraph":
                parts.append(f'<p id="{elem.element_id}">{elem.content}</p>\n\n')

            elif elem.element_type == "table_cell":
                parts.append(f'<td id="{elem.element_id}">{elem.content}</td>\n')

            elif elem.element_type == "list_item":
                parts.append(f'<li id="{elem.element_id}">{elem.content}</li>\n')

            elif elem.element_type == "image":
                src = elem.attributes.get("src", "")
                parts.append(f'<img id="{elem.element_id}" src="{src}" alt="{elem.content}" />\n\n')

            elif elem.element_type == "footnote":
                parts.append(f'<footnote id="{elem.element_id}">{elem.content}</footnote>\n\n')

        return "".join(parts)

    def _build_position_map(self, elements: List[Element]) -> Dict[int, Tuple[int, int, int]]:
        """element_id → position 매핑 생성

        Args:
            elements: 문서 요소 리스트

        Returns:
            Dict[int, Tuple[int, int, int]]: element ID → 위치 매핑
        """
        return {elem.element_id: elem.position for elem in elements}

    def _get_page_info(self) -> Dict[str, int]:
        """페이지 정보 수집

        Returns:
            Dict[str, int]: 페이지 관련 정보
        """
        page_info = {
            "start_page": 1,
            "end_page": 1,
            "total_pages": 1
        }

        try:
            # pyhwpx에서 페이지 정보 가져오기 시도
            if hasattr(self.hwp, 'page_count'):
                page_info["total_pages"] = self.hwp.page_count
            elif hasattr(self.hwp, 'get_page_count'):
                page_info["total_pages"] = self.hwp.get_page_count()
        except Exception as e:
            print(f"페이지 정보 수집 중 에러: {e}", file=sys.stderr)

        return page_info

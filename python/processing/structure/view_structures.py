"""
DocumentView 시스템 데이터 구조 정의

HWP 문서를 구조화된 표현으로 변환하기 위한 핵심 데이터 클래스들
"""

from dataclasses import dataclass, field
from typing import Tuple, List, Dict, Optional, Any


@dataclass
class Element:
    """문서 요소

    HWP 문서의 개별 요소를 표현하는 기본 단위

    Attributes:
        element_id: 요소의 고유 식별자
        element_type: 요소 타입 ("heading", "paragraph", "table_cell", "image", "footnote" 등)
        content: 요소의 텍스트 내용
        position: HWP 내부 위치 (list, para, char) 튜플
        attributes: 요소별 추가 속성 (예: heading의 level, table_cell의 row/col)
    """
    element_id: int
    element_type: str
    content: str
    position: Tuple[int, int, int]
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """데이터 검증"""
        if self.element_id < 0:
            raise ValueError(f"element_id must be non-negative, got {self.element_id}")

        if not self.element_type:
            raise ValueError("element_type cannot be empty")

        if len(self.position) != 3:
            raise ValueError(f"position must be (list, para, char) tuple, got {self.position}")

    def __str__(self) -> str:
        """디버깅용 문자열 표현"""
        return f"Element(id={self.element_id}, type={self.element_type}, pos={self.position}, content={self.content[:30]}...)"


@dataclass
class DocumentView:
    """구조화된 문서 표현

    HWP 문서를 HTML 기반의 구조화된 표현으로 변환한 결과

    Attributes:
        html_content: HTML 형식의 문서 전체 내용
        elements: 문서를 구성하는 모든 Element 리스트
        position_map: element_id → HWP position 매핑 (빠른 조회용)
        page_info: 페이지 관련 정보 (start_page, end_page, total_pages 등)
    """
    html_content: str
    elements: List[Element]
    position_map: Dict[int, Tuple[int, int, int]]
    page_info: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        """데이터 검증 및 일관성 체크"""
        # position_map과 elements의 일관성 검증
        if len(self.position_map) != len(self.elements):
            raise ValueError(
                f"position_map size ({len(self.position_map)}) "
                f"must match elements size ({len(self.elements)})"
            )

        # 모든 element가 position_map에 존재하는지 확인
        for elem in self.elements:
            if elem.element_id not in self.position_map:
                raise ValueError(f"Element {elem.element_id} missing in position_map")

    def get_element_by_id(self, element_id: int) -> Optional[Element]:
        """ID로 요소 조회"""
        for elem in self.elements:
            if elem.element_id == element_id:
                return elem
        return None

    def get_elements_by_type(self, element_type: str) -> List[Element]:
        """타입으로 요소 필터링"""
        return [elem for elem in self.elements if elem.element_type == element_type]

    def get_position(self, element_id: int) -> Optional[Tuple[int, int, int]]:
        """ID로 HWP 위치 조회"""
        return self.position_map.get(element_id)

    def __str__(self) -> str:
        """디버깅용 문자열 표현"""
        return (
            f"DocumentView("
            f"elements={len(self.elements)}, "
            f"html_length={len(self.html_content)}, "
            f"pages={self.page_info.get('total_pages', 'unknown')}"
            f")"
        )

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (JSON 직렬화용)"""
        return {
            "html_content": self.html_content,
            "elements": [
                {
                    "element_id": e.element_id,
                    "element_type": e.element_type,
                    "content": e.content,
                    "position": e.position,
                    "attributes": e.attributes
                }
                for e in self.elements
            ],
            "position_map": {str(k): v for k, v in self.position_map.items()},
            "page_info": self.page_info
        }

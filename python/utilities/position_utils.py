"""
HWP 위치 유틸리티 함수

HWP 문서 내 위치 계산, 비교, 변환 등을 위한 유틸리티 함수 모음
"""

from typing import Tuple, Optional


def calculate_position(element) -> Tuple[int, int, int]:
    """HWP 요소에서 위치 정보 추출

    Element 객체 또는 딕셔너리에서 HWP 위치 튜플을 추출

    Args:
        element: Element 객체 또는 position 속성을 가진 객체/딕셔너리

    Returns:
        Tuple[int, int, int]: (list, para, char) 위치 튜플

    Raises:
        ValueError: 유효한 위치 정보가 없는 경우
    """
    # Element 객체인 경우
    if hasattr(element, 'position'):
        pos = element.position
        if isinstance(pos, tuple) and len(pos) == 3:
            return pos
        raise ValueError(f"Invalid position format: {pos}")

    # 딕셔너리인 경우
    if isinstance(element, dict) and 'position' in element:
        pos = element['position']
        if isinstance(pos, (tuple, list)) and len(pos) == 3:
            return tuple(pos)
        raise ValueError(f"Invalid position format: {pos}")

    raise ValueError(f"Cannot extract position from element: {type(element)}")


def calculate_offset(old_length: int, new_length: int) -> int:
    """편집 후 오프셋 계산

    텍스트 편집 전후의 길이 차이로 후속 요소들의 위치 오프셋 계산

    Args:
        old_length: 편집 전 텍스트 길이
        new_length: 편집 후 텍스트 길이

    Returns:
        int: 위치 오프셋 (양수: 뒤로 이동, 음수: 앞으로 이동)

    Examples:
        >>> calculate_offset(5, 8)  # "hello" → "hello!!!"
        3
        >>> calculate_offset(10, 5)  # "hello world" → "hello"
        -5
    """
    return new_length - old_length


def compare_positions(pos1: Tuple[int, int, int], pos2: Tuple[int, int, int]) -> int:
    """두 위치 비교

    Args:
        pos1: 첫 번째 위치 (list, para, char)
        pos2: 두 번째 위치 (list, para, char)

    Returns:
        int: -1 (pos1이 앞), 0 (같음), 1 (pos1이 뒤)

    Examples:
        >>> compare_positions((0, 0, 0), (0, 0, 5))
        -1
        >>> compare_positions((1, 2, 3), (1, 2, 3))
        0
        >>> compare_positions((0, 5, 0), (0, 3, 10))
        1
    """
    list1, para1, char1 = pos1
    list2, para2, char2 = pos2

    # list 비교
    if list1 < list2:
        return -1
    elif list1 > list2:
        return 1

    # para 비교 (같은 list 내)
    if para1 < para2:
        return -1
    elif para1 > para2:
        return 1

    # char 비교 (같은 para 내)
    if char1 < char2:
        return -1
    elif char1 > char2:
        return 1

    return 0


def is_same_paragraph(pos1: Tuple[int, int, int], pos2: Tuple[int, int, int]) -> bool:
    """같은 문단에 속하는지 확인

    Args:
        pos1: 첫 번째 위치 (list, para, char)
        pos2: 두 번째 위치 (list, para, char)

    Returns:
        bool: 같은 문단이면 True

    Examples:
        >>> is_same_paragraph((0, 1, 5), (0, 1, 10))
        True
        >>> is_same_paragraph((0, 1, 5), (0, 2, 0))
        False
    """
    return pos1[0] == pos2[0] and pos1[1] == pos2[1]


def is_same_list(pos1: Tuple[int, int, int], pos2: Tuple[int, int, int]) -> bool:
    """같은 리스트에 속하는지 확인

    Args:
        pos1: 첫 번째 위치 (list, para, char)
        pos2: 두 번째 위치 (list, para, char)

    Returns:
        bool: 같은 리스트면 True
    """
    return pos1[0] == pos2[0]


def position_to_str(pos: Tuple[int, int, int]) -> str:
    """위치를 문자열로 변환 (디버깅용)

    Args:
        pos: 위치 튜플 (list, para, char)

    Returns:
        str: "L{list}:P{para}:C{char}" 형식 문자열

    Examples:
        >>> position_to_str((0, 5, 10))
        'L0:P5:C10'
    """
    return f"L{pos[0]}:P{pos[1]}:C{pos[2]}"


def str_to_position(pos_str: str) -> Optional[Tuple[int, int, int]]:
    """문자열을 위치 튜플로 변환

    Args:
        pos_str: "L{list}:P{para}:C{char}" 형식 문자열

    Returns:
        Optional[Tuple[int, int, int]]: 위치 튜플 또는 None (파싱 실패 시)

    Examples:
        >>> str_to_position('L0:P5:C10')
        (0, 5, 10)
        >>> str_to_position('invalid')
        None
    """
    try:
        parts = pos_str.split(':')
        if len(parts) != 3:
            return None

        list_pos = int(parts[0][1:])  # 'L0' → 0
        para_pos = int(parts[1][1:])  # 'P5' → 5
        char_pos = int(parts[2][1:])  # 'C10' → 10

        return (list_pos, para_pos, char_pos)
    except (ValueError, IndexError):
        return None


def add_offset(pos: Tuple[int, int, int], char_offset: int) -> Tuple[int, int, int]:
    """위치에 문자 오프셋 추가

    같은 문단 내에서 char 위치만 이동

    Args:
        pos: 원래 위치 (list, para, char)
        char_offset: 문자 오프셋 (양수/음수)

    Returns:
        Tuple[int, int, int]: 오프셋이 적용된 새 위치

    Examples:
        >>> add_offset((0, 5, 10), 3)
        (0, 5, 13)
        >>> add_offset((0, 5, 10), -5)
        (0, 5, 5)
    """
    list_pos, para_pos, char_pos = pos
    new_char_pos = max(0, char_pos + char_offset)  # 음수 방지
    return (list_pos, para_pos, new_char_pos)


def is_position_after(pos1: Tuple[int, int, int], pos2: Tuple[int, int, int]) -> bool:
    """pos1이 pos2보다 뒤에 있는지 확인

    Args:
        pos1: 확인할 위치
        pos2: 기준 위치

    Returns:
        bool: pos1이 pos2보다 뒤에 있으면 True

    Examples:
        >>> is_position_after((0, 5, 10), (0, 5, 5))
        True
        >>> is_position_after((0, 3, 0), (0, 5, 0))
        False
    """
    return compare_positions(pos1, pos2) > 0


def is_position_before(pos1: Tuple[int, int, int], pos2: Tuple[int, int, int]) -> bool:
    """pos1이 pos2보다 앞에 있는지 확인

    Args:
        pos1: 확인할 위치
        pos2: 기준 위치

    Returns:
        bool: pos1이 pos2보다 앞에 있으면 True
    """
    return compare_positions(pos1, pos2) < 0


def validate_position(pos: Tuple[int, int, int]) -> bool:
    """위치 튜플의 유효성 검증

    Args:
        pos: 검증할 위치 튜플

    Returns:
        bool: 유효하면 True

    Examples:
        >>> validate_position((0, 5, 10))
        True
        >>> validate_position((-1, 0, 0))
        False
        >>> validate_position((0, 0))
        False
    """
    if not isinstance(pos, tuple) or len(pos) != 3:
        return False

    list_pos, para_pos, char_pos = pos

    # 모든 값이 음수가 아닌 정수여야 함
    if not all(isinstance(x, int) and x >= 0 for x in pos):
        return False

    return True

# -*- coding: utf-8 -*-
"""
Form/Template Detector for HWP Documents (HWP 2022 이전 호환)

양식 문서 영역 감지를 위한 API 모음
- 기울임/색상 텍스트 블록 연속 감지 (양식 가능성)
- 표 셀 테두리/색상 분석 (디자인적 요소 판단)

References:
- HwpCtrl+API.hwp2022이전.pdf
- ParameterSet+Table.hwp2022이전.pdf
- Action+Table.hwp2022이전.pdf
"""

import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import IntEnum


# ============================================================
# 상수 정의 (HWP 2022 이전 호환)
# ============================================================

class ScanOption(IntEnum):
    """InitScan option 상수"""
    MASK_TEXT = 0x0001        # 일반 텍스트
    MASK_CHAR = 0x0002        # 글자 컨트롤
    MASK_HEADER = 0x0010      # 머리말
    MASK_FOOTER = 0x0020      # 꼬리말
    MASK_ALL = 0x00FF         # 모든 영역


class ScanRange(IntEnum):
    """InitScan range 상수"""
    ALL = 0x0077              # 전체 문서
    SELECTION = 0x0070        # 선택 영역만
    PARAGRAPH = 0x0071        # 현재 문단만


class GetTextState(IntEnum):
    """GetText 반환 상태값"""
    END_OF_DOC = 0            # 문서의 끝 (더 이상 스캔 불가)
    END_OF_SCAN = 1           # 스캔 범위의 끝
    TEXT_NORMAL = 2           # 일반 텍스트
    CTRL_START = 3            # 컨트롤 시작 (테이블, 그림 등)
    CTRL_END = 4              # 컨트롤 끝


class MovePosType(IntEnum):
    """MovePos 타입 상수"""
    MOVE_DOC_BEGIN = 0        # 문서 시작
    MOVE_DOC_END = 1          # 문서 끝
    MOVE_PREV_PARA = 22       # 이전 문단
    MOVE_NEXT_PARA = 23       # 다음 문단
    MOVE_SCAN_POS = 201       # 스캔 위치로 이동 (GetText 후)


class BorderType(IntEnum):
    """표 셀 테두리 타입"""
    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3


# 기본색 (검정색) - COLORREF 형식
DEFAULT_TEXT_COLOR = 0x00000000  # RGB(0, 0, 0)


@dataclass
class TextBlockInfo:
    """텍스트 블록 정보"""
    text: str
    position: Tuple[int, int, int]  # (list, para, char)
    is_italic: bool = False
    text_color: int = 0             # COLORREF 형식
    is_styled: bool = False         # 기울임 또는 색상이 있는지

    def __post_init__(self):
        """is_styled 자동 판단"""
        self.is_styled = self.is_italic or (self.text_color != DEFAULT_TEXT_COLOR)


@dataclass
class FormRegionInfo:
    """양식 영역 정보"""
    start_position: Tuple[int, int, int]
    end_position: Tuple[int, int, int]
    block_count: int
    sample_texts: List[str] = field(default_factory=list)
    reason: str = ""  # 양식으로 판단된 사유


@dataclass
class CellStyleInfo:
    """셀 스타일 정보"""
    row: int
    col: int
    has_left_border: bool = True
    has_right_border: bool = True
    has_top_border: bool = True
    has_bottom_border: bool = True
    has_diagonal: bool = False
    diagonal_flags: List[str] = field(default_factory=list)
    background_color: int = 0xFFFFFF  # COLORREF (기본: 흰색)
    has_all_borders: bool = True
    has_color: bool = False
    is_design_element: bool = False


@dataclass
class TableDesignInfo:
    """표 디자인 정보"""
    table_id: int
    is_design_table: bool = False
    design_cells: List[CellStyleInfo] = field(default_factory=list)
    reason: str = ""
    anchor_pos: Optional[Tuple[int, int, int]] = None


class FormDetector:
    """양식/템플릿 영역 감지기
    
    HWP 2022 이전 API를 사용하여 양식 영역을 감지합니다.
    
    사용 예:
    ```python
    detector = FormDetector(hwp)
    
    # 기울임/색상 기반 양식 감지
    form_regions = detector.detect_styled_form_regions()
    
    # 표의 디자인 요소 감지
    design_tables = detector.detect_design_tables()
    ```
    """
    
    def __init__(
        self, 
        hwp,  # pyhwpx.Hwp 또는 raw COM 객체
        log_callback: Optional[Callable[[str, str], None]] = None,
        consecutive_threshold: int = 3,  # 연속 N개 이상이면 양식으로 판단
    ):
        """
        Args:
            hwp: HWP COM 객체 (pyhwpx.Hwp 래퍼 또는 raw COM)
            log_callback: 로그 콜백 함수 (level, message)
            consecutive_threshold: 연속 스타일 블록 임계값
        """
        self._hwp = hwp
        self._log = log_callback or self._default_log
        self._consecutive_threshold = consecutive_threshold
        self._baseline_color: Optional[int] = None  # 본문 기본색
        
    def _default_log(self, level: str, message: str):
        """기본 로그 함수"""
        print(f"[FormDetector:{level}] {message}", file=sys.stderr)
        
    def _get_raw_hwp(self):
        """pyhwpx.Hwp에서 raw COM 객체 추출"""
        if hasattr(self._hwp, 'hwp'):
            return self._hwp.hwp  # pyhwpx 래퍼
        return self._hwp  # raw COM
    
    # ============================================================
    # 1. 텍스트 스캔 API (기울임/색상 감지용)
    # ============================================================
    
    def init_scan(
        self,
        option: int = ScanOption.MASK_TEXT,
        scan_range: int = ScanRange.ALL,
        spara: int = 0,
        spos: int = 0,
        epara: int = -1,
        epos: int = -1,
    ) -> bool:
        """문서 스캔 초기화 (InitScan)
        
        HWP 2022 이전 API:
        - InitScan(option, range, spara, spos, epara, epos)
        
        Args:
            option: 스캔 옵션 (ScanOption)
            scan_range: 스캔 범위 (ScanRange)
            spara: 시작 문단 (0-based)
            spos: 시작 위치
            epara: 끝 문단 (-1: 끝까지)
            epos: 끝 위치 (-1: 끝까지)
            
        Returns:
            bool: 초기화 성공 여부
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            # pyhwpx 래퍼 사용 시
            if hasattr(self._hwp, 'init_scan'):
                self._hwp.init_scan(
                    option=option,
                    range=scan_range,
                    spara=spara,
                    spos=spos
                )
                return True
            
            # raw COM 사용 시
            if hasattr(raw_hwp, 'InitScan'):
                raw_hwp.InitScan(option, scan_range, spara, spos, epara, epos)
                return True
                
            self._log("error", "InitScan API not found")
            return False
            
        except Exception as e:
            self._log("error", f"InitScan failed: {e}")
            return False
    
    def get_text(self) -> Tuple[int, str]:
        """스캔 스텝 진행 및 텍스트 획득 (GetText)
        
        HWP 2022 이전 API:
        - GetText(text*) → 반복 호출로 연속 텍스트 획득
        - 스크립트 언어용: GetTextBySet(CreateSet("GetText"))
        
        Returns:
            Tuple[int, str]: (상태코드, 텍스트)
            상태코드: GetTextState 참조
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            # pyhwpx 래퍼 사용 시
            if hasattr(self._hwp, 'get_text'):
                result = self._hwp.get_text()
                if isinstance(result, tuple):
                    return result
                return (GetTextState.TEXT_NORMAL, str(result))
            
            # raw COM 사용 시 - GetTextBySet 패턴 (포인터 못 쓰는 언어용)
            if hasattr(raw_hwp, 'CreateSet') and hasattr(raw_hwp, 'GetTextBySet'):
                pset = raw_hwp.CreateSet("GetText")
                state = raw_hwp.GetTextBySet(pset)
                text = pset.Item("Text") if hasattr(pset, 'Item') else ""
                return (state, text)
            
            # 대안: GetText 직접 호출 (일부 버전)
            if hasattr(raw_hwp, 'GetText'):
                # 포인터 방식 - Python에서는 변수 참조로 시도
                text = ""
                state = raw_hwp.GetText(text)
                return (state, text)
                
            self._log("error", "GetText API not found")
            return (GetTextState.END_OF_DOC, "")
            
        except Exception as e:
            self._log("error", f"GetText failed: {e}")
            return (GetTextState.END_OF_DOC, "")
    
    def release_scan(self) -> bool:
        """스캔 종료 및 리소스 해제 (ReleaseScan)
        
        중요: InitScan 후 반드시 호출해야 함
        
        Returns:
            bool: 해제 성공 여부
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            if hasattr(self._hwp, 'release_scan'):
                self._hwp.release_scan()
                return True
            
            if hasattr(raw_hwp, 'ReleaseScan'):
                raw_hwp.ReleaseScan()
                return True
                
            return True  # API가 없어도 에러는 아님
            
        except Exception as e:
            self._log("error", f"ReleaseScan failed: {e}")
            return False
    
    def move_to_scan_pos(self) -> bool:
        """GetText로 얻은 위치(ScanPos)로 캐럿 이동
        
        HWP 2022 이전 API:
        - MovePos(moveScanPos) → 201
        
        Returns:
            bool: 이동 성공 여부
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            if hasattr(self._hwp, 'move_pos'):
                self._hwp.move_pos(MovePosType.MOVE_SCAN_POS)
                return True
            
            if hasattr(raw_hwp, 'MovePos'):
                raw_hwp.MovePos(MovePosType.MOVE_SCAN_POS)
                return True
                
            return False
            
        except Exception as e:
            self._log("error", f"MovePos(ScanPos) failed: {e}")
            return False
    
    # ============================================================
    # 2. 글자 스타일(CharShape) 읽기 API
    # ============================================================
    
    def get_char_shape(self) -> Optional[Dict[str, Any]]:
        """현재 위치/선택의 글자모양(CharShape) 가져오기
        
        HWP 2022 이전 API:
        - CharShape (Property / GetCharShape)
        - selection이 없으면 캐럿 위치의 글자모양 반환
        - selection 내에서 값이 다르면 해당 아이템은 존재하지 않음
        
        주요 아이템:
        - Italic (PIT_UI1): 기울임 여부 (0/1)
        - TextColor (PIT_UI4): 글자색 (COLORREF)
        
        Returns:
            Dict with 'italic', 'text_color' keys, or None
        """
        try:
            raw_hwp = self._get_raw_hwp()
            result = {
                'italic': False,
                'text_color': DEFAULT_TEXT_COLOR,
            }
            
            # 방법 1: CharShape 속성 (pyhwpx)
            if hasattr(self._hwp, 'CharShape'):
                try:
                    cs = self._hwp.CharShape
                    if cs:
                        if hasattr(cs, 'Item'):
                            # Italic 확인
                            try:
                                italic_val = cs.Item("Italic")
                                result['italic'] = bool(italic_val)
                            except:
                                pass
                            
                            # TextColor 확인
                            try:
                                color_val = cs.Item("TextColor")
                                result['text_color'] = int(color_val) if color_val else 0
                            except:
                                pass
                        
                        # 직접 속성 접근
                        if hasattr(cs, 'Italic'):
                            result['italic'] = bool(cs.Italic)
                        if hasattr(cs, 'TextColor'):
                            result['text_color'] = int(cs.TextColor)
                    
                    return result
                except Exception as e:
                    self._log("debug", f"CharShape property access failed: {e}")
            
            # 방법 2: GetCharShape 메서드 (raw COM)
            if hasattr(raw_hwp, 'GetCharShape'):
                try:
                    cs = raw_hwp.GetCharShape()
                    if cs:
                        if hasattr(cs, 'Item'):
                            try:
                                result['italic'] = bool(cs.Item("Italic"))
                            except:
                                pass
                            try:
                                result['text_color'] = int(cs.Item("TextColor"))
                            except:
                                pass
                    return result
                except Exception as e:
                    self._log("debug", f"GetCharShape failed: {e}")
            
            # 방법 3: HParameterSet.HCharShape 사용
            if hasattr(raw_hwp, 'HParameterSet'):
                try:
                    pset = raw_hwp.HParameterSet.HCharShape
                    raw_hwp.HAction.GetDefault("CharShape", pset.HSet)
                    
                    if hasattr(pset, 'Italic'):
                        result['italic'] = bool(pset.Italic)
                    if hasattr(pset, 'TextColor'):
                        result['text_color'] = int(pset.TextColor)
                    
                    return result
                except Exception as e:
                    self._log("debug", f"HParameterSet CharShape failed: {e}")
            
            return result
            
        except Exception as e:
            self._log("error", f"get_char_shape failed: {e}")
            return None
    
    # ============================================================
    # 3. 표 셀 스타일 읽기 API
    # ============================================================
    
    def get_cell_border_info(self) -> Optional[CellStyleInfo]:
        """현재 셀의 테두리 및 배경색 정보 가져오기
        
        HWP 2022 이전 API:
        - CellTableBorder ParameterSet 사용
        - TableCellPropertyGet Action
        
        Returns:
            CellStyleInfo 또는 None
        """
        try:
            raw_hwp = self._get_raw_hwp()
            info = CellStyleInfo(row=0, col=0)
            diagonal_flags = set()

            def _mark_diagonal(flag_name: str, value: Any) -> None:
                try:
                    if value is None:
                        return
                    if isinstance(value, bool):
                        if value:
                            diagonal_flags.add(flag_name)
                        return
                    text = str(value).strip().lower()
                    if text in ("", "0", "false", "none", "null", "off", "no"):
                        return
                    try:
                        if float(text) > 0:
                            diagonal_flags.add(flag_name)
                            return
                    except Exception:
                        pass
                    if text in ("1", "true", "on", "yes"):
                        diagonal_flags.add(flag_name)
                except Exception:
                    return
            
            # 방법 1: HParameterSet.HCellBorderFill 사용
            if hasattr(raw_hwp, 'HParameterSet'):
                try:
                    # 셀 속성 가져오기
                    pset = raw_hwp.HParameterSet.HCellBorderFill
                    raw_hwp.HAction.GetDefault("TableCellPropertyGet", pset.HSet)
                    
                    # 테두리 정보 (BorderType 또는 개별 속성)
                    if hasattr(pset, 'LeftLine'):
                        info.has_left_border = bool(pset.LeftLine)
                    if hasattr(pset, 'RightLine'):
                        info.has_right_border = bool(pset.RightLine)
                    if hasattr(pset, 'TopLine'):
                        info.has_top_border = bool(pset.TopLine)
                    if hasattr(pset, 'BottomLine'):
                        info.has_bottom_border = bool(pset.BottomLine)

                    # 대각선 계열 (구버전 API 호환)
                    if hasattr(pset, 'SlashFlag'):
                        _mark_diagonal("slash", getattr(pset, 'SlashFlag'))
                    if hasattr(pset, 'BackSlashFlag'):
                        _mark_diagonal("backslash", getattr(pset, 'BackSlashFlag'))
                    if hasattr(pset, 'CounterSlashFlag'):
                        _mark_diagonal("counter_slash", getattr(pset, 'CounterSlashFlag'))
                    if hasattr(pset, 'CounterBackSlashFlag'):
                        _mark_diagonal("counter_backslash", getattr(pset, 'CounterBackSlashFlag'))
                    if hasattr(pset, 'CrookedSlashFlag'):
                        _mark_diagonal("crooked_slash", getattr(pset, 'CrookedSlashFlag'))
                    if hasattr(pset, 'DiagonalType'):
                        _mark_diagonal("diagonal", getattr(pset, 'DiagonalType'))
                    
                    # 배경색
                    if hasattr(pset, 'FillColor'):
                        info.background_color = int(pset.FillColor)
                    elif hasattr(pset, 'BackColor'):
                        info.background_color = int(pset.BackColor)
                    
                except Exception as e:
                    self._log("debug", f"HCellBorderFill access failed: {e}")
            
            # 방법 2: CellBorderFill 속성 직접 접근
            if hasattr(self._hwp, 'CellBorderFill'):
                try:
                    cbf = self._hwp.CellBorderFill
                    if cbf:
                        if hasattr(cbf, 'LeftBorderLine'):
                            info.has_left_border = cbf.LeftBorderLine > 0
                        if hasattr(cbf, 'RightBorderLine'):
                            info.has_right_border = cbf.RightBorderLine > 0
                        if hasattr(cbf, 'TopBorderLine'):
                            info.has_top_border = cbf.TopBorderLine > 0
                        if hasattr(cbf, 'BottomBorderLine'):
                            info.has_bottom_border = cbf.BottomBorderLine > 0
                        if hasattr(cbf, 'FillColor'):
                            info.background_color = int(cbf.FillColor)
                        if hasattr(cbf, 'SlashFlag'):
                            _mark_diagonal("slash", getattr(cbf, 'SlashFlag'))
                        if hasattr(cbf, 'BackSlashFlag'):
                            _mark_diagonal("backslash", getattr(cbf, 'BackSlashFlag'))
                        if hasattr(cbf, 'CounterSlashFlag'):
                            _mark_diagonal("counter_slash", getattr(cbf, 'CounterSlashFlag'))
                        if hasattr(cbf, 'CounterBackSlashFlag'):
                            _mark_diagonal("counter_backslash", getattr(cbf, 'CounterBackSlashFlag'))
                        if hasattr(cbf, 'CrookedSlashFlag'):
                            _mark_diagonal("crooked_slash", getattr(cbf, 'CrookedSlashFlag'))
                        if hasattr(cbf, 'DiagonalType'):
                            _mark_diagonal("diagonal", getattr(cbf, 'DiagonalType'))
                except Exception as e:
                    self._log("debug", f"CellBorderFill property failed: {e}")
            
            # 파생 필드 계산
            info.has_all_borders = (
                info.has_left_border and 
                info.has_right_border and 
                info.has_top_border and 
                info.has_bottom_border
            )
            
            # 배경색이 흰색(0xFFFFFF)이 아니면 색상 있음
            info.has_color = (info.background_color != 0xFFFFFF and 
                            info.background_color != 0x00FFFFFF)
            info.diagonal_flags = sorted(diagonal_flags)
            info.has_diagonal = bool(info.diagonal_flags)
            
            return info
            
        except Exception as e:
            self._log("error", f"get_cell_border_info failed: {e}")
            return None
    
    def get_table_cells_info(self, table_ctrl) -> List[CellStyleInfo]:
        """표의 모든 셀 스타일 정보 가져오기
        
        Args:
            table_ctrl: 표 컨트롤 객체
            
        Returns:
            List[CellStyleInfo]
        """
        cells_info = []
        raw_hwp = self._get_raw_hwp()
        
        try:
            # 표 셀 개수 확인
            if hasattr(table_ctrl, 'RowCount') and hasattr(table_ctrl, 'ColCount'):
                rows = table_ctrl.RowCount
                cols = table_ctrl.ColCount
            elif hasattr(table_ctrl, 'Rows') and hasattr(table_ctrl, 'Cols'):
                rows = table_ctrl.Rows
                cols = table_ctrl.Cols
            else:
                self._log("debug", "Cannot get table dimensions")
                return cells_info
            
            # 각 셀 순회
            for row in range(rows):
                for col in range(cols):
                    # 셀로 이동
                    self._move_to_table_cell(table_ctrl, row, col)
                    
                    # 현재 셀 정보 가져오기
                    cell_info = self.get_cell_border_info()
                    if cell_info:
                        cell_info.row = row
                        cell_info.col = col
                        cells_info.append(cell_info)
            
        except Exception as e:
            self._log("error", f"get_table_cells_info failed: {e}")
        
        return cells_info
    
    def _move_to_table_cell(self, table_ctrl, row: int, col: int) -> bool:
        """표의 특정 셀로 이동
        
        Args:
            table_ctrl: 표 컨트롤
            row: 행 번호 (0-based)
            col: 열 번호 (0-based)
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            # 표 앵커로 이동
            if hasattr(table_ctrl, 'GetAnchorPos'):
                anchor = table_ctrl.GetAnchorPos(0)
                if hasattr(self._hwp, 'set_pos_by_set'):
                    self._hwp.set_pos_by_set(anchor)
                elif hasattr(raw_hwp, 'SetPosBySet'):
                    raw_hwp.SetPosBySet(anchor)
            
            # 표 안으로 진입
            if hasattr(self._hwp, 'FindCtrl'):
                self._hwp.FindCtrl()
            elif hasattr(raw_hwp, 'FindCtrl'):
                raw_hwp.FindCtrl()
            
            # 셀 이동 (move_pos 또는 MovePos)
            move_fn = getattr(self._hwp, 'move_pos', None) or getattr(raw_hwp, 'MovePos', None)
            
            if move_fn:
                # 행 이동 (501: 다음 행)
                for _ in range(row):
                    move_fn(501)
                
                # 열 이동 (502: 다음 셀)
                for _ in range(col):
                    move_fn(502)
                    
            return True
            
        except Exception as e:
            self._log("debug", f"_move_to_table_cell failed: {e}")
            return False
    
    # ============================================================
    # 4. 컨텍스트 파악 API
    # ============================================================
    
    def get_key_indicator(self) -> Optional[Dict[str, Any]]:
        """현재 캐럿 위치의 컨텍스트 정보
        
        HWP 2022 이전 API:
        - KeyIndicator(..., ctrlname)
        - 예: 표 셀 내부, 누름틀 등 컨트롤 컨텍스트
        
        Returns:
            Dict with context info
        """
        try:
            raw_hwp = self._get_raw_hwp()
            result = {
                'ctrl_name': '',
                'in_table': False,
                'in_textbox': False,
                'in_field': False,
            }
            
            # KeyIndicator 메서드 호출
            if hasattr(raw_hwp, 'KeyIndicator'):
                try:
                    # 반환값: (line, col, word, pos, over, pagehwp, pageunit, ctrlname)
                    indicator = raw_hwp.KeyIndicator()
                    if indicator and len(indicator) >= 8:
                        ctrl_name = indicator[7] if indicator[7] else ''
                        result['ctrl_name'] = ctrl_name
                        result['in_table'] = '표' in ctrl_name or 'table' in ctrl_name.lower()
                        result['in_textbox'] = '글상자' in ctrl_name or 'textbox' in ctrl_name.lower()
                        result['in_field'] = '필드' in ctrl_name or 'field' in ctrl_name.lower()
                except Exception as e:
                    self._log("debug", f"KeyIndicator failed: {e}")
            
            # pyhwpx에서 key_indicator
            if hasattr(self._hwp, 'key_indicator'):
                try:
                    indicator = self._hwp.key_indicator()
                    if indicator:
                        result.update(indicator)
                except:
                    pass
            
            return result
            
        except Exception as e:
            self._log("error", f"get_key_indicator failed: {e}")
            return None
    
    # ============================================================
    # 5. 대안 검색 API (FindReplace + FindCharShape)
    # ============================================================
    
    def find_styled_text(
        self,
        italic: Optional[bool] = None,
        text_color: Optional[int] = None,
    ) -> bool:
        """특정 스타일의 텍스트를 찾아 점프
        
        HWP 2022 이전 API:
        - FindReplace ParameterSet의 FindCharShape
        - RepeatFind로 계속 찾기
        
        Args:
            italic: True면 기울임 텍스트 찾기
            text_color: 특정 색상의 텍스트 찾기 (COLORREF)
            
        Returns:
            bool: 찾음 여부
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            if not hasattr(raw_hwp, 'HParameterSet'):
                self._log("error", "HParameterSet not available")
                return False
            
            # FindReplace ParameterSet 준비
            pset = raw_hwp.HParameterSet.HFindReplace
            raw_hwp.HAction.GetDefault("FindReplace", pset.HSet)
            
            # FindCharShape 설정
            if hasattr(pset, 'FindCharShape'):
                cs = pset.FindCharShape
                if italic is not None and hasattr(cs, 'Italic'):
                    cs.Italic = 1 if italic else 0
                if text_color is not None and hasattr(cs, 'TextColor'):
                    cs.TextColor = text_color
            
            # 검색 실행
            pset.Direction = 0  # 앞으로 검색
            pset.IgnoreCase = 1
            pset.WholeWordOnly = 0
            pset.FindString = ""  # 빈 문자열 (스타일만 검색)
            pset.MatchPhoneme = 0
            
            result = raw_hwp.HAction.Execute("FindReplace", pset.HSet)
            return bool(result)
            
        except Exception as e:
            self._log("error", f"find_styled_text failed: {e}")
            return False
    
    def repeat_find(self) -> bool:
        """이전 검색 조건으로 다음 항목 찾기.

        HAction.Run("RepeatFind") 가 매칭 실패 시 "문서의 처음/끝까지 찾았습니다" dialog 노출.
        호출 전후 SetMessageBoxMode 으로 dialog 차단 + caller prev_mode 복원.

        Returns:
            bool: 찾음 여부
        """
        prev_mode = None
        try:
            raw_hwp = self._get_raw_hwp()

            if hasattr(raw_hwp, 'HAction'):
                try:
                    prev_mode = raw_hwp.SetMessageBoxMode(0x2FFF1)
                except Exception:
                    prev_mode = None
                result = raw_hwp.HAction.Run("RepeatFind")
                return bool(result)

            return False

        except Exception as e:
            self._log("error", f"repeat_find failed: {e}")
            return False
        finally:
            if prev_mode is not None:
                try:
                    raw_hwp = self._get_raw_hwp()
                    raw_hwp.SetMessageBoxMode(prev_mode)
                except Exception:
                    pass
    
    # ============================================================
    # 6. 고수준 감지 함수
    # ============================================================
    
    def detect_styled_form_regions(
        self,
        page_range: Optional[Tuple[int, int]] = None,
    ) -> List[FormRegionInfo]:
        """기울임/색상 텍스트 블록이 연속된 양식 영역 감지
        
        핵심 로직:
        1. InitScan으로 문서 스캔 시작
        2. GetText로 텍스트 블록 순회
        3. MovePos(ScanPos)로 해당 위치 이동
        4. CharShape에서 Italic/TextColor 확인
        5. 연속 N개 블록이 스타일 적용되면 양식으로 판단
        
        Args:
            page_range: (시작페이지, 끝페이지) - None이면 전체 문서
            
        Returns:
            List[FormRegionInfo]: 감지된 양식 영역 목록
        """
        form_regions = []
        styled_blocks = []  # 연속 스타일 블록 버퍼
        
        try:
            # 스캔 초기화
            if not self.init_scan():
                self._log("error", "Failed to init scan")
                return form_regions
            
            # 첫 본문의 색상을 기준선으로 설정
            self._baseline_color = None
            
            while True:
                state, text = self.get_text()
                
                # 스캔 종료 조건
                if state <= GetTextState.END_OF_SCAN:
                    break
                
                # 빈 텍스트 스킵
                if not text or not text.strip():
                    continue
                
                # 현재 위치로 이동
                if not self.move_to_scan_pos():
                    continue
                
                # 현재 위치 저장
                pos = self._get_current_pos()
                
                # 글자 스타일 확인
                char_shape = self.get_char_shape()
                if not char_shape:
                    continue
                
                # 기준 색상 설정 (첫 본문 텍스트)
                if self._baseline_color is None:
                    self._baseline_color = char_shape.get('text_color', DEFAULT_TEXT_COLOR)
                
                # 스타일 적용 여부 판단
                is_styled = (
                    char_shape.get('italic', False) or
                    char_shape.get('text_color', 0) != self._baseline_color
                )
                
                block_info = TextBlockInfo(
                    text=text,
                    position=pos,
                    is_italic=char_shape.get('italic', False),
                    text_color=char_shape.get('text_color', 0),
                    is_styled=is_styled,
                )
                
                if is_styled:
                    styled_blocks.append(block_info)
                else:
                    # 연속 블록 끊김 - 임계값 체크
                    if len(styled_blocks) >= self._consecutive_threshold:
                        _sample = []
                        for b in styled_blocks[:3]:
                            _sample.append(b.text[:50])
                        form_region = FormRegionInfo(
                            start_position=styled_blocks[0].position,
                            end_position=styled_blocks[-1].position,
                            block_count=len(styled_blocks),
                            sample_texts=_sample,
                            reason=self._get_style_reason(styled_blocks),
                        )
                        form_regions.append(form_region)

                    styled_blocks = []

            # 마지막 연속 블록 처리
            if len(styled_blocks) >= self._consecutive_threshold:
                _sample = []
                for b in styled_blocks[:3]:
                    _sample.append(b.text[:50])
                form_region = FormRegionInfo(
                    start_position=styled_blocks[0].position,
                    end_position=styled_blocks[-1].position,
                    block_count=len(styled_blocks),
                    sample_texts=_sample,
                    reason=self._get_style_reason(styled_blocks),
                )
                form_regions.append(form_region)
            
        finally:
            self.release_scan()
        
        self._log("info", f"Detected {len(form_regions)} styled form regions")
        return form_regions

    def _extract_char_style_from_shape_obj(
        self, char_shape: Any
    ) -> Dict[str, Any]:
        """CharShape 객체에서 italic/text_color 추출."""
        italic = False
        text_color = DEFAULT_TEXT_COLOR
        if not char_shape:
            return {"italic": italic, "text_color": text_color}

        try:
            if hasattr(char_shape, "Item"):
                try:
                    italic = bool(char_shape.Item("Italic"))
                except Exception:
                    pass
                try:
                    color_val = char_shape.Item("TextColor")
                    text_color = int(color_val) if color_val is not None else 0
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if hasattr(char_shape, "Italic"):
                italic = bool(char_shape.Italic)
            if hasattr(char_shape, "TextColor"):
                text_color = int(char_shape.TextColor)
        except Exception:
            pass

        return {"italic": italic, "text_color": text_color}

    def detect_styled_form_regions_from_elements(
        self,
        extracted_elements: List[Dict[str, Any]],
        pos_to_shape: Dict[Tuple[int, int, int], Dict[str, Any]],
    ) -> List[FormRegionInfo]:
        """HDML 스캔 결과를 재사용해 양식 영역 감지 (추가 스캔 없이).

        Args:
            extracted_elements: HDMLExtractor가 만든 스캔 요소 리스트
            pos_to_shape: pos -> {char_shape, para_shape} 매핑

        Returns:
            List[FormRegionInfo]: 감지된 양식 영역 목록
        """
        form_regions: List[FormRegionInfo] = []
        styled_blocks: List[TextBlockInfo] = []
        baseline_color: Optional[int] = None
        self._baseline_color = None

        if not extracted_elements or not pos_to_shape:
            return form_regions

        for element in extracted_elements:
            el_type = element.get("type")
            if el_type not in ("paragraph", "list"):
                continue
            pos = element.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) < 3:
                continue
            text = element.get("text", "")
            if not text or not text.strip():
                continue

            pos_key = tuple(pos)
            shape = pos_to_shape.get(pos_key)
            if not shape or not isinstance(shape, dict):
                continue
            char_shape = shape.get("char_shape")
            char_style = self._extract_char_style_from_shape_obj(char_shape)
            italic = char_style.get("italic", False)
            text_color = char_style.get("text_color", DEFAULT_TEXT_COLOR)

            if baseline_color is None:
                baseline_color = text_color
                self._baseline_color = baseline_color

            is_styled = italic or (text_color != baseline_color)
            block_info = TextBlockInfo(
                text=text,
                position=pos_key,
                is_italic=italic,
                text_color=text_color,
                is_styled=is_styled,
            )

            if is_styled:
                styled_blocks.append(block_info)
            else:
                if len(styled_blocks) >= self._consecutive_threshold:
                    form_region = FormRegionInfo(
                        start_position=styled_blocks[0].position,
                        end_position=styled_blocks[-1].position,
                        block_count=len(styled_blocks),
                        sample_texts=[b.text[:50] for b in styled_blocks[:3]],
                        reason=self._get_style_reason(styled_blocks),
                    )
                    form_regions.append(form_region)
                styled_blocks = []

        if len(styled_blocks) >= self._consecutive_threshold:
            form_region = FormRegionInfo(
                start_position=styled_blocks[0].position,
                end_position=styled_blocks[-1].position,
                block_count=len(styled_blocks),
                sample_texts=[b.text[:50] for b in styled_blocks[:3]],
                reason=self._get_style_reason(styled_blocks),
            )
            form_regions.append(form_region)

        self._log("info", f"Detected {len(form_regions)} styled form regions")
        return form_regions
    
    def _get_style_reason(self, blocks: List[TextBlockInfo]) -> str:
        """스타일 블록들의 양식 판정 사유 생성"""
        italic_count = sum(1 for b in blocks if b.is_italic)
        colored_count = sum(1 for b in blocks if b.text_color != self._baseline_color)
        
        reasons = []
        if italic_count > 0:
            reasons.append(f"기울임텍스트 {italic_count}개")
        if colored_count > 0:
            reasons.append(f"색상텍스트 {colored_count}개")
        
        return ", ".join(reasons) if reasons else "스타일 적용"
    
    def _get_current_pos(self) -> Tuple[int, int, int]:
        """현재 커서 위치 반환"""
        try:
            raw_hwp = self._get_raw_hwp()
            
            if hasattr(self._hwp, 'get_pos'):
                pos = self._hwp.get_pos()
                if pos:
                    return tuple(pos)
            
            if hasattr(raw_hwp, 'GetPos'):
                pos = raw_hwp.GetPos()
                if pos:
                    return (pos[0], pos[1], pos[2])
            
            return (0, 0, 0)
            
        except:
            return (0, 0, 0)
    
    def detect_design_tables(
        self,
        list_pos_range: Optional[Tuple[int, int]] = None,
        root_para_range: Optional[Tuple[int, int]] = None,
    ) -> List[TableDesignInfo]:
        """디자인 요소로 사용된 표 감지
        
        판단 기준:
        1. 현재셀의 테두리가 상하좌우 다 없는데 인접 셀이 색상 있는 경우
        2. 현재셀이 테두리가 상하좌우 다 없으면서 셀에 색상이 있는 경우  
        3. 현재 셀이 색상이 있는 경우 (디자인 가능성)
        
        Args:
            list_pos_range: (min_list_pos, max_list_pos) 범위 내 표만 분석

        Returns:
            List[TableDesignInfo]: 디자인 요소 표 목록
        """
        design_tables = []
        raw_hwp = self._get_raw_hwp()
        min_list_pos = None
        max_list_pos = None
        min_root_para = None
        max_root_para = None
        if list_pos_range:
            min_list_pos, max_list_pos = list_pos_range
        if root_para_range:
            min_root_para, max_root_para = root_para_range

        try:
            # 모든 표 컨트롤 순회
            table_idx = 0
            ctrl = None
            
            # HeadCtrl에서 시작
            if hasattr(raw_hwp, 'HeadCtrl'):
                ctrl = raw_hwp.HeadCtrl
            elif hasattr(self._hwp, 'HeadCtrl'):
                ctrl = self._hwp.HeadCtrl
            
            while ctrl:
                # 표 컨트롤인지 확인
                is_table = False
                if hasattr(ctrl, 'UserDesc'):
                    is_table = ctrl.UserDesc == "표" or ctrl.UserDesc == "table"
                elif hasattr(ctrl, 'CtrlID'):
                    is_table = ctrl.CtrlID == 'tbl'
                
                if is_table:
                    table_idx += 1
                    anchor_pos = None
                    if min_list_pos is not None and max_list_pos is not None:
                        try:
                            if hasattr(ctrl, "GetAnchorPos"):
                                anchor_pos = ctrl.GetAnchorPos(0)
                            elif hasattr(self._hwp, "get_ctrl_pos"):
                                anchor_pos = self._hwp.get_ctrl_pos(ctrl)
                        except Exception:
                            anchor_pos = None
                        if (
                            anchor_pos
                            and isinstance(anchor_pos, (list, tuple))
                            and len(anchor_pos) >= 1
                        ):
                            list_pos = anchor_pos[0]
                            para_pos = anchor_pos[1] if len(anchor_pos) >= 2 else None
                            if list_pos < min_list_pos or list_pos > max_list_pos:
                                ctrl = ctrl.Next if hasattr(ctrl, "Next") else None
                                continue
                            if (
                                list_pos == 0
                                and min_root_para is not None
                                and max_root_para is not None
                                and para_pos is not None
                                and (para_pos < min_root_para or para_pos > max_root_para)
                            ):
                                ctrl = ctrl.Next if hasattr(ctrl, "Next") else None
                                continue
                    elif min_root_para is not None and max_root_para is not None:
                        try:
                            if hasattr(ctrl, "GetAnchorPos"):
                                anchor_pos = ctrl.GetAnchorPos(0)
                            elif hasattr(self._hwp, "get_ctrl_pos"):
                                anchor_pos = self._hwp.get_ctrl_pos(ctrl)
                        except Exception:
                            anchor_pos = None
                        if (
                            anchor_pos
                            and isinstance(anchor_pos, (list, tuple))
                            and len(anchor_pos) >= 2
                        ):
                            list_pos = anchor_pos[0]
                            para_pos = anchor_pos[1]
                            if list_pos == 0 and (
                                para_pos < min_root_para or para_pos > max_root_para
                            ):
                                ctrl = ctrl.Next if hasattr(ctrl, "Next") else None
                                continue

                    design_info = self._analyze_table_design(
                        ctrl,
                        table_idx,
                        anchor_pos=anchor_pos,
                    )
                    if design_info and design_info.is_design_table:
                        design_tables.append(design_info)
                
                # 다음 컨트롤
                ctrl = ctrl.Next if hasattr(ctrl, 'Next') else None
                
        except Exception as e:
            self._log("error", f"detect_design_tables failed: {e}")
        
        self._log("info", f"Detected {len(design_tables)} design tables")
        return design_tables
    
    def _analyze_table_design(
        self,
        table_ctrl,
        table_id: int,
        anchor_pos: Optional[Tuple[int, int, int]] = None,
    ) -> Optional[TableDesignInfo]:
        """단일 표의 디자인 요소 분석
        
        Args:
            table_ctrl: 표 컨트롤
            table_id: 표 ID
            
        Returns:
            TableDesignInfo 또는 None
        """
        try:
            info = TableDesignInfo(table_id=table_id)
            # 표 앵커 위치 저장 (table_group 매핑용)
            if anchor_pos is not None:
                info.anchor_pos = anchor_pos
            else:
                try:
                    if hasattr(table_ctrl, "GetAnchorPos"):
                        info.anchor_pos = table_ctrl.GetAnchorPos(0)
                    elif hasattr(self._hwp, "get_ctrl_pos"):
                        info.anchor_pos = self._hwp.get_ctrl_pos(table_ctrl)
                except Exception:
                    info.anchor_pos = None
            cells = self.get_table_cells_info(table_ctrl)
            
            if not cells:
                return None
            
            # 셀 맵 생성 (row, col -> CellStyleInfo)
            cell_map = {(c.row, c.col): c for c in cells}
            
            design_reasons = []
            
            for cell in cells:
                is_design_cell = False
                cell_reason = ""

                missing_borders = 0
                if not cell.has_left_border:
                    missing_borders += 1
                if not cell.has_right_border:
                    missing_borders += 1
                if not cell.has_top_border:
                    missing_borders += 1
                if not cell.has_bottom_border:
                    missing_borders += 1
                no_borders_all = missing_borders == 4
                weak_border = no_borders_all or missing_borders >= 2

                adjacent_cells = self._get_adjacent_cells(cell, cell_map)
                has_colored_adjacent = any(c.has_color for c in adjacent_cells)

                # 조건 1: 셀 자체에 색상이 있는 경우
                if cell.has_color:
                    is_design_cell = True
                    cell_reason = "셀 색상 적용"

                # 조건 2: 인접 셀 색상 + 현재 셀 테두리 약함(4면 없음 또는 2면 이상 없음)
                if has_colored_adjacent and weak_border:
                    is_design_cell = True
                    cell_reason = "인접셀 색상 + 테두리 약함"

                # 조건 3: 현재 셀 4면 테두리 모두 없음
                if no_borders_all:
                    is_design_cell = True
                    cell_reason = "4면 테두리 없음"
                
                if is_design_cell:
                    cell.is_design_element = True
                    info.design_cells.append(cell)
                    if cell_reason not in design_reasons:
                        design_reasons.append(cell_reason)
            
            # 디자인 표 여부 판단 (조건 충족 셀이 하나라도 있으면 디자인 표로 간주)
            if info.design_cells:
                info.is_design_table = True
                info.reason = ", ".join(design_reasons)
            
            return info
            
        except Exception as e:
            self._log("error", f"_analyze_table_design failed: {e}")
            return None
    
    def _get_adjacent_cells(
        self, 
        cell: CellStyleInfo, 
        cell_map: Dict[Tuple[int, int], CellStyleInfo]
    ) -> List[CellStyleInfo]:
        """인접 셀들 반환 (상하좌우)"""
        adjacent = []
        
        positions = [
            (cell.row - 1, cell.col),  # 위
            (cell.row + 1, cell.col),  # 아래
            (cell.row, cell.col - 1),  # 왼쪽
            (cell.row, cell.col + 1),  # 오른쪽
        ]
        
        for pos in positions:
            if pos in cell_map:
                adjacent.append(cell_map[pos])
        
        return adjacent
    
    def is_form_region(self, position: Tuple[int, int, int]) -> bool:
        """특정 위치가 양식 영역인지 확인
        
        Args:
            position: (list, para, char) 위치
            
        Returns:
            bool: 양식 영역 여부
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            # 해당 위치로 이동
            if hasattr(self._hwp, 'set_pos'):
                self._hwp.set_pos(*position)
            elif hasattr(raw_hwp, 'SetPos'):
                raw_hwp.SetPos(*position)
            
            # 글자 스타일 확인
            char_shape = self.get_char_shape()
            if not char_shape:
                return False
            
            # 기울임 또는 색상 있으면 양식 가능성
            is_styled = (
                char_shape.get('italic', False) or
                char_shape.get('text_color', 0) != DEFAULT_TEXT_COLOR
            )
            
            return is_styled
            
        except Exception as e:
            self._log("error", f"is_form_region failed: {e}")
            return False
    
    def should_skip_edit(self, position: Tuple[int, int, int]) -> Tuple[bool, str]:
        """해당 위치의 편집을 건너뛰어야 하는지 판단
        
        양식/디자인 영역은 편집을 건너뛰어야 합니다.
        
        Args:
            position: (list, para, char) 위치
            
        Returns:
            Tuple[bool, str]: (건너뛰기 여부, 사유)
        """
        try:
            raw_hwp = self._get_raw_hwp()
            
            # 1. 표 안인지 확인
            context = self.get_key_indicator()
            if context and context.get('in_table'):
                # 해당 위치로 이동
                if hasattr(self._hwp, 'set_pos'):
                    self._hwp.set_pos(*position)
                
                # 셀 스타일 확인
                cell_info = self.get_cell_border_info()
                if cell_info:
                    # 디자인 셀 조건
                    if cell_info.has_color:
                        return (True, "디자인 테이블 셀(색상)")
            
            # 2. 스타일 텍스트인지 확인
            if self.is_form_region(position):
                return (True, "양식 텍스트 (기울임/색상)")
            
            return (False, "")
            
        except Exception as e:
            self._log("error", f"should_skip_edit failed: {e}")
            return (False, "")


# ============================================================
# 편의 함수
# ============================================================

def create_form_detector(
    hwp,
    log_callback: Optional[Callable[[str, str], None]] = None,
    consecutive_threshold: int = 3,
) -> FormDetector:
    """FormDetector 인스턴스 생성 헬퍼
    
    Args:
        hwp: HWP COM 객체
        log_callback: 로그 콜백
        consecutive_threshold: 연속 스타일 블록 임계값
        
    Returns:
        FormDetector 인스턴스
    """
    return FormDetector(
        hwp=hwp,
        log_callback=log_callback,
        consecutive_threshold=consecutive_threshold,
    )


def detect_form_regions_quick(hwp) -> List[Dict[str, Any]]:
    """빠른 양식 영역 감지 (간단한 딕셔너리 반환)
    
    Args:
        hwp: HWP COM 객체
        
    Returns:
        List of dicts with form region info
    """
    detector = FormDetector(hwp)
    regions = detector.detect_styled_form_regions()
    
    return [
        {
            'start': region.start_position,
            'end': region.end_position,
            'block_count': region.block_count,
            'samples': region.sample_texts,
            'reason': region.reason,
        }
        for region in regions
    ]


def detect_design_tables_quick(hwp) -> List[Dict[str, Any]]:
    """빠른 디자인 표 감지 (간단한 딕셔너리 반환)
    
    Args:
        hwp: HWP COM 객체
        
    Returns:
        List of dicts with design table info
    """
    detector = FormDetector(hwp)
    tables = detector.detect_design_tables()
    
    return [
        {
            'table_id': table.table_id,
            'is_design': table.is_design_table,
            'design_cell_count': len(table.design_cells),
            'reason': table.reason,
        }
        for table in tables
    ]

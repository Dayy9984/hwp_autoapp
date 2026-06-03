import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass

from processing.extraction.hwp_raw_wrapper import HwpRawWrapper

try:
    from engine.connection.document_connector import HwpConnector
except Exception:
    HwpConnector = None

from engine.state.session_state import (
    retrieve_runtime_id_location_mapping,
    retrieve_recent_insertion_data,
    store_recent_insertion_data,
    check_modification_registry,
    register_modification_entry,
)
from parsing.markup_analyzer import StructuralMarkupAnalyzer
from parsing.block_parser import BlockParser
from enum import Enum, auto


# ============================================================================
# Tokenizer-based Prefix Detection
# ============================================================================

class TokenType(Enum):
    """토큰 타입 정의"""
    WHITESPACE = auto()
    OPEN_PAREN = auto()      # (
    CLOSE_PAREN = auto()     # )
    OPEN_BRACKET = auto()    # [
    CLOSE_BRACKET = auto()   # ]
    NUMBER = auto()          # 0-9
    ALPHA_UPPER = auto()     # A-Z
    ALPHA_LOWER = auto()     # a-z
    HANGUL = auto()          # 가-힣
    ROMAN_UPPER = auto()     # I, V, X, L, C, D, M
    ROMAN_LOWER = auto()     # i, v, x, l, c, d, m
    DOT = auto()             # .
    COLON = auto()           # :
    CIRCLED_NUMBER = auto()  # ①-⑳, ㉑-㉟, etc.
    CIRCLED_LETTER = auto()  # Ⓐ-Ⓩ, ⓐ-ⓩ
    CIRCLED_HANGUL = auto()  # ㉮-㉻
    BULLET = auto()          # -, •, *, →, etc.
    PUA_BULLET = auto()      # \uf06d-\uf095
    OTHER = auto()


@dataclass
class Token:
    """토큰 데이터"""
    type: TokenType
    value: str
    position: int


class PrefixTokenizer:
    """문자열을 토큰 시퀀스로 변환"""

    # PUA 문자 범위 정의
    PUA_RANGES = [
        (0xf06d, 0xf06d),  # F06D
        (0xf071, 0xf077),  # F071-F077
        (0xf080, 0xf08f),  # F080-F08F
        (0xf090, 0xf095),  # F090-F095
        (0xf0a6, 0xf0a6),  # F0A6
        (0xf0d8, 0xf0d8),  # F0D8
        (0xf0fc, 0xf0fc),  # F0FC
    ]

    # 일반 불릿 문자 집합
    BULLET_CHARS = set("-–—•◦∙‣⁃‧·・□○●◉⦿⊙◆◇■▣▪▫◻◼◾◽*★☆▲△▼▽✓✔✕✖✘✚✦✧✳❖❍❑❒❯❱►▸▹▻▶▷➤➔➜➡→⇒ㅇ☞◈▤▥▦▧▨▩°∴∇◎◁◀▷▶♤♠♧♣♡♥◐◑❐❏")

    # 로마 숫자 집합
    ROMAN_UPPER_CHARS = set("IVXLCDM")
    ROMAN_LOWER_CHARS = set("ivxlcdm")

    def __init__(self, text: str):
        self.text = text
        self.position = 0
        self.tokens: List[Token] = []

    def _is_pua_bullet(self, char: str) -> bool:
        """PUA 불릿 문자 확인"""
        if not char:
            return False
        code = ord(char)
        for start, end in self.PUA_RANGES:
            if start <= code <= end:
                return True
        return False

    def _classify_char(self, char: str) -> TokenType:
        """단일 문자를 토큰 타입으로 분류"""
        if char.isspace():
            return TokenType.WHITESPACE
        elif char == '(':
            return TokenType.OPEN_PAREN
        elif char == ')':
            return TokenType.CLOSE_PAREN
        elif char == '[':
            return TokenType.OPEN_BRACKET
        elif char == ']':
            return TokenType.CLOSE_BRACKET
        elif char == '.':
            return TokenType.DOT
        elif char == ':':
            return TokenType.COLON
        # 원형 문자 감지 (MUST be before isdigit() check!)
        # Python's isdigit() returns True for circled numbers
        elif '\u2460' <= char <= '\u2473':  # ①-⑳
            return TokenType.CIRCLED_NUMBER
        elif '\u3251' <= char <= '\u325f':  # ㉑-㉟
            return TokenType.CIRCLED_NUMBER
        elif char == '\u24ea':  # ⓪
            return TokenType.CIRCLED_NUMBER
        elif '\u278a' <= char <= '\u2793':  # ➊-➓
            return TokenType.CIRCLED_NUMBER
        elif '\u2780' <= char <= '\u2789':  # ➀-➉
            return TokenType.CIRCLED_NUMBER
        elif '\u2474' <= char <= '\u2487':  # ⑴-⒇
            return TokenType.CIRCLED_NUMBER
        elif '\u2488' <= char <= '\u249b':  # ⒈-⒛
            return TokenType.CIRCLED_NUMBER
        elif '\u24b6' <= char <= '\u24e9':  # Ⓐ-Ⓩⓐ-ⓩ
            return TokenType.CIRCLED_LETTER
        elif '\u249c' <= char <= '\u24b5':  # ⒜-⒵
            return TokenType.CIRCLED_LETTER
        elif '\u326e' <= char <= '\u327b':  # ㉮-㉻
            return TokenType.CIRCLED_HANGUL
        elif char.isdigit():
            return TokenType.NUMBER
        elif char in self.ROMAN_UPPER_CHARS:
            return TokenType.ROMAN_UPPER
        elif char in self.ROMAN_LOWER_CHARS:
            return TokenType.ROMAN_LOWER
        elif char.isupper() and char.isalpha():
            return TokenType.ALPHA_UPPER
        elif char.islower() and char.isalpha():
            return TokenType.ALPHA_LOWER
        elif '가' <= char <= '힣':
            return TokenType.HANGUL
        elif self._is_pua_bullet(char):
            return TokenType.PUA_BULLET
        elif char in self.BULLET_CHARS:
            return TokenType.BULLET
        else:
            return TokenType.OTHER

    def tokenize(self, max_length: int = 20) -> List[Token]:
        """텍스트를 토큰 리스트로 변환 (최대 max_length 문자까지)"""
        self.tokens = []
        pos = 0

        while pos < len(self.text) and pos < max_length:
            char = self.text[pos]
            token_type = self._classify_char(char)

            # 동일 타입의 연속 문자를 하나의 토큰으로 병합
            if token_type in (TokenType.WHITESPACE, TokenType.NUMBER,
                            TokenType.ROMAN_UPPER, TokenType.ROMAN_LOWER):
                value = char
                start_pos = pos
                pos += 1
                while pos < len(self.text) and pos < max_length:
                    next_char = self.text[pos]
                    if self._classify_char(next_char) == token_type:
                        value += next_char
                        pos += 1
                    else:
                        break
                self.tokens.append(Token(token_type, value, start_pos))
            else:
                self.tokens.append(Token(token_type, char, pos))
                pos += 1

        return self.tokens


class PrefixDetector:
    """토큰 시퀀스를 분석하여 글머리 기호 패턴 감지"""

    def __init__(self):
        pass

    def _match_pattern(self, tokens: List[Token]) -> Optional[int]:
        """토큰 시퀀스에서 글머리 패턴 매칭, 매칭된 마지막 토큰 인덱스 반환"""
        if not tokens:
            return None

        idx = 0

        # 선행 공백 스킵
        while idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
            idx += 1

        if idx >= len(tokens):
            return None

        start_idx = idx

        # 패턴 1: 원형 문자 (㉮, ①, Ⓐ 등) + 공백
        if tokens[idx].type in (TokenType.CIRCLED_NUMBER, TokenType.CIRCLED_LETTER, TokenType.CIRCLED_HANGUL):
            idx += 1
            if idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
                return idx
            return None

        # 패턴 2: 불릿/PUA 문자 + 공백
        if tokens[idx].type in (TokenType.BULLET, TokenType.PUA_BULLET):
            idx += 1
            if idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
                return idx
            return None

        # 패턴 3: 괄호형 - (숫자|영문|로마|한글) + 선택적 점 + 공백
        if tokens[idx].type == TokenType.OPEN_PAREN:
            idx += 1
            if idx >= len(tokens):
                return None

            # 숫자 시퀀스 (1-3자리)
            if tokens[idx].type == TokenType.NUMBER:
                if len(tokens[idx].value) > 3:
                    return None
                idx += 1
            # 단일 영문/한글
            elif tokens[idx].type in (TokenType.ALPHA_UPPER, TokenType.ALPHA_LOWER, TokenType.HANGUL):
                if len(tokens[idx].value) != 1:
                    return None
                idx += 1
            # 로마숫자 (1-6자리)
            elif tokens[idx].type in (TokenType.ROMAN_UPPER, TokenType.ROMAN_LOWER):
                if len(tokens[idx].value) > 6:
                    return None
                idx += 1
            else:
                return None

            # 닫는 괄호 필수
            if idx >= len(tokens) or tokens[idx].type != TokenType.CLOSE_PAREN:
                return None
            idx += 1

            # 선택적 점
            if idx < len(tokens) and tokens[idx].type == TokenType.DOT:
                idx += 1

            # 공백 필수
            if idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
                return idx
            return None

        # 패턴 4: 대괄호형 - [숫자|영문|로마|한글] + 선택적 점 + 공백
        if tokens[idx].type == TokenType.OPEN_BRACKET:
            idx += 1
            if idx >= len(tokens):
                return None

            # 숫자 시퀀스 (1-3자리)
            if tokens[idx].type == TokenType.NUMBER:
                if len(tokens[idx].value) > 3:
                    return None
                idx += 1
            # 단일 영문/한글
            elif tokens[idx].type in (TokenType.ALPHA_UPPER, TokenType.ALPHA_LOWER, TokenType.HANGUL):
                if len(tokens[idx].value) != 1:
                    return None
                idx += 1
            # 로마숫자 (1-6자리)
            elif tokens[idx].type in (TokenType.ROMAN_UPPER, TokenType.ROMAN_LOWER):
                if len(tokens[idx].value) > 6:
                    return None
                idx += 1
            else:
                return None

            # 닫는 대괄호 필수
            if idx >= len(tokens) or tokens[idx].type != TokenType.CLOSE_BRACKET:
                return None
            idx += 1

            # 선택적 점
            if idx < len(tokens) and tokens[idx].type == TokenType.DOT:
                idx += 1

            # 공백 필수
            if idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
                return idx
            return None

        # 패턴 5: 숫자/영문/로마/한글 + 점/괄호/콜론 + 공백
        if tokens[idx].type == TokenType.NUMBER:
            if len(tokens[idx].value) > 3:
                return None
            idx += 1
        elif tokens[idx].type in (TokenType.ALPHA_UPPER, TokenType.ALPHA_LOWER, TokenType.HANGUL):
            if len(tokens[idx].value) != 1:
                return None
            idx += 1
        elif tokens[idx].type in (TokenType.ROMAN_UPPER, TokenType.ROMAN_LOWER):
            if len(tokens[idx].value) > 6:
                return None
            idx += 1
        else:
            return None

        # 구분자 (점/괄호/콜론) 필수
        if idx >= len(tokens):
            return None
        if tokens[idx].type not in (TokenType.DOT, TokenType.CLOSE_PAREN, TokenType.COLON):
            return None
        idx += 1

        # 공백 필수
        if idx < len(tokens) and tokens[idx].type == TokenType.WHITESPACE:
            return idx
        return None

    def detect(self, text: Optional[str]) -> Optional[str]:
        """글머리 기호 감지 (토크나이저 기반)"""
        if not text:
            return None

        try:
            # 마크다운 강조 래퍼 제거 (**, __ 등)
            cleaned = text
            try:
                import re
                cleaned = re.sub(r"^\s*(?:\*{2,3}|_{2,3})+\s*", "", text)
            except Exception:
                pass

            # 토큰화
            tokenizer = PrefixTokenizer(cleaned)
            tokens = tokenizer.tokenize(max_length=20)

            # 패턴 매칭
            end_idx = self._match_pattern(tokens)
            if end_idx is None:
                return None

            # 매칭된 토큰들로부터 원본 문자열 추출
            last_token = tokens[end_idx]
            prefix_end_pos = last_token.position + len(last_token.value)

            return cleaned[:prefix_end_pos]

        except Exception:
            return None


class ParagraphFormatter:
    """문단 서식 적용 빌더 (Builder pattern)"""

    # 정렬 타입 매핑
    ALIGN_TYPES = {
        "left": "Left",
        "center": "Center",
        "right": "Right",
        "justify": "Justify",
    }

    def __init__(self, hwp, logger):
        # HwpConnector가 전달되면 connector.hwp (HwpRawWrapper)를 사용
        self.hwp = hwp if hasattr(hwp, "CharShapeSpacingDecrease") else (hwp.hwp if hasattr(hwp, "hwp") else hwp)
        self.logger = logger
        self._font_size_pt = None
        self._font_family_name = None
        self._alignment = None
        self._spacing_pt = None
        self._indent_pt = None

    def with_font_size(self, size_pt: float):
        """폰트 크기 설정 (method chaining)"""
        self._font_size_pt = size_pt
        return self

    def with_font_family(self, family: str):
        """폰트 패밀리 설정 (method chaining)"""
        self._font_family_name = family
        return self

    def with_alignment(self, align: str):
        """문단 정렬 설정 (method chaining)"""
        self._alignment = align
        return self

    def with_spacing(self, spacing_pt: float):
        """문단 간격 설정 (method chaining)"""
        self._spacing_pt = spacing_pt
        return self

    def with_indentation(self, indent_pt: float):
        """들여쓰기 설정 (method chaining)"""
        self._indent_pt = indent_pt
        return self

    def _select_current_paragraph(self):
        """현재 문단 선택"""
        self.hwp.MoveParaBegin()
        self.hwp.MoveSelParaEnd()

    def _apply_character_formatting(self):
        """문자 서식 적용 (폰트 크기 + 폰트 패밀리)"""
        if self._font_size_pt is None and self._font_family_name is None:
            return

        try:
            char_params = self.hwp.HParameterSet.HCharShape
            self.hwp.HAction.GetDefault("CharShape", char_params.HSet)

            # 폰트 크기 적용
            if self._font_size_pt is not None:
                size_val = float(self._font_size_pt)
                if not (1.0 <= size_val <= 150.0):
                    self.logger(
                        f"[WARNING] Font size out of range: {size_val}pt (1-150pt recommended)",
                        "WARNING"
                    )
                # Convert pt to HwpUnit (1pt = 100 HwpUnit)
                char_params.Height = int(round(size_val * 100.0))
                self.logger(
                    f"[STYLE] Font size: {size_val}pt → {char_params.Height} HwpUnit",
                    "INFO"
                )

            # 폰트 패밀리 적용
            if self._font_family_name is not None:
                family_str = str(self._font_family_name)

                # FontType 설정 (best-effort)
                try:
                    ft = self.hwp.FontType("TTF")
                    for attr in ["FontTypeHangul", "FontTypeLatin", "FontTypeHanja",
                               "FontTypeJapanese", "FontTypeOther", "FontTypeSymbol",
                               "FontTypeUser"]:
                        try:
                            setattr(char_params, attr, ft)
                        except:
                            pass
                except:
                    pass

                # FaceName 일괄 설정
                for attr in ["FaceNameHangul", "FaceNameLatin", "FaceNameHanja",
                           "FaceNameJapanese", "FaceNameOther", "FaceNameSymbol",
                           "FaceNameUser"]:
                    setattr(char_params, attr, family_str)

                self.logger(f"[STYLE] Font family: {family_str}", "INFO")

            # CharShape 액션 실행
            self.hwp.HAction.Execute("CharShape", char_params.HSet)

        except Exception as e:
            self.logger(f"[ERROR] Character formatting failed: {e}", "ERROR")

    def _apply_paragraph_alignment(self):
        """문단 정렬 적용"""
        if self._alignment is None:
            return

        try:
            align_lower = str(self._alignment).lower()
            if align_lower not in self.ALIGN_TYPES:
                self.logger(
                    f"[ERROR] Invalid alignment: '{self._alignment}' (must be left/center/right/justify)",
                    "ERROR"
                )
                return

            align_value = self.ALIGN_TYPES[align_lower]
            self.hwp.set_para(AlignType=align_value)
            self.logger(f"[STYLE] Alignment: {align_value}", "INFO")

        except Exception as e:
            self.logger(f"[ERROR] Alignment failed: {e}", "ERROR")

    def _apply_paragraph_spacing(self):
        """문단 간격 적용"""
        if self._spacing_pt is None:
            return

        try:
            spacing_value = float(self._spacing_pt)
            para_params = self.hwp.HParameterSet.HParaShape
            self.hwp.HAction.GetDefault("ParagraphShape", para_params.HSet)
            # Convert pt to HwpUnit for spacing (1pt = 200 HwpUnit)
            para_params.PrevSpacing = int(spacing_value * 200)
            self.hwp.HAction.Execute("ParagraphShape", para_params.HSet)
            self.logger(f"[STYLE] Spacing: {spacing_value}pt", "INFO")

        except Exception as e:
            self.logger(f"[ERROR] Spacing failed: {e}", "ERROR")

    def _apply_paragraph_indentation(self):
        """들여쓰기 적용"""
        if self._indent_pt is None:
            return

        try:
            self.hwp.set_para(Indentation=0)
            indent_value = float(self._indent_pt)
            para_params = self.hwp.HParameterSet.HParaShape
            self.hwp.HAction.GetDefault("ParagraphShape", para_params.HSet)
            # Convert pt to HwpUnit for indentation (1pt = 200 HwpUnit)
            para_params.Indentation = int(indent_value * 200)
            self.hwp.HAction.Execute("ParagraphShape", para_params.HSet)
            self.logger(f"[STYLE] Indentation: {indent_value}pt", "INFO")

        except Exception as e:
            self.logger(f"[ERROR] Indentation failed: {e}", "ERROR")

    def apply(self):
        """모든 서식 적용 실행"""
        # 1. 문단 선택
        self._select_current_paragraph()

        # 2. 문자 서식 적용
        self._apply_character_formatting()

        # 3. 선택 해제
        self.hwp.Cancel()

        # 4. 문단 속성 적용
        self._apply_paragraph_alignment()
        self._apply_paragraph_spacing()
        self._apply_paragraph_indentation()


@dataclass
class CellFormattingConfig:
    """Type-safe cell formatting configuration"""
    fit_mode: str = "none"
    font_size: Optional[float] = None
    font_family: Optional[str] = None
    align: Optional[str] = None
    spacing: Optional[float] = None
    indentation: Optional[float] = None


class ContentModifier:
    """한글 문서 텍스트 대체 클래스"""

    # 접두(글머리/번호 + 공백) 제외 선택 시 최대 이동 문자 수
    MAX_PREFIX_MOVE_CHARS = 20

    # Structural markup analyzer (state machine-based, replaces regex)
    # Detects table, list, li elements using explicit parsing logic
    # Block parser replaces complex regex patterns for table/list extraction

    def __init__(
        self,
        hwp,  # HwpConnector 또는 HwpRawWrapper
        block_manager,
        log_to_main: Callable[[str, Optional[str]], None],
        temp_log_path: Optional[str] = None,
    ):
        """초기화 (의존성 주입 + 상태 관리자 패턴)"""

        class LoggingAdapter:
            """로깅 어댑터 (다양한 시그니처 통합)"""
            def __init__(self, logger_fn: Optional[Callable]):
                self._raw_logger = logger_fn
                self._call_strategy = self._detect_signature(logger_fn)

            def _detect_signature(self, logger_fn) -> str:
                """로거 함수 시그니처 감지"""
                if logger_fn is None:
                    return "noop"

                # 시그니처 감지 시도
                import inspect
                try:
                    sig = inspect.signature(logger_fn)
                    params = list(sig.parameters.keys())
                    if len(params) >= 2 and params[0] in ["level", "severity"]:
                        return "level_first"
                    elif len(params) >= 2 and params[1] in ["level", "severity"]:
                        return "message_first"
                except Exception:
                    pass

                return "auto_detect"

            def __call__(self, message: str, level: Optional[str] = None):
                """통합 로깅 호출"""
                if self._call_strategy == "noop":
                    return

                if self._call_strategy == "level_first":
                    self._raw_logger(level or "INFO", message)
                elif self._call_strategy == "message_first":
                    self._raw_logger(message, level)
                else:
                    # 런타임 감지
                    try:
                        if level is None:
                            self._raw_logger("INFO", message)
                        else:
                            self._raw_logger(level, message)
                    except TypeError:
                        try:
                            self._raw_logger(message, level)
                        except Exception:
                            self._raw_logger(message)

        class StateManager:
            """상태 변수 관리자"""
            def __init__(self):
                # 삽입 추적
                self.last_insert_info = None
                self.saved_caret_pos = None

                # 실행 로깅
                self.execution_logs: List[Dict] = []
                self.ai_raw_response: str = ""
                self.user_instruction: str = ""
                self.model_provider: str = "claude"

                # 표 처리 상태
                self.table_prev_group_id: Optional[int] = None
                self.table_group_op_count: int = 0
                self.table_prev_block_id: Optional[str] = None
                self.table_groups_initialized = set()

                # 편집 추적
                self.edited_blocks = set()

        # 핵심 의존성 주입
        self.hwp = self._unwrap_hwp_connector(hwp)
        self.segment_registry = block_manager
        self.log_to_main = LoggingAdapter(log_to_main)
        self._temp_log_path = temp_log_path

        # 상태 관리자 초기화
        state = StateManager()
        self.last_insert_info = state.last_insert_info
        self.saved_caret_pos = state.saved_caret_pos
        self.execution_logs = state.execution_logs
        self.ai_raw_response = state.ai_raw_response
        self.user_instruction = state.user_instruction
        self.model_provider = state.model_provider
        self._table_prev_group_id = state.table_prev_group_id
        self._table_group_op_count = state.table_group_op_count
        self._table_prev_block_id = state.table_prev_block_id
        self._table_groups_initialized = state.table_groups_initialized
        self._edited_blocks = state.edited_blocks

        # 컴포넌트 팩토리 초기화
        self.prefix_detector = PrefixDetector()
        self._markup_analyzer = StructuralMarkupAnalyzer()
        self._block_parser = BlockParser()

    @staticmethod
    def _unwrap_hwp_connector(hwp_obj):
        """HwpConnector 래핑 해제 - HwpRawWrapper 반환"""
        if HwpConnector and isinstance(hwp_obj, HwpConnector):
            return hwp_obj.hwp  # HwpRawWrapper
        return hwp_obj

    def _retrieve_segment_position(self, block_id: str) -> Optional[Tuple[int, int, int]]:
        """세그먼트 ID로부터 조정된 문서 좌표 조회

        Args:
            block_id: 조회할 세그먼트 식별자

        Returns:
            (list_pos, para_pos, char_pos) 튜플 또는 None
        """
        registry_ref = getattr(self, "segment_registry", None)
        if registry_ref is None:
            self.log_to_main("[ERROR] 세그먼트 레지스트리 미초기화")
            return None

        parsed_result = self._parse_scoped_segment_identifier(block_id)
        extracted_id = parsed_result[1]

        if extracted_id is None:
            return None

        coords = registry_ref.get_adjusted_position(extracted_id)

        # Fallback: 셀 ID인 경우 첫 번째 자식 문단 ID 시도 (CVD 형식 호환성)
        if coords is None and extracted_id:
            try:
                child_id = str(int(extracted_id) + 1)
                coords = registry_ref.get_adjusted_position(child_id)
                if coords:
                    self.log_to_main(f"[INFO] 셀 ID {extracted_id} -> 문단 ID {child_id} fallback 성공")
            except (ValueError, TypeError):
                pass

        if coords is None:
            self.log_to_main(f"[ERROR] 좌표 조회 실패: {block_id}")

        return coords

    def _parse_scoped_segment_identifier(
        self, block_id: Optional[str]
    ) -> Tuple[Optional[int], Optional[str]]:
        if block_id is None:
            return None, None
        raw = str(block_id).strip()
        if ":" in raw:
            scope_raw, inner_raw = raw.split(":", 1)
            if scope_raw.isdigit() and inner_raw.isdigit():
                return int(scope_raw), inner_raw
        return None, raw

    def _resolve_scoped_segment_identifier(
        self, block_id: Optional[str], context: str
    ) -> Optional[str]:
        _, raw_id = self._parse_scoped_segment_identifier(block_id)
        return raw_id

    def _validate_segment_type(
        self, block_id: Optional[str], allowed_types: Optional[set], context: str
    ) -> bool:
        if block_id is None:
            return False
        return True

    def _inspect_current_type(self):
        """현재 커서 위치의 콘텐츠 유형 판별

        Returns:
            "list" (목록 컨텍스트) 또는 "paragraph" (일반 텍스트)
        """
        header_indicator = self.hwp.get_heading_string()
        type_mapping = {True: "list", False: "paragraph"}
        has_header = bool(header_indicator)
        return type_mapping[has_header]

    def _retrieve_current_font_size_point(self) -> Optional[float]:
        """현재 문단의 문자 높이를 포인트 단위로 조회

        Returns:
            문자 크기(pt) 또는 None (조회 실패 시)
        """
        selection_ops = [
            lambda: self.hwp.MoveParaBegin(),
            lambda: self.hwp.MoveSelParaEnd(),
        ]

        for op in selection_ops:
            op()

        return self._extract_font_height_as_points()

    def _extract_font_height_as_points(self) -> Optional[float]:
        """CharShape에서 높이 추출 후 포인트 변환"""
        try:
            char_shape_ref = self.hwp.CharShape
            height_unit = char_shape_ref.Item("Height")
            self.log_to_main(f"[FONT] {height_unit}", "DEBUG")
            converted = self.hwp.HwpUnitToPoint(height_unit)
            return float(converted) if converted is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _insert_styled_content(self, text: str) -> None:
        """마크다운 마크업 해석 및 서식 삽입 (상태 기계 패턴)"""
        from enum import Enum, auto
        from dataclasses import dataclass
        from typing import List, Tuple

        class MarkupToken(Enum):
            """마크업 토큰 타입"""
            BOLD = auto()       # **
            ITALIC = auto()     # *
            HIGHLIGHT = auto()  # ==
            TEXT = auto()       # 일반 텍스트

        @dataclass
        class StyleState:
            """스타일 상태"""
            bold: bool = False
            italic: bool = False
            highlight: bool = False

            def toggle_bold(self):
                self.bold = not self.bold

            def toggle_italic(self):
                self.italic = not self.italic

            def toggle_highlight(self):
                self.highlight = not self.highlight

            def to_font_kwargs(self, has_bold: bool, has_italic: bool) -> dict:
                """폰트 설정 딕셔너리 생성"""
                kwargs = {}
                if has_bold:
                    kwargs["Bold"] = self.bold
                if has_italic:
                    kwargs["Italic"] = self.italic
                return kwargs

        class MarkupParser:
            """마크업 파서 (토큰 기반)"""
            def __init__(self, content: str):
                self.content = content
                self.position = 0
                self.length = len(content)

            def has_more(self) -> bool:
                return self.position < self.length

            def peek_two(self) -> str:
                """2문자 미리보기"""
                if self.position + 1 < self.length:
                    return self.content[self.position:self.position + 2]
                return ""

            def current_char(self) -> str:
                """현재 문자"""
                return self.content[self.position] if self.has_more() else ""

            def advance(self, count: int = 1):
                """위치 전진"""
                self.position += count

            def next_token(self) -> Tuple[MarkupToken, str]:
                """다음 토큰 추출"""
                if not self.has_more():
                    return MarkupToken.TEXT, ""

                # 2문자 토큰 체크
                two_char = self.peek_two()
                if two_char == "**":
                    self.advance(2)
                    return MarkupToken.BOLD, "**"
                if two_char == "==":
                    self.advance(2)
                    return MarkupToken.HIGHLIGHT, "=="

                # 단일 * 체크 (다음이 *가 아닐 때만)
                char = self.current_char()
                if char == "*":
                    next_pos = self.position + 1
                    if next_pos >= self.length or self.content[next_pos] != "*":
                        self.advance()
                        return MarkupToken.ITALIC, "*"

                # 일반 문자
                self.advance()
                return MarkupToken.TEXT, char

        class StyledSegmentRenderer:
            """스타일 적용 렌더러 (저장→설정→입력→복원 패턴)"""
            def __init__(self, hwp_api, has_bold: bool, has_italic: bool):
                self.api = hwp_api
                self.has_bold = has_bold
                self.has_italic = has_italic
                self.buffer = []

            def append(self, char: str):
                """버퍼에 문자 추가"""
                self.buffer.append(char)

            def flush(self, state: StyleState):
                """버퍼 내용을 현재 스타일로 렌더링 (저장→설정→입력→복원 패턴)"""
                if not self.buffer:
                    return

                content = "".join(self.buffer)
                self.buffer.clear()

                try:
                    # 1. 현재 입력 모양(CharShape) 저장
                    base = self.api.HParameterSet.HCharShape
                    self.api.HAction.GetDefault("CharShape", base.HSet)

                    # 2. 작업용 CharShape 설정
                    work = self.api.HParameterSet.HCharShape
                    self.api.HAction.GetDefault("CharShape", work.HSet)

                    # 스타일 적용
                    if self.has_bold:
                        work.Bold = 1 if state.bold else 0
                    if self.has_italic:
                        work.Italic = 1 if state.italic else 0

                    # 3. 현재 입력 모양을 work로 변경
                    self.api.HAction.Execute("CharShape", work.HSet)

                    # 4. 텍스트 입력
                    self.api.HAction.GetDefault("InsertText", self.api.HParameterSet.HInsertText.HSet)
                    self.api.HParameterSet.HInsertText.Text = content
                    self.api.HAction.Execute("InsertText", self.api.HParameterSet.HInsertText.HSet)

                    # 형광펜은 선택 후 적용 필요 (별도 처리)
                    if state.highlight:
                        try:
                            start_pos = self.api.get_pos()
                            # 방금 입력한 텍스트 선택
                            for _ in range(len(content)):
                                self.api.HAction.Run("MovePrevChar")
                            for _ in range(len(content)):
                                self.api.HAction.Run("MoveSelNextChar")
                            self.api.markpen_on_selection(r=255, g=255, b=0)
                            self.api.Cancel()
                            self.api.set_pos(*start_pos)
                        except Exception:
                            pass

                    # 5. 현재 입력 모양을 원래대로 복원
                    self.api.HAction.Execute("CharShape", base.HSet)

                except Exception as e:
                    # 폴백: 단순 삽입
                    try:
                        self.api.insert_text(content)
                    except Exception:
                        pass

        def render_plain_text(content: str):
            """마크업 없는 텍스트 렌더링 (저장→설정→입력→복원 패턴)"""
            try:
                # 1. 현재 입력 모양(CharShape) 저장
                base = self.hwp.HParameterSet.HCharShape
                self.hwp.HAction.GetDefault("CharShape", base.HSet)

                # 2. 작업용 CharShape - 기본 스타일로 명시적 설정
                work = self.hwp.HParameterSet.HCharShape
                self.hwp.HAction.GetDefault("CharShape", work.HSet)
                work.Bold = 0
                work.Italic = 0

                # 3. 현재 입력 모양을 work로 변경
                self.hwp.HAction.Execute("CharShape", work.HSet)

                # 4. 텍스트 입력
                self.hwp.HAction.GetDefault("InsertText", self.hwp.HParameterSet.HInsertText.HSet)
                self.hwp.HParameterSet.HInsertText.Text = content
                self.hwp.HAction.Execute("InsertText", self.hwp.HParameterSet.HInsertText.HSet)

                # 5. 현재 입력 모양을 원래대로 복원
                self.hwp.HAction.Execute("CharShape", base.HSet)

            except Exception:
                # 폴백: 단순 삽입
                self.hwp.insert_text(content)

        def process_line(line_content: str):
            """단일 라인 처리 (마크업 파싱 + 렌더링)"""
            # 마크업 존재 여부 감지
            has_bold = "**" in line_content
            has_italic = bool(re.search(r"\*[^*\n]+\*", line_content))
            has_highlight = "==" in line_content

            # 마크업 없으면 빠른 경로
            if not (has_bold or has_italic or has_highlight):
                render_plain_text(line_content)
                return

            # 상태 기계 실행
            parser = MarkupParser(line_content)
            renderer = StyledSegmentRenderer(self.hwp, has_bold, has_italic)
            state = StyleState()

            while parser.has_more():
                token_type, token_value = parser.next_token()

                if token_type == MarkupToken.BOLD:
                    renderer.flush(state)
                    state.toggle_bold()
                elif token_type == MarkupToken.ITALIC:
                    renderer.flush(state)
                    state.toggle_italic()
                elif token_type == MarkupToken.HIGHLIGHT:
                    renderer.flush(state)
                    state.toggle_highlight()
                elif token_type == MarkupToken.TEXT:
                    renderer.append(token_value)

            renderer.flush(state)

        # 메인 로직
        if text is None:
            text = ""

        normalized = self._normalize_newlines(text)

        # 멀티라인 처리
        if "\n" in normalized:
            lines = normalized.split("\n")
            for idx, line in enumerate(lines):
                if idx > 0:
                    self.hwp.BreakPara()
                process_line(line if line else " ")
        else:
            process_line(normalized)

    def _detect_prefix(self, text: Optional[str]) -> Optional[str]:
        """문단 시작부의 글머리 기호(+뒤 공백) 접두 문자열을 반환. 없으면 None

        토크나이저 기반 구현으로 패턴 매칭을 수행합니다.

        지원 글머리/접두 패턴 (선행 공백 허용, 접두 후 공백 필수):
        - 괄호형: (1), (12), (a), (A), (i), (I), (iv), (IV), (가), (나) [+선택적 .]
        - 대괄호형: [1], [12], [a], [A], [i], [I], [iv], [IV], [가], [나] [+선택적 .]
        - 점/괄호/콜론형: 1., 1), a., A), i., I), iv., IV., 가., 가), 1:, a:, I:
        - 로마숫자: I, II, III, IV, V, ... (소문자 포함) + ., ) 또는 :
        - 원형/원문자: ①-⑳, ㉑-㉟, Ⓐ-Ⓩ, ⓐ-ⓩ, ㉮-㉻ 등
        - 불릿/특수기호: -, –, —, •, ◦, ∙, ‣, ⁃, ‧, ·, □, ○, ●, ◉, ⦿, ⊙, ◆, ◇, ■, ▪, ▫, *, ►, ▸, ▹, ▻, ▶, ▷, ➤, ➔, →, ⇒
        - PUA 문자: \uf06d-\uf095 (Wingdings/Symbol 폰트 기호)
        """
        return self.prefix_detector.detect(text)

    def _select_content_excluding_prefix(
        self, end_list: int, end_para: int, end_pos: int, prefix_len: int
    ) -> None:
        """문단 본문(접두 제외)을 선택 (함수형 접근)

        접두 길이만큼 커서를 이동한 후 문단 끝까지 선택합니다.

        Args:
            end_list: 리스트 인덱스
            end_para: 문단 인덱스
            end_pos: 위치 인덱스
            prefix_len: 건너뛸 접두 문자 수
        """
        # 커서 복원 및 이동을 함수로 캡슐화
        def restore_and_move_cursor():
            self.hwp.set_pos(end_list, end_para, end_pos)
            self.hwp.MoveParaBegin()
            # Iterator 기반 커서 이동 (for loop 대신 map 사용)
            if prefix_len > 0:
                list(map(lambda _: self.hwp.MoveNextChar(), range(prefix_len)))

        def select_to_end():
            self.hwp.MoveSelParaEnd()

        # 작업 실행 및 에러 처리
        try:
            restore_and_move_cursor()
            select_to_end()
        except Exception:
            # 에러 발생 시 선택 취소 시도 (best-effort)
            self._cancel_selection_safe()

    def _cancel_selection_safe(self):
        """선택 취소 (에러 무시)"""
        try:
            self.hwp.Cancel()
        except:
            pass

    def _apply_indent(
        self,
        old_text: Optional[str] = None,
        new_text: Optional[str] = None,
        context_type: str = "paragraph",
        is_multiline: bool = False,
    ) -> None:
        """글머리 기호 기반 들여쓰기 (Strategy + Command + Template Method 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class PrefixAnalysis:
            """접두 분석 결과"""
            old_prefix: Opt[str]
            new_prefix: Opt[str]

            @staticmethod
            def normalize(prefix: Opt[str]) -> str:
                return re.sub(r"\s+", " ", prefix or "").strip()

            def has_identical_prefix(self) -> bool:
                return (self.old_prefix is not None and
                        self.new_prefix is not None and
                        self.normalize(self.old_prefix) == self.normalize(self.new_prefix))

            def should_skip(self) -> bool:
                return self.new_prefix is None

        class HeadingGuard:
            """제목 라인 감지 가드"""
            HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")

            @classmethod
            def is_heading(cls, content: Opt[str]) -> bool:
                try:
                    return bool(content and cls.HEADING_PATTERN.match(content))
                except Exception:
                    return False

        class CursorCommand(ABC):
            """커서 명령 (추상)"""
            @abstractmethod
            def execute(self) -> None:
                pass

        class MoveToPrefixEndCommand(CursorCommand):
            """접두 끝으로 이동 명령"""
            def __init__(self, hwp, prefix: str, max_chars: int):
                self.hwp = hwp
                self.prefix = prefix
                self.max_chars = max_chars

            def execute(self) -> None:
                self.hwp.MoveParaBegin()
                move_distance = min(len(self.prefix), self.max_chars)
                for _ in range(move_distance):
                    self.hwp.MoveNextChar()

        class ApplyIndentCommand(CursorCommand):
            """들여쓰기 적용 명령"""
            def __init__(self, hwp, logger):
                self.hwp = hwp
                self.logger = logger

            def execute(self) -> None:
                try:
                    self.hwp.ParagraphShapeIndentAtCaret()
                except Exception as e:
                    self.logger(f"[ERROR] 들여쓰기 실패: {e}")

        class IndentStrategy(ABC):
            """들여쓰기 전략 (추상)"""
            @abstractmethod
            def apply(self, hwp, prefix: str, max_chars: int, logger) -> None:
                pass

        class StandardIndentStrategy(IndentStrategy):
            """표준 들여쓰기 전략 (동일/새 접두 공통)"""
            def apply(self, hwp, prefix: str, max_chars: int, logger) -> None:
                move_cmd = MoveToPrefixEndCommand(hwp, prefix, max_chars)
                indent_cmd = ApplyIndentCommand(hwp, logger)
                move_cmd.execute()
                indent_cmd.execute()

        class IndentApplicator:
            """들여쓰기 적용기 (Template Method)"""
            def __init__(self, hwp, logger, max_chars: int, detector):
                self.hwp = hwp
                self.logger = logger
                self.max_chars = max_chars
                self.detector = detector
                self.strategy = StandardIndentStrategy()

            def apply_indent(self, old_text: Opt[str], new_text: Opt[str]) -> None:
                # 1단계: 제목 가드
                if HeadingGuard.is_heading(new_text):
                    self.logger("[SKIP] heading 라인: 들여쓰기 생략")
                    return

                # 2단계: 접두 분석
                analysis = PrefixAnalysis(
                    old_prefix=self.detector(old_text) if old_text is not None else None,
                    new_prefix=self.detector(new_text)
                )

                # 3단계: 스킵 가드
                if analysis.should_skip():
                    self.logger("[SKIP] 글머리 기호 접두 없음")
                    return

                # 4단계: 들여쓰기 적용 (동일/새 접두 구분 없이 동일 전략)
                self.strategy.apply(self.hwp, analysis.new_prefix, self.max_chars, self.logger)

        # Main execution: 들여쓰기 적용
        applicator = IndentApplicator(self.hwp, self.log_to_main, self.MAX_PREFIX_MOVE_CHARS, self._detect_prefix)
        applicator.apply_indent(old_text, new_text)

    def _align_leading_spaces(
        self, old_text: str, new_text: Optional[str]
    ) -> Tuple[str, int, int, int]:
        """선행 공백 정렬 (함수형 접근)"""
        from collections import namedtuple

        SpaceInfo = namedtuple('SpaceInfo', ['text', 'trim_count', 'old_count', 'new_count'])

        def count_leading_spaces(content: str) -> int:
            """문자열 앞의 공백 개수 계산"""
            match = re.match(r"^[ ]+", content or "")
            return len(match.group(0)) if match else 0

        def trim_leading_by_count(content: str, count: int) -> str:
            """지정된 개수만큼 선행 공백 제거"""
            return content[count:] if count > 0 else content

        def log_alignment(info: SpaceInfo) -> None:
            """공백 정렬 정보 로깅"""
            if info.trim_count > 0:
                self.log_to_main(
                    f"[공백 정렬] old={info.old_count}, new={info.new_count}, trim={info.trim_count}"
                )
                self.log_to_main(f"[공백 정렬] 결과: '{info.text[:50]}...'")

        actual_content = new_text if new_text is not None else " "

        try:
            old_spaces = count_leading_spaces(old_text)
            new_spaces = count_leading_spaces(actual_content)
            common_spaces = min(old_spaces, new_spaces)

            trimmed = trim_leading_by_count(actual_content, common_spaces)
            result = SpaceInfo(trimmed, common_spaces, old_spaces, new_spaces)

            log_alignment(result)
            return result.text, result.trim_count, result.old_count, result.new_count

        except Exception as e:
            self.log_to_main(f"[공백 정렬] 오류: {e}")
            return actual_content, 0, 0, 0

    def _normalize_newlines(self, text: Optional[str]) -> str:
        """줄바꿈 문자 정규화 (함수형 파이프라인)"""
        from functools import reduce

        if text is None:
            return " "

        # 변환 파이프라인 정의
        line_break_transforms = [
            lambda content: content.replace("\r\n", "\n"),
            lambda content: content.replace("\r", "\n"),
            lambda content: content.replace("\\n", "\n"),
            lambda content: content.replace("\u2028", "\n"),
            lambda content: content.replace("\u2029", "\n"),
            lambda content: content.replace("\u0085", "\n"),
        ]

        try:
            return reduce(lambda acc, transform: transform(acc), line_break_transforms, str(text))
        except Exception:
            return str(text) if text else " "

    def _initialize_table_configuration(
        self, block_id: Optional[str], position: Optional[Tuple[int, int, int]] = None
    ) -> None:
        """표 그룹 초기화 (Guard + Context Manager + State 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt
        from contextlib import contextmanager

        # === Dataclass for validation ===

        @dataclass
        class PositionValidator:
            """위치 검증기"""
            @staticmethod
            def is_valid(pos: any) -> bool:
                """유효한 (list, para, char) 위치인지 검증"""
                return pos and isinstance(pos, tuple) and len(pos) == 3

        # === Context Manager for cursor ===

        @contextmanager
        def cursor_preservation_context(hwp, logger):
            """커서 위치 백업/복원 (Context Manager)"""
            saved_pos = None
            try:
                saved_pos = hwp.get_pos()
            except Exception:
                pass

            try:
                yield saved_pos
            finally:
                if PositionValidator.is_valid(saved_pos):
                    try:
                        hwp.set_pos(*saved_pos)
                    except Exception:
                        pass

        # === Guard Pattern: early exits ===

        class TableInitializationGuard:
            """표 초기화 가드 (검증 체인)"""
            @staticmethod
            def check_prerequisites(registry, block_id: str) -> Opt[str]:
                """전제 조건 검증 (None = 실패)"""
                if not registry or not block_id:
                    return None
                return block_id

            @staticmethod
            def resolve_block_id(modifier, block_id: str) -> Opt[str]:
                """블록 ID 해결 (None = 실패)"""
                resolved = modifier._resolve_scoped_segment_identifier(
                    block_id, "_initial_table_setup"
                )
                return resolved

            @staticmethod
            def get_group_id(registry, block_id: str) -> Opt[str]:
                """그룹 ID 조회 (None = 실패)"""
                try:
                    return registry.get_table_group_id(block_id)
                except Exception:
                    return None

            @staticmethod
            def check_already_initialized(group_id: str, initialized_set: set) -> bool:
                """이미 초기화됨 (True = skip)"""
                try:
                    return group_id in initialized_set
                except Exception:
                    return False

        # === State Pattern: Table Group State ===

        class TableGroupState:
            """표 그룹 상태"""
            def __init__(self, group_id: str, initialized_set: set):
                self.group_id = group_id
                self.initialized_set = initialized_set

            def mark_initialized(self):
                """초기화 완료 마킹"""
                try:
                    self.initialized_set.add(self.group_id)
                except Exception:
                    pass

            def is_initialized(self) -> bool:
                """초기화 여부"""
                return TableInitializationGuard.check_already_initialized(
                    self.group_id, self.initialized_set
                )

        # === Position Resolution Strategy ===

        class PositionResolver:
            """위치 해결 전략"""
            @staticmethod
            def resolve(
                explicit_pos: any, group_id: str, registry
            ) -> Opt[Tuple[int, int, int]]:
                """위치 해결: explicit → representative"""
                # 1. Explicit position (최우선)
                if PositionValidator.is_valid(explicit_pos):
                    return explicit_pos

                # 2. Representative position (fallback)
                if group_id:
                    try:
                        rep_pos = registry.get_representative_pos_for_table_group(group_id)
                        if PositionValidator.is_valid(rep_pos):
                            return rep_pos
                    except Exception:
                        pass

                return None

        # === Template Method: Initialization ===

        class TableInitializer:
            """표 초기화 실행기 (Template Method)"""
            def __init__(self, hwp, modifier, logger):
                self.hwp = hwp
                self.modifier = modifier
                self.logger = logger

            def initialize(self, target_pos: Tuple, group_state: TableGroupState):
                """초기화 실행 (Template Method)"""
                try:
                    self.hwp.set_pos(*target_pos)
                    if not self.hwp.is_cell():
                        return False

                    # 초기화 작업 수행
                    self._execute_initialization()

                    # 상태 업데이트
                    group_state.mark_initialized()
                    self.logger(f"[TABLE] 표 그룹 {group_state.group_id} 초기화 완료")
                    return True

                except Exception as e:
                    self.logger(
                        f"[WARN] 표 그룹 {group_state.group_id} 초기화 실패: {e}",
                        "WARNING",
                    )
                    return False

            def _execute_initialization(self):
                """초기화 작업 (세부 구현)"""
                self.modifier._navigate_to_root_table()
                self.modifier._configure_table_text_wrapping(treat_as_char=False)
                self.modifier._configure_table_boundary_behavior(type="Cell")

        # === Main execution ===

        try:
            guard = TableInitializationGuard()

            # 1. Guard checks (early exits)
            block_id = guard.check_prerequisites(self.segment_registry, block_id)
            if not block_id:
                return

            block_id = guard.resolve_block_id(self, block_id)
            if not block_id:
                return

            group_id = guard.get_group_id(self.segment_registry, block_id)
            if not group_id:
                return

            # 2. State check
            group_state = TableGroupState(group_id, self._table_groups_initialized)
            if group_state.is_initialized():
                return

            # 3. Position resolution
            target_pos = PositionResolver.resolve(position, group_id, self.segment_registry)
            if not target_pos:
                return

            # 4. Execute with cursor preservation
            with cursor_preservation_context(self.hwp, self.log_to_main):
                initializer = TableInitializer(self.hwp, self, self.log_to_main)
                initializer.initialize(target_pos, group_state)

        except Exception as e:
            self.log_to_main(f"[ERROR] _initial_table_setup 실패: {e}", "ERROR")

    def _validate_table_page_boundary(self):
        """표의 페이지 경계 처리 및 TreatAsChar 속성 자동 조정"""
        cell_context = self.hwp.is_cell()
        if not cell_context:
            return

        self._navigate_to_root_table()

        try:
            anchor_position = self.hwp.get_pos()
            self._initialize_table_boundary_defaults()
            self.hwp.set_pos(*anchor_position)

            page_samples = self._collect_table_page_samples()
            self.hwp.set_pos(*anchor_position)

            single_page = self._check_single_page_span(page_samples)
            self._apply_page_span_configuration(single_page)
            return True

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[TABLE] 페이지 경계 검증 오류: {exc}")

    def _initialize_table_boundary_defaults(self):
        """표 경계 기본값 초기화"""
        self._configure_table_text_wrapping(treat_as_char=False)
        self._configure_table_boundary_behavior(type="Cell")

    def _collect_table_page_samples(self) -> Tuple[int, int, int]:
        """표 경계 페이지 샘플 수집"""
        # 시작 셀 페이지
        self.hwp.TableColBegin()
        self.hwp.TableColPageUp()
        self.hwp.move_pos(4)
        start_page = self.hwp.current_page

        # 끝 셀 페이지 (1차)
        self.hwp.TableColEnd()
        self.hwp.TableColPageDown()
        self.hwp.TableColEnd()
        self.hwp.move_pos(5)
        end_page_primary = self.hwp.current_page

        # 끝 셀 페이지 (2차 - 병합 셀 대응)
        self.hwp.TableColEnd()
        self.hwp.TableLeftCell()
        self.hwp.TableColPageDown()
        self.hwp.move_pos(5)
        end_page_secondary = self.hwp.current_page

        return (start_page, end_page_primary, end_page_secondary)

    def _check_single_page_span(self, samples: Tuple[int, int, int]) -> bool:
        """단일 페이지 스팬 여부 확인"""
        unique_pages = set(samples)
        return len(unique_pages) == 1

    def _apply_page_span_configuration(self, single_page: bool):
        """페이지 스팬에 따른 설정 적용"""
        if single_page:
            self._configure_table_text_wrapping(treat_as_char=True)
        else:
            self._configure_table_text_wrapping(treat_as_char=False)
            self._configure_table_boundary_behavior(type="Cell")

    def _schedule_table_boundary_validation(self, block_id: Optional[str]):
        """표 분할 체크 스케줄링 (Context Manager + State Machine + Template Method + Command 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt
        from contextlib import contextmanager
        from abc import ABC, abstractmethod
        from enum import Enum

        # ==================== 데이터 클래스 ====================
        @dataclass
        class TableGroupState:
            """표 그룹 상태"""
            prev_group_id: Opt[str]
            operation_count: int
            prev_block_id: Opt[str]

            def reset(self):
                """상태 리셋"""
                self.prev_group_id = None
                self.operation_count = 0
                self.prev_block_id = None

            def start_new_group(self, group_id: str, block_id: str):
                """새 그룹 시작"""
                self.prev_group_id = group_id
                self.operation_count = 1
                self.prev_block_id = block_id

            def increment_operation(self, block_id: str):
                """같은 그룹 내 연속 작업"""
                self.operation_count += 1
                self.prev_block_id = block_id

        @dataclass
        class ValidationContext:
            """검증 컨텍스트"""
            modifier: 'ContentModifier'
            target_group_id: Opt[str]
            target_block_id: Opt[str]

        class ValidationTrigger(Enum):
            """검증 트리거 유형"""
            GROUP_EXIT = "group_exit"
            PERIODIC_CHECK = "periodic_check"
            GROUP_TRANSITION = "group_transition"

        # ==================== Context Manager ====================
        @contextmanager
        def cursor_preserving_context(hwp):
            """커서 위치 보존 Context Manager"""
            saved_position = None
            try:
                saved_position = hwp.get_pos()
            except Exception:
                pass

            try:
                yield
            finally:
                # 복원 (성공/예외 모두)
                if saved_position and isinstance(saved_position, tuple) and len(saved_position) == 3:
                    try:
                        hwp.set_pos(*saved_position)
                    except Exception:
                        pass

        # ==================== 위치 해결 (Template Method) ====================
        class PositionResolver:
            """위치 해결기 (Template Method 패턴)"""
            def __init__(self, segment_registry):
                self.segment_registry = segment_registry

            def resolve_target_position(self, block_id: Opt[str], fallback_group_id: Opt[str]) -> Opt[tuple]:
                """대상 위치 해결 (Template Method)"""
                # 1. 블록 ID로 시도
                position = self._resolve_by_block(block_id)
                if position:
                    return position

                # 2. Fallback: 그룹 대표 위치
                return self._resolve_by_group(fallback_group_id)

            def _resolve_by_block(self, block_id: Opt[str]) -> Opt[tuple]:
                """블록 ID로 위치 해결"""
                if not block_id:
                    return None
                try:
                    return self.segment_registry.get_adjusted_position(block_id)
                except Exception:
                    return None

            def _resolve_by_group(self, group_id: Opt[str]) -> Opt[tuple]:
                """그룹 대표 위치 해결"""
                if group_id is None:
                    return None
                try:
                    return self.segment_registry.get_representative_pos_for_table_group(group_id)
                except Exception:
                    return None

        # ==================== 검증 명령 (Command + Template Method) ====================
        class ValidationCommand(ABC):
            """검증 명령 (추상, Template Method)"""
            def __init__(self, context: ValidationContext, resolver: PositionResolver):
                self.context = context
                self.resolver = resolver

            def execute(self):
                """Template Method: 공통 실행 흐름"""
                # 1. 위치 해결
                target_pos = self._resolve_position()
                if not target_pos:
                    return

                # 2. 커서 보존 + 이동 + 검증 + 복원
                with cursor_preserving_context(self.context.modifier.hwp):
                    if self._move_to_position(target_pos):
                        self.context.modifier._validate_table_page_boundary()

            @abstractmethod
            def _resolve_position(self) -> Opt[tuple]:
                """위치 해결 (하위 클래스 구현)"""
                pass

            def _move_to_position(self, position: tuple) -> bool:
                """위치로 이동"""
                if position and isinstance(position, tuple) and len(position) == 3:
                    try:
                        self.context.modifier.hwp.set_pos(*position)
                        return True
                    except Exception:
                        pass
                return False

        class PreviousGroupValidation(ValidationCommand):
            """이전 그룹 검증"""
            def _resolve_position(self) -> Opt[tuple]:
                return self.resolver.resolve_target_position(
                    self.context.target_block_id,
                    self.context.target_group_id
                )

        class CurrentGroupValidation(ValidationCommand):
            """현재 그룹 검증"""
            def _resolve_position(self) -> Opt[tuple]:
                return self.resolver.resolve_target_position(
                    self.context.target_block_id,
                    self.context.target_group_id
                )

        # ==================== State Machine ====================
        class TableGroupStateMachine:
            """표 그룹 상태 머신"""
            def __init__(self, modifier):
                self.modifier = modifier
                self.resolver = PositionResolver(modifier.segment_registry)

            def process_event(
                self,
                current_group_id: Opt[str],
                block_id: str,
                state: TableGroupState
            ):
                """이벤트 처리 (상태 전이 + 검증 트리거)"""
                # Case 1: 그룹 외부 (current_group_id is None)
                if current_group_id is None:
                    if state.prev_group_id is not None and state.operation_count > 0:
                        # 그룹 탈출 → 이전 그룹 검증
                        self._trigger_validation(
                            ValidationTrigger.GROUP_EXIT,
                            state.prev_group_id,
                            state.prev_block_id
                        )
                    state.reset()
                    return

                # Case 2: 같은 그룹 연속 작업
                if state.prev_group_id == current_group_id:
                    state.increment_operation(block_id)
                    if state.operation_count % 20 == 0:
                        # 주기적 검증 (매 20회)
                        self._trigger_validation(
                            ValidationTrigger.PERIODIC_CHECK,
                            current_group_id,
                            state.prev_block_id
                        )
                    return

                # Case 3: 다른 그룹으로 전환
                if state.prev_group_id is not None and state.operation_count > 0:
                    # 이전 그룹 마무리 검증
                    self._trigger_validation(
                        ValidationTrigger.GROUP_TRANSITION,
                        state.prev_group_id,
                        state.prev_block_id
                    )

                # 새 그룹 시작
                state.start_new_group(current_group_id, block_id)

            def _trigger_validation(
                self,
                trigger: ValidationTrigger,
                target_group_id: Opt[str],
                target_block_id: Opt[str]
            ):
                """검증 트리거 (Command 실행)"""
                ctx = ValidationContext(
                    modifier=self.modifier,
                    target_group_id=target_group_id,
                    target_block_id=target_block_id
                )

                if trigger == ValidationTrigger.PERIODIC_CHECK:
                    command = CurrentGroupValidation(ctx, self.resolver)
                else:
                    command = PreviousGroupValidation(ctx, self.resolver)

                command.execute()

        # ==================== 메인 로직 ====================
        try:
            # 1. 사전 검증
            if not self.segment_registry or not block_id:
                return

            resolved_id = self._resolve_scoped_segment_identifier(
                block_id, "_deferred_check_table_page_break"
            )
            if resolved_id is None:
                return

            block_id = resolved_id

            # 2. 현재 그룹 식별
            current_group_id = self.segment_registry.get_table_group_id(block_id)

            # 3. 상태 객체 생성
            state = TableGroupState(
                prev_group_id=self._table_prev_group_id,
                operation_count=self._table_group_op_count,
                prev_block_id=self._table_prev_block_id
            )

            # 4. State Machine 실행
            state_machine = TableGroupStateMachine(self)
            state_machine.process_event(current_group_id, block_id, state)

            # 5. 상태 저장
            self._table_prev_group_id = state.prev_group_id
            self._table_group_op_count = state.operation_count
            self._table_prev_block_id = state.prev_block_id

        except Exception:
            pass

    def complete_table_validations(self) -> None:
        """편집 세션 마무리 시 대기 중인 표 분할 검증 수행"""
        pending_validation = self._has_pending_table_validation()

        if pending_validation:
            cursor_snapshot = self._capture_cursor_state()
            validation_coords = self._resolve_validation_target_coords()

            self._navigate_if_valid(validation_coords)
            self._validate_table_page_boundary()
            self._restore_cursor_state(cursor_snapshot)

        self._finalize_table_session_state()

    def _has_pending_table_validation(self) -> bool:
        """대기 중인 표 검증 여부"""
        group_valid = self._table_prev_group_id is not None
        ops_exist = self._table_group_op_count > 0
        return group_valid and ops_exist

    def _capture_cursor_state(self) -> Optional[Tuple[int, int, int]]:
        """현재 커서 상태 캡처"""
        try:
            return self.hwp.get_pos()
        except (AttributeError, RuntimeError):
            return None

    def _resolve_validation_target_coords(self) -> Optional[Tuple[int, int, int]]:
        """검증 대상 좌표 결정 (블록 ID 우선, 그룹 ID 대안)"""
        resolvers = [
            lambda: self.segment_registry.get_adjusted_position(self._table_prev_block_id)
            if self._table_prev_block_id else None,
            lambda: self.segment_registry.get_representative_pos_for_table_group(self._table_prev_group_id)
            if self._table_prev_group_id is not None else None,
        ]

        for resolver in resolvers:
            try:
                coords = resolver()
                if self._is_valid_position_tuple(coords):
                    return coords
            except (AttributeError, KeyError, TypeError):
                continue
        return None

    def _is_valid_position_tuple(self, coords) -> bool:
        """위치 튜플 유효성 검사"""
        return isinstance(coords, tuple) and len(coords) == 3

    def _navigate_if_valid(self, coords: Optional[Tuple[int, int, int]]) -> None:
        """유효한 좌표로 이동"""
        if coords is None:
            return
        try:
            self.hwp.set_pos(coords[0], coords[1], coords[2])
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _restore_cursor_state(self, snapshot: Optional[Tuple[int, int, int]]) -> None:
        """커서 상태 복원"""
        self._navigate_if_valid(snapshot)

    def _finalize_table_session_state(self) -> None:
        """표 세션 상태 정리"""
        self._table_prev_group_id = None
        self._table_group_op_count = 0
        self._table_prev_block_id = None
        init_set = getattr(self, "_table_groups_initialized", None)
        if init_set is not None:
            init_set.clear()

    def _navigate_to_root_table(self):
        """중첩 표 구조에서 최상위 표로 이동"""
        max_depth_limit = 3
        traversal_history = []

        for depth in range(max_depth_limit + 1):
            position_before = self.hwp.get_pos()
            traversal_history.append(position_before)

            self.hwp.MoveParentList()
            cell_check = self.hwp.is_cell()
            self.log_to_main(f"is_cell: {cell_check}")

            should_stop = (not cell_check) or (depth >= max_depth_limit)
            if should_stop:
                self.hwp.set_pos(position_before[0], position_before[1], position_before[2])
                break

    def _configure_table_boundary_behavior(self, type: str = "Cell") -> bool:
        """표 객체의 페이지 경계 분할 방식을 지정

        Args:
            type: 분할 단위 ("Cell" 또는 "None")

        Returns:
            설정 적용 성공 여부
        """
        boundary_modes = {"Cell": "Cell", "None": "None"}
        mode_key = boundary_modes.get(type, "Cell")

        parent_ref = getattr(self.hwp, "ParentCtrl", None)
        if parent_ref is None:
            return self._log_boundary_failure("상위 컨트롤 없음")

        ctrl_select_ok = self._select_parent_control(parent_ref)
        if not ctrl_select_ok:
            return self._log_boundary_failure("컨트롤 선택 실패")

        return self._apply_boundary_mode(mode_key)

    def _select_parent_control(self, ctrl_ref) -> bool:
        """상위 컨트롤 선택"""
        try:
            self.hwp.select_ctrl(ctrl_ref)
            return True
        except (AttributeError, RuntimeError):
            return False

    def _apply_boundary_mode(self, mode: str) -> bool:
        """경계 모드 적용"""
        try:
            shape_params = self.hwp.HParameterSet.HShapeObject
            self.hwp.HAction.GetDefault("TablePropertyDialog", shape_params.HSet)
            shape_params.PageBreak = self.hwp.TableBreak(mode)
            self.hwp.HAction.Execute("TablePropertyDialog", shape_params.HSet)
            return True
        except (AttributeError, RuntimeError, TypeError) as err:
            return self._log_boundary_failure(str(err))

    def _log_boundary_failure(self, reason: str) -> bool:
        """경계 설정 실패 로깅"""
        self.log_to_main(f"[TABLE] 페이지 경계 설정 실패: {reason}")
        return False

    def _configure_table_text_wrapping(self, inline_mode: bool) -> bool:
        """상위 표 컨트롤의 인라인 배치 모드 구성

        Args:
            inline_mode: True면 글자처럼 취급, False면 독립 배치

        Returns:
            속성 변경 성공 여부
        """
        # 상위 컨트롤 참조 획득
        container = getattr(self.hwp, "ParentCtrl", None)
        if container is None:
            return False

        # 표 컨트롤 타입 검증
        desc_attr = getattr(container, "UserDesc", "")
        if desc_attr != "표":
            return False

        # 속성 객체 조작
        try:
            props = container.Properties
            target_value = bool(inline_mode)
            props.SetItem("TreatAsChar", target_value)
            container.Properties = props

            mode_label = "인라인" if target_value else "독립"
            self.log_to_main(f"[TABLE] 배치 모드 변경: {mode_label}")
            return True

        except (AttributeError, TypeError, RuntimeError):
            return False

    def _convert_hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """HEX 색상 문자열을 RGB 튜플로 변환

        Args:
            hex_color: '#RRGGBB' 또는 'RRGGBB' 형식 문자열

        Returns:
            (R, G, B) 튜플, 변환 실패 시 기본 회색
        """
        FALLBACK_COLOR = (200, 205, 223)  # 연한 회색

        # 입력 정규화
        normalized = hex_color.strip().lstrip("#")
        if len(normalized) != 6:
            return FALLBACK_COLOR

        # 각 채널 파싱 (bytes.fromhex 활용)
        try:
            rgb_bytes = bytes.fromhex(normalized)
            return (rgb_bytes[0], rgb_bytes[1], rgb_bytes[2])
        except (ValueError, IndexError):
            return FALLBACK_COLOR

    def _validate_rgb_tuple(self, color) -> Optional[Tuple[int, int, int]]:
        """다양한 색상 표현을 RGB 튜플로 정규화

        지원 형식:
        - 시퀀스: (R, G, B) 또는 [R, G, B]
        - 정수: Windows COLORREF (0xBBGGRR)
        - 문자열: '#RRGGBB' 헥스 코드

        Returns:
            유효한 (R, G, B) 튜플 또는 None
        """
        # 타입별 변환기 매핑
        converters = {
            tuple: self._rgb_from_sequence,
            list: self._rgb_from_sequence,
            int: self._rgb_from_colorref,
            str: self._rgb_from_hex_string,
        }

        converter = converters.get(type(color))
        if converter is None:
            return None

        try:
            return converter(color)
        except (ValueError, TypeError, IndexError):
            return None

    def _rgb_from_sequence(self, seq) -> Optional[Tuple[int, int, int]]:
        """시퀀스(tuple/list)에서 RGB 추출"""
        if len(seq) < 3:
            return None
        return (int(seq[0]), int(seq[1]), int(seq[2]))

    def _rgb_from_colorref(self, colorref: int) -> Tuple[int, int, int]:
        """Windows COLORREF(0xBBGGRR)를 RGB로 변환"""
        # struct.unpack 대신 비트 연산 사용 (다른 접근법)
        raw = colorref.to_bytes(4, byteorder='little')
        return (raw[0], raw[1], raw[2])

    def _rgb_from_hex_string(self, hex_str: str) -> Optional[Tuple[int, int, int]]:
        """헥스 문자열을 RGB로 변환"""
        if not hex_str.startswith("#"):
            return None
        return self._convert_hex_to_rgb(hex_str)

    def _retrieve_current_cell_character_style(self) -> Dict:
        """현재 셀의 글자 속성을 딕셔너리로 반환

        셀 전체를 선택한 후 문자 모양 정보를 조회합니다.
        """
        empty_style: Dict = {}

        # 셀 전체 선택 시도
        select_ok = self._try_select_all_in_cell()
        if not select_ok:
            return empty_style

        # 문자 속성 조회
        char_dict = getattr(self.hwp, "get_charshape_as_dict", lambda: None)()
        return char_dict if char_dict else empty_style

    def _try_select_all_in_cell(self) -> bool:
        """셀 내 전체 선택 수행"""
        try:
            self.hwp.SelectAll()
            return True
        except (AttributeError, RuntimeError):
            return False

    def _retrieve_current_cell_background_color(self) -> Optional[Tuple[int, int, int]]:
        """현재 셀의 배경색 RGB 반환

        단색(WinBrush) 또는 그라데이션(GradBrush)의 첫 색상 추출.
        """
        try:
            border_params = self.hwp.HParameterSet.HCellBorderFill
            self.hwp.HAction.GetDefault("CellFill", border_params.HSet)
            fill_attrs = border_params.FillAttr

            # 채우기 유형 판별
            extracted_color = self._extract_fill_color(fill_attrs)
            return extracted_color

        except (AttributeError, RuntimeError):
            return None

    def _extract_fill_color(self, fill_attrs) -> Optional[Tuple[int, int, int]]:
        """FillAttr에서 색상 추출 (단색/그라데이션 대응)"""
        fill_type = getattr(fill_attrs, "type", 0)

        # 단색 브러시 확인
        win_brush_flag = self.hwp.BrushType("WinBrush")
        if fill_type & win_brush_flag:
            raw_color = getattr(fill_attrs, "WinBrushFaceColor", None)
            return self._validate_rgb_tuple(raw_color) if raw_color else None

        # 그라데이션 브러시 확인
        grad_brush_flag = self.hwp.BrushType("GradBrush")
        if fill_type & grad_brush_flag:
            color_count = getattr(fill_attrs, "GradationColorNum", 0)
            if color_count > 0:
                first_color = fill_attrs.GradationColor.Item(0)
                return self._validate_rgb_tuple(first_color)

        return None

    def _navigate_to_table_initial_cell(self) -> bool:
        """표의 첫 번째 셀(좌상단)로 커서 이동

        Returns:
            이동 성공 여부
        """
        navigation_sequence = [
            lambda: self.hwp.TableColBegin(),
            lambda: self.hwp.TableColPageUp(),
        ]

        for nav_op in navigation_sequence:
            try:
                nav_op()
            except (AttributeError, RuntimeError):
                return False

        return True

    def _retrieve_adjacent_table_style(self, prefer_above: bool = True) -> Dict:
        """인접 표의 스타일 속성 추출 (기본: 상위 표 우선)

        Args:
            prefer_above: True면 상위 표 우선 탐색

        Returns:
            스타일 딕셔너리 (bg_rgb, font_faces, font_size_pt)
        """
        default_style = {
            "bg_rgb": None,
            "font_faces": {},
            "font_size_pt": None,
        }

        try:
            anchor_coords = self.hwp.get_pos()
            extracted = self._extract_first_table_cell_style()
            self.hwp.set_pos(*anchor_coords)
            return extracted

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[TABLE] 인접 표 스타일 추출 오류: {exc}")
            return default_style

    def _extract_first_table_cell_style(self) -> Dict:
        """첫 번째 표 셀의 스타일 속성 추출"""
        self.hwp.get_into_nth_table(1, select_cell=False)
        self.hwp.SelectAll()

        style_extractors = {
            "font_size_pt": self._retrieve_current_font_size_point,
            "font_faces": self._retrieve_current_cell_character_style,
            "bg_rgb": self._retrieve_current_cell_background_color,
        }

        return {key: extractor() for key, extractor in style_extractors.items()}

    def _apply_cell_style(
        self,
        bg_rgb: Tuple[int, int, int],
        faces: Dict,
        size_pt: int,
        bold: bool,
        align: Optional[str] = None,
    ):
        """셀 스타일 적용 (Fluent Builder 패턴)"""

        class CellStyleApplicator:
            """셀 스타일 적용기 (Fluent Interface)"""

            def __init__(self, hwp_api, error_logger):
                self.api = hwp_api
                self.logger = error_logger
                self._is_valid = self.api.is_cell()

            def validate_context(self):
                """셀 컨텍스트 유효성 검사"""
                return self if self._is_valid else None

            def select_all_content(self):
                """셀 전체 선택"""
                if not self._is_valid:
                    return self
                try:
                    self.api.SelectAll()
                except Exception:
                    pass
                return self

            def apply_font_attributes(self, height_pt: int, is_bold: bool):
                """폰트 속성 적용"""
                if not self._is_valid:
                    return self
                try:
                    self.api.set_font(Height=height_pt, Bold=is_bold)
                except Exception:
                    pass
                return self

            def apply_paragraph_alignment(self, alignment_type: Optional[str]):
                """문단 정렬 적용"""
                if not self._is_valid or not alignment_type:
                    return self
                try:
                    self.api.set_para(AlignType=alignment_type)
                except Exception as err:
                    self.logger(f"[TABLE] 정렬 실패: {err}")
                return self

            def apply_font_families(self, font_map: Dict):
                """글꼴 패밀리 적용 (함수형 접근)"""
                if not self._is_valid or not isinstance(font_map, dict):
                    return self

                face_keys = [
                    "FaceNameHangul", "FaceNameLatin", "FaceNameHanja",
                    "FaceNameJapanese", "FaceNameOther", "FaceNameSymbol", "FaceNameUser"
                ]

                def set_face_name(char_shape, key: str) -> None:
                    """단일 폰트명 설정"""
                    value = font_map.get(key)
                    if value:
                        try:
                            char_shape.SetItem(key, value)
                        except Exception:
                            pass

                try:
                    shape_config = self.api.CharShape
                    list(map(lambda k: set_face_name(shape_config, k), face_keys))
                    self.api.CharShape = shape_config
                except Exception:
                    pass

                return self

            def apply_background_color(self, color_tuple: Tuple[int, int, int], validator_fn, fallback_fn):
                """배경색 적용"""
                if not self._is_valid or color_tuple is None:
                    return self

                try:
                    validated = validator_fn(color_tuple) or fallback_fn("#c8cddf")
                    self.api.cell_fill(validated)
                except Exception as err:
                    self.logger(f"[TABLE] 배경색 실패: {err}")

                return self

            def finalize(self):
                """스타일 적용 완료"""
                if not self._is_valid:
                    return
                try:
                    self.api.Cancel()
                except Exception:
                    pass

        try:
            applicator = CellStyleApplicator(self.hwp, self.log_to_main)

            if not applicator.validate_context():
                return

            (applicator
             .select_all_content()
             .apply_font_attributes(size_pt, bold)
             .apply_paragraph_alignment(align)
             .apply_font_families(faces)
             .apply_background_color(bg_rgb, self._validate_rgb_tuple, self._convert_hex_to_rgb)
             .finalize())

        except Exception as err:
            self.log_to_main(f"[TABLE] 스타일 적용 오류: {err}")

    def _apply_default_table_formatting(
        self,
        first_cell_pos: Tuple[int, int, int],
        row_num: int,
        col_num: int,
        header_axis: Optional[str],
    ) -> None:
        """표 기본 스타일 적용 (커맨드 + 전략 패턴)"""
        from dataclasses import dataclass
        from typing import Optional, Tuple
        from abc import ABC, abstractmethod

        @dataclass
        class TableFormattingConfig:
            """표 서식 설정 정보"""
            first_cell_pos: Tuple[int, int, int]
            row_num: int
            col_num: int
            header_axis: Optional[str]
            border_width: str = "0.5mm"
            header_bg_color: Tuple[int, int, int] = (223, 230, 247)

        class HwpActionCommand:
            """HWP 액션 명령 캡슐화"""
            def __init__(self, hwp, logger):
                self.hwp = hwp
                self.logger = logger

            def navigate_to_first_cell(self, pos: Tuple[int, int, int]) -> bool:
                """표 첫 셀로 이동"""
                try:
                    self.hwp.set_pos(*pos)
                    self.hwp.HAction.Run("TableColPageUp")
                    self.hwp.HAction.Run("TableColBegin")
                    return True
                except Exception as e:
                    self.logger(f"[TABLE] 표 첫 셀로 이동 실패: {e}")
                    return False

            def select_entire_table(self) -> bool:
                """표 전체 선택"""
                try:
                    self.hwp.HAction.Run("TableCellBlock")
                    self.hwp.HAction.Run("TableCellBlockExtend")
                    self.hwp.HAction.Run("TableCellBlockExtend")
                    return True
                except Exception:
                    return False

            def apply_border(self, pset, width: str) -> bool:
                """표 테두리 적용"""
                try:
                    self.hwp.HAction.GetDefault("CellBorderFill", pset.HSet)
                    pset.BorderWidthTop = self.hwp.HwpLineWidth(width)
                    pset.BorderWidthBottom = self.hwp.HwpLineWidth(width)
                    self.hwp.HAction.Execute("CellBorderFill", pset.HSet)
                    self.hwp.HAction.Run("Cancel")
                    return True
                except Exception as e:
                    self.logger(f"[TABLE] 표 테두리 스타일 적용 실패: {e}")
                    return False

            def cancel_selection(self):
                """선택 취소"""
                self.hwp.HAction.Run("Cancel")

        class HeaderFormattingStrategy(ABC):
            """헤더 서식 적용 전략 (추상)"""
            def __init__(self, hwp, bg_configurator, logger):
                self.hwp = hwp
                self.bg_configurator = bg_configurator
                self.logger = logger

            @abstractmethod
            def select_header_range(self, row_num: int):
                """헤더 영역 선택 (추상 메서드)"""
                pass

            def apply_formatting(self, pset, bg_color: Tuple[int, int, int], row_num: int):
                """헤더 서식 적용 (공통 로직)"""
                try:
                    # 헤더 영역 선택
                    self.select_header_range(row_num)

                    # 볼드 및 중앙 정렬
                    self.hwp.HAction.Run("CharShapeBold")
                    self.hwp.HAction.Run("ParagraphShapeAlignCenter")

                    # 배경색 설정
                    self.bg_configurator(pset, *bg_color)

                    # 전략별 추가 서식
                    self.apply_additional_formatting(pset)

                    self.hwp.HAction.Run("Cancel")
                    return True

                except Exception as e:
                    self.logger(f"[TABLE] 헤더 스타일 적용 실패: {e}")
                    return False

            def apply_additional_formatting(self, pset):
                """전략별 추가 서식 (오버라이드 가능)"""
                pass

        class HeaderRowStrategy(HeaderFormattingStrategy):
            """헤더 행 전략"""
            def select_header_range(self, row_num: int):
                """첫 행 전체 선택"""
                self.hwp.HAction.Run("TableColPageUp")
                self.hwp.HAction.Run("TableColBegin")
                self.hwp.HAction.Run("TableCellBlock")
                self.hwp.HAction.Run("TableCellBlockExtend")
                self.hwp.HAction.Run("TableColEnd")

            def apply_additional_formatting(self, pset):
                """첫 행 하단 이중선 설정"""
                self.hwp.HAction.GetDefault("CellBorderFill", pset.HSet)
                pset.BorderWidthBottom = self.hwp.HwpLineWidth("0.5mm")
                pset.BorderTypeBottom = 8  # HwpLineType("DoubleSlim")
                self.hwp.HAction.Execute("CellBorderFill", pset.HSet)

        class HeaderColumnStrategy(HeaderFormattingStrategy):
            """헤더 열 전략"""
            def select_header_range(self, row_num: int):
                """첫 열 전체 선택"""
                self.hwp.HAction.Run("TableColPageUp")
                self.hwp.HAction.Run("TableColBegin")
                self.hwp.HAction.Run("TableCellBlock")
                for _ in range(row_num - 1):
                    self.hwp.HAction.Run("TableLowerCellAppend")

        class TableFormattingPipeline:
            """표 서식 적용 파이프라인"""
            def __init__(self, config: TableFormattingConfig, command: HwpActionCommand,
                         pset, navigate_helper, bg_helper):
                self.config = config
                self.command = command
                self.pset = pset
                self.navigate_helper = navigate_helper
                self.bg_helper = bg_helper

            def execute(self) -> None:
                """파이프라인 실행"""
                # 1단계: 첫 셀로 이동
                if not self._navigate_to_cell():
                    return

                # 2단계: 표 전체 서식
                self._format_entire_table()

                # 3단계: 헤더 서식 (선택적)
                if self.config.header_axis:
                    self._apply_header_formatting()

            def _navigate_to_cell(self) -> bool:
                """첫 셀 이동"""
                try:
                    self.command.hwp.set_pos(*self.config.first_cell_pos)
                    self.navigate_helper()
                    return True
                except Exception as e:
                    self.command.logger(f"[TABLE] 표 첫 셀로 이동 실패: {e}")
                    return False

            def _format_entire_table(self):
                """표 전체 서식 적용"""
                if self.command.select_entire_table():
                    self.command.apply_border(self.pset, self.config.border_width)

            def _apply_header_formatting(self):
                """헤더 서식 적용"""
                # 첫 셀로 재이동
                self.command.hwp.set_pos(*self.config.first_cell_pos)
                self.navigate_helper()

                # 전략 선택
                strategy = self._select_header_strategy()
                if strategy:
                    strategy.apply_formatting(
                        self.pset,
                        self.config.header_bg_color,
                        self.config.row_num
                    )

            def _select_header_strategy(self) -> Optional[HeaderFormattingStrategy]:
                """헤더 전략 선택"""
                if self.config.header_axis == "row":
                    return HeaderRowStrategy(self.command.hwp, self.bg_helper, self.command.logger)
                elif self.config.header_axis == "column":
                    return HeaderColumnStrategy(self.command.hwp, self.bg_helper, self.command.logger)
                return None

        # 메인 로직: 파이프라인 실행
        config = TableFormattingConfig(first_cell_pos, row_num, col_num, header_axis)
        pset_border = self.hwp.HParameterSet.HCellBorderFill
        command = HwpActionCommand(self.hwp, self.log_to_main)

        pipeline = TableFormattingPipeline(
            config,
            command,
            pset_border,
            self._navigate_to_table_initial_cell,
            self._configure_cell_background_via_api
        )
        pipeline.execute()

    def _configure_cell_background_via_api(
        self, border_params, red: int, green: int, blue: int
    ) -> bool:
        """HWP API를 통한 셀 배경색 직접 설정

        CellFill 액션을 사용하여 단색 Windows 브러시를 적용합니다.
        """
        try:
            # 단계별 설정 적용
            self._load_cell_fill_defaults(border_params)
            self._setup_solid_color_brush(border_params.FillAttr, red, green, blue)
            self._execute_cell_fill_action(border_params)
            return True

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[TABLE] 배경색 API 설정 오류: {exc}")
            return False

    def _load_cell_fill_defaults(self, params) -> None:
        """CellFill 기본 설정 로드"""
        self.hwp.HAction.GetDefault("CellFill", params.HSet)

    def _setup_solid_color_brush(self, fill_attr, r: int, g: int, b: int) -> None:
        """단색 브러시 속성 구성"""
        # 브러시 조합 타입 설정
        combined_type = self.hwp.BrushType("NullBrush|WinBrush")
        fill_attr.type = combined_type

        # 주 색상과 해치 색상 지정
        neutral_gray = self.hwp.RGBColor(153, 153, 153)
        fill_attr.WinBrushFaceColor = self.hwp.RGBColor(r, g, b)
        fill_attr.WinBrushHatchColor = neutral_gray

        # 해치 스타일 비활성화
        fill_attr.WinBrushFaceStyle = self.hwp.HatchStyle("None")
        fill_attr.WindowsBrush = 1

    def _execute_cell_fill_action(self, params) -> None:
        """CellFill 액션 실행"""
        self.hwp.HAction.Execute("CellFill", params.HSet)

    def _apply_table_section_formatting(
        self,
        first_cell_pos: Tuple[int, int, int],
        row_num: int,
        col_num: int,
        header_axis: Optional[str],
    ) -> None:
        """표 헤더/본문 영역에 차별화된 스타일 적용

        인접 표의 스타일을 참조하여 헤더 행/열에 강조 스타일을 적용.

        Args:
            first_cell_pos: 표 첫 셀 위치 좌표
            row_num: 행 개수
            col_num: 열 개수
            header_axis: 'row'(첫 행) 또는 'column'(첫 열) 헤더 지정
        """
        # 스타일 설정 로드
        style_cfg = self._load_table_style_configuration()

        # 시작 위치로 이동
        if not self._position_to_table_origin(first_cell_pos):
            return

        # 셀 순회 및 스타일 적용
        self._iterate_and_style_cells(row_num, col_num, header_axis, style_cfg)

    def _load_table_style_configuration(self) -> Dict:
        """표 스타일 설정 로드 (인접 표 참조 또는 기본값)"""
        DEFAULT_BG = self._convert_hex_to_rgb("#c8cddf")
        DEFAULT_FONTS = {
            "FaceNameHangul": "한컴돋음",
            "FaceNameLatin": "Arial",
            "FaceNameHanja": "한컴돋음",
        }
        DEFAULT_SIZE = 12

        nearby_style = self._retrieve_adjacent_table_style(prefer_above=True)

        return {
            "background": nearby_style.get("bg_rgb") or DEFAULT_BG,
            "fonts": nearby_style.get("font_faces") or DEFAULT_FONTS,
            "size": int(nearby_style.get("font_size_pt") or DEFAULT_SIZE),
        }

    def _position_to_table_origin(self, pos: Tuple[int, int, int]) -> bool:
        """표 첫 셀로 커서 이동"""
        try:
            self.hwp.set_pos(*pos)
            self._navigate_to_table_initial_cell()
            return True
        except Exception as err:
            self.log_to_main(f"[TABLE] 원점 이동 실패: {err}")
            return False

    def _iterate_and_style_cells(
        self, rows: int, cols: int, axis: Optional[str], cfg: Dict
    ) -> None:
        """모든 셀을 순회하며 헤더/본문 스타일 적용"""
        cell_count = rows * cols
        NEXT_CELL_CODE = 101

        for cell_idx in range(cell_count):
            # 헤더 여부 판정
            row_idx, col_idx = divmod(cell_idx, cols)
            needs_header_style = self._is_header_position(row_idx, col_idx, axis)

            # 헤더 셀에만 스타일 적용
            if needs_header_style:
                self._apply_cell_style(
                    cfg["background"], cfg["fonts"], cfg["size"], True, align="Center"
                )

            # 다음 셀로 이동 (마지막 셀 제외)
            if cell_idx < cell_count - 1:
                if not self._advance_to_next_cell(NEXT_CELL_CODE):
                    break

    def _is_header_position(self, row: int, col: int, axis: Optional[str]) -> bool:
        """주어진 위치가 헤더 영역인지 판정"""
        if axis == "row" and row == 0:
            return True
        if axis == "column" and col == 0:
            return True
        return False

    def _advance_to_next_cell(self, move_code: int) -> bool:
        """다음 셀로 이동"""
        hwp_ref = self.hwp
        navigation_success = False
        try:
            hwp_ref.move_pos(move_code)
            navigation_success = True
        except (AttributeError, RuntimeError, TypeError):
            navigation_success = False
        return navigation_success

    def _retrieve_current_line_length(self) -> int:
        """현재 줄의 텍스트 길이(문자 수) 반환

        줄 시작부터 끝까지 선택 후 문자 수 계산.
        """
        content = self._select_and_get_line_content()
        return len(content)

    def _select_and_get_line_content(self) -> str:
        """현재 줄 전체를 선택하고 텍스트 반환"""
        try:
            # 줄 시작으로 이동 후 줄 끝까지 선택
            self.hwp.MoveLineBegin()
            self.hwp.MoveSelLineEnd()
            selected = self.hwp.get_selected_text(keep_select=False)
            return selected if selected else ""
        except (AttributeError, RuntimeError):
            self._cancel_selection()
            return ""

    def _retrieve_current_cell_length(self) -> int:
        """셀 내 문자 수 계산

        전체 선택 후 선택된 텍스트의 길이를 유니코드 문자 단위로 반환.
        """
        cell_text = self._select_and_get_cell_content()
        char_count = sum(1 for _ in cell_text) if cell_text else 0
        return char_count

    def _select_and_get_cell_content(self) -> str:
        """셀 전체를 선택하고 텍스트 반환"""
        try:
            self.hwp.SelectAll()
            selected = self.hwp.get_selected_text(keep_select=False)
            return selected if selected else ""
        except (AttributeError, RuntimeError):
            self._cancel_selection()
            return ""

    def _cancel_selection(self) -> None:
        """선택 취소 (에러 무시)"""
        try:
            self.hwp.Cancel()
        except Exception:
            pass

    def _validate_optimal_line_break(
        self, end_list: int, end_para: int, end_pos: int, type: str
    ) -> bool:
        """마지막 줄의 시각적 균형 검증

        Args:
            end_list: 리스트 위치
            end_para: 문단 위치
            end_pos: 문자 위치
            type: 컨텍스트 유형 ("cell" 또는 기타)

        Returns:
            줄바꿈이 시각적으로 적절한지 여부
        """
        target_coords = (end_list, end_para, end_pos)
        self.hwp.set_pos(*target_coords)

        final_line_len = self._retrieve_current_line_length()
        if final_line_len == 0:
            return True

        preceding_line_len = self._measure_preceding_line_length(target_coords)
        if preceding_line_len is None:
            return True

        return self._evaluate_line_balance(final_line_len, preceding_line_len, type, target_coords)

    def _measure_preceding_line_length(
        self, reference_coords: Tuple[int, int, int]
    ) -> Optional[int]:
        """선행 줄 길이 측정"""
        self.hwp.set_pos(*reference_coords)
        self.hwp.MoveUp()
        current_pos = self.hwp.get_pos()

        # 동일 문단 내인지 확인
        same_context = (
            current_pos[0] == reference_coords[0] and
            current_pos[1] == reference_coords[1]
        )
        return self._retrieve_current_line_length() if same_context else None

    def _evaluate_line_balance(
        self, final_len: int, prior_len: int, context_type: str, restore_coords: Tuple
    ) -> bool:
        """줄 균형 평가"""
        threshold_map = {"cell": 0.3}
        min_threshold = threshold_map.get(context_type, 0.15)

        balance_ok = True
        if prior_len > 0:
            length_ratio = final_len / prior_len
            balance_ok = length_ratio >= min_threshold

            # 셀 컨텍스트: 극단적 불균형 추가 검사
            cell_imbalance = (
                context_type == "cell" and
                prior_len >= 3 and
                final_len <= 1
            )
            if cell_imbalance:
                balance_ok = False

        self.hwp.set_pos(*restore_coords)
        return balance_ok

    def _reset_spacing_to_zero(self) -> None:
        """자간 초기화 (커맨드 패턴)"""

        class SpacingResetCommand:
            """자간 리셋 명령"""
            def __init__(self, hwp_instance, logger):
                self.hwp = hwp_instance
                self.logger = logger
                self.checkpoint = self._capture_position()

            def _capture_position(self):
                """현재 위치 캡처"""
                try:
                    return self.hwp.get_pos()
                except Exception:
                    return None

            def _select_paragraph(self):
                """문단 선택"""
                self.hwp.MoveParaEnd()
                self.hwp.MoveSelParaBegin()

            def _apply_zero_spacing(self) -> bool:
                """모든 자간 속성을 0으로 설정"""
                char_params = self.hwp.HParameterSet.HCharShape
                self.hwp.HAction.GetDefault("CharShape", char_params.HSet)

                # 자간 속성 리스트에 map 적용
                spacing_attrs = ['SpacingHangul', 'SpacingHanja', 'SpacingLatin',
                                 'SpacingJapanese', 'SpacingOther', 'SpacingSymbol', 'SpacingUser']
                list(map(lambda attr: setattr(char_params, attr, 0), spacing_attrs))

                return self.hwp.HAction.Execute("CharShape", char_params.HSet)

            def _restore_position(self):
                """위치 복원"""
                if self.checkpoint and isinstance(self.checkpoint, tuple) and len(self.checkpoint) == 3:
                    try:
                        self.hwp.set_pos(*self.checkpoint)
                    except Exception:
                        pass

            def execute(self):
                """명령 실행"""
                try:
                    self._select_paragraph()
                    success = self._apply_zero_spacing()

                    if not success:
                        self.logger("[SPACING] 자간 리셋 실패", "DEBUG")

                    self.hwp.Cancel()
                except Exception as e:
                    self.logger(f"[SPACING] 오류: {e}", "DEBUG")
                finally:
                    self._restore_position()

        command = SpacingResetCommand(self.hwp, self.log_to_main)
        command.execute()

    def _perform_post_paragraph_cleanup(
        self,
        text: str,
        context_type: str = "paragraph",
        is_multiline: bool = False,
        fit_mode: str = "none",
        old_text: Optional[str] = None,
        user_align: Optional[str] = None,
        user_indentation: Optional[float] = None,
    ) -> None:
        """문단 후처리 (Strategy + Command + Guard + Template Method 패턴)

        Args:
            text: 입력된 텍스트
            context_type: "cell" 또는 "paragraph"
            is_multiline: 다줄 여부
            fit_mode: 입력값과 무관하게 내부에서 "none"으로 강제됨
            old_text: 기존 텍스트 (들여쓰기 비교용)
            user_align: 사용자 지정 정렬
            user_indentation: 사용자 지정 들여쓰기
        """
        from dataclasses import dataclass
        from typing import Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class CleanupContext:
            """정리 컨텍스트"""
            text: str
            context_type: str
            is_multiline: bool
            old_text: Opt[str]

        class UserConfigGuard:
            """사용자 설정 가드"""
            def __init__(self, user_align: Opt[str], user_indentation: Opt[float]):
                self.user_align = user_align
                self.user_indentation = user_indentation

            def should_reset_indentation(self) -> bool:
                return self.user_indentation is None

            def should_apply_cell_alignment(self, context_type: str, is_multiline: bool) -> bool:
                return self.user_align is None and context_type == "cell" and is_multiline

        class CleanupCommand(ABC):
            """정리 명령 (추상)"""
            @abstractmethod
            def execute(self) -> None:
                pass

        class ResetIndentationCommand(CleanupCommand):
            """들여쓰기 리셋 명령"""
            def __init__(self, hwp, guard: UserConfigGuard):
                self.hwp = hwp
                self.guard = guard

            def execute(self) -> None:
                if self.guard.should_reset_indentation():
                    self.hwp.set_para(Indentation=0)

        class ApplyCellAlignmentCommand(CleanupCommand):
            """셀 정렬 적용 명령"""
            def __init__(self, hwp, ctx: CleanupContext, guard: UserConfigGuard, detector):
                self.hwp = hwp
                self.ctx = ctx
                self.guard = guard
                self.detector = detector

            def execute(self) -> None:
                if not self.guard.should_apply_cell_alignment(self.ctx.context_type, self.ctx.is_multiline):
                    return

                try:
                    has_prefix = bool(self.detector(self.ctx.text)) if self.ctx.text else False
                    align_type = "Left" if has_prefix else "Justify"
                    self.hwp.set_para(AlignType=align_type)
                except Exception:
                    pass

        class ApplyIndentCommand(CleanupCommand):
            """들여쓰기 적용 명령"""
            def __init__(self, indent_fn, ctx: CleanupContext):
                self.indent_fn = indent_fn
                self.ctx = ctx

            def execute(self) -> None:
                self.indent_fn(
                    old_text=self.ctx.old_text,
                    new_text=self.ctx.text,
                    context_type=self.ctx.context_type,
                    is_multiline=self.ctx.is_multiline,
                )

        class ResetSpacingCommand(CleanupCommand):
            """자간 리셋 명령"""
            def __init__(self, reset_fn):
                self.reset_fn = reset_fn

            def execute(self) -> None:
                self.reset_fn()

        class FitModeStrategy(ABC):
            """핏 모드 전략 (추상)"""
            @abstractmethod
            def apply_fit(self, optimize_fn, ctx: CleanupContext) -> None:
                pass

        class AlwaysFitStrategy(FitModeStrategy):
            """항상 핏 적용"""
            def apply_fit(self, optimize_fn, ctx: CleanupContext) -> None:
                optimize_fn()

        class AutoFitStrategy(FitModeStrategy):
            """자동 핏 적용"""
            def apply_fit(self, optimize_fn, ctx: CleanupContext) -> None:
                if ctx.context_type == "cell":
                    optimize_fn()
                elif ctx.text and len(ctx.text) > 2:
                    optimize_fn()

        class NoneFitStrategy(FitModeStrategy):
            """핏 적용 안 함"""
            def apply_fit(self, optimize_fn, ctx: CleanupContext) -> None:
                pass  # Skip

        class MoveCursorCommand(CleanupCommand):
            """커서 이동 명령"""
            def __init__(self, hwp):
                self.hwp = hwp

            def execute(self) -> None:
                self.hwp.MoveParaEnd()

        class CleanupProcess:
            """정리 프로세스 (Template Method)"""
            FIT_STRATEGIES = {
                "always": AlwaysFitStrategy(),
                "auto": AutoFitStrategy(),
                "none": NoneFitStrategy(),
            }

            def __init__(self, modifier, hwp, logger):
                self.modifier = modifier
                self.hwp = hwp
                self.logger = logger

            def cleanup(self, ctx: CleanupContext, fit_mode: str, user_align: Opt[str], user_indentation: Opt[float]) -> None:
                try:
                    guard = UserConfigGuard(user_align, user_indentation)

                    # Command chain 구성
                    commands = [
                        ResetIndentationCommand(self.hwp, guard),
                        ApplyCellAlignmentCommand(self.hwp, ctx, guard, self.modifier._detect_prefix),
                        ApplyIndentCommand(self.modifier._apply_indent, ctx),
                        ResetSpacingCommand(self.modifier._reset_spacing_to_zero),
                    ]

                    # Command chain 실행
                    for cmd in commands:
                        cmd.execute()

                    # Fit 전략 실행
                    fit_strategy = self.FIT_STRATEGIES.get(fit_mode, NoneFitStrategy())
                    fit_strategy.apply_fit(self.modifier._optimize_content_fit, ctx)

                    # 커서 이동
                    MoveCursorCommand(self.hwp).execute()

                except Exception as e:
                    try:
                        self.logger(f"[WARN] 문단 후처리 실패: {e}")
                    except Exception:
                        pass

        # 전역 정책: fit_mode 는 항상 비활성화한다.
        fit_mode = "none"

        # Main execution: 정리 프로세스
        ctx = CleanupContext(text, context_type, is_multiline, old_text)
        process = CleanupProcess(self, self.hwp, self.log_to_main)
        process.cleanup(ctx, fit_mode, user_align, user_indentation)

    def _optimize_content_fit(self) -> None:
        if not self.hwp.is_cell():
            self._optimize_paragraph_content_fit()
        else:
            self._optimize_cell_content_fit()

    def _optimize_paragraph_content_fit(self) -> None:
        """문단 최적화 (Context Manager + State Machine 패턴)"""
        from dataclasses import dataclass
        from contextlib import contextmanager
        from typing import Tuple, Optional

        @dataclass
        class OptimizationContext:
            """최적화 컨텍스트"""
            end_list: int
            end_para: int
            end_pos: int
            prefix_len: int = 0

        class PositionRestorer:
            """위치 복원 Context Manager"""
            def __init__(self, hwp):
                self.hwp = hwp
                self.saved_pos = None

            def __enter__(self):
                """위치 저장"""
                try:
                    self.saved_pos = self.hwp.get_pos()
                except Exception:
                    pass
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                """위치 복원 (성공/예외 모두)"""
                if self.saved_pos and isinstance(self.saved_pos, tuple) and len(self.saved_pos) == 3:
                    try:
                        self.hwp.set_pos(*self.saved_pos)
                    except Exception:
                        pass
                return False

        class LineWrapDetector:
            """줄바꿈 감지기"""
            def __init__(self, hwp):
                self.hwp = hwp

            def detect_wrap(self) -> Tuple[bool, Optional[OptimizationContext]]:
                """줄바꿈 발생 여부 감지 및 컨텍스트 생성"""
                self.hwp.MoveParaEnd()
                end_list, end_para, end_pos = self.hwp.get_pos()

                # 위로 이동하여 줄바꿈 여부 판단
                self.hwp.MoveUp()
                up_list, up_para, _ = self.hwp.get_pos()
                self.hwp.set_pos(end_list, end_para, end_pos)

                wrap_occurred = (up_list == end_list) and (up_para == end_para)

                if wrap_occurred:
                    context = OptimizationContext(end_list, end_para, end_pos)
                    return True, context
                return False, None

        class PrefixExtractor:
            """접두사 추출기"""
            def __init__(self, hwp, prefix_detector, max_chars):
                self.hwp = hwp
                self.prefix_detector = prefix_detector
                self.max_chars = max_chars

            def extract(self, context: OptimizationContext) -> int:
                """접두사 길이 추출"""
                try:
                    self.hwp.set_pos(context.end_list, context.end_para, context.end_pos)
                    self.hwp.MoveParaBegin()
                    self.hwp.MoveSelParaEnd()
                    para_text = self.hwp.get_selected_text(keep_select=False) or ""
                    self.hwp.set_pos(context.end_list, context.end_para, context.end_pos)

                    detected = self.prefix_detector(para_text)
                    if detected:
                        return min(len(detected), self.max_chars)
                except Exception:
                    pass
                return 0

        class SpacingOptimizationLoop:
            """자간 최적화 반복 루프"""
            def __init__(self, hwp, content_selector, validator):
                self.hwp = hwp
                self.content_selector = content_selector
                self.validator = validator
                self.max_iterations = 10

            def optimize(self, context: OptimizationContext):
                """자간 최적화 실행"""
                iterations = self.max_iterations
                while iterations > 0:
                    self.content_selector(
                        context.end_list,
                        context.end_para,
                        context.end_pos,
                        context.prefix_len
                    )
                    self.hwp.CharShapeSpacingDecrease()
                    self.hwp.CharShapeSpacingDecrease()
                    self.hwp.CharShapeSpacingDecrease()

                    iterations -= 1

                    # 최적성 재평가
                    if self.validator(context.end_list, context.end_para, context.end_pos, "paragraph"):
                        break

        class ParagraphOptimizationPipeline:
            """문단 최적화 파이프라인"""
            def __init__(self, hwp, wrap_detector, validator, prefix_extractor, optimizer, logger):
                self.hwp = hwp
                self.wrap_detector = wrap_detector
                self.validator = validator
                self.prefix_extractor = prefix_extractor
                self.optimizer = optimizer
                self.logger = logger

            def execute(self):
                """파이프라인 실행"""
                try:
                    # 1단계: 줄바꿈 감지
                    wrap_occurred, context = self.wrap_detector.detect_wrap()
                    if not wrap_occurred:
                        return

                    # 2단계: 최적성 검증
                    if self.validator(context.end_list, context.end_para, context.end_pos, "paragraph"):
                        return

                    # 3단계: 접두사 추출
                    context.prefix_len = self.prefix_extractor.extract(context)

                    # 4단계: 자간 최적화
                    self.optimizer.optimize(context)

                except Exception as e:
                    self.logger(f"[PARA-POST] 후처리 실패: {e}")

        # 메인 로직: Context Manager로 위치 보호하며 파이프라인 실행
        with PositionRestorer(self.hwp):
            wrap_detector = LineWrapDetector(self.hwp)
            prefix_extractor = PrefixExtractor(
                self.hwp,
                self._detect_prefix,
                self.MAX_PREFIX_MOVE_CHARS
            )
            optimizer = SpacingOptimizationLoop(
                self.hwp,
                self._select_content_excluding_prefix,
                self._validate_optimal_line_break
            )
            pipeline = ParagraphOptimizationPipeline(
                self.hwp,
                wrap_detector,
                self._validate_optimal_line_break,
                prefix_extractor,
                optimizer,
                self.log_to_main
            )
            pipeline.execute()

    def _optimize_cell_content_fit(self) -> None:
        """셀 줄바꿈 최적화 (Context Manager + State Machine + Template Method)"""
        from contextlib import contextmanager
        from dataclasses import dataclass
        from typing import Tuple, Optional

        @contextmanager
        def position_restorer(hwp):
            """위치 복원 Context Manager"""
            saved_pos = None
            try:
                saved_pos = hwp.get_pos()
            except Exception:
                pass

            try:
                yield
            finally:
                if saved_pos and isinstance(saved_pos, tuple) and len(saved_pos) == 3:
                    try:
                        hwp.set_pos(*saved_pos)
                    except Exception:
                        pass

        @dataclass
        class Position:
            """위치 정보"""
            list_: int
            para: int
            pos: int

        class CellValidator:
            """셀 검증"""
            def __init__(self, hwp):
                self.hwp = hwp

            def is_cell(self) -> bool:
                """셀 여부 확인"""
                return self.hwp.is_cell()

        class LineWrapDetector:
            """줄바꿈 감지"""
            def __init__(self, hwp):
                self.hwp = hwp

            def detect(self, end_pos: Position) -> bool:
                """줄바꿈 발생 여부 감지"""
                self.hwp.MoveUp()
                up_list, up_para, _ = self.hwp.get_pos()
                self.hwp.set_pos(end_pos.list_, end_pos.para, end_pos.pos)
                return (up_list == end_pos.list_) and (up_para == end_pos.para)

        class PreviousParagraphChecker:
            """이전 문단 존재 여부 확인"""
            def __init__(self, hwp):
                self.hwp = hwp

            def has_previous_paragraph(self, end_pos: Position) -> bool:
                """같은 셀 내 이전 문단 존재 확인"""
                try:
                    self.hwp.set_pos(end_pos.list_, end_pos.para, end_pos.pos)
                    self.hwp.MoveParaBegin()
                    base_list, base_para, _ = self.hwp.get_pos()
                    self.hwp.MoveUp()
                    prev_list, prev_para, _ = self.hwp.get_pos()
                    return (prev_list == base_list) and (prev_para != base_para)
                except Exception:
                    return False
                finally:
                    try:
                        self.hwp.set_pos(end_pos.list_, end_pos.para, end_pos.pos)
                    except Exception:
                        pass

        class LineBreakValidator:
            """줄바꿈 검증"""
            def __init__(self, modifier):
                self.modifier = modifier

            def is_pretty_break(self, end_pos: Position) -> bool:
                """줄바꿈이 예쁜지 검증"""
                return self.modifier._validate_optimal_line_break(
                    end_pos.list_, end_pos.para, end_pos.pos, "cell"
                )

        class PrefixExtractor:
            """Prefix 추출"""
            def __init__(self, modifier):
                self.modifier = modifier

            def extract_prefix_length(self, end_pos: Position) -> int:
                """Prefix 길이 추출"""
                try:
                    self.modifier.hwp.set_pos(end_pos.list_, end_pos.para, end_pos.pos)
                    self.modifier.hwp.MoveParaBegin()
                    self.modifier.hwp.MoveSelParaEnd()
                    para_text = self.modifier.hwp.get_selected_text(keep_select=False) or ""
                    self.modifier.hwp.set_pos(end_pos.list_, end_pos.para, end_pos.pos)

                    detected = self.modifier._detect_prefix(para_text)
                    if detected:
                        return min(len(detected), self.modifier.MAX_PREFIX_MOVE_CHARS)
                    return 0
                except Exception:
                    return 0

        class SpacingAdjuster:
            """자간 조정"""
            def __init__(self, modifier):
                self.modifier = modifier

            def adjust_until_pretty(self, end_pos: Position, prefix_len: int) -> bool:
                """예쁜 줄바꿈이 될 때까지 자간 조정"""
                safety = 10
                validator = LineBreakValidator(self.modifier)

                while safety > 0:
                    self.modifier._select_content_excluding_prefix(
                        end_pos.list_, end_pos.para, end_pos.pos, prefix_len
                    )
                    self.modifier.hwp.CharShapeSpacingDecrease()
                    self.modifier.hwp.CharShapeSpacingDecrease()
                    self.modifier.hwp.CharShapeSpacingDecrease()

                    safety -= 1
                    if validator.is_pretty_break(end_pos):
                        return True
                return False

        class CellOptimizationPipeline:
            """셀 최적화 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier
                self.hwp = modifier.hwp

            def execute(self):
                """파이프라인 실행"""
                # 1. 셀 검증
                if not CellValidator(self.hwp).is_cell():
                    return

                # 2. 문단 끝으로 이동
                self.hwp.MoveParaEnd()
                end_list, end_para, end_pos = self.hwp.get_pos()
                end_position = Position(end_list, end_para, end_pos)

                # 3. 줄바꿈 감지
                wrap_detector = LineWrapDetector(self.hwp)
                if not wrap_detector.detect(end_position):
                    return

                # 4. 이전 문단 체크
                prev_checker = PreviousParagraphChecker(self.hwp)
                has_prev_para = prev_checker.has_previous_paragraph(end_position)

                # 5. 줄바꿈 검증
                validator = LineBreakValidator(self.modifier)
                if validator.is_pretty_break(end_position):
                    return

                # 6. Prefix 추출
                prefix_extractor = PrefixExtractor(self.modifier)
                prefix_len = prefix_extractor.extract_prefix_length(end_position)

                # 7. 자간 조정
                adjuster = SpacingAdjuster(self.modifier)
                adjuster.adjust_until_pretty(end_position, prefix_len)

        # Pipeline execution with position restoration
        try:
            with position_restorer(self.hwp):
                pipeline = CellOptimizationPipeline(self)
                pipeline.execute()
        except Exception as e:
            self.log_to_main(f"[CELL-POST] 후처리 실패: {e}")

    def _process_multi_line_operation(
        self, operation: str, block_id: str, text: str, method: Callable, **kwargs
    ) -> bool:
        """복수 행 텍스트를 개별 행으로 분리하여 순차 처리

        Args:
            operation: 작업 유형 (list 포함 여부로 후속 함수 결정)
            block_id: 대상 세그먼트 식별자
            text: 줄바꿈 포함 텍스트
            method: 첫 행 처리 콜백
            **kwargs: 스타일 파라미터

        Returns:
            전체 처리 성공 여부
        """
        line_segments = text.split("\n")
        segment_count = len(line_segments)

        # 첫 행 처리
        initial_params = {**kwargs, "new_text": line_segments[0]}
        initial_result = method(block_id, **initial_params)

        if not initial_result:
            return False

        # 후속 행 처리 함수 선택
        operation_mapping = {"list": "append_list", "default": "append_paragraph"}
        handler_key = "list" if "list" in operation else "default"
        continuation_handler = operation_mapping[handler_key]

        # 후속 행 반복 처리 (enumerate로 인덱스 추적)
        for line_idx, line_content in enumerate(line_segments[1:], start=1):
            append_ok = self.prepare_edit_operation(
                continuation_handler, block_id, new_text=line_content
            )
            if not append_ok:
                self.log_to_main(
                    f"[ERROR] 다중행 처리 실패 (행 {line_idx}/{segment_count - 1})"
                )
                return False

        return True

    def _process_markup_fragments(
        self, parts: List[Dict], operation: str, block_id: str, **kwargs
    ) -> bool:
        """HTML 마크업 처리 (Strategy + Template Method + Command + State 패턴)"""
        from dataclasses import dataclass
        from typing import Dict as DictType, Any
        from abc import ABC, abstractmethod

        # ==================== 상태 클래스 ====================
        @dataclass
        class ProcessingState:
            """처리 상태"""
            is_first_element: bool = True

            def mark_first_processed(self):
                """첫 요소 처리 완료 표시"""
                self.is_first_element = False

        # ==================== Operation 변환기 ====================
        class OperationTransformer:
            """Operation 변환 (replace → append)"""
            @staticmethod
            def transform_for_subsequent(original_op: str) -> str:
                """후속 요소용 operation 변환"""
                if not original_op.startswith("replace_"):
                    return original_op

                # replace_* → append_paragraph 매핑
                mapping = {
                    "replace_paragraph": "append_paragraph",
                    "replace_list": "append_paragraph",
                    "replace_cell_content": "append_paragraph"
                }
                return mapping.get(original_op, original_op)

        # ==================== 파트 처리 전략 (Strategy + Template Method) ====================
        class PartProcessor(ABC):
            """파트 처리기 (추상, Template Method)"""
            def __init__(self, modifier, logger):
                self.modifier = modifier
                self.logger = logger

            def process(
                self,
                part: DictType[str, Any],
                part_index: int,
                original_op: str,
                block_id: str,
                state: ProcessingState,
                kwargs: DictType
            ) -> bool:
                """Template Method: 파트 처리 흐름"""
                # 1. 검증
                if not self._validate(part):
                    return True  # 빈 파트는 스킵

                # 2. 로깅
                self._log_processing(part)

                # 3. 전처리 (첫 요소 특별 처리)
                if not self._preprocess_if_first(part_index, original_op, block_id, state, kwargs):
                    return False

                # 4. 메인 처리
                return self._process_content(part, original_op, block_id, state, kwargs)

            @abstractmethod
            def _validate(self, part: DictType) -> bool:
                """파트 검증 (하위 구현)"""
                pass

            @abstractmethod
            def _log_processing(self, part: DictType):
                """로깅 (하위 구현)"""
                pass

            def _preprocess_if_first(
                self, part_index: int, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """첫 요소 전처리 (기본: 스킵, 하위 클래스에서 오버라이드)"""
                return True

            @abstractmethod
            def _process_content(
                self, part: DictType, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """컨텐츠 처리 (하위 구현)"""
                pass

        class TextPartProcessor(PartProcessor):
            """텍스트 파트 처리기"""
            def _validate(self, part: DictType) -> bool:
                return bool(part.get("content"))

            def _log_processing(self, part: DictType):
                pass  # 상세 로그 생략

            def _process_content(
                self, part: DictType, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """텍스트 삽입"""
                new_kwargs = kwargs.copy()
                new_kwargs["new_text"] = part["content"]

                # Operation 결정
                if state.is_first_element and original_op.startswith("replace_"):
                    current_op = original_op
                    state.mark_first_processed()
                else:
                    current_op = OperationTransformer.transform_for_subsequent(original_op)

                success = self.modifier.prepare_edit_operation(current_op, block_id, **new_kwargs)
                if not success:
                    self.logger(f"[ERROR] HTML 텍스트 부분 처리 실패: {current_op}")
                return success

        class TablePartProcessor(PartProcessor):
            """테이블 파트 처리기"""
            def _validate(self, part: DictType) -> bool:
                return "rows" in part and bool(part["rows"])

            def _log_processing(self, part: DictType):
                self.logger(f"[HTML] 테이블 생성: {len(part['rows'])}개 행")

            def _preprocess_if_first(
                self, part_index: int, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """첫 요소이고 replace인 경우 빈 텍스트로 먼저 replace"""
                if part_index == 0 and original_op.startswith("replace_"):
                    replace_kwargs = kwargs.copy()
                    replace_kwargs["new_text"] = " "
                    success = self.modifier.prepare_edit_operation(original_op, block_id, **replace_kwargs)
                    if not success:
                        self.logger("[ERROR] 테이블 전 replace 실패")
                        return False
                    state.mark_first_processed()
                return True

            def _process_content(
                self, part: DictType, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """테이블 생성"""
                create_kwargs = kwargs.copy()
                create_kwargs.pop("new_text", None)
                create_kwargs.pop("text", None)
                create_kwargs["row_texts"] = part["rows"]

                success = self.modifier.prepare_edit_operation("create_table", block_id, **create_kwargs)
                if not success:
                    self.logger("[ERROR] 테이블 생성 실패")
                return success

        class ListPartProcessor(PartProcessor):
            """리스트 파트 처리기"""
            def _validate(self, part: DictType) -> bool:
                return "items" in part and bool(part["items"])

            def _log_processing(self, part: DictType):
                self.logger(f"[HTML] 리스트 추가: {len(part['items'])}개 항목")

            def _process_content(
                self, part: DictType, original_op: str, block_id: str,
                state: ProcessingState, kwargs: DictType
            ) -> bool:
                """리스트 항목 처리 (케이스별 분기)"""
                items = part["items"]
                part_index = 0  # 컨텍스트에서 전달 필요 시 추가

                # Case 1: 첫 요소 + replace_list
                if state.is_first_element and original_op == "replace_list":
                    return self._process_first_replace_list(items, block_id, state)

                # Case 2: 첫 요소 + replace_paragraph
                elif state.is_first_element and original_op == "replace_paragraph":
                    return self._process_first_replace_paragraph(items, block_id, state)

                # Case 3: 그 외 (모두 append_list)
                else:
                    return self._process_append_all(items, block_id)

            def _process_first_replace_list(self, items: list, block_id: str, state: ProcessingState) -> bool:
                """첫 항목 replace, 나머지 append"""
                # 첫 항목 replace
                success = self.modifier.prepare_edit_operation("replace_list", block_id, new_text=items[0])
                if not success:
                    self.logger("[ERROR] 리스트 첫 항목 replace 실패")
                    return False
                state.mark_first_processed()

                # 나머지 append
                return self._process_append_all(items[1:], block_id)

            def _process_first_replace_paragraph(self, items: list, block_id: str, state: ProcessingState) -> bool:
                """먼저 빈 텍스트로 replace, 모든 항목 append"""
                # 빈 텍스트로 replace
                success = self.modifier.prepare_edit_operation("replace_paragraph", block_id, new_text=" ")
                if not success:
                    self.logger("[ERROR] 리스트 전 replace 실패")
                    return False
                state.mark_first_processed()

                # 모든 항목 append
                return self._process_append_all(items, block_id)

            def _process_append_all(self, items: list, block_id: str) -> bool:
                """모든 항목을 append_list로 처리"""
                for item in items:
                    success = self.modifier.prepare_edit_operation("append_list", block_id, new_text=item)
                    if not success:
                        self.logger(f"[ERROR] 리스트 항목 추가 실패: {item[:50]}")
                        return False
                return True

        # ==================== 처리기 팩토리 ====================
        class ProcessorFactory:
            """파트 타입별 처리기 생성"""
            @staticmethod
            def create(part_type: str, modifier, logger) -> PartProcessor:
                """파트 타입에 맞는 처리기 생성"""
                mapping = {
                    "text": TextPartProcessor,
                    "table": TablePartProcessor,
                    "list": ListPartProcessor
                }
                processor_class = mapping.get(part_type)
                if not processor_class:
                    raise ValueError(f"Unknown part type: {part_type}")
                return processor_class(modifier, logger)

        # ==================== 메인 로직 ====================
        self.log_to_main(f"[HTML] {len(parts)}개 부분으로 분리 처리")

        state = ProcessingState()
        factory = ProcessorFactory()

        for i, part in enumerate(parts):
            part_type = part.get("type")
            self.log_to_main(f"[HTML] Part {i + 1}/{len(parts)}: type={part_type}")

            try:
                processor = factory.create(part_type, self, self.log_to_main)
                success = processor.process(part, i, operation, block_id, state, kwargs)
                if not success:
                    return False
            except ValueError as e:
                self.log_to_main(f"[ERROR] Unknown part type: {e}")
                return False

        return True

    def _split_row_preserving_markup(self, row_text: str) -> List[str]:
        """
        행 텍스트 분리 (State + Strategy + Builder 패턴)

        Args:
            row_text: 셀 구분자 |가 포함된 행 텍스트

        Returns:
            List[str]: 분리된 셀 텍스트 리스트
        """
        from dataclasses import dataclass, field
        from typing import List as ListType
        from abc import ABC, abstractmethod

        @dataclass
        class ParserState:
            """파서 상태"""
            in_table: bool = False
            position: int = 0
            current_cell: ListType[str] = field(default_factory=list)
            cells: ListType[str] = field(default_factory=list)

            def finalize_cell(self) -> None:
                """현재 셀 완성"""
                if self.current_cell:
                    self.cells.append("".join(self.current_cell))
                    self.current_cell = []

        class CharHandlerStrategy(ABC):
            """문자 처리 전략 (추상)"""
            @abstractmethod
            def can_handle(self, char: str, state: ParserState, text: str) -> bool:
                pass

            @abstractmethod
            def handle(self, state: ParserState, text: str) -> int:
                """처리 후 다음 위치 반환"""
                pass

        class TableTagHandler(CharHandlerStrategy):
            """<table> 태그 처리기"""
            TAG_START = "<table"
            TAG_END = "</table>"
            TAG_START_LEN = 6
            TAG_END_LEN = 8

            def can_handle(self, char: str, state: ParserState, text: str) -> bool:
                pos = state.position
                return pos + self.TAG_START_LEN <= len(text) and text[pos:pos + self.TAG_START_LEN] == self.TAG_START

            def handle(self, state: ParserState, text: str) -> int:
                pos = state.position
                end_idx = text.find(self.TAG_END, pos)
                if end_idx != -1:
                    # 전체 테이블을 현재 셀에 추가
                    state.current_cell.append(text[pos:end_idx + self.TAG_END_LEN])
                    return end_idx + self.TAG_END_LEN
                return pos + 1

        class PipeSeparatorHandler(CharHandlerStrategy):
            """| 구분자 처리기"""
            def can_handle(self, char: str, state: ParserState, text: str) -> bool:
                return char == "|" and not state.in_table

            def handle(self, state: ParserState, text: str) -> int:
                state.finalize_cell()
                return state.position + 1

        class DefaultCharHandler(CharHandlerStrategy):
            """일반 문자 처리기"""
            def can_handle(self, char: str, state: ParserState, text: str) -> bool:
                return True  # Fallback

            def handle(self, state: ParserState, text: str) -> int:
                state.current_cell.append(text[state.position])
                return state.position + 1

        class RowSplitter:
            """행 분리기 (Template Method)"""
            def __init__(self):
                self.handlers = [
                    TableTagHandler(),
                    PipeSeparatorHandler(),
                    DefaultCharHandler(),
                ]

            def split(self, row_text: str) -> ListType[str]:
                state = ParserState()

                while state.position < len(row_text):
                    char = row_text[state.position]

                    # Handler chain 실행
                    for handler in self.handlers:
                        if handler.can_handle(char, state, row_text):
                            state.position = handler.handle(state, row_text)
                            break

                # 마지막 셀 추가
                state.finalize_cell()
                return state.cells

        # Main execution: 행 분리
        splitter = RowSplitter()
        return splitter.split(row_text)

    def _normalize_and_detect_markup(
        self, cell_text: str
    ) -> Tuple[str, bool, List, bool]:
        """
        텍스트 정규화 및 structural markup 감지

        Args:
            cell_text: 입력 텍스트

        Returns:
            Tuple of (normalized_text, has_markup, parsed_parts, is_multiline)
        """
        # 다중 문단 여부 판정
        is_multiline_input = isinstance(cell_text, str) and (
            "\n" in cell_text or "\r" in cell_text or "<br" in cell_text
        )

        # <br> 태그를 줄바꿈으로 변환 (table/list가 없는 경우만)
        if cell_text and "<table" not in cell_text and "<list" not in cell_text:
            cell_text = (
                cell_text.replace("<br>", "\n")
                .replace("<br/>", "\n")
                .replace("<br />", "\n")
            )

        # Structural markup detection using state machine approach
        has_markup = self._markup_analyzer.contains_structural_markup(cell_text)
        parts = []

        if has_markup:
            parts = self._parse_markup_elements(cell_text)

        return cell_text, has_markup, parts, is_multiline_input

    def _process_text_parts(
        self,
        parts: List[Dict],
        config: CellFormattingConfig,
        is_multiline_input: bool,
        first_part: bool,
    ) -> bool:
        """Text parts 처리 (State Machine + Strategy + Template Method)"""
        from dataclasses import dataclass
        from typing import List, Tuple
        from abc import ABC, abstractmethod

        class BoldStateTracker:
            """굵게 상태 추적기 (State Machine)"""
            def __init__(self):
                self.pending = False

            def apply_prefix(self, text: str) -> str:
                """pending 상태면 ** 접두사 추가"""
                return f"**{text}" if self.pending else text

            def update(self, line: str):
                """줄의 ** 개수로 상태 업데이트"""
                if line.count("**") % 2 == 1:
                    self.pending = not self.pending

        class ContentNormalizer:
            """Content 정규화"""
            @staticmethod
            def normalize(content: str) -> str:
                """빈 content → 공백 변환"""
                return content if content else " "

        class LineProcessorStrategy(ABC):
            """줄 처리 전략 (추상)"""
            def __init__(self, modifier, config, is_multiline_input):
                self.modifier = modifier
                self.config = config
                self.is_multiline_input = is_multiline_input

            @abstractmethod
            def process(self, content: str, first_part: bool, bold_tracker: BoldStateTracker) -> bool:
                """줄 처리 (first_part 반환)"""
                pass

            def _insert_text(self, text: str, first_part: bool) -> bool:
                """텍스트 삽입 (first_part에 따라 BreakPara 결정)"""
                if first_part:
                    self.modifier._insert_styled_content(text)
                    return False
                else:
                    self.modifier.hwp.BreakPara()
                    self.modifier._insert_styled_content(text)
                    return first_part

            def _apply_style_if_needed(self):
                """스타일 적용 (파라미터가 제공된 경우)"""
                if any(
                    p is not None
                    for p in [
                        self.config.font_size,
                        self.config.font_family,
                        self.config.align,
                        self.config.spacing,
                        self.config.indentation,
                    ]
                ):
                    self.modifier._apply_formatting_to_current_paragraph(
                        self.config.font_size,
                        self.config.font_family,
                        self.config.align,
                        self.config.spacing,
                        self.config.indentation,
                    )

            def _cleanup(self, text: str):
                """cleanup 수행"""
                self.modifier._perform_post_paragraph_cleanup(
                    text,
                    "cell" if self.modifier.hwp.is_cell() else "paragraph",
                    is_multiline=self.is_multiline_input,
                    fit_mode=self.config.fit_mode,
                    user_align=self.config.align,
                    user_indentation=self.config.indentation,
                )

        class MultiLineProcessor(LineProcessorStrategy):
            """다중 줄 처리기 (줄바꿈 포함)"""
            def process(self, content: str, first_part: bool, bold_tracker: BoldStateTracker) -> bool:
                lines = content.split("\n")
                for line in lines:
                    text = line if line else " "
                    text = bold_tracker.apply_prefix(text)
                    first_part = self._insert_text(text, first_part)
                    self._apply_style_if_needed()
                    self._cleanup(text)
                    bold_tracker.update(line)
                return first_part

        class SingleLineProcessor(LineProcessorStrategy):
            """단일 줄 처리기 (줄바꿈 없음)"""
            def process(self, content: str, first_part: bool, bold_tracker: BoldStateTracker) -> bool:
                first_part = self._insert_text(content, first_part)
                self._apply_style_if_needed()
                self._cleanup(content)
                return first_part

        class TextPartPipeline:
            """Text parts 처리 파이프라인"""
            def __init__(self, modifier, config, is_multiline_input):
                self.modifier = modifier
                self.config = config
                self.is_multiline_input = is_multiline_input
                self.normalizer = ContentNormalizer()
                self.bold_tracker = BoldStateTracker()

            def process_all(self, parts: List[Dict], first_part: bool) -> bool:
                """모든 parts 처리"""
                for part in parts:
                    if part["type"] == "text":
                        content = self.normalizer.normalize(part["content"])
                        processor = self._select_processor(content)
                        first_part = processor.process(content, first_part, self.bold_tracker)
                return first_part

            def _select_processor(self, content: str) -> LineProcessorStrategy:
                """줄바꿈 포함 여부로 processor 선택"""
                if "\n" in content:
                    return MultiLineProcessor(self.modifier, self.config, self.is_multiline_input)
                else:
                    return SingleLineProcessor(self.modifier, self.config, self.is_multiline_input)

        # Pipeline execution
        pipeline = TextPartPipeline(self, config, is_multiline_input)
        return pipeline.process_all(parts, first_part)

    def _process_list_part(
        self,
        part: Dict,
        config: CellFormattingConfig,
        is_multiline_input: bool,
        first_part: bool,
    ) -> bool:
        """
        List part 처리 (리스트 항목 삽입 및 스타일 적용)

        Args:
            part: 파싱된 list part
            config: 셀 포맷팅 설정
            is_multiline_input: 다중 줄 입력 여부
            first_part: 첫 번째 part 여부

        Returns:
            업데이트된 first_part 값
        """
        if "items" not in part:
            return first_part

        # 리스트 항목들을 문단으로 추가
        for item in part["items"]:
            if not first_part:
                self.hwp.BreakPara()
            first_part = False
            self._insert_styled_content(item)

            # 리스트 항목 입력 직후: 스타일 적용 → 들여쓰기/자간/핏 적용
            # 스타일 적용 (파라미터가 제공된 경우)
            if any(
                p is not None
                for p in [
                    config.font_size,
                    config.font_family,
                    config.align,
                    config.spacing,
                    config.indentation,
                ]
            ):
                self._apply_formatting_to_current_paragraph(
                    config.font_size,
                    config.font_family,
                    config.align,
                    config.spacing,
                    config.indentation,
                )

            self._perform_post_paragraph_cleanup(
                item,
                "cell" if self.hwp.is_cell() else "paragraph",
                is_multiline=is_multiline_input,
                fit_mode=config.fit_mode,
                user_align=config.align,
                user_indentation=config.indentation,
            )

        return first_part

    def _process_plain_text_lines(
        self, cell_text: str, config: CellFormattingConfig
    ) -> None:
        """
        마크업 없는 plain text 줄바꿈 처리 (Strategy + Command + Template Method 패턴)

        Args:
            cell_text: Plain text (마크업 없음)
            config: 셀 포맷팅 설정
        """
        from dataclasses import dataclass
        from typing import List, Callable
        from abc import ABC, abstractmethod

        @dataclass
        class ProcessingContext:
            """라인 처리 컨텍스트"""
            line: str
            is_multiline: bool
            context_type: str

            @staticmethod
            def sanitize_line(line: str) -> str:
                return line if line else " "

        class FormattingGuard:
            """포맷팅 적용 가드"""
            @staticmethod
            def should_apply(config: CellFormattingConfig) -> bool:
                return any(
                    p is not None
                    for p in [config.font_size, config.font_family, config.align, config.spacing, config.indentation]
                )

        class LineCommand(ABC):
            """라인 처리 명령 (추상)"""
            @abstractmethod
            def execute(self) -> None:
                pass

        class InsertContentCommand(LineCommand):
            """콘텐츠 삽입 명령"""
            def __init__(self, inserter: Callable, content: str):
                self.inserter = inserter
                self.content = content

            def execute(self) -> None:
                self.inserter(self.content)

        class ApplyFormattingCommand(LineCommand):
            """포맷팅 적용 명령"""
            def __init__(self, formatter: Callable, config: CellFormattingConfig, guard: FormattingGuard):
                self.formatter = formatter
                self.config = config
                self.guard = guard

            def execute(self) -> None:
                if self.guard.should_apply(self.config):
                    self.formatter(
                        self.config.font_size,
                        self.config.font_family,
                        self.config.align,
                        self.config.spacing,
                        self.config.indentation,
                    )

        class CleanupCommand(LineCommand):
            """정리 명령"""
            def __init__(self, cleanup: Callable, ctx: ProcessingContext, config: CellFormattingConfig):
                self.cleanup = cleanup
                self.ctx = ctx
                self.config = config

            def execute(self) -> None:
                self.cleanup(
                    self.ctx.line,
                    self.ctx.context_type,
                    is_multiline=self.ctx.is_multiline,
                    fit_mode=self.config.fit_mode,
                    user_align=self.config.align,
                    user_indentation=self.config.indentation,
                )

        class LineProcessorStrategy(ABC):
            """라인 처리 전략 (추상, Template Method)"""
            def __init__(self, modifier, config: CellFormattingConfig):
                self.modifier = modifier
                self.config = config
                self.guard = FormattingGuard()

            @abstractmethod
            def pre_process(self) -> None:
                """전처리 (하위 클래스 구현)"""
                pass

            def process_line(self, ctx: ProcessingContext) -> None:
                """라인 처리 템플릿 (공통 흐름)"""
                self.pre_process()
                line_content = ctx.sanitize_line(ctx.line)

                # Command chain 실행
                commands = [
                    InsertContentCommand(self.modifier._insert_styled_content, line_content),
                    ApplyFormattingCommand(self.modifier._apply_formatting_to_current_paragraph, self.config, self.guard),
                    CleanupCommand(self.modifier._perform_post_paragraph_cleanup, ctx, self.config),
                ]

                for cmd in commands:
                    cmd.execute()

        class FirstLineProcessor(LineProcessorStrategy):
            """첫 줄 처리기"""
            def pre_process(self) -> None:
                pass  # 첫 줄은 전처리 불필요

        class AdditionalLineProcessor(LineProcessorStrategy):
            """추가 줄 처리기"""
            def __init__(self, modifier, config: CellFormattingConfig, hwp):
                super().__init__(modifier, config)
                self.hwp = hwp

            def pre_process(self) -> None:
                self.hwp.BreakPara()

        # Main execution: Pipeline
        cell_lines = cell_text.split("\n")

        if not cell_lines:
            self._insert_styled_content(" ")
            return

        # Context type 결정
        context_type = "cell" if self.hwp.is_cell() else "paragraph"
        is_multiline = len(cell_lines) > 1

        # 첫 줄 처리
        first_processor = FirstLineProcessor(self, config)
        first_ctx = ProcessingContext(cell_lines[0], is_multiline, context_type)
        first_processor.process_line(first_ctx)

        # 추가 줄 처리 (있는 경우)
        if is_multiline:
            additional_processor = AdditionalLineProcessor(self, config, self.hwp)
            for line in cell_lines[1:]:
                add_ctx = ProcessingContext(line, True, context_type)
                additional_processor.process_line(add_ctx)

    def _process_table_part(
        self,
        part: Dict,
        config: CellFormattingConfig,
        is_multiline_input: bool,
        first_part: bool,
    ) -> Tuple[int, bool]:
        """Table part 처리 (Strategy + Template Method + Builder + Pipeline 패턴)"""
        from dataclasses import dataclass
        from typing import List, Optional
        from abc import ABC, abstractmethod

        # ==================== 데이터 클래스 ====================
        @dataclass
        class TableDimensions:
            """테이블 크기 정보"""
            rows: int
            cols: int
            total_cells: int

        @dataclass
        class CellProcessingContext:
            """셀 처리 컨텍스트"""
            row_idx: int
            col_idx: int
            cell_value: str
            config: CellFormattingConfig
            is_multiline_input: bool

        # ==================== Cell Value 처리 전략 (Strategy + Template Method) ====================
        class CellValueProcessor(ABC):
            """셀 값 처리기 (추상, Template Method 패턴)"""
            def __init__(self, modifier, logger):
                self.modifier = modifier
                self.logger = logger

            def process(self, ctx: CellProcessingContext) -> bool:
                """Template Method: 공통 흐름 정의"""
                try:
                    # 1. 컨텐츠 삽입
                    self._insert_content(ctx)

                    # 2. 포맷팅 적용 (공통 로직)
                    self._apply_formatting(ctx)

                    return True
                except Exception as err:
                    self.logger(f"[WARN] 내부 표 셀 후처리 실패: {err}")
                    return False

            @abstractmethod
            def _insert_content(self, ctx: CellProcessingContext):
                """컨텐츠 삽입 (하위 클래스 구현)"""
                pass

            def _apply_formatting(self, ctx: CellProcessingContext):
                """포맷팅 적용 (공통 로직)"""
                # 스타일 적용
                if any(p is not None for p in [ctx.config.font_size, ctx.config.font_family, ctx.config.align]):
                    self.modifier._apply_formatting_to_current_paragraph(
                        ctx.config.font_size,
                        ctx.config.font_family,
                        ctx.config.align,
                        None,
                        None,
                    )

                # 후처리 정리
                context_type = "cell" if self.modifier.hwp.is_cell() else "paragraph"
                self.modifier._perform_post_paragraph_cleanup(
                    ctx.cell_value if hasattr(self, '_last_text') else ctx.cell_value,
                    context_type,
                    is_multiline=ctx.is_multiline_input,
                    fit_mode=ctx.config.fit_mode,
                    user_align=ctx.config.align,
                    user_indentation=ctx.config.indentation,
                )

        class MultilineCellProcessor(CellValueProcessor):
            """다중 줄 셀 처리기"""
            def _insert_content(self, ctx: CellProcessingContext):
                """줄바꿈 포함 컨텐츠 삽입"""
                lines = ctx.cell_value.split("\n")
                for idx, line_text in enumerate(lines):
                    if idx > 0:
                        self.modifier.hwp.BreakPara()
                    text = line_text if line_text else " "
                    self.modifier._insert_styled_content(text)
                    self._last_text = text  # 마지막 텍스트 저장
                    if idx < len(lines) - 1:
                        # 중간 줄: 즉시 포맷팅 적용
                        self._apply_formatting(ctx)

        class SingleLineCellProcessor(CellValueProcessor):
            """단일 줄 셀 처리기"""
            def _insert_content(self, ctx: CellProcessingContext):
                """단일 컨텐츠 삽입"""
                self.modifier._insert_styled_content(ctx.cell_value)

        class EmptyCellProcessor(CellValueProcessor):
            """빈 셀 처리기"""
            def _insert_content(self, ctx: CellProcessingContext):
                """공백 삽입"""
                self.modifier._insert_styled_content(" ")

        # ==================== 셀 처리 전략 선택 (Factory) ====================
        class CellProcessorFactory:
            """셀 처리기 팩토리"""
            @staticmethod
            def create(cell_value: str, col_idx: int, cells: List[str], modifier, logger) -> CellValueProcessor:
                """셀 값에 따라 적절한 처리기 생성"""
                if col_idx >= len(cells):
                    # 범위 초과 → 빈 셀
                    return EmptyCellProcessor(modifier, logger)

                val = cells[col_idx] if cells[col_idx] else " "
                if isinstance(val, str) and "\n" in val:
                    # 줄바꿈 포함 → 다중 줄
                    return MultilineCellProcessor(modifier, logger)

                # 단일 줄
                return SingleLineCellProcessor(modifier, logger)

        # ==================== 테이블 구축 (Builder) ====================
        class InnerTableBuilder:
            """내부 테이블 빌더"""
            def __init__(self, modifier, logger):
                self.modifier = modifier
                self.logger = logger
                self.dimensions: Optional[TableDimensions] = None
                self.rows_data: List[str] = []
                self.first_cell_pos = None

            def set_dimensions(self, rows: List[str]) -> 'InnerTableBuilder':
                """테이블 크기 설정"""
                first_row_cells = rows[0].split("|")
                cols = len(first_row_cells)
                row_count = len(rows)
                self.dimensions = TableDimensions(
                    rows=row_count,
                    cols=cols,
                    total_cells=row_count * cols
                )
                self.rows_data = rows
                return self

            def prepare_cursor(self) -> 'InnerTableBuilder':
                """커서 위치 준비"""
                hwp = self.modifier.hwp
                hwp.MoveParaEnd()
                hwp.BreakPara()
                hwp.BreakPara()
                hwp.BreakPara()
                hwp.MoveLineUp()
                return self

            def create_table_structure(self) -> bool:
                """테이블 구조 생성"""
                result = self.modifier.hwp.create_table(
                    rows=self.dimensions.rows,
                    cols=self.dimensions.cols,
                    treat_as_char=True,
                    header=False,
                )
                if result:
                    self.first_cell_pos = self.modifier.hwp.get_pos()
                return result

            def fill_cells(self, config: CellFormattingConfig, is_multiline_input: bool) -> int:
                """셀 채우기"""
                factory = CellProcessorFactory()
                cell_counter = 0

                for row_idx, row_text in enumerate(self.rows_data):
                    cells = row_text.split("|")
                    for col_idx in range(self.dimensions.cols):
                        # Factory로 처리기 생성
                        cell_val = cells[col_idx] if col_idx < len(cells) and cells[col_idx] else " "
                        processor = factory.create(cell_val, col_idx, cells, self.modifier, self.logger)

                        # Context 생성 및 처리
                        ctx = CellProcessingContext(
                            row_idx=row_idx,
                            col_idx=col_idx,
                            cell_value=cell_val,
                            config=config,
                            is_multiline_input=is_multiline_input,
                        )
                        processor.process(ctx)

                        # 다음 셀로 이동
                        cell_counter += 1
                        if cell_counter < self.dimensions.total_cells:
                            self.modifier.hwp.move_pos(101)

                return self.dimensions.total_cells

            def apply_table_formatting(self) -> 'InnerTableBuilder':
                """테이블 스타일 적용"""
                try:
                    self.modifier.hwp.set_pos(*self.first_cell_pos)
                    self.modifier._apply_table_section_formatting(
                        first_cell_pos=self.first_cell_pos,
                        row_num=self.dimensions.rows,
                        col_num=self.dimensions.cols,
                        header_axis="row",
                    )
                except Exception as err:
                    self.logger(f"[WARN] 내부 표 스타일 적용 실패: {err}")
                return self

            def escape_table(self) -> 'InnerTableBuilder':
                """테이블 탈출"""
                try:
                    hwp = self.modifier.hwp
                    hwp.TableColEnd()
                    hwp.TableColPageDown()
                    hwp.move_pos(5)
                    hwp.MoveRight()
                except Exception as err:
                    self.logger(f"[WARN] 표 탈출 시퀀스 실패: {err}")
                return self

        # ==================== 테이블 처리 파이프라인 ====================
        class TablePartPipeline:
            """테이블 파트 처리 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier

            def execute(
                self,
                part: Dict,
                config: CellFormattingConfig,
                is_multiline_input: bool,
                first_part: bool
            ) -> Tuple[int, bool]:
                """파이프라인 실행"""
                added_cells = 0

                # 1단계: 검증
                if "rows" not in part or not part["rows"]:
                    return added_cells, first_part

                # 2단계: 준비
                if not first_part:
                    self.modifier.hwp.BreakPara()
                first_part = False

                rows = part["rows"]
                if not rows:
                    return added_cells, first_part

                # 3단계: 테이블 빌드
                builder = InnerTableBuilder(self.modifier, self.modifier.log_to_main)
                builder.set_dimensions(rows)

                dims = builder.dimensions
                self.modifier.log_to_main(f"    셀 내부에 {dims.rows}x{dims.cols} 표 생성 시도")

                builder.prepare_cursor()
                table_created = builder.create_table_structure()

                if table_created:
                    # 4단계: 셀 채우기
                    added_cells = builder.fill_cells(config, is_multiline_input)
                    self.modifier.log_to_main(f"    셀 내부 표 생성 완료 ({added_cells}개 셀 추가)")

                    # 5단계: 후처리
                    builder.apply_table_formatting().escape_table()
                else:
                    # Fallback: 텍스트로 변환
                    self.modifier.log_to_main("    셀 내부 표 생성 실패 - 텍스트로 변환")
                    for row in rows:
                        self.modifier.hwp.BreakPara()
                        self.modifier._insert_styled_content(row)

                return added_cells, first_part

        # ==================== 메인 로직 ====================
        pipeline = TablePartPipeline(self)
        return pipeline.execute(part, config, is_multiline_input, first_part)

    def _process_markup_for_row_operation(
        self,
        cell_text: str,
        fit_mode: str = "none",
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        align: Optional[str] = None,
        spacing: Optional[float] = None,
        indentation: Optional[float] = None,
    ) -> int:
        """
        셀 내용에 HTML이 포함된 경우 처리하는 공통 메서드

        Args:
            cell_text: 셀에 입력할 텍스트 (HTML 포함 가능)
            fit_mode: 호환용 인자 (내부 정책상 항상 "none"으로 강제)
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬
            spacing: 문단 간격 (포인트 단위)
            indentation: 들여쓰기 (포인트 단위)

        Returns:
            int: 추가된 셀 개수 (셀 내부에 표가 생성된 경우)
        """
        try:
            # Configuration object 생성 (type-safe)
            config = CellFormattingConfig(
                fit_mode=fit_mode,
                font_size=font_size,
                font_family=font_family,
                align=align,
                spacing=spacing,
                indentation=indentation,
            )

            # 1단계: 정규화 및 마크업 감지
            normalized_text, has_markup, parts, is_multiline = (
                self._normalize_and_detect_markup(cell_text)
            )

            if has_markup:
                self.log_to_main("  셀에 HTML 요소 감지")
                added_cells = 0
                first_part = True

                # 2단계: parts 순회하며 타입별 처리
                for part in parts:
                    if part["type"] == "text":
                        first_part = self._process_text_parts(
                            [part], config, is_multiline, first_part
                        )
                    elif part["type"] == "table":
                        table_cells, first_part = self._process_table_part(
                            part, config, is_multiline, first_part
                        )
                        added_cells += table_cells
                    elif part["type"] == "list":
                        first_part = self._process_list_part(
                            part, config, is_multiline, first_part
                        )

                return added_cells
            else:
                # 3단계: Plain text 처리
                self._process_plain_text_lines(normalized_text, config)
                return 0
        except Exception as e:
            self.log_to_main(f"    셀 내용 처리 실패: {e}")
            return 0

    def _parse_markup_elements(self, text: str) -> List[Dict]:
        """마크업 요소 파싱 (추출자 + 조립자 패턴)"""
        from dataclasses import dataclass
        from typing import List, Dict

        @dataclass
        class BlockElement:
            """블록 요소"""
            type: str
            start: int
            end: int
            content: str

        class BlockExtractor:
            """블록 추출자"""
            def __init__(self, block_parser):
                self.block_parser = block_parser

            def extract_all(self, text: str) -> List[BlockElement]:
                """table과 list 블록 모두 추출"""
                elements = []

                # Table 블록 추출
                for block in self.block_parser.extract_table_blocks(text):
                    elements.append(BlockElement("table", block.start_pos, block.end_pos, block.content))

                # List 블록 추출
                for block in self.block_parser.extract_list_blocks(text):
                    elements.append(BlockElement("list", block.start_pos, block.end_pos, block.content))

                # 위치순 정렬
                elements.sort(key=lambda x: x.start)
                return elements

        class WhitespaceChecker:
            """공백 체크 (줄바꿈 포함 여부)"""
            @staticmethod
            def should_preserve(text: str) -> bool:
                """줄바꿈 포함 또는 내용 있음 → 보존"""
                return bool(text) and (text.strip() or "\n" in text or "\r" in text)

        class ElementParser:
            """요소 파서"""
            def __init__(self, table_parser, list_parser):
                self.table_parser = table_parser
                self.list_parser = list_parser

            def parse(self, element: BlockElement) -> Dict:
                """요소 타입에 따라 파싱"""
                if element.type == "table":
                    return self._parse_table(element)
                elif element.type == "list":
                    return self._parse_list(element)
                return None

            def _parse_table(self, element: BlockElement) -> Dict:
                """테이블 파싱"""
                table_rows = self.table_parser(element.content)
                if table_rows:
                    return {
                        "type": "table",
                        "content": element.content,
                        "rows": table_rows,
                    }
                return None

            def _parse_list(self, element: BlockElement) -> Dict:
                """리스트 파싱"""
                list_items = self.list_parser(element.content)
                if list_items:
                    return {
                        "type": "list",
                        "content": element.content,
                        "items": list_items,
                        "heading_text": list_items[0].split(" ")[0] if list_items else "",
                    }
                return None

        class PartAssembler:
            """부분 조립자"""
            def __init__(self, text: str, whitespace_checker):
                self.text = text
                self.whitespace_checker = whitespace_checker
                self.parts = []
                self.current_pos = 0

            def assemble(self, elements: List[BlockElement], parser: ElementParser) -> List[Dict]:
                """텍스트와 HTML 요소 조립"""
                for elem in elements:
                    # 요소 이전 텍스트
                    self._add_before_text(elem.start)

                    # HTML 요소 파싱 및 추가
                    parsed = parser.parse(elem)
                    if parsed:
                        self.parts.append(parsed)

                    self.current_pos = elem.end

                # 마지막 텍스트
                self._add_after_text()

                return self.parts if self.parts else [{"type": "text", "content": ""}]

            def _add_before_text(self, position: int):
                """요소 이전 텍스트 추가"""
                if self.current_pos < position:
                    before_text = self.text[self.current_pos:position]
                    if self.whitespace_checker.should_preserve(before_text):
                        self.parts.append({"type": "text", "content": before_text})

            def _add_after_text(self):
                """마지막 텍스트 추가"""
                if self.current_pos < len(self.text):
                    after_text = self.text[self.current_pos:]
                    if self.whitespace_checker.should_preserve(after_text):
                        self.parts.append({"type": "text", "content": after_text})

        # 메인 로직: 파이프라인 실행
        # 1단계: 빈 텍스트 처리
        if not text:
            return [{"type": "text", "content": text}]

        # 2단계: 블록 추출
        extractor = BlockExtractor(self._block_parser)
        elements = extractor.extract_all(text)

        # 3단계: HTML 요소 없음 처리
        if not elements:
            return [{"type": "text", "content": text}]

        # 4단계: 파싱 및 조립
        whitespace_checker = WhitespaceChecker()
        parser = ElementParser(self._parse_table_markup, self._parse_list_markup)
        assembler = PartAssembler(text, whitespace_checker)

        return assembler.assemble(elements, parser)

    def _parse_table_markup(self, table_html: str) -> List[str]:
        """
        테이블 HTML 파싱 (Strategy + Template Method + Builder 패턴)

        Args:
            table_html: <table>...</table> 형태의 HTML

        Returns:
            List[str]: 각 행을 '|'로 구분한 문자열 리스트
        """
        from typing import List as ListType
        from abc import ABC, abstractmethod

        class CellCleanerStrategy(ABC):
            """셀 정리 전략 (추상)"""
            @abstractmethod
            def clean(self, text: str) -> str:
                pass

        class BrTagReplacer(CellCleanerStrategy):
            """<br> 태그 변환"""
            PATTERN = re.compile(r"<br\s*/?>", re.IGNORECASE)

            def clean(self, text: str) -> str:
                return self.PATTERN.sub("\n", text)

        class HtmlTagRemover(CellCleanerStrategy):
            """HTML 태그 제거"""
            PATTERN = re.compile(r"<[^>]+>")

            def clean(self, text: str) -> str:
                return self.PATTERN.sub("", text)

        class NumericPrefixRemover(CellCleanerStrategy):
            """숫자: 패턴 제거"""
            PATTERN = re.compile(r"^\d+:\s*")

            def clean(self, text: str) -> str:
                return self.PATTERN.sub("", text)

        class CellTextProcessor:
            """셀 텍스트 처리기 (Template Method)"""
            def __init__(self):
                self.cleaners = [
                    BrTagReplacer(),
                    HtmlTagRemover(),
                    NumericPrefixRemover(),
                ]

            def process(self, cell_text: str) -> str:
                text = cell_text.strip()
                for cleaner in self.cleaners:
                    text = cleaner.clean(text)
                return text

        class CellExtractionStrategy(ABC):
            """셀 추출 전략 (추상)"""
            @abstractmethod
            def extract(self, tr_content: str, parser) -> ListType[str]:
                pass

        class StandardCellExtraction(CellExtractionStrategy):
            """표준 셀 추출 (<td>, <th> 태그)"""
            def __init__(self, processor: CellTextProcessor):
                self.processor = processor

            def extract(self, tr_content: str, parser) -> ListType[str]:
                cell_texts = parser.extract_table_cells(tr_content)
                return [self.processor.process(cell) for cell in cell_texts]

        class PipeSeparatedExtraction(CellExtractionStrategy):
            """파이프 구분 셀 추출 (Fallback)"""
            HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

            def extract(self, tr_content: str, parser) -> ListType[str]:
                clean_content = self.HTML_TAG_PATTERN.sub("", tr_content).strip()
                if "|" in clean_content:
                    return [cell.strip() for cell in clean_content.split("|") if cell.strip()]
                return []

        class RowBuilder:
            """행 빌더"""
            def __init__(self, processor: CellTextProcessor):
                self.processor = processor
                self.strategies = [
                    StandardCellExtraction(processor),
                    PipeSeparatedExtraction(),
                ]

            def build(self, tr_content: str, parser) -> str:
                for strategy in self.strategies:
                    cells = strategy.extract(tr_content, parser)
                    if cells:
                        return "|".join(cells)
                return None

        # Main execution: Pipeline
        processor = CellTextProcessor()
        builder = RowBuilder(processor)
        row_contents = self._block_parser.extract_table_rows(table_html)

        rows = [
            row
            for tr_content in row_contents
            if (row := builder.build(tr_content, self._block_parser)) is not None
        ]

        self.log_to_main(f"[DEBUG] 테이블 HTML 파싱 결과: {rows}")
        return rows

    def _parse_list_markup(self, list_html: str) -> List[str]:
        """
        리스트 HTML 파싱 (Strategy + Template Method + Builder 패턴)

        Args:
            list_html: <list -> ... 형태의 HTML

        Returns:
            List[str]: 리스트 항목들의 리스트
        """
        from dataclasses import dataclass
        from typing import Optional as Opt
        from abc import ABC, abstractmethod

        @dataclass
        class MarkerContext:
            """마커 컨텍스트"""
            raw_text: str
            stripped: str
            numeric_suffix: Opt[int] = None

        class MarkerProcessorStrategy(ABC):
            """마커 처리 전략 (추상)"""
            @abstractmethod
            def can_process(self, ctx: MarkerContext) -> bool:
                pass

            @abstractmethod
            def process(self, ctx: MarkerContext) -> str:
                pass

        class NumericMarkerProcessor(MarkerProcessorStrategy):
            """숫자 마커 처리기"""
            SUFFIX_CHARS = [')', ':', '.']

            def can_process(self, ctx: MarkerContext) -> bool:
                return ctx.numeric_suffix is not None

            def process(self, ctx: MarkerContext) -> str:
                suffix = next((ch for ch in self.SUFFIX_CHARS if ch in ctx.stripped), "")
                return f"{ctx.numeric_suffix}{suffix}"

        class RawMarkerProcessor(MarkerProcessorStrategy):
            """원본 마커 처리기 (A., B), C:, I., iv) 등)"""
            def can_process(self, ctx: MarkerContext) -> bool:
                return True  # Fallback

            def process(self, ctx: MarkerContext) -> str:
                return ctx.stripped if ctx.stripped else ""

        class TextCleaner:
            """중첩 태그 제거기"""
            NESTED_PREFIXES = ('<li', '<list')

            @staticmethod
            def clean(text: str) -> str:
                text = text.strip()
                while any(text.startswith(prefix) for prefix in TextCleaner.NESTED_PREFIXES):
                    close_pos = text.find('>')
                    if close_pos == -1:
                        break
                    text = text[close_pos + 1:].strip()
                return text

        class ListItemBuilder:
            """리스트 항목 빌더"""
            @staticmethod
            def build(marker: str, text: str) -> Opt[str]:
                if marker and text:
                    return f"{marker} {text}"
                elif marker:
                    return marker
                elif text:
                    return text
                return None

        class ListItemProcessor:
            """리스트 항목 처리기 (Template Method)"""
            def __init__(self, block_parser):
                self.block_parser = block_parser
                self.processors = [NumericMarkerProcessor(), RawMarkerProcessor()]

            def process_marker(self, marker_text: Opt[str]) -> str:
                if not marker_text:
                    return ""

                ctx = MarkerContext(
                    raw_text=marker_text,
                    stripped=marker_text.strip(),
                    numeric_suffix=self.block_parser.extract_numeric_list_tag(marker_text)
                )

                for processor in self.processors:
                    if processor.can_process(ctx):
                        return processor.process(ctx)

                return ""

            def process_item(self, marker_text: Opt[str], content_text: Opt[str]) -> Opt[str]:
                marker = self.process_marker(marker_text)
                text = TextCleaner.clean(content_text) if content_text else ""
                return ListItemBuilder.build(marker, text)

        # 1단계: 태그 정규화 (공백 삽입)
        try:
            list_html = re.sub(
                r"<(li|list)(?!\s)((?:[IVXLCDMivxlcdm]{1,6}|[A-Za-z]|[가-힣]|[+-]?[0-9]{1,3})(?:[\.:\)])?)>",
                r"<\1 \2>",
                list_html,
            )
        except Exception:
            pass

        # 2단계: 항목 추출
        list_item_tuples = self._block_parser.extract_list_items(list_html)

        # 3단계: 항목 처리 (Pipeline)
        processor = ListItemProcessor(self._block_parser)
        items = [
            item
            for marker, content in list_item_tuples
            if (item := processor.process_item(marker, content)) is not None
        ]

        self.log_to_main(f"[DEBUG] 리스트 HTML 파싱 결과: {items}")
        return items

    def prepare_edit_operation(
        self,
        operation: str,
        block_id: str,
        **kwargs,
    ) -> bool:
        """편집 작업 통합 진입점 (Strategy + Factory + Chain of Responsibility)"""
        from dataclasses import dataclass, field
        from typing import List, Dict, Any, Callable, Optional
        from abc import ABC, abstractmethod
        import re

        # ==================== 데이터 클래스 ====================
        @dataclass
        class OperationContext:
            """Operation 실행 컨텍스트"""
            operation: str
            block_id: str
            kwargs: Dict[str, Any]
            log_index: int
            position: Optional[tuple]
            params: Dict[str, Any]

        @dataclass
        class OperationConfig:
            """Operation 분류 설정"""
            continuous_insert_ops: List[str] = field(default_factory=lambda: [
                "append_paragraph", "append_list", "append_table_row",
                "create_table", "replace_cell_content"
            ])
            append_ops: List[str] = field(default_factory=lambda: [
                "append_paragraph", "append_list"
            ])
            newline_unallowed_ops: List[str] = field(default_factory=lambda: [
                "find_and_replace_in_paragraph", "find_and_replace_all"
            ])
            newline_handled_ops: List[str] = field(default_factory=lambda: [
                "replace_footnote", "replace_table_row", "append_table_row",
                "create_table", "replace_cell_content"
            ])
            html_processable_ops: List[str] = field(default_factory=lambda: [
                "replace_paragraph", "append_paragraph", "replace_list",
                "append_list", "replace_cell_content", "replace_table_row",
                "append_table_row"
            ])
            table_init_ops: List[str] = field(default_factory=lambda: [
                "replace_paragraph", "append_paragraph", "replace_list",
                "append_list", "replace_cell_content", "replace_table_row",
                "append_table_row"
            ])

        # ==================== 전처리기 (Chain of Responsibility) ====================
        class PreprocessorChain(ABC):
            """전처리 체인 (추상)"""
            def __init__(self, modifier):
                self.modifier = modifier
                self.next_processor: Optional[PreprocessorChain] = None

            def set_next(self, processor: 'PreprocessorChain') -> 'PreprocessorChain':
                """다음 프로세서 설정"""
                self.next_processor = processor
                return processor

            def process(self, ctx: OperationContext, config: OperationConfig) -> bool:
                """처리 (실패 시 False 반환)"""
                if not self._process_impl(ctx, config):
                    return False
                if self.next_processor:
                    return self.next_processor.process(ctx, config)
                return True

            @abstractmethod
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                """구현체"""
                pass

        class ContextInitializer(PreprocessorChain):
            """컨텍스트 초기화"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                if self.modifier.saved_caret_pos is None:
                    self.modifier.saved_caret_pos = ctx.position
                return True

        class TextNormalizer(PreprocessorChain):
            """텍스트 정규화"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                for key in ["new_text", "text", "footnote_text"]:
                    if key in ctx.kwargs and isinstance(ctx.kwargs[key], str):
                        ctx.kwargs[key] = self.modifier._normalize_newlines(ctx.kwargs[key])
                        self.modifier.log_to_main(f"[PRE-PROCESS] {key} 표준화 완료", "DEBUG")

                if "row_texts" in ctx.kwargs and isinstance(ctx.kwargs["row_texts"], list):
                    ctx.kwargs["row_texts"] = [
                        self.modifier._normalize_newlines(text) if isinstance(text, str) else text
                        for text in ctx.kwargs["row_texts"]
                    ]
                    self.modifier.log_to_main("[PRE-PROCESS] row_texts 표준화 완료", "DEBUG")
                return True

        class TableInitializer(PreprocessorChain):
            """표 초기화"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                if ctx.operation in config.table_init_ops:
                    try:
                        self.modifier._initialize_table_configuration(ctx.block_id, ctx.position)
                    except Exception:
                        self.modifier.log_to_main(
                            f"[WARN] 표 그룹 초기화 실패 (block_id={ctx.block_id})", "WARNING"
                        )
                return True

        class ContinuousInsertManager(PreprocessorChain):
            """연속 삽입 관리"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                if ctx.operation not in config.continuous_insert_ops:
                    self.modifier.last_insert_info = None
                return True

        class TextExtractor(PreprocessorChain):
            """텍스트 추출 및 정규화"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                if "new_text" not in ctx.kwargs and "text" in ctx.kwargs:
                    ctx.kwargs["new_text"] = text
                return True

        class NewlineValidator(PreprocessorChain):
            """줄바꿈 검증"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                if ctx.operation in config.newline_unallowed_ops:
                    new_text = ctx.kwargs.get("new_text", "")
                    if "\n" in new_text:
                        self.modifier.log_to_main(
                            f"[ERROR] {ctx.operation}: new_text에 줄바꿈(\\n)이 포함되어 있음"
                        )
                        self.modifier._record_operation_log(
                            ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, False
                        )
                        return False
                return True

        class BlockGuard(PreprocessorChain):
            """동일 블록 재-replace 방지"""
            def _process_impl(self, ctx: OperationContext, config: OperationConfig) -> bool:
                try:
                    block_key = str(ctx.block_id) if ctx.block_id is not None else None
                except Exception:
                    block_key = None

                if (
                    block_key
                    and hasattr(self.modifier, "_edited_blocks")
                    and (block_key in self.modifier._edited_blocks)
                ):
                    replace_to_append = {
                        "replace_paragraph": "append_paragraph",
                        "replace_list": "append_list",
                        "replace_table_row": "append_table_row",
                    }
                    if ctx.operation in replace_to_append:
                        new_op = replace_to_append[ctx.operation]
                        self.modifier.log_to_main(
                            f"[GUARD] 동일 블록 재-replace 방지: {ctx.operation} → {new_op} (block_id={ctx.block_id})"
                        )
                        ctx.operation = new_op
                return True

        # ==================== Operation 핸들러 (Strategy) ====================
        class OperationHandler(ABC):
            """Operation 핸들러 (추상)"""
            def __init__(self, modifier):
                self.modifier = modifier

            @abstractmethod
            def can_handle(self, ctx: OperationContext, config: OperationConfig) -> bool:
                """처리 가능 여부"""
                pass

            @abstractmethod
            def execute(self, ctx: OperationContext, config: OperationConfig, method: Callable) -> bool:
                """실행"""
                pass

        class HtmlHandler(OperationHandler):
            """HTML 요소 처리"""
            def can_handle(self, ctx: OperationContext, config: OperationConfig) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                return self.modifier._markup_analyzer.contains_structural_markup(text)

            def execute(self, ctx: OperationContext, config: OperationConfig, method: Callable) -> bool:
                if ctx.operation not in config.html_processable_ops:
                    self.modifier.log_to_main(f"[ERROR] {ctx.operation}: HTML 요소가 포함되어 있음")
                    self.modifier._record_operation_log(
                        ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, False
                    )
                    return False

                if ctx.operation != "replace_cell_content":
                    text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                    self.modifier.log_to_main(f"[HTML] HTML 요소 감지됨: {ctx.operation}")
                    parts = self.modifier._parse_markup_elements(text)

                    if parts and any(p.get("type") in ("table", "list") for p in parts):
                        result = self.modifier._process_markup_fragments(
                            parts, ctx.operation, ctx.block_id, **ctx.kwargs
                        )
                        self.modifier._record_operation_log(
                            ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, result
                        )
                        self.modifier._register_segment_modification(ctx.block_id, ctx.operation, result)
                        return result
                return True  # fallback to next handler

        class AppendMultilineHandler(OperationHandler):
            """Append 작업 다중 줄 처리"""
            def can_handle(self, ctx: OperationContext, config: OperationConfig) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                return ctx.operation in config.append_ops and "\n" in text

            def execute(self, ctx: OperationContext, config: OperationConfig, method: Callable) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    line_kwargs = ctx.kwargs.copy()
                    line_kwargs["new_text"] = line if line else " "

                    success = method(ctx.block_id, **line_kwargs)
                    if not success:
                        self.modifier.log_to_main(
                            f"[ERROR] {ctx.operation} 줄바꿈 처리 중 실패 (줄 {i + 1})"
                        )
                        self.modifier._record_operation_log(
                            ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, False
                        )
                        return False
                self.modifier._record_operation_log(ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, True)
                self.modifier._register_segment_modification(ctx.block_id, ctx.operation, True)
                return True

        class GeneralMultilineHandler(OperationHandler):
            """일반 다중 줄 처리"""
            def can_handle(self, ctx: OperationContext, config: OperationConfig) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                return "\n" in text and ctx.operation not in config.newline_handled_ops

            def execute(self, ctx: OperationContext, config: OperationConfig, method: Callable) -> bool:
                text = ctx.kwargs.get("new_text", ctx.kwargs.get("text", ""))
                result = self.modifier._process_multi_line_operation(
                    ctx.operation, ctx.block_id, text, method, **ctx.kwargs
                )
                self.modifier._record_operation_log(
                    ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, result
                )
                self.modifier._register_segment_modification(ctx.block_id, ctx.operation, result)
                return result

        class DefaultHandler(OperationHandler):
            """기본 핸들러 (단일 줄)"""
            def can_handle(self, ctx: OperationContext, config: OperationConfig) -> bool:
                return True  # always

            def execute(self, ctx: OperationContext, config: OperationConfig, method: Callable) -> bool:
                result = self._execute_operation(ctx, method)
                self.modifier._record_operation_log(
                    ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, result
                )
                self.modifier._register_segment_modification(ctx.block_id, ctx.operation, result)
                return result

            def _execute_operation(self, ctx: OperationContext, method: Callable) -> bool:
                """Operation별 실행 로직"""
                if ctx.operation == "find_and_replace_all":
                    return bool(method(ctx.kwargs.get("old_text", ""), ctx.kwargs.get("new_text", "")))
                elif ctx.operation in ["append_table_row", "replace_table_row"]:
                    return self._execute_table_row(ctx, method)
                elif ctx.operation == "insert_footnote":
                    return bool(method(
                        ctx.block_id,
                        ctx.kwargs.get("footnote_anchor_text", ""),
                        ctx.kwargs.get("footnote_text", "")
                    ))
                elif ctx.operation == "create_table":
                    return bool(method(
                        ctx.block_id,
                        ctx.kwargs.get("row_texts", []),
                        header=ctx.kwargs.get("header"),
                        font_size=ctx.kwargs.get("font_size"),
                        font_family=ctx.kwargs.get("font_family"),
                        align=ctx.kwargs.get("align")
                    ))
                elif ctx.operation in ["delete_table", "delete_textbox", "delete_table_row"]:
                    return bool(method(ctx.block_id))
                elif ctx.operation == "apply_para_style":
                    return bool(method(
                        ctx.block_id,
                        font_size=ctx.kwargs.get("font_size"),
                        font_family=ctx.kwargs.get("font_family"),
                        align=ctx.kwargs.get("align"),
                        spacing=ctx.kwargs.get("spacing"),
                        indentation=ctx.kwargs.get("indentation")
                    ))
                else:
                    return bool(method(ctx.block_id, **ctx.kwargs))

            def _execute_table_row(self, ctx: OperationContext, method: Callable) -> bool:
                """테이블 행 처리"""
                row_texts = ctx.kwargs.get("row_texts", [])
                # LLM이 셀별 개별 요소 flat array로 보내는 경우 자동 변환:
                # ["셀1", "셀2", "셀3"] → ["셀1|셀2|셀3"] (1행 처리)
                # 파이프 구분자가 하나도 없는 경우에만 적용
                if row_texts and isinstance(row_texts, (list, tuple)) and len(row_texts) >= 2:
                    row_texts = ['|'.join(str(rt) for rt in row_texts)]
                processed_rows = list(row_texts)
                return bool(method(
                    ctx.block_id,
                    processed_rows,
                    font_size=ctx.kwargs.get("font_size"),
                    font_family=ctx.kwargs.get("font_family"),
                    align=ctx.kwargs.get("align")
                ))

        # ==================== 파이프라인 ====================
        class OperationPipeline:
            """Operation 실행 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier
                self.config = OperationConfig()
                self.preprocessors = self._build_preprocessor_chain()
                self.handlers = self._build_handlers()

            def _build_preprocessor_chain(self) -> PreprocessorChain:
                """전처리 체인 구축"""
                init = ContextInitializer(self.modifier)
                norm = TextNormalizer(self.modifier)
                table = TableInitializer(self.modifier)
                cont = ContinuousInsertManager(self.modifier)
                extr = TextExtractor(self.modifier)
                valid = NewlineValidator(self.modifier)
                guard = BlockGuard(self.modifier)

                init.set_next(norm).set_next(table).set_next(cont).set_next(extr).set_next(valid).set_next(guard)
                return init

            def _build_handlers(self) -> List[OperationHandler]:
                """핸들러 체인 구축"""
                return [
                    HtmlHandler(self.modifier),
                    AppendMultilineHandler(self.modifier),
                    GeneralMultilineHandler(self.modifier),
                    DefaultHandler(self.modifier)
                ]

            def execute(self, ctx: OperationContext) -> bool:
                """파이프라인 실행"""
                # 1. 전처리
                if not self.preprocessors.process(ctx, self.config):
                    return False

                # 2. 메서드 가져오기
                method = getattr(self.modifier, ctx.operation, None)
                if not method:
                    self.modifier.log_to_main(f"[ERROR] 메서드를 찾을 수 없음: {ctx.operation}")
                    self.modifier._record_operation_log(
                        ctx.log_index, ctx.operation, ctx.block_id, ctx.position, ctx.params, False
                    )
                    return False

                # 3. 핸들러 실행
                for handler in self.handlers:
                    if handler.can_handle(ctx, self.config):
                        result = handler.execute(ctx, self.config, method)
                        if isinstance(result, bool):
                            return result
                return False

        # ==================== 파이프라인 실행 ====================
        log_index = len(self.execution_logs) + 1
        position = self._retrieve_segment_position(block_id) if block_id else None
        params = kwargs.copy()

        ctx = OperationContext(
            operation=operation,
            block_id=block_id,
            kwargs=kwargs,
            log_index=log_index,
            position=position,
            params=params
        )

        pipeline = OperationPipeline(self)
        return pipeline.execute(ctx)

    # paragraph
    def _contains_style_parameters(self, **kwargs) -> bool:
        """스타일 관련 파라미터 존재 여부 검사

        검사 대상: font_size, font_family, align, spacing, indentation

        Returns:
            하나 이상의 스타일 파라미터가 유효값으로 존재하면 True
        """
        style_param_names = frozenset({
            "font_size", "font_family", "align", "spacing", "indentation"
        })

        present_values = (
            kwargs.get(param_name)
            for param_name in style_param_names
        )

        return sum(1 for v in present_values if v is not None) > 0

    def _normalize_for_semantic_compare(self, text: Optional[str]) -> str:
        """공백/줄바꿈/태그 차이를 무시한 비교용 텍스트 정규화."""
        normalized = self._normalize_newlines(text)
        normalized = re.sub(r"<br\s*/?>", "\n", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"<[^>]+>", " ", normalized)
        normalized = normalized.replace("\u00a0", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _should_bypass_replacement(
        self, old_text: str, new_text: str, context: str = "", **kwargs
    ) -> bool:
        """대체 작업 생략 여부 판정

        콘텐츠 변경 없고 스타일 파라미터도 없으면 불필요한 작업으로 판단

        Args:
            old_text: 현재 텍스트
            new_text: 목표 텍스트
            context: 디버그 컨텍스트
            **kwargs: 스타일 옵션

        Returns:
            True = 작업 생략, False = 작업 수행
        """
        old_raw = "" if old_text is None else str(old_text)
        new_raw = "" if new_text is None else str(new_text)
        content_unchanged = (old_raw == new_raw)
        semantic_unchanged = (
            self._normalize_for_semantic_compare(old_raw)
            == self._normalize_for_semantic_compare(new_raw)
        )
        style_absent = not self._contains_style_parameters(**kwargs)
        skip_conditions = [content_unchanged or semantic_unchanged, style_absent]

        should_skip = all(skip_conditions)

        if should_skip:
            reason = "콘텐츠/스타일 변경 없음" if content_unchanged else "의미 동일(공백/줄바꿈 차이)"
            self.log_to_main(f"[SKIP] {context}: {reason}")

        return should_skip

    def replace_paragraph_content(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """블록 ID 기반 문단 대체 (Builder + Guard + Strategy 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Dict, Any

        @dataclass
        class StyleParams:
            """스타일 파라미터"""
            font_size: Opt[Any] = None
            font_family: Opt[str] = None
            align: Opt[str] = None
            spacing: Opt[Any] = None
            indentation: Opt[Any] = None

            @staticmethod
            def from_kwargs(kwargs: Dict) -> 'StyleParams':
                return StyleParams(
                    font_size=kwargs.get("font_size"),
                    font_family=kwargs.get("font_family"),
                    align=kwargs.get("align"),
                    spacing=kwargs.get("spacing"),
                    indentation=kwargs.get("indentation"),
                )

            def to_dict(self) -> Dict:
                return {
                    "font_size": self.font_size,
                    "font_family": self.font_family,
                    "align": self.align,
                    "spacing": self.spacing,
                    "indentation": self.indentation,
                }

        class TextExtractor:
            """텍스트 추출 전략"""
            @staticmethod
            def extract_new_text(new_text: Opt[str], kwargs: Dict) -> str:
                return new_text if new_text is not None else kwargs.get("new_text", "")

            @staticmethod
            def extract_old_text(kwargs: Dict, registry) -> str:
                old_text = kwargs.get("old_text")
                if old_text is None and registry:
                    try:
                        block_id_str = str(kwargs.get("block_id", ""))
                        old_text = registry.get_text(block_id_str) or ""
                    except Exception:
                        old_text = ""
                return old_text if old_text is not None else ""

        class ReplacementBuilder:
            """대체 작업 빌더 (Template Method)"""
            def __init__(self, modifier, block_id: str, kwargs: Dict):
                self.modifier = modifier
                self.block_id = block_id
                self.kwargs = kwargs
                self.new_text = TextExtractor.extract_new_text(kwargs.get("new_text"), kwargs)
                self.old_text = None
                self.style_params = StyleParams.from_kwargs(kwargs)

            def prepare(self) -> bool:
                """준비 (텍스트 추출 + 스킵 검사)"""
                self.kwargs["block_id"] = self.block_id
                self.old_text = TextExtractor.extract_old_text(self.kwargs, self.modifier.segment_registry)

                # 스킵 가드
                if self.modifier._should_bypass_replacement(
                    self.old_text, self.new_text,
                    f"replace_paragraph({self.block_id})",
                    **self.style_params.to_dict()
                ):
                    return False  # Skip

                return True  # Proceed

            def execute(self) -> bool:
                """실행"""
                return self.modifier.replace_content_elements(
                    self.block_id,
                    self.new_text,
                    block_type="paragraph",
                    **self.style_params.to_dict()
                )

        # Main execution: 빌더 패턴으로 대체 작업 구성 및 실행
        builder = ReplacementBuilder(self, block_id, {**kwargs, "new_text": new_text})

        if not builder.prepare():
            return True  # Bypassed (스킵됨, 성공으로 간주)

        # AI 가 font_size/font_family 명시 안 한 경우 기존 서식 백업 → 복원 (기존 서식 무조건 유지).
        # AI 가 명시한 경우엔 사용자 의도 우선 — 백업 skip.
        preserve_shape = kwargs.get("font_size") is None and kwargs.get("font_family") is None
        saved_shape = self._backup_charshape_at_block(block_id) if preserve_shape else None

        result = builder.execute()

        if saved_shape and result:
            self._restore_charshape_at_block(block_id, saved_shape)

        return result

    # 백업 대상 CharShape 속성 — 글자 모양 모든 속성 (자간, 장평, 베이스라인, 폰트, 색상, 윤곽 등).
    _CHARSHAPE_PROPS = (
        "Height",                # 글자 크기
        "Bold", "Italic", "UnderlineType", "UnderlineShape", "UnderlineColor",
        "StrikeOutType", "StrikeOutShape", "StrikeOutColor",
        "TextColor", "ShadeColor", "OutLineType", "ShadowType", "ShadowColor",
        "ShadowOffsetX", "ShadowOffsetY",
        "Spacing",               # 자간 (글자 간격)
        "Ratio",                 # 장평 (가로 비율)
        "RelSize",               # 상대 크기
        "OffsetY",               # 베이스라인
        "FaceNameUser", "FaceNameSymbol", "FaceNameOther",
        "FaceNameJapanese", "FaceNameHanja", "FaceNameLatin", "FaceNameHangul",
        "EmphasizeType", "BorderFillId",
    )
    # 백업 대상 ParaShape 속성 — 문단 모양 (줄간격, 정렬, 들여쓰기, 여백).
    _PARASHAPE_PROPS = (
        "LineSpacing", "LineSpacingType",
        "LeftMargin", "RightMargin", "Indentation",
        "PrevSpacing", "NextSpacing",
        "AlignType",
        "BreakLatinWord", "BreakNonLatinWord", "SnapToGrid",
        "Condense", "FontLineHeight", "FontLineHeightType",
        "TextDir", "VertAlign",
    )

    # inline 변동 detection 시 비교 대상 — 가장 흔히 인지되는 속성만
    _CRITICAL_CHAR_KEYS = ("Height", "FaceNameHangul", "FaceNameLatin", "Bold", "Italic", "TextColor")

    def _capture_charshape_props(self) -> Dict[str, Any]:
        """현재 cursor 위치의 CharShape 모든 속성 캡처."""
        cs = self.hwp.HParameterSet.HCharShape
        self.hwp.HAction.GetDefault("CharShape", cs.HSet)
        out: Dict[str, Any] = {}
        for prop in self._CHARSHAPE_PROPS:
            try:
                out[prop] = getattr(cs, prop)
            except Exception:
                pass
        return out

    def _backup_charshape_at_block(self, block_id: str) -> Optional[Dict[str, Any]]:
        """block_id 의 paragraph 의 현재 CharShape + ParaShape 모두 백업.

        AI 가 서식 인자 명시 안 한 replace 호출 시 기존 서식 전체 보존을 위해 사용.
        실패 시 None — 호출자가 복원 skip.

        paragraph 시작과 끝의 CharShape 비교 → 다르면 inline 변동 표시 — 복원 시
        통일 적용 skip 으로 inline 손실 방지.
        """
        try:
            position = self.segment_registry.get_adjusted_position(block_id) if self.segment_registry else None
            if not position:
                return None

            # paragraph 시작 위치 의 CharShape
            self.hwp.set_pos(*position)
            char_start = self._capture_charshape_props()

            # paragraph 끝 위치 의 CharShape — MoveSelParaEnd 후 select 해제 + 끝 위치 의 charshape
            try:
                self.hwp.HAction.Run("MoveSelParaEnd")
                end_pos = self.hwp.GetPos()
                try:
                    self.hwp.HAction.Run("Cancel")
                except Exception:
                    pass
                self.hwp.SetPos(*end_pos)
                char_end = self._capture_charshape_props()
            except Exception:
                char_end = dict(char_start)

            # inline 변동 detection
            inline_varied = False
            for key in self._CRITICAL_CHAR_KEYS:
                if char_start.get(key) != char_end.get(key):
                    inline_varied = True
                    break

            # ParaShape — paragraph 단위라 시작 위치 만으로 OK
            self.hwp.set_pos(*position)
            ps = self.hwp.HParameterSet.HParaShape
            self.hwp.HAction.GetDefault("ParagraphShape", ps.HSet)
            para_props: Dict[str, Any] = {}
            for prop in self._PARASHAPE_PROPS:
                try:
                    para_props[prop] = getattr(ps, prop)
                except Exception:
                    pass

            return {
                "char": char_start,
                "char_end": char_end,
                "para": para_props,
                "inline_varied": inline_varied,
            }
        except Exception:
            return None

    def _restore_charshape_at_block(self, block_id: str, saved: Dict[str, Any]) -> None:
        """저장된 CharShape + ParaShape 으로 paragraph 복원.  fire-and-forget — 실패해도 silent.

        inline 변동 감지 시 (paragraph 안에 다른 글자 크기/폰트 혼재):
          - CharShape 통일 적용 skip — paragraph 안 inline 변동 손실 방지
          - ParaShape 만 복원 (paragraph 단위 속성: 줄간격/정렬/들여쓰기 — 항상 안전)
          - 새 text 의 inline 은 HWP COM default (insertion point charshape) 으로 처리
        """
        try:
            position = self.segment_registry.get_adjusted_position(block_id) if self.segment_registry else None
            if not position:
                return

            inline_varied = bool(saved.get("inline_varied"))

            # CharShape 복원 — inline 변동 없을 때만 (paragraph 통일 charshape 일 때)
            if not inline_varied:
                # paragraph 전체 select
                self.hwp.set_pos(*position)
                try:
                    self.hwp.HAction.Run("MoveSelParaEnd")
                except Exception:
                    try:
                        self.hwp.MoveSelParaEnd()
                    except Exception:
                        pass

                char_props = saved.get("char") or {}
                if char_props:
                    cs = self.hwp.HParameterSet.HCharShape
                    self.hwp.HAction.GetDefault("CharShape", cs.HSet)
                    for key, value in char_props.items():
                        try:
                            setattr(cs, key, value)
                        except Exception:
                            pass
                    try:
                        self.hwp.HAction.Execute("CharShape", cs.HSet)
                    except Exception:
                        pass

                try:
                    self.hwp.Cancel()
                except Exception:
                    pass
            else:
                try:
                    self.log_to_main(
                        f"[INFO] paragraph {block_id}: inline charshape 변동 감지 → CharShape 통일 skip (inline 손실 방지)"
                    )
                except Exception:
                    pass

            # ParaShape 복원 — paragraph 단위 속성이라 inline 변동과 무관, 항상 안전
            para_props = saved.get("para") or {}
            if para_props:
                self.hwp.set_pos(*position)
                ps = self.hwp.HParameterSet.HParaShape
                self.hwp.HAction.GetDefault("ParagraphShape", ps.HSet)
                for key, value in para_props.items():
                    try:
                        setattr(ps, key, value)
                    except Exception:
                        pass
                try:
                    self.hwp.HAction.Execute("ParagraphShape", ps.HSet)
                except Exception:
                    pass

            try:
                self.hwp.Cancel()
            except Exception:
                pass
        except Exception:
            pass

    def _append_paragraph_impl(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """append_paragraph 내부 구현.

        Args:
            block_id: 기준 세그먼트 ID
            new_text: 삽입할 텍스트
            **kwargs: 스타일 파라미터

        Returns:
            삽입 성공 여부
        """
        content_value = new_text if new_text is not None else kwargs.get("new_text", "")
        style_keys = ("font_size", "font_family", "align", "spacing", "indentation")
        style_opts = {key: kwargs.get(key) for key in style_keys}

        # AI 가 서식 명시 안 한 경우 기존 paragraph 서식 백업 → append 후 복원.
        preserve_shape = style_opts.get("font_size") is None and style_opts.get("font_family") is None
        saved_shape = self._backup_charshape_at_block(block_id) if preserve_shape else None

        result = self.append_content_elements(
            block_id, content_value, block_type="paragraph", **style_opts
        )

        if saved_shape and result:
            self._restore_charshape_at_block(block_id, saved_shape)

        return result

    def append_paragraph_content(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """하위 호환 alias. 표준 API는 append_paragraph."""
        return self._append_paragraph_impl(block_id, new_text, **kwargs)

    def append_to_paragraph(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """하위 호환 alias. 표준 API는 append_paragraph."""
        return self._append_paragraph_impl(block_id, new_text, **kwargs)

    def remove_paragraph(self, block_id: str) -> bool:
        """블록 ID 기반 문단을 삭제 (문단 자체를 제거)"""
        return self.remove_content_elements(block_id, block_type="paragraph")

    # list

    def replace_list_content(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """블록 ID 기반 리스트 대체 (Builder + Guard + Strategy 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Dict, Any

        @dataclass
        class StyleConfig:
            """스타일 구성"""
            font_size: Opt[Any] = None
            font_family: Opt[str] = None
            align: Opt[str] = None
            spacing: Opt[Any] = None
            indentation: Opt[Any] = None

            @staticmethod
            def from_kwargs(kwargs: Dict) -> 'StyleConfig':
                return StyleConfig(
                    font_size=kwargs.get("font_size"),
                    font_family=kwargs.get("font_family"),
                    align=kwargs.get("align"),
                    spacing=kwargs.get("spacing"),
                    indentation=kwargs.get("indentation"),
                )

            def to_dict(self) -> Dict:
                return {
                    "font_size": self.font_size,
                    "font_family": self.font_family,
                    "align": self.align,
                    "spacing": self.spacing,
                    "indentation": self.indentation,
                }

        class ContentExtractor:
            """콘텐츠 추출 전략"""
            @staticmethod
            def extract_new_content(new_text: Opt[str], kwargs: Dict) -> str:
                return new_text if new_text is not None else kwargs.get("new_text", "")

            @staticmethod
            def extract_old_content(kwargs: Dict, registry) -> str:
                old_text = kwargs.get("old_text")
                if old_text is None and registry:
                    try:
                        block_id_str = str(kwargs.get("block_id", ""))
                        old_text = registry.get_text(block_id_str) or ""
                    except Exception:
                        old_text = ""
                return old_text if old_text is not None else ""

        class ListReplacementBuilder:
            """리스트 대체 작업 빌더 (Template Method)"""
            BLOCK_TYPE = "list"

            def __init__(self, modifier, block_id: str, kwargs: Dict):
                self.modifier = modifier
                self.block_id = block_id
                self.kwargs = kwargs
                self.new_content = ContentExtractor.extract_new_content(kwargs.get("new_text"), kwargs)
                self.old_content = None
                self.style_config = StyleConfig.from_kwargs(kwargs)

            def prepare(self) -> bool:
                """준비 (콘텐츠 추출 + 스킵 검사)"""
                self.kwargs["block_id"] = self.block_id
                self.old_content = ContentExtractor.extract_old_content(self.kwargs, self.modifier.segment_registry)

                if self.modifier._should_bypass_replacement(
                    self.old_content, self.new_content,
                    f"replace_list({self.block_id})",
                    **self.style_config.to_dict()
                ):
                    return False  # Skip
                return True  # Proceed

            def execute(self) -> bool:
                """실행"""
                return self.modifier.replace_content_elements(
                    self.block_id,
                    self.new_content,
                    block_type=self.BLOCK_TYPE,
                    **self.style_config.to_dict()
                )

        builder = ListReplacementBuilder(self, block_id, {**kwargs, "new_text": new_text})
        if not builder.prepare():
            return True  # Bypassed

        # AI 가 font_size/font_family 명시 안 한 경우 기존 서식 백업 → 복원.
        preserve_shape = kwargs.get("font_size") is None and kwargs.get("font_family") is None
        saved_shape = self._backup_charshape_at_block(block_id) if preserve_shape else None

        result = builder.execute()

        if saved_shape and result:
            self._restore_charshape_at_block(block_id, saved_shape)

        return result

    def append_to_list(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """지정 세그먼트 뒤에 새 리스트 항목 추가

        Args:
            block_id: 기준 세그먼트 ID
            new_text: 삽입할 텍스트
            **kwargs: 스타일 파라미터

        Returns:
            삽입 성공 여부
        """
        content_value = new_text if new_text is not None else kwargs.get("new_text", "")
        formatting_attrs = ["font_size", "font_family", "align", "spacing", "indentation"]
        format_dict = {attr: kwargs.get(attr) for attr in formatting_attrs}

        # 서식 명시 안 된 경우 기존 list item 서식 백업/복원.
        preserve_shape = format_dict.get("font_size") is None and format_dict.get("font_family") is None
        saved_shape = self._backup_charshape_at_block(block_id) if preserve_shape else None

        result = self.append_content_elements(
            block_id, content_value, block_type="list", **format_dict
        )

        if saved_shape and result:
            self._restore_charshape_at_block(block_id, saved_shape)

        return result

    def remove_list(self, block_id: str) -> bool:
        """블록 ID 기반 리스트를 삭제"""
        return self.remove_content_elements(block_id, block_type="list")

    def replace_content_elements(
        self,
        block_id: str,
        new_text: str,
        block_type: str = "paragraph",
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        align: Optional[str] = None,
        spacing: Optional[float] = None,
        indentation: Optional[float] = None,
    ) -> bool:
        """인라인 요소 대체 (Strategy + Template Method + Command + Pipeline 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Tuple
        from abc import ABC, abstractmethod

        # ==================== 데이터 클래스 ====================
        @dataclass
        class LeadingSpaceAlignment:
            """선행 공백 정렬 정보"""
            actual_new_text: str
            adjust_offset: int
            old_leading_count: int
            new_leading_count: int

            @property
            def is_space_reduced(self) -> bool:
                """공백 감소 여부"""
                return self.old_leading_count > self.new_leading_count

        @dataclass
        class ReplaceContext:
            """대체 컨텍스트"""
            block_id: str
            block_type: str
            old_text: str
            new_text: str
            position: Tuple[int, int, int]
            font_size: Opt[float]
            font_family: Opt[str]
            align: Opt[str]
            spacing: Opt[float]
            indentation: Opt[float]

            @property
            def block_name(self) -> str:
                """블록 이름"""
                return "리스트" if self.block_type == "list" else "문단"

        # ==================== 텍스트 삽입 헬퍼 (Template Method) ====================
        class TextInserter:
            """텍스트 삽입 헬퍼 (중복 제거)"""
            def __init__(self, hwp, insert_method):
                self.hwp = hwp
                self.insert_method = insert_method

            def insert_with_leading_space_handling(
                self,
                text: str,
                alignment: LeadingSpaceAlignment,
                position: Tuple[int, int, int]
            ):
                """선행 공백 처리와 함께 텍스트 삽입"""
                list_pos, para_pos, char_pos = position

                if alignment.is_space_reduced:
                    # 공백 감소: 전체 문단 교체
                    self.hwp.set_pos(list_pos, para_pos, 0)
                    self.hwp.MoveSelParaEnd()
                    self.insert_method(text if text else " ")
                else:
                    # 공백 유지/증가: 부분 교체
                    self.insert_method(alignment.actual_new_text if alignment.actual_new_text else " ")

        # ==================== 대체 전략 (Strategy + Template Method) ====================
        class ReplaceStrategy(ABC):
            """대체 전략 (추상)"""
            def __init__(self, modifier, logger):
                self.modifier = modifier
                self.logger = logger

            @abstractmethod
            def execute(self, ctx: ReplaceContext) -> bool:
                """대체 실행 (하위 클래스 구현)"""
                pass

            def _log_success(self, ctx: ReplaceContext):
                """성공 로깅 (공통)"""
                self.logger(
                    f"[OK] 블록 {ctx.block_id}의 {ctx.block_name} 대체 완료: "
                    f"'{ctx.old_text[:30]}...' → '{(ctx.new_text or '')[:30]}...'"
                )

        class EmptyTextReplacer(ReplaceStrategy):
            """빈 텍스트 대체"""
            def execute(self, ctx: ReplaceContext) -> bool:
                """빈 텍스트에 새 내용 삽입"""
                list_pos, para_pos, char_pos = ctx.position
                self.modifier.hwp.set_pos(list_pos, para_pos, char_pos)
                self.modifier._insert_styled_content(ctx.new_text if ctx.new_text else " ")
                self.logger(f"[OK] 블록 {ctx.block_id}의 빈 {ctx.block_name}에 새 내용 삽입 완료")
                return True

        class NonEmptyTextReplacer(ReplaceStrategy):
            """비어있지 않은 텍스트 대체"""
            def execute(self, ctx: ReplaceContext) -> bool:
                """find_replace 기반 대체 (with fallback)"""
                # 1. 선행 공백 정렬
                alignment = self._align_leading_spaces(ctx)

                # 2. 커서 이동 + 검색
                found = self._search_text(ctx, alignment)

                # 3. TextInserter 생성
                inserter = TextInserter(self.modifier.hwp, self.modifier._insert_styled_content)

                if found:
                    # 3a. 검색 성공 → 삽입
                    inserter.insert_with_leading_space_handling(ctx.new_text, alignment, ctx.position)
                    self._log_success(ctx)
                else:
                    # 3b. 검색 실패 → Fallback
                    if not self._fallback_replace(ctx, alignment, inserter):
                        return False

                # 4. 연속 삽입 기준점 저장
                self._save_insert_info(ctx)
                return True

            def _align_leading_spaces(self, ctx: ReplaceContext) -> LeadingSpaceAlignment:
                """선행 공백 정렬"""
                actual_new_text, adjust_n, old_leading, new_leading = (
                    self.modifier._align_leading_spaces(ctx.old_text, ctx.new_text)
                )
                return LeadingSpaceAlignment(
                    actual_new_text=actual_new_text,
                    adjust_offset=adjust_n,
                    old_leading_count=old_leading,
                    new_leading_count=new_leading
                )

            def _search_text(self, ctx: ReplaceContext, alignment: LeadingSpaceAlignment) -> bool:
                """텍스트 검색 (문단 경계 검증 포함)"""
                list_pos, para_pos, char_pos = ctx.position
                try:
                    self.modifier.hwp.set_pos(list_pos, para_pos, char_pos)
                    for _ in range(alignment.adjust_offset):
                        self.modifier.hwp.MoveNextChar()
                except Exception:
                    pass
                found = self.modifier.hwp.find(
                    ctx.old_text[alignment.adjust_offset:],
                    direction="Forward",
                    regex=False
                )
                if not found:
                    return False
                # find()는 전체 문서를 순방향 검색하므로, 매칭 결과가
                # 대상 문단 밖에 있을 수 있음. 위치를 검증하여 다른 문단의
                # 텍스트를 교체하는 사고를 방지한다.
                try:
                    cur = self.modifier.hwp.get_pos()
                    if cur[0] != list_pos or cur[1] != para_pos:
                        # 다른 문단에서 매칭됨 → 대상 문단으로 복귀 후
                        # 전체 선택 방식으로 전환
                        self.modifier.hwp.set_pos(list_pos, para_pos, 0)
                        self.modifier.hwp.MoveSelParaEnd()
                        return False
                except Exception:
                    pass
                return True

            def _fallback_replace(
                self,
                ctx: ReplaceContext,
                alignment: LeadingSpaceAlignment,
                inserter: TextInserter
            ) -> bool:
                """Fallback 대체"""
                self.logger("[WARNING] find_replace 실패")

                list_pos, para_pos, char_pos = ctx.position
                self.modifier.hwp.set_pos(list_pos, para_pos, char_pos)
                for _ in range(alignment.adjust_offset):
                    self.modifier.hwp.MoveNextChar()

                start_pos = self.modifier.hwp.get_pos()
                self.modifier.hwp.MoveParaEnd()
                end_pos = self.modifier.hwp.get_pos()

                # 길이 검증
                if (end_pos[2] - start_pos[2]) != (len(alignment.actual_new_text) + 8):
                    self.modifier.hwp.set_pos(list_pos, para_pos, char_pos)
                    self.modifier.hwp.MoveSelParaEnd()
                    inserter.insert_with_leading_space_handling(ctx.new_text, alignment, ctx.position)
                    self._log_success(ctx)
                    return True
                else:
                    self.logger("[WARNING] replace 실패")
                    return False

            def _save_insert_info(self, ctx: ReplaceContext):
                """연속 삽입 정보 저장"""
                list_pos, para_pos, _ = ctx.position
                self.modifier.last_insert_info = {
                    "block_id": ctx.block_id,
                    "list_pos": list_pos,
                    "next_para_pos": para_pos,
                }

        # ==================== 대체 명령 (Command) ====================
        class ReplaceCommand:
            """대체 명령"""
            def __init__(self, modifier, logger):
                self.modifier = modifier
                self.logger = logger

            def execute(self, ctx: ReplaceContext) -> bool:
                """명령 실행 (전략 선택 + 실행)"""
                # 전략 선택
                if ctx.old_text == "":
                    strategy = EmptyTextReplacer(self.modifier, self.logger)
                else:
                    strategy = NonEmptyTextReplacer(self.modifier, self.logger)

                # 전략 실행
                return strategy.execute(ctx)

        # ==================== 처리 파이프라인 ====================
        class ReplaceElementsPipeline:
            """대체 처리 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier

            def execute(self, ctx: ReplaceContext) -> bool:
                """파이프라인 실행"""
                # 1. 위치 이동
                list_pos, para_pos, char_pos = ctx.position
                self.modifier.hwp.set_pos(list_pos, para_pos, char_pos)

                # 2. 대체 명령 실행
                command = ReplaceCommand(self.modifier, self.modifier.log_to_main)
                if not command.execute(ctx):
                    return False

                # 3. 포맷팅 적용
                if any(p is not None for p in [ctx.font_size, ctx.font_family, ctx.align, ctx.spacing, ctx.indentation]):
                    self.modifier._apply_formatting_to_current_paragraph(
                        ctx.font_size, ctx.font_family, ctx.align, ctx.spacing, ctx.indentation
                    )

                # 4. 후처리 정리
                text_for_processing = ctx.new_text if ctx.new_text is not None else ""
                context_type = "cell" if self.modifier.hwp.is_cell() else "paragraph"
                is_multiline_text = "\n" in text_for_processing

                self.modifier._perform_post_paragraph_cleanup(
                    text_for_processing,
                    context_type,
                    is_multiline=is_multiline_text,
                    fit_mode="none",
                    old_text=ctx.old_text,
                    user_align=ctx.align,
                    user_indentation=ctx.indentation,
                )

                # 5. 표 검증 스케줄링
                self.modifier._schedule_table_boundary_validation(ctx.block_id)
                return True

        # ==================== 메인 로직 ====================
        try:
            # 1. ID 검증 및 해결
            block_id_key = str(block_id) if block_id is not None else None
            resolved_id = self._resolve_scoped_segment_identifier(block_id_key, "replace_block_elements")
            if resolved_id is None:
                return False

            # 2. 타입 검증
            allowed_types = {"text"} if block_type == "paragraph" else {"list"} if block_type == "list" else None
            if not self._validate_segment_type(resolved_id, allowed_types, "replace_block_elements"):
                return False
            block_id = resolved_id

            # 3. 위치 조회
            position = self._retrieve_segment_position(block_id)
            if not position:
                return False

            # 4. 텍스트 조회
            old_text = self.segment_registry.get_text(block_id)
            if old_text is None:
                self.log_to_main(f"[ERROR] 블록 ID {block_id}에 대한 텍스트를 찾을 수 없습니다.")
                return False

            # 5. Context 생성
            ctx = ReplaceContext(
                block_id=block_id,
                block_type=block_type,
                old_text=old_text,
                new_text=new_text,
                position=position,
                font_size=font_size,
                font_family=font_family,
                align=align,
                spacing=spacing,
                indentation=indentation
            )

            # 6. Pipeline 실행
            pipeline = ReplaceElementsPipeline(self)
            return pipeline.execute(ctx)

        except Exception as e:
            block_name = "리스트" if block_type == "list" else "문단"
            self.log_to_main(f"[ERROR] 블록 {block_id} {block_name} 대체 실패: {e}")
            return False

    def append_content_elements(
        self,
        block_id: str,
        new_text: str,
        block_type: str = "paragraph",
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        align: Optional[str] = None,
        spacing: Optional[float] = None,
        indentation: Optional[float] = None,
    ) -> bool:
        """인라인 요소 추가 (State + Strategy + Guard + Template Method + Pipeline 패턴)"""
        from dataclasses import dataclass
        from typing import Optional as Opt, Tuple
        from abc import ABC, abstractmethod

        # ==================== 데이터 클래스 ====================
        @dataclass
        class PositionInfo:
            """위치 정보"""
            list_pos: int
            para_pos: int
            char_pos: int
            adjusted_block_id: str

        @dataclass
        class AppendContext:
            """Append 컨텍스트"""
            block_id: str
            block_type: str
            new_text: str
            font_size: Opt[float]
            font_family: Opt[str]
            align: Opt[str]
            spacing: Opt[float]
            indentation: Opt[float]

            @property
            def block_name(self) -> str:
                """블록 이름"""
                return "리스트" if self.block_type == "list" else "문단"

        # ==================== TD 셀 Guard ====================
        class TDCellGuard:
            """TD 셀 타겟 보정 Guard"""
            def __init__(self, segment_registry, logger):
                self.segment_registry = segment_registry
                self.logger = logger

            def adjust_target_if_td_cell(self, block_id: str) -> str:
                """TD 셀인 경우 마지막 콘텐츠 블록으로 보정"""
                try:
                    if not self.segment_registry:
                        return block_id

                    block = self.segment_registry.get_block(str(block_id))
                    if not block or getattr(block, "block_type", "") != "td":
                        return block_id

                    # TD 셀: 마지막 콘텐츠 블록 조회
                    last_content_id = self.segment_registry.get_last_content_block_id_in_same_cell(str(block_id))

                    if last_content_id and str(last_content_id) != str(block_id):
                        self.logger(f"[GUARD] td 셀 보정: {block_id} → 마지막 문단 블록 {last_content_id}")
                        return str(last_content_id)
                    else:
                        self.logger("[GUARD] td 셀 보정: 콘텐츠 없음 → td 첫 위치에서 처리")
                        return block_id

                except Exception as err:
                    self.logger(f"[WARN] td 셀 보정 실패: {err}", "WARNING")
                    return block_id

        # ==================== 위치 해결 전략 (Strategy) ====================
        class PositionResolutionStrategy(ABC):
            """위치 해결 전략 (추상)"""
            @abstractmethod
            def resolve(self, ctx: AppendContext, modifier) -> Opt[PositionInfo]:
                """위치 해결 (하위 클래스 구현)"""
                pass

        class CachedPositionStrategy(PositionResolutionStrategy):
            """캐시된 위치 사용 (연속 삽입)"""
            def resolve(self, ctx: AppendContext, modifier) -> Opt[PositionInfo]:
                """캐시에서 위치 가져오기"""
                info = modifier.last_insert_info
                modifier.log_to_main(
                    f"[연속 삽입] 블록 {ctx.block_id} - 마지막 삽입 다음 위치 (P:{info['next_para_pos']}) 사용"
                )
                return PositionInfo(
                    list_pos=info["list_pos"],
                    para_pos=info["next_para_pos"],
                    char_pos=0,
                    adjusted_block_id=ctx.block_id
                )

        class ResolvedPositionStrategy(PositionResolutionStrategy):
            """위치 해결 (새 삽입, TD Guard 포함)"""
            def resolve(self, ctx: AppendContext, modifier) -> Opt[PositionInfo]:
                """위치 해결 + TD 셀 보정"""
                # 1. TD 셀 Guard 적용
                guard = TDCellGuard(modifier.segment_registry, modifier.log_to_main)
                adjusted_id = guard.adjust_target_if_td_cell(ctx.block_id)

                # 2. 위치 조회
                position = modifier._retrieve_segment_position(adjusted_id)
                if not position:
                    return None

                list_pos, para_pos, char_pos = position
                modifier.log_to_main(
                    f"[새 삽입] 블록 {adjusted_id} - 원본 위치 (P:{para_pos}) 사용"
                )

                # 3. 연속 삽입 초기화
                modifier.last_insert_info = None

                return PositionInfo(
                    list_pos=list_pos,
                    para_pos=para_pos,
                    char_pos=char_pos,
                    adjusted_block_id=adjusted_id
                )

        # ==================== 삽입 상태 (State Pattern) ====================
        class InsertionState:
            """삽입 상태 판별기"""
            @staticmethod
            def select_strategy(ctx: AppendContext, modifier) -> PositionResolutionStrategy:
                """삽입 상태에 따라 전략 선택"""
                if modifier.last_insert_info and modifier.last_insert_info["block_id"] == ctx.block_id:
                    # 연속 삽입
                    return CachedPositionStrategy()
                else:
                    # 새 삽입
                    return ResolvedPositionStrategy()

        # ==================== Append 명령 (Template Method) ====================
        class AppendCommand:
            """Append 명령 (Template Method 패턴)"""
            def __init__(self, modifier, position: PositionInfo):
                self.modifier = modifier
                self.position = position

            def execute(self, ctx: AppendContext) -> bool:
                """Template Method: 삽입 흐름"""
                # 1. 커서 이동
                self.modifier.hwp.set_pos(
                    self.position.list_pos,
                    self.position.para_pos,
                    self.position.char_pos
                )

                # 2. 현재 타입 확인
                current_type = self.modifier._inspect_current_type()

                # 3. 새 블록 생성
                self._create_new_paragraph()

                # 4. Heading text 조정 (list → paragraph)
                if current_type == "list" and ctx.block_type == "paragraph":
                    self.modifier.hwp.DeleteBack()

                # 5. 텍스트 삽입
                self.modifier._insert_styled_content(ctx.new_text)

                # 6. 연속 삽입 정보 저장
                self._save_insert_info(ctx)

                # 7. 성공 로깅
                self.modifier.log_to_main(
                    f"[OK] 블록 {ctx.block_id} 뒤에 새 {ctx.block_name} 삽입 완료 "
                    f"(P:{self.position.para_pos + 1})"
                )

                # 8. BlockManager 업데이트
                self._update_block_manager(ctx)

                return True

            def _create_new_paragraph(self):
                """새 문단 생성"""
                self.modifier.hwp.MoveParaEnd()
                self.modifier.hwp.BreakPara()

            def _save_insert_info(self, ctx: AppendContext):
                """연속 삽입 정보 저장"""
                self.modifier.last_insert_info = {
                    "block_id": ctx.block_id,
                    "list_pos": self.position.list_pos,
                    "next_para_pos": self.position.para_pos + 1,
                }

            def _update_block_manager(self, ctx: AppendContext):
                """BlockManager 업데이트"""
                if self.modifier.segment_registry:
                    self.modifier.segment_registry.update_after_para_pos_append(
                        self.position.adjusted_block_id, 1
                    )
                    self.modifier.log_to_main(
                        f"[BlockManager] 새 {ctx.block_name} 삽입 후 para_pos 업데이트 완료"
                    )

        # ==================== Append 파이프라인 ====================
        class AppendElementsPipeline:
            """Append 처리 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier

            def execute(self, ctx: AppendContext, position: PositionInfo) -> bool:
                """파이프라인 실행"""
                try:
                    # 1. Append 명령 실행
                    command = AppendCommand(self.modifier, position)
                    if not command.execute(ctx):
                        return False

                    # 2. 포맷팅 적용
                    if any(p is not None for p in [ctx.font_size, ctx.font_family, ctx.align, ctx.spacing, ctx.indentation]):
                        self.modifier._apply_formatting_to_current_paragraph(
                            ctx.font_size, ctx.font_family, ctx.align, ctx.spacing, ctx.indentation
                        )

                    # 3. 후처리 정리
                    text_for_processing = ctx.new_text if ctx.new_text is not None else ""
                    context_type = "cell" if self.modifier.hwp.is_cell() else "paragraph"
                    is_multiline_text = "\n" in text_for_processing

                    self.modifier._perform_post_paragraph_cleanup(
                        text_for_processing,
                        context_type,
                        is_multiline=is_multiline_text,
                        fit_mode="none",
                        user_align=ctx.align,
                        user_indentation=ctx.indentation,
                    )

                    # 4. 표 검증 스케줄링
                    self.modifier._schedule_table_boundary_validation(ctx.block_id)

                    return True

                except Exception as err:
                    self.modifier.log_to_main(
                        f"[ERROR] 블록 {ctx.block_id} 뒤에 새 {ctx.block_name} 삽입 실패: {err}"
                    )
                    self.modifier.last_insert_info = None
                    return False

        # ==================== 메인 로직 ====================
        # 1. ID 검증 및 해결
        block_id_key = str(block_id) if block_id is not None else None
        resolved_id = self._resolve_scoped_segment_identifier(block_id_key, "append_block_elements")
        if resolved_id is None:
            return False

        # 2. 타입 검증
        allowed_types = {"text"} if block_type == "paragraph" else {"list"} if block_type == "list" else None
        if not self._validate_segment_type(resolved_id, allowed_types, "append_block_elements"):
            return False
        block_id = resolved_id

        # 3. Context 생성
        ctx = AppendContext(
            block_id=block_id,
            block_type=block_type,
            new_text=new_text,
            font_size=font_size,
            font_family=font_family,
            align=align,
            spacing=spacing,
            indentation=indentation
        )

        # 4. 위치 해결 전략 선택 및 실행
        state = InsertionState()
        strategy = state.select_strategy(ctx, self)
        position = strategy.resolve(ctx, self)
        if not position:
            return False

        # 5. Pipeline 실행
        pipeline = AppendElementsPipeline(self)
        return pipeline.execute(ctx, position)

    def remove_content_elements(
        self, block_id: str, block_type: str = "paragraph"
    ) -> bool:
        """문단 또는 리스트 블록 완전 제거

        Args:
            block_id: 대상 세그먼트 ID
            block_type: "paragraph" 또는 "list"

        Returns:
            제거 성공 여부
        """
        normalized_key = str(block_id) if block_id is not None else None
        validated_id = self._resolve_scoped_segment_identifier(normalized_key, "delete_block_elements")

        if validated_id is None:
            return False

        type_constraints = {"paragraph": {"text"}, "list": {"list"}}
        constraint_set = type_constraints.get(block_type)

        if not self._validate_segment_type(validated_id, constraint_set, "delete_block_elements"):
            return False

        coords = self._retrieve_segment_position(validated_id)
        if coords is None:
            return False

        return self._execute_content_element_removal(validated_id, coords, block_type)

    def _execute_content_element_removal(
        self, seg_id: str, coords: Tuple[int, int, int], elem_type: str
    ) -> bool:
        """콘텐츠 요소 제거 실행.

        이전 동작: MoveSelParaEnd + DeleteBack 2회.
          1차 DeleteBack: select 부분 (paragraph 본문) 삭제
          2차 DeleteBack: paragraph break 삭제 (paragraph 자체 제거)
        문제: paragraph 가 짧거나 빈 경우 2차 DeleteBack 이 이전 paragraph 의 끝 글자 삭제.
          사용자 신고 케이스 일치 ("범위가 아닌 다른 곳을 지워버림").

        새 동작:
          1. cursor 를 paragraph 시작에 setpos
          2. MoveSelParaEnd 로 paragraph 본문 select
          3. select 끝을 다음 paragraph 시작 까지 확장 (MoveSelectNextParaBegin / MoveSelectRight)
          4. Delete (1회) — select 영역 정확히 삭제 = paragraph 자체 제거
          5. 이전 paragraph 영역 절대 침범 안 함
        """
        type_labels = {"list": "리스트", "paragraph": "문단"}
        label = type_labels.get(elem_type, "블록")

        try:
            self.hwp.set_pos(coords[0], coords[1], coords[2])
            self.hwp.MoveSelParaEnd()

            # select 끝을 paragraph break 너머 (다음 paragraph 시작) 까지 확장.
            # 우선 MoveSelectNextParaBegin (paragraph block 단위 안전) → 실패 시 MoveSelectRight (1 char).
            extended = False
            for action_name in ("MoveSelectNextParaBegin", "MoveSelectRight", "MoveSelDown"):
                try:
                    self.hwp.HAction.Run(action_name)
                    extended = True
                    break
                except Exception:
                    continue

            if extended:
                # select 영역 한 번에 삭제 — 이전 paragraph 안 건드림
                try:
                    self.hwp.HAction.Run("Delete")
                except Exception:
                    try:
                        self.hwp.Delete()
                    except Exception:
                        # 마지막 fallback — 기존 동작 (단 회귀 위험 존재)
                        self.hwp.DeleteBack()
                        self.hwp.DeleteBack()
            else:
                # select 확장 실패 — 기존 동작 fallback (이전 paragraph 끝 글자 손실 위험)
                self.log_to_main(
                    f"[WARN] {label} 제거: select 확장 실패 ({seg_id}) — fallback DeleteBack 2회"
                )
                self.hwp.DeleteBack()
                self.hwp.DeleteBack()

            # 리스트 타입은 추가 삭제 필요 (list marker 까지)
            extra_deletion_types = {"list"}
            if elem_type in extra_deletion_types:
                try:
                    self.hwp.DeleteBack()
                except Exception:
                    pass

            self.log_to_main(f"[OK] {label} 제거 완료: {seg_id}")
            return True

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[ERROR] {label} 제거 실패 ({seg_id}): {exc}")
            return False

    def search_and_replace_in_paragraph(
        self, block_id: str, old_text: str = None, new_text: str = None, **kwargs
    ) -> bool:
        """블록 ID 기반 문단 내 텍스트 찾아 대체 (Strategy + Template Method + Guard + Command 패턴)"""
        from dataclasses import dataclass
        from typing import Tuple as TupleType, Optional
        from abc import ABC, abstractmethod

        # Dataclass for parameters
        @dataclass
        class ReplacementParams:
            """대체 파라미터"""
            old_text: str
            new_text: str
            block_id: str

            @classmethod
            def from_args(cls, old: str, new: str, block_id: str, kwargs: dict):
                """인자에서 파라미터 추출"""
                old = old or kwargs.get("old_text", "")
                new = new or kwargs.get("new_text", "")
                return cls(old, new, block_id)

            def get_replacement(self) -> str:
                """대체 텍스트 (None → 공백)"""
                return self.new_text if self.new_text else " "

        # Dataclass for block context
        @dataclass
        class BlockContext:
            """블록 컨텍스트"""
            block_id: str
            block_type: Optional[str]
            list_pos: int
            para_pos: int

        # Guard Pattern for early exits
        class ReplacementGuard:
            """대체 가드 (Early Exit 검증)"""
            @staticmethod
            def check_empty_old_text(params: ReplacementParams, logger) -> bool:
                """old_text 없음 → Skip (True = 통과)"""
                if not params.old_text:
                    logger(f"[SKIP] 블록 {params.block_id} find_and_replace_in_paragraph: old_text 없음")
                    return True
                return False

            @staticmethod
            def check_identical_texts(params: ReplacementParams, logger) -> bool:
                """동일 텍스트 → Skip (True = 통과)"""
                if params.old_text == params.new_text:
                    logger(f"[SKIP] 블록 {params.block_id} find_and_replace_in_paragraph: 동일 텍스트")
                    return True
                return False

            @staticmethod
            def check_text_not_found(params: ReplacementParams, ctx: BlockContext, registry, logger) -> bool:
                """텍스트 없음 → Skip (True = 통과)"""
                if not registry:
                    return False
                try:
                    if ctx.block_type == "td":
                        current = registry.get_cell_text(ctx.list_pos) or ""
                    else:
                        current = registry.get_text(ctx.block_id) or ""
                    if current and params.old_text not in current:
                        logger(f"[SKIP] 블록 {ctx.block_id} find_and_replace_in_paragraph: 대상 텍스트 없음")
                        return True
                except Exception:
                    pass
                return False

        # Strategy Pattern for text replacement
        class TextReplacerStrategy(ABC):
            """텍스트 대체 전략 (추상, Template Method)"""
            def __init__(self, hwp, modifier, logger):
                self.hwp = hwp
                self.modifier = modifier
                self.logger = logger

            def replace(self, params: ReplacementParams, ctx: BlockContext) -> bool:
                """Template Method: (성공여부)"""
                if not self._validate_preconditions(ctx):
                    return False
                self._execute_replacement(params, ctx)
                return True

            @abstractmethod
            def _validate_preconditions(self, ctx: BlockContext) -> bool:
                """사전 조건 검증"""
                pass

            @abstractmethod
            def _execute_replacement(self, params: ReplacementParams, ctx: BlockContext):
                """대체 실행"""
                pass

        # Concrete strategies
        class CellReplacer(TextReplacerStrategy):
            """셀 대체 전략 (safety-bounded loop)"""
            def _validate_preconditions(self, ctx: BlockContext) -> bool:
                """셀 내부인지 확인"""
                if not self.hwp.is_cell():
                    self.logger(f"[ERROR] 셀 내부가 아닌 위치에서 find_and_replace_in_paragraph 수행 ({ctx.block_id})")
                    return False
                return True

            def _execute_replacement(self, params: ReplacementParams, ctx: BlockContext):
                """셀 내부에서 대체 (list_pos 경계 확인)"""
                start_list_pos = self.hwp.get_pos()[0]
                safety = 0
                while safety < 400:
                    safety += 1
                    found = self.hwp.find(params.old_text, direction="Forward", regex=False)
                    if not found:
                        break
                    if not self.hwp.is_cell():
                        break
                    cur_pos = self.hwp.get_pos()
                    if not cur_pos or cur_pos[0] != start_list_pos:
                        break
                    self.modifier._insert_styled_content(params.get_replacement())

        class ParagraphReplacer(TextReplacerStrategy):
            """문단 대체 전략 (paragraph boundary)"""
            def _validate_preconditions(self, ctx: BlockContext) -> bool:
                """항상 True (문단은 검증 불필요)"""
                return True

            def _execute_replacement(self, params: ReplacementParams, ctx: BlockContext):
                """문단 내부에서 대체 (para_pos 경계 확인)"""
                start_pos = self.hwp.get_pos()
                while True:
                    found = self.hwp.find(params.old_text, direction="Forward", regex=False)
                    if not found:
                        break
                    if self.hwp.get_pos()[1] != start_pos[1]:
                        break
                    self.modifier._insert_styled_content(params.get_replacement())

        # Factory Pattern for strategy selection
        class ReplacerFactory:
            """대체 전략 팩토리"""
            @staticmethod
            def create(block_type: str, hwp, modifier, logger) -> TextReplacerStrategy:
                """블록 타입에 맞는 전략 생성"""
                if block_type == "td":
                    return CellReplacer(hwp, modifier, logger)
                return ParagraphReplacer(hwp, modifier, logger)

        # Command Pattern for registry update
        class RegistryUpdateCommand:
            """Registry 업데이트 커맨드"""
            def __init__(self, registry, logger):
                self.registry = registry
                self.logger = logger

            def execute(self, params: ReplacementParams, ctx: BlockContext):
                """텍스트 업데이트 실행"""
                if not self.registry:
                    return

                replacement = params.get_replacement()

                # 1. 현재 텍스트 가져오기
                current = self._get_current_text(ctx)

                # 2. 텍스트 대체
                updated = self._replace_text(current, params.old_text, replacement)

                # 3. Registry 업데이트
                self._update_registry(ctx, updated)

            def _get_current_text(self, ctx: BlockContext) -> str:
                """현재 텍스트 가져오기 (TD vs 일반)"""
                try:
                    if ctx.block_type == "td":
                        return self.registry.get_cell_text(ctx.list_pos) or ""
                    return self.registry.get_text(ctx.block_id) or ""
                except Exception:
                    return ""

            def _replace_text(self, current: str, old: str, replacement: str) -> str:
                """텍스트 대체 로직"""
                if old:
                    updated = current.replace(old, replacement)
                else:
                    updated = current
                if updated == "" and replacement:
                    updated = replacement
                return updated

            def _update_registry(self, ctx: BlockContext, updated: str):
                """Registry 업데이트 (TD는 추가 업데이트)"""
                self.registry.update_text(ctx.block_id, updated)
                if ctx.block_type == "td":
                    self.registry.update_cell_text_blocks(ctx.list_pos, updated)

        # === Main execution pipeline ===
        try:
            # 1. Block ID resolution
            block_id_key = str(block_id) if block_id is not None else None
            resolved_id = self._resolve_scoped_segment_identifier(block_id_key, "find_and_replace_in_paragraph")
            if resolved_id is None:
                return False
            if not self._validate_segment_type(resolved_id, {"text"}, "find_and_replace_in_paragraph"):
                return False
            block_id = resolved_id

            # 2. Parameter extraction
            params = ReplacementParams.from_args(old_text, new_text, block_id, kwargs)

            # 3. Guard checks (early exits)
            if ReplacementGuard.check_empty_old_text(params, self.log_to_main):
                return True
            if ReplacementGuard.check_identical_texts(params, self.log_to_main):
                return True

            # 4. Block context extraction
            block_type = None
            if self.segment_registry:
                try:
                    block = self.segment_registry.get_block(block_id)
                    if block:
                        block_type = getattr(block, "block_type", None)
                except Exception:
                    pass

            position = self._retrieve_segment_position(block_id)
            if not position:
                return False

            list_pos, para_pos, _ = position
            ctx = BlockContext(block_id, block_type, list_pos, para_pos)

            # 5. Pre-check: text existence
            if ReplacementGuard.check_text_not_found(params, ctx, self.segment_registry, self.log_to_main):
                return True

            # 6. Position cursor
            self.hwp.set_pos(list_pos, para_pos, 0)

            # 7. Execute replacement (Strategy Pattern)
            replacer = ReplacerFactory.create(block_type, self.hwp, self, self.log_to_main)
            if not replacer.replace(params, ctx):
                return False

            # 8. Reposition cursor
            self.hwp.set_pos(list_pos, para_pos, 0)

            self.log_to_main(f"[OK] 블록 {block_id}에서 '{params.old_text}'를 '{params.new_text}'로 대체 완료")

            # 9. Update registry (Command Pattern)
            updater = RegistryUpdateCommand(self.segment_registry, self.log_to_main)
            updater.execute(params, ctx)

            return True

        except Exception as e:
            self.log_to_main(f"[ERROR] 블록 {block_id}에서 '{old_text}'를 '{new_text}'로 대체 실패: {e}")
            return False

    def search_and_replace_globally(self, old_text: str, new_text: str) -> bool:
        """문서 전체에서 문자열 검색 후 일괄 대체

        Args:
            old_text: 검색 대상 문자열
            new_text: 대체 문자열

        Returns:
            처리 성공 여부
        """
        replacement_value = new_text if new_text else " "
        match_count = 0
        search_opts = {"direction": "Forward", "regex": False}

        try:
            self.hwp.MoveDocBegin()

            # 검색-대체 반복 (최대 안전 한도 설정)
            iteration_limit = 10000
            for _ in range(iteration_limit):
                match_found = self.hwp.find(old_text, **search_opts)
                if not match_found:
                    break
                self._insert_styled_content(replacement_value)
                match_count += 1

            self.log_to_main(
                f"[OK] 전역 대체 완료: '{old_text}' → '{new_text}' ({match_count}건)"
            )
            return True

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[ERROR] 전역 대체 실패: {exc}")
            return False

    ## td 관련 로직
    def replace_cell_content(
        self, block_id: str, new_text: str = None, **kwargs
    ) -> bool:
        """지정된 셀 내부 텍스트 교체 (빌더 + 전략 + 커맨드 패턴)

        Args:
            block_id: 블록 ID
            new_text: 새 텍스트. 줄바꿈이 있으면 pre_edit_process에서 처리됨
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬
            spacing: 문단 간격
            indentation: 들여쓰기
        """
        # 서식 명시 안 됐으면 기존 셀 서식 백업 — 함수 끝에서 복원.
        _preserve = kwargs.get("font_size") is None and kwargs.get("font_family") is None
        _saved_shape = self._backup_charshape_at_block(block_id) if _preserve else None

        from dataclasses import dataclass
        from typing import Optional, Tuple
        from abc import ABC, abstractmethod
        import re

        # ===== 데이터 클래스 =====
        @dataclass
        class CellReplaceConfig:
            """셀 교체 설정"""
            block_id: str
            content: str
            list_pos: int
            para_pos: int
            char_pos: int
            font_size: Optional[int] = None
            font_family: Optional[str] = None
            align: Optional[str] = None
            spacing: Optional[int] = None
            indentation: Optional[int] = None
            fit_mode: str = "none"

        @dataclass
        class CellContext:
            """셀 컨텍스트 정보"""
            previous_text: str
            current_type: str

        @dataclass
        class ParagraphMetrics:
            """문단 메트릭 정보"""
            table_count: int
            newline_count: int
            added_cells: int

            @property
            def total_paragraphs(self) -> int:
                """총 추가된 문단 수"""
                return self.newline_count + (self.table_count * 3)

        # ===== 전략 패턴: 문단 계산 전략 =====
        class ParagraphCountStrategy(ABC):
            """문단 수 계산 추상 전략"""
            @abstractmethod
            def calculate(self, text: str) -> Tuple[int, int]:
                """테이블 수와 줄바꿈 수 계산"""
                pass

        class MarkupAwareParagraphCounter(ParagraphCountStrategy):
            """마크업 인식 문단 카운터"""
            TABLE_PATTERN = re.compile(r"<table(?![A-Za-z0-9:_-])", re.IGNORECASE)
            TABLE_REMOVAL = re.compile(r"<table[^>]*>[\s\S]*?(?:</table>\r?\n?|$)", re.IGNORECASE)
            NEWLINE_NORMALIZE = re.compile(r"(?:\r?\n){2,}")

            def calculate(self, text: str) -> Tuple[int, int]:
                """테이블과 줄바꿈 수 계산"""
                # 테이블 감지
                has_table = bool(self.TABLE_PATTERN.search(text))

                if has_table:
                    table_count = len(re.findall(r"<table\b", text, re.IGNORECASE))
                    # 테이블 본문 제거
                    text_without_tables = self.TABLE_REMOVAL.sub("", text)
                else:
                    table_count = 0
                    text_without_tables = text

                # 줄바꿈 카운트
                if text_without_tables:
                    normalized = self.NEWLINE_NORMALIZE.sub("\n", text_without_tables)
                    newline_count = normalized.count("\n")
                else:
                    newline_count = 0

                return table_count, newline_count

        # ===== 커맨드 패턴: 셀 변경 커맨드 =====
        class CellContentCommand:
            """셀 콘텐츠 변경 커맨드"""
            def __init__(self, hwp_api, type_inspector):
                self.hwp = hwp_api
                self.inspector = type_inspector

            def execute_replacement(self, config: CellReplaceConfig, content_processor) -> int:
                """셀 콘텐츠 교체 실행"""
                # 1. 커서 이동
                self.hwp.set_pos(config.list_pos, config.para_pos, config.char_pos)

                # 2. 셀 검증
                if not self.hwp.is_cell():
                    raise ValueError(f"셀 블록 내부가 아님 ({config.block_id})")

                # 3. 타입 확인
                current_type = self.inspector()

                # 4. 기존 콘텐츠 삭제
                self.hwp.SelectAll()
                if current_type == "list":
                    self.hwp.DeleteBack()
                    self.hwp.DeleteBack()

                # 5. 새 콘텐츠 삽입
                added_cells = content_processor(
                    config.content,
                    fit_mode=config.fit_mode,
                    font_size=config.font_size,
                    font_family=config.font_family,
                    align=config.align,
                    spacing=config.spacing,
                    indentation=config.indentation
                )

                return added_cells

        # ===== 빌더 패턴: 셀 교체 빌더 =====
        class CellReplacementBuilder:
            """셀 교체 작업 빌더"""
            def __init__(self, logger):
                self.logger = logger
                self.config = None
                self.context = None
                self.metrics = None

            def with_config(self, config: CellReplaceConfig):
                """설정 적용"""
                self.config = config
                return self

            def with_context(self, context: CellContext):
                """컨텍스트 적용"""
                self.context = context
                return self

            def validate_placeholder(self) -> bool:
                """Placeholder 검증"""
                if not self.config.content.strip():
                    normalized = "".join(self.context.previous_text.split())
                    if "내용기재" in normalized:
                        self.logger(f"[SKIP] replace_cell_content({self.config.block_id}): empty for placeholder")
                        return False
                return True

            def validate_bypass(self, bypass_checker) -> bool:
                """중복 교체 스킵 검증"""
                style_params = {
                    "font_size": self.config.font_size,
                    "font_family": self.config.font_family,
                    "align": self.config.align,
                    "spacing": self.config.spacing,
                    "indentation": self.config.indentation
                }
                return not bypass_checker(
                    self.context.previous_text,
                    self.config.content,
                    f"replace_cell_content({self.config.block_id})",
                    **style_params
                )

            def compute_metrics(self, added_cells: int, counter: ParagraphCountStrategy):
                """메트릭 계산"""
                table_count, newline_count = counter.calculate(self.config.content)
                self.metrics = ParagraphMetrics(
                    table_count=table_count,
                    newline_count=newline_count,
                    added_cells=added_cells
                )
                return self

            def update_registry(self, registry):
                """레지스트리 업데이트"""
                if not registry:
                    return self

                # 텍스트 업데이트
                registry.update_text(self.config.block_id, self.config.content)

                # 문단 수 업데이트
                if self.metrics.total_paragraphs > 0:
                    self.logger(f"[BlockManager] 셀 내부에 {self.metrics.total_paragraphs}개 문단 추가 반영")
                    registry.update_after_para_pos_append(
                        self.config.block_id,
                        self.metrics.total_paragraphs
                    )

                # 셀 추가 업데이트
                if self.metrics.added_cells > 0:
                    registry.update_after_list_pos_append(
                        self.config.block_id,
                        self.metrics.added_cells,
                        self.config.list_pos + 1
                    )
                    self.logger(f"[BlockManager] 셀 내부 표로 인한 list_pos {self.metrics.added_cells} 증가")

                return self

            def save_continuation_state(self, state_holder, position_getter):
                """연속 작업 상태 저장"""
                try:
                    current_pos = position_getter()
                    state_holder.last_insert_info = {
                        "block_id": self.config.block_id,
                        "list_pos": self.config.list_pos,
                        "next_para_pos": current_pos[1]
                    }
                except Exception as e:
                    self.logger(f"[ERROR] 셀 블록 {self.config.block_id} 연속 삽입 기준점 설정 실패: {e}")
                return self

            def finalize(self, boundary_validator):
                """최종 처리"""
                boundary_validator(self.config.block_id)
                self.logger(f"[CELL] prev_text_len={len(self.context.previous_text)} new_text_len={len(self.config.content)}")
                self.logger(f"[OK] 셀 블록 {self.config.block_id} 내용 교체 완료")
                return True

        # ===== 메인 로직 =====
        # 1. ID 검증
        normalized_id = str(block_id) if block_id is not None else None
        resolved_id = self._resolve_scoped_segment_identifier(normalized_id, "replace_cell_content")
        if not resolved_id or not self._validate_segment_type(resolved_id, {"td"}, "replace_cell_content"):
            return False

        # 2. 파라미터 추출
        content = new_text if new_text is not None else kwargs.get("new_text", "")
        actual_content = content if content is not None else " "

        position = self._retrieve_segment_position(resolved_id)
        if not position:
            return False

        # 3. 설정 구축
        config = CellReplaceConfig(
            block_id=resolved_id,
            content=actual_content,
            list_pos=position[0],
            para_pos=position[1],
            char_pos=position[2],
            font_size=kwargs.get("font_size"),
            font_family=kwargs.get("font_family"),
            align=kwargs.get("align"),
            spacing=kwargs.get("spacing"),
            indentation=kwargs.get("indentation"),
            fit_mode="none"
        )

        # 4. 컨텍스트 구축
        prev_text = ""
        try:
            if self.segment_registry:
                prev_text = self.segment_registry.get_cell_text(config.list_pos) or ""
        except Exception:
            pass

        context = CellContext(
            previous_text=prev_text,
            current_type=""
        )

        # 5. 빌더 생성 및 검증
        builder = CellReplacementBuilder(self.log_to_main)
        builder.with_config(config).with_context(context)

        if not builder.validate_placeholder():
            return True

        if not builder.validate_bypass(self._should_bypass_replacement):
            return True

        # 6. 교체 실행
        try:
            command = CellContentCommand(self.hwp, self._inspect_current_type)
            added_cells = command.execute_replacement(config, self._process_markup_for_row_operation)

            # 7. 메트릭 계산 및 업데이트
            counter = MarkupAwareParagraphCounter()
            _result = (builder
                .compute_metrics(added_cells, counter)
                .update_registry(self.segment_registry)
                .save_continuation_state(self, self.hwp.get_pos)
                .finalize(self._schedule_table_boundary_validation))
            if _saved_shape and _result:
                self._restore_charshape_at_block(block_id, _saved_shape)
            return _result

        except Exception as e:
            self.log_to_main(f"[ERROR] 셀 블록 {config.block_id} 내용 교체 실패: {e}")
            return False

    def clear_cell_content(self, block_id: str) -> bool:
        """테이블 셀 내부 콘텐츠 제거

        Args:
            block_id: 대상 셀 세그먼트 ID

        Returns:
            제거 성공 여부
        """
        normalized_key = str(block_id) if block_id is not None else None
        validated_id = self._resolve_scoped_segment_identifier(normalized_key, "delete_cell_content")

        validation_checks = [
            validated_id is not None,
            self._validate_segment_type(validated_id, {"td"}, "delete_cell_content") if validated_id else False,
        ]

        if not all(validation_checks):
            return False

        coords = self._retrieve_segment_position(validated_id)
        if coords is None:
            return False

        return self._execute_cell_content_clear(validated_id, coords)

    def _execute_cell_content_clear(self, seg_id: str, coords: Tuple[int, int, int]) -> bool:
        """셀 콘텐츠 삭제 실행"""
        try:
            self.hwp.set_pos(coords[0], coords[1], coords[2])

            if not self.hwp.is_cell():
                self.log_to_main(f"[ERROR] 셀 콘텐츠 삭제: 셀 컨텍스트 아님 ({seg_id})")
                return False

            self.hwp.SelectAll()
            self.hwp.DeleteBack()

            self.log_to_main(f"[OK] 셀 콘텐츠 삭제 완료 (좌표: {coords})")
            self._schedule_table_boundary_validation(seg_id)
            return True

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[ERROR] 셀 콘텐츠 삭제 실패: {exc}")
            return False

    def replace_table_row(self, block_id: str, row_texts=None, **kwargs) -> bool:
        """블록 ID 기반 표의 행 대체 (전략 + 커맨드 + 빌더 패턴)

        Args:
            block_id: 대상 셀이 있는 블록 ID
            row_texts: 문자열 리스트. 첫 항목은 대상 행을 대체, 추가 항목은 그 아래에 행 추가
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬

        Returns:
            bool: 성공 여부
        """
        from dataclasses import dataclass
        from typing import List, Tuple, Optional, Callable
        from abc import ABC, abstractmethod

        # ==================== 데이터 클래스 ====================
        @dataclass
        class RowReplaceConfig:
            """행 대체 설정"""
            block_id: str
            row_data: List[str]
            initial_position: Tuple[int, int, int]
            font_size: Optional[int] = None
            font_family: Optional[str] = None
            align: Optional[str] = None

        @dataclass
        class TableRowPosition:
            """표 행 위치 정보"""
            row_start_list: int
            col_count: int
            additional_col_idx: int = 0

            def increment_col_offset(self, delta: int):
                """열 오프셋 증가 (내포 표 삽입 시)"""
                self.additional_col_idx += delta

            def get_cell_list_pos(self, col_idx: int) -> int:
                """주어진 열 인덱스에 대한 list_pos 계산"""
                return self.row_start_list + col_idx + self.additional_col_idx

        @dataclass
        class CellMetrics:
            """셀 메트릭 정보"""
            col_idx: int
            list_pos: int
            old_text: str = ""
            new_text: str = ""

        # ==================== 커맨드 패턴 ====================
        class CellUpdateCommand:
            """셀 업데이트 커맨드 (작업 캡슐화)"""
            def __init__(self, hwp, segment_registry, logger):
                self.hwp = hwp
                self.registry = segment_registry
                self.logger = logger

            def replace_cell_content(
                self,
                metrics: CellMetrics,
                config: RowReplaceConfig,
                bypass_check: Callable,
                markup_processor: Callable
            ) -> int:
                """셀 내용 대체 실행

                Returns:
                    추가된 열 개수 (내포 표 삽입 시)
                """
                # 방어 로직: 내용이 같고 스타일 변경 없으면 스킵
                if bypass_check(
                    metrics.old_text,
                    metrics.new_text,
                    f"replace_table_row cell({config.block_id}, col={metrics.col_idx})",
                    font_size=config.font_size,
                    font_family=config.font_family,
                    align=config.align,
                ):
                    return 0

                # 셀 내용 선택 및 대체
                self.hwp.SelectAll()
                added_cols = markup_processor(
                    metrics.new_text,
                    font_size=config.font_size,
                    font_family=config.font_family,
                    align=config.align,
                    spacing=None,
                    indentation=None,
                )

                # 레지스트리 업데이트
                if self.registry:
                    td_id = self.registry.get_td_block_id_by_list_pos(metrics.list_pos)
                    if td_id:
                        self.registry.update_text(td_id, metrics.new_text or "")
                        self.registry.update_cell_text_blocks(metrics.list_pos, metrics.new_text or "")

                return added_cols

            def clear_cell_content(self, metrics: CellMetrics):
                """셀 내용 비우기 (빈 텍스트로 대체)"""
                self.hwp.SelectAll()
                self.hwp.InsertText(" ")

                if self.registry:
                    td_id = self.registry.get_td_block_id_by_list_pos(metrics.list_pos)
                    if td_id:
                        self.registry.update_text(td_id, "")
                        self.registry.update_cell_text_blocks(metrics.list_pos, "")

        # ==================== 전략 패턴 ====================
        class RowOperationStrategy(ABC):
            """행 작업 추상 전략"""
            @abstractmethod
            def execute(
                self,
                row_text: str,
                config: RowReplaceConfig,
                command: CellUpdateCommand,
                helpers: dict
            ) -> Optional[TableRowPosition]:
                """행 작업 실행

                Returns:
                    작업 후 행 위치 정보 (상태 저장용)
                """
                pass

        class ReplaceRowStrategy(RowOperationStrategy):
            """기존 행 대체 전략"""
            def __init__(self, hwp, segment_registry, logger):
                self.hwp = hwp
                self.registry = segment_registry
                self.logger = logger

            def execute(
                self,
                row_text: str,
                config: RowReplaceConfig,
                command: CellUpdateCommand,
                helpers: dict
            ) -> Optional[TableRowPosition]:
                """기존 행 셀 대체 실행"""
                self.logger("[TABLE ROW REPLACE] 행 대체")

                # 1. 현재 행 시작 위치로 이동
                list_pos, para_pos, char_pos = config.initial_position
                self.hwp.set_pos(list_pos, para_pos, char_pos)
                self.hwp.move_pos(104)  # 행의 처음
                row_start_list = self.hwp.get_pos()[0]

                # 2. 열 개수 계산
                self.hwp.move_pos(105)  # 행의 끝
                row_end_list = self.hwp.get_pos()[0]
                col_count = row_end_list - row_start_list + 1

                # 3. 셀 파싱
                cells = helpers["split_row"](row_text)

                # 4. 위치 객체 생성
                position = TableRowPosition(row_start_list, col_count)

                # 5. 각 셀 순회 및 대체
                for col_idx in range(col_count):
                    current_list = position.get_cell_list_pos(col_idx)
                    self.hwp.set_pos(current_list, 0, 0)

                    # 기존 텍스트 조회
                    old_text = ""
                    if self.registry:
                        old_text = self.registry.get_cell_text(current_list) or ""

                    if col_idx < len(cells):
                        # 셀 텍스트 대체
                        metrics = CellMetrics(col_idx, current_list, old_text, cells[col_idx])
                        added_cols = command.replace_cell_content(
                            metrics, config, helpers["bypass_check"], helpers["markup_processor"]
                        )
                        position.increment_col_offset(added_cols)
                    else:
                        # 텍스트 없는 셀: 빈 셀로 대체
                        metrics = CellMetrics(col_idx, current_list, old_text, "")
                        command.clear_cell_content(metrics)

                self.logger(f"[OK] 블록 {config.block_id}의 표 행 대체 완료 (셀 {col_count}개)")
                return position

        class AppendRowStrategy(RowOperationStrategy):
            """새 행 추가 전략"""
            def __init__(self, hwp, segment_registry, logger):
                self.hwp = hwp
                self.registry = segment_registry
                self.logger = logger

            def execute(
                self,
                row_text: str,
                config: RowReplaceConfig,
                command: CellUpdateCommand,
                helpers: dict
            ) -> Optional[TableRowPosition]:
                """새 행 추가 및 채우기 실행"""
                self.logger(f"[TABLE ROW APPEND] 추가 행: {row_text}")

                # 1. 새 행 추가
                self.hwp.TableAppendRow()

                # 2. 새 행 시작 위치로 이동
                self.hwp.move_pos(104)  # 행의 처음
                new_row_start_list = self.hwp.get_pos()[0]

                # 3. 열 개수 계산
                self.hwp.move_pos(105)  # 행의 끝
                new_row_end_list = self.hwp.get_pos()[0]
                new_col_count = new_row_end_list - new_row_start_list + 1

                # 4. 셀 파싱
                cells = helpers["split_row"](row_text)

                # 5. 각 셀 순회 및 채우기
                for col_idx in range(new_col_count):
                    current_list = new_row_start_list + col_idx
                    self.hwp.set_pos(current_list, 0, 0)

                    if col_idx < len(cells):
                        # 셀 텍스트 입력
                        helpers["markup_processor"](
                            cells[col_idx],
                            font_size=config.font_size,
                            font_family=config.font_family,
                            align=config.align,
                            spacing=None,
                            indentation=None,
                        )
                    else:
                        # 빈 셀 입력
                        self.hwp.InsertText(" ")

                self.logger(f"[OK] 표에 새 행 추가 완료 (셀 {new_col_count}개)")

                # 6. 레지스트리 업데이트
                if self.registry:
                    self.registry.update_after_list_pos_append(
                        config.block_id, new_col_count, new_row_start_list
                    )

                return TableRowPosition(new_row_start_list, new_col_count)

        # ==================== 빌더 패턴 ====================
        class TableRowReplacementBuilder:
            """표 행 대체 작업 빌더 (파이프라인 오케스트레이션)"""
            def __init__(self, hwp, segment_registry, logger):
                self.hwp = hwp
                self.registry = segment_registry
                self.logger = logger
                self.config: Optional[RowReplaceConfig] = None
                self.last_position: Optional[TableRowPosition] = None

            def with_config(self, config: RowReplaceConfig):
                """설정 바인딩"""
                self.config = config
                return self

            def reset_continuation_state(self, state_holder):
                """연속 삽입 상태 리셋"""
                state_holder.last_insert_info = None
                return self

            def process_rows(self, helpers: dict) -> bool:
                """행 교체 처리 (Replace 전용 — 행 추가 없음)"""
                if not self.config or not self.config.row_data:
                    return False

                command = CellUpdateCommand(self.hwp, self.registry, self.logger)
                replace_strategy = ReplaceRowStrategy(self.hwp, self.registry, self.logger)

                self.last_position = replace_strategy.execute(
                    self.config.row_data[0], self.config, command, helpers
                )

                return True

            def save_continuation_state(self, state_holder, is_single_row: bool):
                """연속 호출 상태 저장"""
                if is_single_row and self.last_position:
                    state_holder.last_insert_info = {
                        "block_id": self.config.block_id,
                        "type": "table_row",
                        "last_row_list_pos": self.last_position.row_start_list,
                    }
                elif not is_single_row and self.last_position:
                    # 다중 행 처리 시 마지막 행 위치 저장
                    state_holder.last_insert_info = {
                        "block_id": self.config.block_id,
                        "type": "table_row",
                        "last_row_list_pos": self.last_position.row_start_list,
                    }
                return self

            def finalize(self, validation_callback: Callable):
                """최종 검증 및 완료"""
                if self.config:
                    validation_callback(self.config.block_id)
                return True

        # ==================== 메인 로직 ====================
        # 1. Validation & Preparation
        block_id_key = str(block_id) if block_id is not None else None
        resolved_id = self._resolve_scoped_segment_identifier(block_id_key, "replace_table_row")
        if resolved_id is None:
            return False
        if not self._validate_segment_type(resolved_id, {"td"}, "replace_table_row"):
            return False
        block_id = resolved_id

        # 2. Parameter Normalization
        row_texts = row_texts if row_texts is not None else kwargs.get("row_texts", [])
        font_size = kwargs.get("font_size")
        font_family = kwargs.get("font_family")
        align = kwargs.get("align")

        # 3. Input Validation
        if (
            not isinstance(row_texts, (list, tuple))
            or len(row_texts) == 0
            or all((rt is None) or (str(rt).strip() == "") for rt in row_texts)
        ):
            self.log_to_main("[ERROR] replace_table_row: row_texts가 비어있음")
            return False

        # 4. Position Retrieval
        position = self._retrieve_segment_position(block_id)
        if not position:
            return False

        try:
            # 5. Config Creation
            rows = [str(rt) if rt is not None else "" for rt in row_texts]
            config = RowReplaceConfig(
                block_id=block_id,
                row_data=rows,
                initial_position=position,
                font_size=font_size,
                font_family=font_family,
                align=align
            )

            # 6. Helper Functions Bundle
            helpers = {
                "split_row": self._split_row_preserving_markup,
                "bypass_check": self._should_bypass_replacement,
                "markup_processor": self._process_markup_for_row_operation,
            }

            # 7. Builder Pipeline Execution
            builder = TableRowReplacementBuilder(self.hwp, self.segment_registry, self.log_to_main)
            return (builder
                .with_config(config)
                .reset_continuation_state(self)
                .process_rows(helpers) and
                builder
                .save_continuation_state(self, is_single_row=(len(rows) == 1))
                .finalize(self._schedule_table_boundary_validation))

        except Exception as e:
            self.log_to_main(f"[ERROR] 블록 {block_id}의 표 행 대체 실패: {e}")
            import traceback

            traceback.print_exc()
            return False

    def append_table_row(self, block_id: str, row_texts=None, **kwargs) -> bool:
        """블록 ID 기반 표의 행 추가 (빌더 패턴 + 전략 패턴)

        Args:
            block_id: 대상 셀이 있는 블록 ID
            row_texts: 문자열 리스트. 각 항목은 한 행을 의미하며, 셀은 '|'로 구분, 셀 내 줄바꿈은 '\n' 사용
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬

        Returns:
            bool: 성공 여부
        """
        from dataclasses import dataclass
        from typing import List, Tuple, Optional
        from abc import ABC, abstractmethod

        # ===== 데이터 클래스 정의 =====
        @dataclass
        class RowAppendConfig:
            """행 추가 설정"""
            block_id: str
            row_data: List[str]
            font_size: Optional[int] = None
            font_family: Optional[str] = None
            align: Optional[str] = None

            def validate(self) -> bool:
                """입력 유효성 검증"""
                if not self.row_data:
                    return False
                return not all(not r or not r.strip() for r in self.row_data)

        @dataclass
        class TablePosition:
            """표 위치 정보"""
            list_pos: int
            para_pos: int = 0
            char_pos: int = 0

        @dataclass
        class RowMetrics:
            """행 메트릭 정보"""
            column_count: int
            row_start_list: int

        # ===== 전략 패턴: 위치 결정 전략 =====
        class PositionStrategy(ABC):
            """위치 결정 추상 전략"""
            @abstractmethod
            def determine_position(self) -> Optional[TablePosition]:
                pass

        class ContinuationStrategy(PositionStrategy):
            """연속 호출 시 위치 전략"""
            def __init__(self, last_info: dict, target_block: str, logger):
                self.last_info = last_info
                self.target_block = target_block
                self.logger = logger

            def determine_position(self) -> Optional[TablePosition]:
                if (self.last_info
                    and self.last_info.get("block_id") == self.target_block
                    and self.last_info.get("type") == "table_row"):

                    pos = TablePosition(list_pos=self.last_info["last_row_list_pos"])
                    self.logger(f"[TABLE ROW APPEND] 연속 호출 감지 - 마지막 행 위치 사용: L:{pos.list_pos}")
                    return pos
                return None

        class InitialPositionStrategy(PositionStrategy):
            """초기 호출 시 위치 전략"""
            def __init__(self, position_retriever, block_id: str):
                self.position_retriever = position_retriever
                self.block_id = block_id

            def determine_position(self) -> Optional[TablePosition]:
                raw_pos = self.position_retriever(self.block_id)
                if not raw_pos:
                    return None
                return TablePosition(*raw_pos)

        # ===== 빌더 패턴: 행 구축 및 채우기 =====
        class TableRowBuilder:
            """표 행 빌더"""
            def __init__(self, hwp_api, markup_splitter):
                self.hwp = hwp_api
                self.splitter = markup_splitter
                self.cells = []

            def parse_row_text(self, row_text: str):
                """행 텍스트를 셀로 파싱"""
                self.cells = self.splitter(row_text)
                return self

            def create_new_row(self):
                """새 행 생성"""
                try:
                    if hasattr(self.hwp, "HAction") and self.hwp.HAction:
                        self.hwp.HAction.Run("TableAppendRow")
                    else:
                        self.hwp.TableAppendRow()
                except Exception:
                    self.hwp.TableAppendRow()
                return self

            def measure_dimensions(self) -> RowMetrics:
                """행 치수 측정"""
                # 행 시작으로 이동
                self.hwp.move_pos(104)
                start_pos = self.hwp.get_pos()
                start_list = start_pos[0]

                # 행 끝으로 이동
                self.hwp.move_pos(105)
                end_pos = self.hwp.get_pos()
                end_list = end_pos[0]

                # 열 개수 계산
                col_count = end_list - start_list + 1

                # 행 시작으로 복귀
                self.hwp.move_pos(104)
                new_row_pos = self.hwp.get_pos()

                return RowMetrics(
                    column_count=col_count,
                    row_start_list=new_row_pos[0]
                )

            def populate_cells(self, config: RowAppendConfig, metrics: RowMetrics, content_processor):
                """셀에 콘텐츠 채우기"""
                available_cells = min(len(self.cells), metrics.column_count)

                for cell_idx in range(available_cells):
                    if cell_idx > 0:
                        self.hwp.move_pos(101)  # 다음 셀로

                    content_processor(
                        self.cells[cell_idx],
                        font_size=config.font_size,
                        font_family=config.font_family,
                        align=config.align,
                        spacing=None,
                        indentation=None
                    )

                return self

        # ===== 연속 상태 관리 =====
        class ContinuationStateManager:
            """연속 호출 상태 관리자"""
            def __init__(self, state_holder):
                self.holder = state_holder

            def save_state(self, block_id: str, row_start: int):
                """현재 상태 저장"""
                self.holder.last_insert_info = {
                    "block_id": block_id,
                    "type": "table_row",
                    "last_row_list_pos": row_start
                }

            def clear_state(self):
                """상태 초기화"""
                self.holder.last_insert_info = None

        # ===== 메인 로직 시작 =====
        # 1. 파라미터 정규화
        normalized_id = str(block_id) if block_id is not None else None
        resolved_id = self._resolve_scoped_segment_identifier(normalized_id, "append_table_row")
        if not resolved_id or not self._validate_segment_type(resolved_id, {"td"}, "append_table_row"):
            return False

        # 2. 설정 구축
        config = RowAppendConfig(
            block_id=resolved_id,
            row_data=[str(rt) if rt is not None else "" for rt in (row_texts or kwargs.get("row_texts", []))],
            font_size=kwargs.get("font_size"),
            font_family=kwargs.get("font_family"),
            align=kwargs.get("align")
        )

        if not config.validate():
            self.log_to_main("[ERROR] append_table_row: row_texts가 비어있음")
            return False

        # 3. 위치 전략 선택 및 실행
        continuation_strategy = ContinuationStrategy(self.last_insert_info, config.block_id, self.log_to_main)
        position = continuation_strategy.determine_position()

        state_manager = ContinuationStateManager(self)

        if not position:
            # 초기 호출
            initial_strategy = InitialPositionStrategy(self._retrieve_segment_position, config.block_id)
            position = initial_strategy.determine_position()
            if not position:
                return False
            state_manager.clear_state()

        # 4. 행 빌더 생성
        builder = TableRowBuilder(self.hwp, self._split_row_preserving_markup)

        try:
            # 5. 각 행 처리
            for row_index, row_text in enumerate(config.row_data):
                # 첫 행만 위치 설정
                if row_index == 0:
                    self.hwp.set_pos(position.list_pos, position.para_pos, position.char_pos)

                # 행 구축 파이프라인
                metrics = (builder
                    .parse_row_text(row_text)
                    .create_new_row()
                    .measure_dimensions())

                # 셀 채우기
                builder.populate_cells(config, metrics, self._process_markup_for_row_operation)

                # 로깅
                self.log_to_main(f"[OK] 행 {row_index + 1} 추가 완료 (셀 {metrics.column_count}개)")

                # 레지스트리 업데이트
                if self.segment_registry:
                    self.segment_registry.update_after_list_pos_append(
                        config.block_id, metrics.column_count, metrics.row_start_list
                    )

                # 상태 저장
                state_manager.save_state(config.block_id, metrics.row_start_list)

            # 6. 완료 처리
            self.log_to_main(f"[OK] 블록 {config.block_id}의 표에 총 {len(config.row_data)}개 행 추가 완료")
            self._schedule_table_boundary_validation(config.block_id)

            return True

        except Exception as e:
            self.log_to_main(f"[ERROR] 블록 {config.block_id}의 표에 행 추가 실패: {e}")
            import traceback
            traceback.print_exc()
            return False

    def remove_table_row(self, block_id: str) -> bool:
        """블록 ID 기반 표 행 삭제 (Guard + Command + Strategy + Template Method 패턴)

        Args:
            block_id: 삭제할 행에 있는 셀의 블록 ID

        Returns:
            bool: 성공 여부
        """
        from dataclasses import dataclass
        from typing import Optional as Opt, Tuple
        from abc import ABC, abstractmethod

        @dataclass
        class RowMetadata:
            """행 메타데이터"""
            col_count: int
            row_start_list: int
            row_end_list: int

        class ValidationGuard:
            """검증 가드 (체인)"""
            def __init__(self, modifier):
                self.modifier = modifier

            def validate(self, block_id_key: str) -> Opt[str]:
                # 1단계: ID 해결
                resolved_id = self.modifier._resolve_scoped_segment_identifier(block_id_key, "delete_table_row")
                if resolved_id is None:
                    return None

                # 2단계: 타입 검증
                if not self.modifier._validate_segment_type(resolved_id, {"td"}, "delete_table_row"):
                    return None

                return resolved_id

            def validate_position(self, block_id: str) -> Opt[Tuple[int, int, int]]:
                position = self.modifier._retrieve_segment_position(block_id)
                return position if position else None

        class ColumnCountStrategy:
            """열 개수 계산 전략"""
            ROW_START_CODE = 104
            ROW_END_CODE = 105

            @staticmethod
            def calculate(hwp) -> RowMetadata:
                # 행의 처음으로 이동
                hwp.move_pos(ColumnCountStrategy.ROW_START_CODE)
                row_start_pos = hwp.get_pos()
                row_start_list = row_start_pos[0]

                # 행의 끝으로 이동
                hwp.move_pos(ColumnCountStrategy.ROW_END_CODE)
                row_end_pos = hwp.get_pos()
                row_end_list = row_end_pos[0]

                # 열 개수 계산
                col_count = row_end_list - row_start_list + 1

                return RowMetadata(col_count, row_start_list, row_end_list)

        class RowRemovalCommand(ABC):
            """행 삭제 명령 (추상)"""
            @abstractmethod
            def execute(self) -> None:
                pass

        class DeleteRowCommand(RowRemovalCommand):
            """행 삭제 명령"""
            def __init__(self, hwp):
                self.hwp = hwp

            def execute(self) -> None:
                try:
                    if hasattr(self.hwp, "HAction") and self.hwp.HAction:
                        self.hwp.HAction.Run("TableDeleteRow")
                    else:
                        self.hwp.TableSubtractRow()
                except Exception:
                    self.hwp.TableSubtractRow()

        class UpdateRegistryCommand(RowRemovalCommand):
            """레지스트리 업데이트 명령"""
            def __init__(self, registry, block_id: str, metadata: RowMetadata):
                self.registry = registry
                self.block_id = block_id
                self.metadata = metadata

            def execute(self) -> None:
                if self.registry:
                    self.registry.update_after_list_pos_deletion(
                        self.block_id,
                        self.metadata.col_count,
                        self.metadata.row_start_list,
                    )

        class RowRemovalProcess:
            """행 삭제 프로세스 (Template Method)"""
            def __init__(self, modifier, hwp, logger):
                self.modifier = modifier
                self.hwp = hwp
                self.logger = logger
                self.guard = ValidationGuard(modifier)
                self.strategy = ColumnCountStrategy()

            def remove_row(self, block_id: str) -> bool:
                # 1단계: 연속 삽입 리셋
                self.modifier.last_insert_info = None

                # 2단계: 검증 (Guard chain)
                block_id_key = str(block_id) if block_id is not None else None
                resolved_id = self.guard.validate(block_id_key)
                if not resolved_id:
                    return False

                position = self.guard.validate_position(resolved_id)
                if not position:
                    return False

                # 3단계: 행 삭제 실행
                try:
                    list_pos, para_pos, char_pos = position

                    # 3-1. 커서 이동
                    self.hwp.set_pos(list_pos, para_pos, char_pos)

                    # 3-2. 메타데이터 계산
                    metadata = self.strategy.calculate(self.hwp)
                    self.logger(f"[TABLE ROW DELETE] 행 삭제 - 셀 개수: {metadata.col_count}")
                    self.logger(f"  삭제할 행 범위: L:{metadata.row_start_list} ~ L:{metadata.row_end_list}")

                    # 3-3. 원래 위치로 복원
                    self.hwp.set_pos(list_pos, para_pos, char_pos)

                    # 3-4. 명령 실행 (Command chain)
                    commands = [
                        DeleteRowCommand(self.hwp),
                        UpdateRegistryCommand(self.modifier.segment_registry, resolved_id, metadata),
                    ]

                    for cmd in commands:
                        cmd.execute()

                    self.logger(f"[OK] 블록 {resolved_id}의 표 행 삭제 완료 (셀 {metadata.col_count}개)")
                    return True

                except Exception as e:
                    self.logger(f"[ERROR] 블록 {resolved_id}의 표 행 삭제 실패: {e}")
                    import traceback
                    traceback.print_exc()
                    return False

        # Main execution: 행 삭제 프로세스
        process = RowRemovalProcess(self, self.hwp, self.log_to_main)
        return process.remove_row(block_id)

    def create_table(self, block_id: str, row_texts=None, **kwargs) -> bool:
        """블록 ID 기반 표 생성 (빌더 + 팩토리 + 전략 패턴)

        Args:
            block_id: 표를 생성할 블록 ID
            row_texts: 문자열 리스트. 각 항목은 한 행을 의미하며, 셀은 '|'로 구분, 셀 내 줄바꿈은 '\n' 사용
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬
        """
        from dataclasses import dataclass
        from typing import List, Tuple, Optional
        from abc import ABC, abstractmethod

        # ===== 데이터 클래스 =====
        @dataclass
        class TableSpec:
            """표 사양"""
            block_id: str
            rows: List[str]
            row_count: int
            col_count: int
            header_axis: str
            font_size: Optional[int] = None
            font_family: Optional[str] = None
            align: Optional[str] = None

            @staticmethod
            def from_rows(block_id: str, row_texts: List[str], header: str, **style) -> 'TableSpec':
                """행 데이터로부터 표 사양 생성"""
                normalized_rows = [str(rt) if rt is not None else "" for rt in row_texts]
                header_axis = header.strip().lower() if header else "row"
                if header_axis not in ("row", "column"):
                    header_axis = "row"

                return TableSpec(
                    block_id=block_id,
                    rows=normalized_rows,
                    row_count=len(normalized_rows),
                    col_count=1,
                    header_axis=header_axis,
                    font_size=style.get("font_size"),
                    font_family=style.get("font_family"),
                    align=style.get("align")
                )

        @dataclass
        class TablePosition:
            """표 삽입 위치"""
            list_pos: int
            para_pos: int
            char_pos: int = 0

        @dataclass
        class TableMetrics:
            """표 메트릭 정보"""
            total_cells: int
            first_cell_pos: Tuple[int, int, int]
            current_list_pos: int

            @property
            def table_start_list(self) -> int:
                return self.current_list_pos - self.total_cells

        # ===== 전략 패턴: 열 계산 전략 =====
        class ColumnCountStrategy(ABC):
            @abstractmethod
            def calculate_max_columns(self, rows: List[str], splitter) -> int:
                pass

        class MaxColumnStrategy(ColumnCountStrategy):
            def calculate_max_columns(self, rows: List[str], splitter) -> int:
                if not rows:
                    return 1

                max_cols = 1
                for row in rows:
                    col_count = len(splitter(row))
                    if col_count > max_cols:
                        max_cols = col_count

                return max_cols

        # ===== 전략 패턴: 위치 결정 전략 =====
        class InsertPositionStrategy(ABC):
            @abstractmethod
            def determine(self) -> Optional[TablePosition]:
                pass

        class ContinuationPositionStrategy(InsertPositionStrategy):
            def __init__(self, last_info: dict, target_block: str, logger):
                self.last_info = last_info
                self.target_block = target_block
                self.logger = logger

            def determine(self) -> Optional[TablePosition]:
                if self.last_info and self.last_info.get("block_id") == self.target_block:
                    pos = TablePosition(
                        list_pos=self.last_info["list_pos"],
                        para_pos=self.last_info["next_para_pos"]
                    )
                    self.logger(f"[연속 삽입] 블록 {self.target_block} - 마지막 삽입 다음 위치 (P:{pos.para_pos}) 사용")
                    return pos
                return None

        class DirectPositionStrategy(InsertPositionStrategy):
            def __init__(self, position_retriever, block_id: str, logger):
                self.retriever = position_retriever
                self.block_id = block_id
                self.logger = logger

            def determine(self) -> Optional[TablePosition]:
                raw_pos = self.retriever(self.block_id)
                if not raw_pos:
                    return None

                pos = TablePosition(*raw_pos)
                self.logger(f"[새 삽입] 블록 {self.block_id} - 원본 위치 (P:{pos.para_pos}) 사용")
                return pos

        # ===== 팩토리 패턴: 표 생성 팩토리 =====
        class TableFactory:
            def __init__(self, hwp_api, logger):
                self.hwp = hwp_api
                self.logger = logger

            def prepare_insertion_space(self):
                self.hwp.MoveParaEnd()
                self.hwp.BreakPara()
                self.hwp.BreakPara()
                self.hwp.BreakPara()
                self.hwp.MoveLineUp()

            def create_empty_table(self, spec: TableSpec) -> bool:
                self.logger(f"[TABLE CREATE] 블록 {spec.block_id}에 {spec.row_count}x{spec.col_count} 표 생성")

                result = self.hwp.create_table(
                    rows=spec.row_count,
                    cols=spec.col_count,
                    treat_as_char=False,
                    header=False
                )

                if not result:
                    self.logger("[ERROR] 표 생성 실패")
                    return False

                return True

        # ===== 빌더 패턴: 표 구축 빌더 =====
        class TableBuilder:
            def __init__(self, hwp_api, logger):
                self.hwp = hwp_api
                self.logger = logger
                self.spec = None
                self.position = None
                self.metrics = None

            def with_spec(self, spec: TableSpec):
                self.spec = spec
                return self

            def with_position(self, position: TablePosition):
                self.position = position
                return self

            def calculate_columns(self, strategy: ColumnCountStrategy, splitter):
                self.spec.col_count = strategy.calculate_max_columns(self.spec.rows, splitter)
                return self

            def move_to_position(self):
                self.hwp.set_pos(self.position.list_pos, self.position.para_pos, self.position.char_pos)
                self.logger(f"  - 위치: list_pos={self.position.list_pos}, para_pos={self.position.para_pos}, char_pos={self.position.char_pos}")
                return self

            def create_structure(self, factory: TableFactory) -> bool:
                factory.prepare_insertion_space()
                success = factory.create_empty_table(self.spec)
                if not success:
                    return False

                first_cell_pos = self.hwp.get_pos()
                self.metrics = TableMetrics(
                    total_cells=self.spec.row_count * self.spec.col_count,
                    first_cell_pos=first_cell_pos,
                    current_list_pos=0
                )
                return True

            def populate_cells(self, cell_splitter, content_processor):
                cell_count = 0
                total_cells = self.metrics.total_cells

                for row_idx, row_text in enumerate(self.spec.rows):
                    cells = cell_splitter(row_text)

                    for col_idx in range(self.spec.col_count):
                        cell_text = cells[col_idx] if col_idx < len(cells) else ""

                        content_processor(
                            cell_text,
                            font_size=self.spec.font_size,
                            font_family=self.spec.font_family,
                            align=self.spec.align,
                            spacing=None,
                            indentation=None
                        )

                        cell_count += 1

                        if cell_count < total_cells:
                            self.hwp.move_pos(101)

                self.metrics.current_list_pos = self.hwp.get_pos()[0]
                self.logger(f"[OK] 블록 {self.spec.block_id}에 표 생성 완료 ({self.spec.row_count}x{self.spec.col_count}, {cell_count}개 셀 입력)")
                return self

            def apply_styling(self, style_applicator):
                try:
                    style_applicator(
                        first_cell_pos=self.metrics.first_cell_pos,
                        row_num=self.spec.row_count,
                        col_num=self.spec.col_count,
                        header_axis=self.spec.header_axis
                    )
                except Exception as e:
                    self.logger(f"[WARN] 표 스타일 적용 실패: {e}")
                return self

            def update_registry(self, registry):
                if not registry:
                    return self

                registry.update_after_para_pos_append(self.spec.block_id, 3)

                registry.update_after_list_pos_append(
                    self.spec.block_id,
                    self.metrics.total_cells,
                    self.metrics.table_start_list
                )

                self.logger(f"[BlockManager] para_pos 3 증가, list_pos {self.metrics.total_cells} 증가 업데이트 완료")
                return self

            def save_continuation(self, state_holder):
                state_holder.last_insert_info = {
                    "block_id": self.spec.block_id,
                    "list_pos": self.position.list_pos,
                    "next_para_pos": self.position.para_pos + 3
                }
                return self

            def finalize(self, boundary_validator):
                boundary_validator()
                return True

        # ===== 메인 로직 =====
        # 1. 파라미터 검증
        if row_texts is None:
            row_texts = kwargs.get("row_texts", [])

        if (not isinstance(row_texts, (list, tuple))
            or len(row_texts) == 0
            or all((rt is None) or (str(rt).strip() == "") for rt in row_texts)):
            self.log_to_main("[ERROR] create_table: row_texts가 제공되지 않음")
            return False

        # 2. 표 사양 생성
        spec = TableSpec.from_rows(
            block_id=block_id,
            row_texts=row_texts,
            header=kwargs.get("header") or "",
            font_size=kwargs.get("font_size"),
            font_family=kwargs.get("font_family"),
            align=kwargs.get("align")
        )

        # 3. 위치 전략 선택
        continuation_strategy = ContinuationPositionStrategy(self.last_insert_info, spec.block_id, self.log_to_main)
        position = continuation_strategy.determine()

        if not position:
            direct_strategy = DirectPositionStrategy(self._retrieve_segment_position, spec.block_id, self.log_to_main)
            position = direct_strategy.determine()
            if not position:
                return False

        # 4. 빌더 생성 및 실행
        try:
            col_strategy = MaxColumnStrategy()
            factory = TableFactory(self.hwp, self.log_to_main)
            builder = TableBuilder(self.hwp, self.log_to_main)

            # 표 구축 파이프라인
            (builder
                .with_spec(spec)
                .with_position(position)
                .calculate_columns(col_strategy, self._split_row_preserving_markup)
                .move_to_position())

            if not builder.create_structure(factory):
                return False

            return (builder
                .populate_cells(self._split_row_preserving_markup, self._process_markup_for_row_operation)
                .apply_styling(self._apply_default_table_formatting)
                .update_registry(self.segment_registry)
                .save_continuation(self)
                .finalize(self._validate_table_page_boundary))

        except Exception as e:
            self.log_to_main(f"[ERROR] 블록 {spec.block_id}에 표 생성 실패: {e}")
            import traceback
            traceback.print_exc()
            return False

    def remove_table(self, block_id: str) -> bool:
        """세그먼트 식별자 기반 표 객체 제거

        Args:
            block_id: 표 내부 셀 또는 표 위치의 세그먼트 ID

        Returns:
            제거 성공 여부
        """
        # 식별자 검증 및 정규화
        normalized_id = str(block_id) if block_id is not None else None
        validated_id = self._resolve_scoped_segment_identifier(normalized_id, "delete_table")

        if validated_id is None:
            return False

        # 위치 조회
        coords = self._retrieve_segment_position(validated_id)
        if coords is None:
            self.log_to_main(f"[ERROR] 표 삭제: 세그먼트 {validated_id} 좌표 조회 실패")
            return False

        return self._execute_table_removal(validated_id, coords)

    def _execute_table_removal(self, seg_id: str, coords: Tuple[int, int, int]) -> bool:
        """실제 표 삭제 실행"""
        try:
            lp, pp, cp = coords
            self.hwp.set_pos(lp, pp, cp)
            self.log_to_main(f"[DELETE TABLE] 위치 이동: ({lp}, {pp}, {cp})")

            # 셀 위치 확인 (필요시 인접 위치 탐색)
            if not self._ensure_cell_position():
                self.log_to_main(f"[ERROR] 세그먼트 {seg_id} 인근 표 없음")
                return False

            # 표 제거 시도 (복수 전략)
            return self._apply_table_deletion_strategies(seg_id)

        except (AttributeError, RuntimeError, TypeError) as exc:
            self.log_to_main(f"[ERROR] 표 삭제 예외: {exc}")
            return False

    def _ensure_cell_position(self) -> bool:
        """현재 위치가 셀인지 확인, 아니면 인접 탐색"""
        if self.hwp.is_cell():
            return True
        self.hwp.MoveRight()
        return self.hwp.is_cell()

    def _apply_table_deletion_strategies(self, seg_id: str) -> bool:
        """표 삭제 전략 순차 적용"""
        parent_ref = self.hwp.ParentCtrl
        self.hwp.select_ctrl(parent_ref)

        # 전략 1: delete_ctrl
        if self.hwp.delete_ctrl(parent_ref):
            self.log_to_main(f"[OK] 표 삭제 완료 (ctrl 방식): {seg_id}")
            return True

        # 전략 2: Delete 액션
        self.log_to_main("[WARN] ctrl 삭제 실패, 대안 시도")
        if self.hwp.Delete():
            self.log_to_main(f"[OK] 표 삭제 완료 (action 방식): {seg_id}")
            return True

        return False

    def remove_textbox(self, block_id: str) -> bool:
        """글상자 삭제 (검증자 + 전략 패턴)"""
        from dataclasses import dataclass
        from typing import Tuple, Optional

        @dataclass
        class TextboxRemoveContext:
            """글상자 삭제 컨텍스트"""
            block_id: str
            position: Tuple[int, int, int]
            parent_ctrl: object
            ctrl_id: str
            user_desc: str

        class ControlValidator:
            """컨트롤 검증자"""
            @staticmethod
            def validate_parent(hwp, block_id: str, logger) -> Optional[object]:
                """상위 컨트롤 존재 확인"""
                parent_ctrl = getattr(hwp, "ParentCtrl", None)
                if not parent_ctrl:
                    logger(f"[ERROR] delete_textbox: 블록 {block_id} 위치에 상위 컨트롤이 없습니다.")
                    return None
                return parent_ctrl

            @staticmethod
            def validate_ctrl_id(ctrl_id: str, user_desc: str, block_id: str, logger) -> bool:
                """CtrlID가 gso(그리기 개체)인지 확인"""
                if ctrl_id != "gso":
                    logger(
                        f"[ERROR] delete_textbox: 블록 {block_id}의 상위 컨트롤이 글상자가 아님 "
                        f"(CtrlID={ctrl_id}, UserDesc={user_desc})"
                    )
                    return False
                return True

        class TextboxDetector:
            """텍스트 박스 감지기"""
            TEXTBOX_KEYWORDS = ("사각형", "textbox", "text box")

            @staticmethod
            def is_textbox(user_desc: str) -> bool:
                """UserDesc 기반 텍스트 박스 판별"""
                normalized_desc = (
                    str(user_desc).replace(" ", "").lower() if user_desc is not None else ""
                )
                return any(key in normalized_desc for key in TextboxDetector.TEXTBOX_KEYWORDS)

        class ControlDeleteStrategy:
            """컨트롤 삭제 전략"""
            def __init__(self, hwp, logger):
                self.hwp = hwp
                self.logger = logger

            def delete(self, parent_ctrl, block_id: str) -> bool:
                """두 가지 방법으로 삭제 시도 (delete_ctrl → Delete 폴백)"""
                # 1차 시도: delete_ctrl
                result = self._try_delete_ctrl(parent_ctrl)

                # 2차 시도: Delete 액션 (폴백)
                if not result:
                    result = self._try_delete_action(block_id)

                return result

            def _try_delete_ctrl(self, parent_ctrl) -> bool:
                """delete_ctrl 메서드로 삭제 시도"""
                try:
                    return bool(self.hwp.delete_ctrl(parent_ctrl))
                except Exception as e:
                    self.logger(f"[WARN] delete_textbox: delete_ctrl 실패, Delete 액션 시도: {e}")
                    return False

            def _try_delete_action(self, block_id: str) -> bool:
                """Delete 액션으로 삭제 시도 (폴백)"""
                try:
                    return bool(self.hwp.Delete())
                except Exception as e:
                    self.logger(f"[ERROR] delete_textbox: Delete 액션 실패 (block_id={block_id}): {e}")
                    return False

        class TextboxRemovePipeline:
            """글상자 삭제 파이프라인"""
            def __init__(self, hwp, id_resolver, position_retriever, logger):
                self.hwp = hwp
                self.id_resolver = id_resolver
                self.position_retriever = position_retriever
                self.logger = logger

            def execute(self, block_id: str) -> bool:
                """파이프라인 실행"""
                try:
                    # 1단계: ID 해결
                    resolved_id = self._resolve_id(block_id)
                    if not resolved_id:
                        return False

                    # 2단계: 위치 조회 및 이동
                    context = self._prepare_context(resolved_id)
                    if not context:
                        return False

                    # 3단계: 검증
                    if not self._validate(context):
                        return False

                    # 4단계: 선택 및 삭제
                    return self._delete_control(context)

                except Exception as e:
                    self.logger(f"[ERROR] 블록 {block_id}의 글상자 삭제 실패: {e}")
                    return False

            def _resolve_id(self, block_id: str) -> Optional[str]:
                """블록 ID 해결"""
                block_id_key = str(block_id) if block_id is not None else None
                return self.id_resolver(block_id_key, "delete_textbox")

            def _prepare_context(self, block_id: str) -> Optional[TextboxRemoveContext]:
                """위치 조회 및 컨텍스트 준비"""
                position = self.position_retriever(block_id)
                if not position:
                    return None

                self.hwp.set_pos(*position)

                parent_ctrl = ControlValidator.validate_parent(self.hwp, block_id, self.logger)
                if not parent_ctrl:
                    return None

                ctrl_id = getattr(parent_ctrl, "CtrlID", None)
                user_desc = getattr(parent_ctrl, "UserDesc", None)

                return TextboxRemoveContext(block_id, position, parent_ctrl, ctrl_id, user_desc)

            def _validate(self, context: TextboxRemoveContext) -> bool:
                """컨트롤 검증"""
                # CtrlID 검증
                if not ControlValidator.validate_ctrl_id(
                    context.ctrl_id, context.user_desc, context.block_id, self.logger
                ):
                    return False

                # TextBox 감지
                if not TextboxDetector.is_textbox(context.user_desc):
                    self.logger(
                        f"[ERROR] delete_textbox: UserDesc 기준으로 텍스트 박스로 식별되지 않음 "
                        f"(CtrlID={context.ctrl_id}, UserDesc={context.user_desc})"
                    )
                    return False

                return True

            def _delete_control(self, context: TextboxRemoveContext) -> bool:
                """컨트롤 선택 및 삭제"""
                # 선택
                try:
                    self.hwp.select_ctrl(context.parent_ctrl)
                except Exception as e:
                    self.logger(
                        f"[ERROR] delete_textbox: 컨트롤 선택 실패 (block_id={context.block_id}): {e}"
                    )
                    return False

                # 삭제
                delete_strategy = ControlDeleteStrategy(self.hwp, self.logger)
                result = delete_strategy.delete(context.parent_ctrl, context.block_id)

                if result:
                    self.logger(
                        f"[OK] 블록 {context.block_id}의 글상자 삭제 완료 "
                        f"(CtrlID={context.ctrl_id}, UserDesc={context.user_desc})"
                    )
                return result

        # 메인 로직: 파이프라인 실행
        pipeline = TextboxRemovePipeline(
            self.hwp,
            self._resolve_scoped_segment_identifier,
            self._retrieve_segment_position,
            self.log_to_main
        )
        return pipeline.execute(block_id)

    def insert_annotation(
        self,
        block_id: str,
        footnote_anchor_text: str = None,
        footnote_text: str = None,
        **kwargs,
    ) -> bool:
        """블록 ID 기반 각주 삽입 (Strategy + Template Method + Chain + Builder 패턴)"""
        from dataclasses import dataclass
        from typing import Tuple as TupleType, Optional, List as ListType
        from abc import ABC, abstractmethod
        import re

        # Dataclass for parameters
        @dataclass
        class FootnoteParams:
            """각주 파라미터"""
            anchor_text: str
            content: str
            block_id: str

            @classmethod
            def from_args(cls, anchor: str, text: str, block_id: str, kwargs: dict):
                """인자에서 파라미터 추출 및 정규화"""
                anchor = anchor or kwargs.get("footnote_anchor_text", "")
                text = text or kwargs.get("footnote_text", "")
                # 숫자 패턴 제거
                normalized_text = re.sub(r"^\d+[\.\)]\s*", "", text.strip())
                return cls(anchor, normalized_text, block_id)

            def validate(self) -> Optional[str]:
                """검증 (None = 성공, str = 에러 메시지)"""
                if not self.anchor_text or not self.content:
                    return "footnote_anchor_text와 footnote_text가 필요합니다."
                return None

        # Dataclass for search context
        @dataclass
        class SearchContext:
            """앵커 텍스트 검색 컨텍스트"""
            anchor: str
            list_pos: int
            para_pos: int
            char_pos: int
            block_id: str

        # Strategy Pattern for anchor search
        class AnchorSearchStrategy(ABC):
            """앵커 텍스트 검색 전략 (추상, Template Method)"""
            def __init__(self, hwp, logger):
                self.hwp = hwp
                self.logger = logger

            def search(self, ctx: SearchContext) -> TupleType[bool, Optional[str]]:
                """Template Method: (성공여부, 에러메시지)"""
                self._prepare_position(ctx)
                found = self._execute_find(ctx)
                if not found:
                    return False, self._get_error_message(ctx)
                return True, None

            @abstractmethod
            def _prepare_position(self, ctx: SearchContext):
                """위치 준비"""
                pass

            def _execute_find(self, ctx: SearchContext) -> bool:
                """검색 실행"""
                return self.hwp.find(ctx.anchor, direction="Forward", regex=False)

            @abstractmethod
            def _get_error_message(self, ctx: SearchContext) -> str:
                """에러 메시지 생성"""
                pass

        # Concrete strategies
        class PrimaryForwardSearch(AnchorSearchStrategy):
            """1차 검색: 현재 위치에서 Forward"""
            def _prepare_position(self, ctx: SearchContext):
                self.hwp.set_pos(ctx.list_pos, ctx.para_pos, ctx.char_pos)

            def _get_error_message(self, ctx: SearchContext) -> str:
                return ""  # 폴백으로 넘어가므로 에러 없음

        class BackscanBoundarySearch(AnchorSearchStrategy):
            """2차 검색: Backscan 후 경계 확인"""
            def __init__(self, hwp, logger, stayed_in_block: bool):
                super().__init__(hwp, logger)
                self.stayed_in_block = stayed_in_block

            def _prepare_position(self, ctx: SearchContext):
                if self.stayed_in_block:
                    # 같은 블록 내 → 현재 위치 유지
                    pass
                else:
                    # 경계 넘음 → 블록 처음으로
                    self.hwp.set_pos(ctx.list_pos, ctx.para_pos, 0)

            def _get_error_message(self, ctx: SearchContext) -> str:
                label = "local backscan" if self.stayed_in_block else "block-begin"
                return f"블록 {ctx.block_id}에서 '{ctx.anchor}'를 찾을 수 없습니다. (fallback: {label})"

        # Chain of Responsibility for fallback search
        class SearchChain:
            """검색 전략 체인"""
            def __init__(self, hwp, logger):
                self.hwp = hwp
                self.logger = logger

            def execute(self, ctx: SearchContext) -> TupleType[bool, Optional[str]]:
                """체인 실행: 1차 → Backscan → 2차"""
                # 1. Primary search
                primary = PrimaryForwardSearch(self.hwp, self.logger)
                found, _ = primary.search(ctx)
                if found:
                    return True, None

                # 2. Backscan to check boundary
                stayed_in_block = self._backscan_check_boundary(ctx)

                # 3. Fallback search
                fallback = BackscanBoundarySearch(self.hwp, self.logger, stayed_in_block)
                return fallback.search(ctx)

            def _backscan_check_boundary(self, ctx: SearchContext) -> bool:
                """경계 확인을 위한 Backscan (True = 같은 블록 유지)"""
                self.hwp.set_pos(ctx.list_pos, ctx.para_pos, ctx.char_pos)
                max_back = max(1, len(ctx.anchor))
                moved = 0
                while moved <= max_back:
                    try:
                        self.hwp.MovePrevChar()
                        cur_list, cur_para, _ = self.hwp.get_pos()
                    except Exception:
                        break
                    if cur_list != ctx.list_pos or cur_para != ctx.para_pos:
                        return False  # 경계 넘음
                    moved += 1
                return True  # 같은 블록 유지

        # Builder Pattern for multiline content
        class FootnoteContentBuilder:
            """각주 내용 빌더 (다중 줄 처리)"""
            def __init__(self, hwp, modifier, logger):
                self.hwp = hwp
                self.modifier = modifier
                self.logger = logger

            def build(self, content: str, block_id: str, registry) -> int:
                """내용 구축 (반환: 추가된 para 개수)"""
                if "\n" not in content:
                    return self._build_single_line(content)
                return self._build_multiline(content, block_id, registry)

            def _build_single_line(self, content: str) -> int:
                """단일 줄"""
                self.modifier._insert_styled_content(content)
                return 0

            def _build_multiline(self, content: str, block_id: str, registry) -> int:
                """다중 줄 (첫 줄 + 나머지 줄들)"""
                lines = content.split("\n")
                first, rest = lines[0], lines[1:]

                # 첫 줄
                self.modifier._insert_styled_content(first)

                # 나머지 줄들 (새 문단)
                for line in rest:
                    self.hwp.MoveParaEnd()
                    self.hwp.BreakPara()
                    self.modifier._insert_styled_content(line)

                # Registry 업데이트
                if registry:
                    registry.update_after_para_pos_append(block_id, len(rest))

                return len(rest)

        # Command Pattern for registry update
        class RegistryUpdateCommand:
            """Registry 업데이트 커맨드"""
            @staticmethod
            def update_list_pos(registry, block_id: str, footnote_pos: int):
                """list_pos 업데이트"""
                if registry:
                    registry.update_after_list_pos_append(block_id, 1, footnote_pos)

        # === Main execution pipeline ===
        try:
            # 1. Parameter extraction and validation
            params = FootnoteParams.from_args(footnote_anchor_text, footnote_text, block_id, kwargs)
            error = params.validate()
            if error:
                self.log_to_main(f"[ERROR] insert_footnote: {error}")
                return False

            # 2. Position retrieval
            position = self._retrieve_segment_position(block_id)
            if not position:
                return False

            list_pos, para_pos, char_pos = position

            # 3. Anchor text search (Chain of Responsibility)
            search_ctx = SearchContext(params.anchor_text, list_pos, para_pos, char_pos, block_id)
            chain = SearchChain(self.hwp, self.log_to_main)
            found, error_msg = chain.execute(search_ctx)
            if not found:
                self.log_to_main(f"[ERROR] {error_msg}")
                return False

            # 4. Cancel selection (find 후 텍스트 선택 상태)
            self.hwp.Cancel()

            # 5. Insert footnote
            self.hwp.InsertFootnote()

            # 6. Build content (Builder Pattern)
            builder = FootnoteContentBuilder(self.hwp, self, self.log_to_main)
            builder.build(params.content, block_id, self.segment_registry)

            # 6.5. Close footnote editor and return to main body
            self.hwp.HAction.Run("CloseEx")

            # 7. Get footnote position
            footnote_list_pos = self.hwp.get_pos()[0]

            self.log_to_main(f"[OK] 블록 {block_id}에 각주 삽입 완료")

            # 8. Update registry (Command Pattern)
            RegistryUpdateCommand.update_list_pos(self.segment_registry, block_id, footnote_list_pos)

            return True

        except Exception as e:
            self.log_to_main(f"[ERROR] 블록 {block_id}에 각주 삽입 실패: {e}")
            import traceback
            traceback.print_exc()
            return False

    def replace_annotation(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """각주 내용 대체 (전략 + 파이프라인 패턴)"""
        from dataclasses import dataclass
        from typing import Optional, Tuple, List
        from abc import ABC, abstractmethod
        import re

        @dataclass
        class AnnotationReplaceConfig:
            """각주 대체 설정"""
            block_id: str
            anchor_id_int: int
            content_id: str
            new_text: str
            cleaned_text: str
            position: Tuple[int, int, int]

        class ParamsExtractor:
            """파라미터 추출기"""
            @staticmethod
            def extract_text(direct_text: Optional[str], kwargs: dict) -> Optional[str]:
                """직접 또는 kwargs에서 new_text 추출"""
                return direct_text if direct_text is not None else kwargs.get("new_text", "")

        class IdConverter:
            """블록 ID 변환기"""
            @staticmethod
            def convert(block_id: str, logger) -> Optional[Tuple[int, str]]:
                """앵커 ID → 내용 ID 변환"""
                try:
                    anchor_id_int = int(block_id)
                    content_id = str(anchor_id_int + 1)
                    return anchor_id_int, content_id
                except Exception:
                    logger(f"[ERROR] 각주 대체: block_id를 정수로 변환할 수 없음: {block_id}")
                    return None

        class TextCleaner:
            """텍스트 정리기"""
            @staticmethod
            def clean(text: str) -> str:
                """숫자 접두사 제거 (예: '1) 내용' → '내용')"""
                return re.sub(r"^[\d]+[)\.]\s*", "", text.strip())

        class TextInsertionStrategy(ABC):
            """텍스트 삽입 전략 (추상)"""
            def __init__(self, hwp, inserter, logger):
                self.hwp = hwp
                self.inserter = inserter
                self.logger = logger

            @abstractmethod
            def insert(self, text: str):
                """텍스트 삽입 (추상 메서드)"""
                pass

        class SingleLineStrategy(TextInsertionStrategy):
            """단일 줄 전략"""
            def insert(self, text: str):
                """단순 대체"""
                self.inserter(text)
                self.logger("  - 단일 줄 입력 완료")

        class MultiLineStrategy(TextInsertionStrategy):
            """다중 줄 전략"""
            def insert(self, text: str):
                """첫 줄 대체 + 나머지 줄 추가"""
                lines = text.split("\n")
                first_line = lines[0]
                remaining_lines = lines[1:]

                self.logger(f"  - 줄바꿈 감지: 첫 줄과 {len(remaining_lines)}개 추가 줄")

                # 첫 줄 대체
                self.inserter(first_line)

                # 나머지 줄 추가
                for line in remaining_lines:
                    self.hwp.MoveParaEnd()
                    self.hwp.BreakPara()
                    self.inserter(line)
                    self.logger(f"    - 추가 줄 입력: {line[:30]}...")

        class AnnotationReplacePipeline:
            """각주 대체 파이프라인"""
            def __init__(self, hwp, position_retriever, inserter, logger):
                self.hwp = hwp
                self.position_retriever = position_retriever
                self.inserter = inserter
                self.logger = logger

            def execute(self, config: AnnotationReplaceConfig) -> bool:
                """파이프라인 실행"""
                try:
                    # 1단계: 위치로 이동 및 선택
                    self.hwp.set_pos(config.position[0], 0, 0)
                    self.logger(f"  - 위치 이동: {config.position}")
                    self.hwp.MoveNextChar()
                    self.hwp.MoveNextChar()
                    self.hwp.MoveSelParaEnd()

                    # 2단계: 전략 선택 및 텍스트 삽입
                    strategy = self._select_strategy(config.cleaned_text)
                    strategy.insert(config.cleaned_text)

                    self.logger("[OK] 각주 내용 대체 완료")
                    return True

                except Exception as e:
                    self.logger(f"[ERROR] 각주 내용 대체 실패: {e}")
                    import traceback
                    traceback.print_exc()
                    return False

            def _select_strategy(self, text: str) -> TextInsertionStrategy:
                """텍스트 유형에 따라 전략 선택"""
                if "\n" in text:
                    return MultiLineStrategy(self.hwp, self.inserter, self.logger)
                else:
                    return SingleLineStrategy(self.hwp, self.inserter, self.logger)

        # 메인 로직: 파이프라인 구성 및 실행
        # 1단계: 파라미터 추출
        extracted_text = ParamsExtractor.extract_text(new_text, kwargs)
        if not extracted_text:
            self.log_to_main("[ERROR] replace_footnote: new_text가 제공되지 않음")
            return False

        # 2단계: ID 변환
        id_result = IdConverter.convert(block_id, self.log_to_main)
        if not id_result:
            return False
        anchor_id_int, content_id = id_result

        self.log_to_main("[FOOTNOTE REPLACE] 각주 대체 시작")
        self.log_to_main(f"  - 앵커 블록 ID: {block_id}")
        self.log_to_main(f"  - 내용 블록 ID: {content_id}")

        # 3단계: 위치 조회
        content_position = self._retrieve_segment_position(content_id)
        if not content_position:
            self.log_to_main(f"[ERROR] 각주 내용 블록 {content_id}의 위치를 찾을 수 없음")
            return False

        # 4단계: 텍스트 정리
        cleaned_text = TextCleaner.clean(extracted_text)
        self.log_to_main(f"  - 원본 텍스트: {extracted_text[:50]}...")
        self.log_to_main(f"  - 정리된 텍스트: {cleaned_text[:50]}...")

        # 5단계: 파이프라인 실행
        config = AnnotationReplaceConfig(
            block_id, anchor_id_int, content_id,
            extracted_text, cleaned_text, content_position
        )
        pipeline = AnnotationReplacePipeline(
            self.hwp,
            self._retrieve_segment_position,
            self._insert_styled_content,
            self.log_to_main
        )
        return pipeline.execute(config)

    def remove_annotation(self, block_id: str) -> bool:
        """블록 ID 기반 각주 삭제 (Guard + Command + Template Method 패턴)

        변경 추적 모드(수정 추적)에서 바로 화면에서 사라지지 않으므로
        위치 업데이트는 수행하지 않는다.
        """
        from dataclasses import dataclass
        from typing import Optional as Opt, Tuple
        from abc import ABC, abstractmethod

        @dataclass
        class AnnotationIds:
            """각주 ID 쌍"""
            num_id: str
            fn_id: str

            @staticmethod
            def from_block_id(block_id: str) -> Opt['AnnotationIds']:
                try:
                    num_id_int = int(block_id)
                    return AnnotationIds(str(num_id_int), str(num_id_int + 1))
                except Exception:
                    return None

        class PositionGuard:
            """위치 검증 가드"""
            @staticmethod
            def validate_positions(num_pos: Opt[Tuple], fn_pos: Opt[Tuple]) -> bool:
                if not num_pos or not fn_pos:
                    return False
                if fn_pos[0] == 0:  # fn_position 리스트 위치가 0이면 실패
                    return False
                return True

        class DeletionCommand(ABC):
            """삭제 명령 (추상)"""
            @abstractmethod
            def execute(self) -> bool:
                pass

        class DeleteFootnoteContentCommand(DeletionCommand):
            """각주 내용 삭제 명령"""
            def __init__(self, hwp, fn_position: Tuple):
                self.hwp = hwp
                self.fn_position = fn_position

            def execute(self) -> bool:
                try:
                    self.hwp.set_pos(self.fn_position[0], 0, 0)
                    try:
                        self.hwp.SelectAll()
                        self.hwp.DeleteBack()
                    except Exception:
                        pass  # 각주 본문이 비어있거나 선택 실패 시 무시
                    return True
                except Exception:
                    return False

        class DeleteFootnoteNumberCommand(DeletionCommand):
            """각주 번호 삭제 명령"""
            CHAR_OFFSET = 8

            def __init__(self, hwp, num_position: Tuple):
                self.hwp = hwp
                self.num_position = num_position

            def execute(self) -> bool:
                try:
                    list_pos, para_pos, char_pos = self.num_position
                    self.hwp.set_pos(list_pos, para_pos, char_pos + self.CHAR_OFFSET)
                    self.hwp.DeleteBack()
                    return True
                except Exception:
                    return False

        class AnnotationRemovalProcess:
            """각주 삭제 프로세스 (Template Method)"""
            def __init__(self, modifier, hwp, logger):
                self.modifier = modifier
                self.hwp = hwp
                self.logger = logger

            def remove(self, block_id: str) -> bool:
                # 1단계: ID 변환 및 검증
                ids = AnnotationIds.from_block_id(block_id)
                if not ids:
                    self.logger(f"[ERROR] 각주 삭제: block_id를 정수로 변환할 수 없음: {block_id}")
                    return False

                # 2단계: 위치 조회
                num_position = self.modifier._retrieve_segment_position(ids.num_id)
                fn_position = self.modifier._retrieve_segment_position(ids.fn_id)

                # 3단계: 위치 검증
                if not PositionGuard.validate_positions(num_position, fn_position):
                    return False

                # 4단계: 삭제 명령 실행 (Command chain)
                try:
                    commands = [
                        DeleteFootnoteContentCommand(self.hwp, fn_position),
                        DeleteFootnoteNumberCommand(self.hwp, num_position),
                    ]

                    for cmd in commands:
                        if not cmd.execute():
                            return False

                    self.logger(f"[OK] 블록 {block_id}의 각주 삭제 완료")
                    return True

                except Exception as e:
                    self.logger(f"[ERROR] 블록 {block_id}의 각주 삭제 실패: {e}")
                    return False

        # Main execution: 각주 삭제 프로세스
        process = AnnotationRemovalProcess(self, self.hwp, self.log_to_main)
        return process.remove(block_id)

    def _apply_formatting_to_current_paragraph(
        self,
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        align: Optional[str] = None,
        spacing: Optional[float] = None,
        indentation: Optional[float] = None,
    ) -> None:
        """현재 문단에 스타일 적용 (Builder pattern 사용)

        Args:
            font_size: 폰트 크기 (1~150 포인트)
            font_family: 폰트 패밀리 이름
            align: 문단 정렬 ("left", "center", "right", "justify")
            spacing: 문단 간격 (포인트 단위)
            indentation: 들여쓰기 (포인트 단위)

        Note:
            호출 전에 커서가 대상 문단에 위치해 있어야 함
            ParagraphFormatter 빌더를 통해 서식 적용
        """
        # Builder pattern으로 서식 구성 및 적용
        formatter = ParagraphFormatter(self.hwp, self.log_to_main)

        # Method chaining으로 설정
        if font_size is not None:
            formatter.with_font_size(font_size)
        if font_family is not None:
            formatter.with_font_family(font_family)
        if align is not None:
            formatter.with_alignment(align)
        if spacing is not None:
            formatter.with_spacing(spacing)
        if indentation is not None:
            formatter.with_indentation(indentation)

        # 최종 적용
        formatter.apply()

    def apply_paragraph_formatting(
        self,
        block_id: str,
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        align: Optional[str] = None,
        spacing: Optional[float] = None,
        indentation: Optional[float] = None,
        **kwargs,
    ) -> bool:
        """블록 ID 기반 문단 스타일 적용 (빌더 + 검증자 패턴)

        Args:
            block_id: 스타일을 적용할 문단/리스트/셀의 블록 ID
            font_size: 폰트 크기 (1~150 포인트, float)
            font_family: 폰트 패밀리 이름 (예: "맑은 고딕", "D2Coding")
            align: 문단 정렬 ("left", "center", "right", "justify")
            spacing: 문단 간격 (포인트 단위, float)
            indentation: 들여쓰기 (포인트 단위, float, 음수 가능)

        Returns:
            bool: 성공 여부
        """
        from dataclasses import dataclass
        from typing import Optional

        @dataclass
        class ParagraphFormattingParams:
            """문단 서식 파라미터"""
            font_size: Optional[float] = None
            font_family: Optional[str] = None
            align: Optional[str] = None
            spacing: Optional[float] = None
            indentation: Optional[float] = None

            def has_any_param(self) -> bool:
                """최소 하나의 파라미터가 설정되었는지 확인"""
                return any([
                    self.font_size is not None,
                    self.font_family is not None,
                    self.align is not None,
                    self.spacing is not None,
                    self.indentation is not None
                ])

        class FormattingParamsBuilder:
            """파라미터 병합 빌더"""
            @staticmethod
            def merge_params(
                direct_params: dict,
                kwargs_params: dict
            ) -> ParagraphFormattingParams:
                """직접 인자와 kwargs를 병합하여 파라미터 객체 생성"""
                return ParagraphFormattingParams(
                    font_size=direct_params.get('font_size') or kwargs_params.get('font_size'),
                    font_family=direct_params.get('font_family') or kwargs_params.get('font_family'),
                    align=direct_params.get('align') or kwargs_params.get('align'),
                    spacing=direct_params.get('spacing') or kwargs_params.get('spacing'),
                    indentation=direct_params.get('indentation') or kwargs_params.get('indentation')
                )

        class FormattingValidator:
            """서식 검증자"""
            @staticmethod
            def validate(params: ParagraphFormattingParams, logger) -> bool:
                """파라미터 유효성 검증"""
                if not params.has_any_param():
                    logger("[ERROR] apply_para_style: 최소 하나의 스타일 파라미터가 필요합니다.")
                    return False
                return True

        class FormattingApplicator:
            """서식 적용자"""
            def __init__(self, hwp, formatter, logger):
                self.hwp = hwp
                self.formatter = formatter
                self.logger = logger

            def apply(
                self,
                position: tuple,
                params: ParagraphFormattingParams,
                block_id: str
            ) -> bool:
                """서식 적용 실행"""
                try:
                    list_pos, para_pos, char_pos = position

                    # 1. 커서 이동
                    self.hwp.set_pos(list_pos, para_pos, char_pos)

                    # 2. 서식 적용 (이미 리팩토링된 메서드 사용)
                    self.formatter(
                        params.font_size,
                        params.font_family,
                        params.align,
                        params.spacing,
                        params.indentation
                    )

                    self.logger(f"[OK] 블록 {block_id}에 문단 스타일 적용 완료")
                    return True

                except Exception as e:
                    self.logger(f"[ERROR] 블록 {block_id}에 문단 스타일 적용 실패: {e}")
                    import traceback
                    traceback.print_exc()
                    return False

        # 메인 로직: 파이프라인 실행
        # 1. 파라미터 병합
        direct_params = {
            'font_size': font_size,
            'font_family': font_family,
            'align': align,
            'spacing': spacing,
            'indentation': indentation
        }
        params = FormattingParamsBuilder.merge_params(direct_params, kwargs)

        # 2. 검증
        if not FormattingValidator.validate(params, self.log_to_main):
            return False

        # 3. 위치 조회
        position = self._retrieve_segment_position(block_id)
        if not position:
            return False

        # 4. 서식 적용
        applicator = FormattingApplicator(
            self.hwp,
            self._apply_formatting_to_current_paragraph,
            self.log_to_main
        )
        return applicator.apply(position, params, block_id)

    def apply_character_formatting(
        self,
        block_id: str,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        font_size: Optional[float] = None,
        font_family: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """블록 ID 기반 글자 스타일 적용 (빌더 + 전략 패턴)"""
        from dataclasses import dataclass
        from typing import Optional

        @dataclass
        class CharacterFormattingParams:
            """글자 서식 파라미터"""
            bold: Optional[bool] = None
            italic: Optional[bool] = None
            font_size: Optional[float] = None
            font_family: Optional[str] = None

            def has_any_param(self) -> bool:
                """최소 하나의 파라미터가 설정되었는지 확인"""
                return any([
                    self.bold is not None,
                    self.italic is not None,
                    self.font_size is not None,
                    self.font_family is not None
                ])

            def has_paragraph_format(self) -> bool:
                """문단 서식 파라미터 존재 여부"""
                return self.font_size is not None or self.font_family is not None

            def has_character_format(self) -> bool:
                """글자 서식 파라미터 존재 여부"""
                return self.bold is not None or self.italic is not None

        class BooleanCoercionStrategy:
            """Boolean 타입 강제 변환 전략"""
            @staticmethod
            def coerce(val: Optional[object]) -> Optional[bool]:
                """다양한 타입을 boolean으로 변환"""
                if val is None:
                    return None
                if isinstance(val, bool):
                    return val
                if isinstance(val, (int, float)):
                    return bool(val)
                if isinstance(val, str):
                    return val.strip().lower() in ("true", "1", "yes", "y")
                return None

        class CharacterParamsBuilder:
            """파라미터 병합 및 타입 변환 빌더"""
            @staticmethod
            def build(
                direct_params: dict,
                kwargs_params: dict,
                coercer: BooleanCoercionStrategy
            ) -> CharacterFormattingParams:
                """직접 인자와 kwargs를 병합하고 boolean 타입 변환"""
                bold = direct_params.get('bold') or kwargs_params.get('bold')
                italic = direct_params.get('italic') or kwargs_params.get('italic')

                return CharacterFormattingParams(
                    bold=coercer.coerce(bold),
                    italic=coercer.coerce(italic),
                    font_size=direct_params.get('font_size') or kwargs_params.get('font_size'),
                    font_family=direct_params.get('font_family') or kwargs_params.get('font_family')
                )

        class CharacterFormattingValidator:
            """서식 검증자"""
            @staticmethod
            def validate(params: CharacterFormattingParams, logger) -> bool:
                """파라미터 유효성 검증"""
                if not params.has_any_param():
                    logger("[ERROR] apply_charshape: 최소 하나의 스타일 파라미터가 필요합니다.")
                    return False
                return True

        class CharacterFormattingApplicator:
            """서식 적용자 (문단 + 글자)"""
            def __init__(self, hwp, paragraph_formatter, logger):
                self.hwp = hwp
                self.paragraph_formatter = paragraph_formatter
                self.logger = logger

            def apply(
                self,
                position: tuple,
                params: CharacterFormattingParams,
                block_id: str
            ) -> bool:
                """서식 적용 실행"""
                try:
                    list_pos, para_pos, char_pos = position
                    self.hwp.set_pos(list_pos, para_pos, char_pos)

                    # 1단계: 문단 서식 적용 (font_size, font_family)
                    if params.has_paragraph_format():
                        self.paragraph_formatter(
                            font_size=params.font_size,
                            font_family=params.font_family
                        )

                    # 2단계: 글자 서식 적용 (bold, italic)
                    if params.has_character_format():
                        self._apply_character_attributes(params)

                    self.logger(f"[OK] 블록 {block_id}에 글자 스타일 적용 완료")
                    return True

                except Exception as e:
                    self.logger(f"[ERROR] 블록 {block_id}에 글자 스타일 적용 실패: {e}")
                    return False

            def _apply_character_attributes(self, params: CharacterFormattingParams):
                """글자 속성 적용 (선택 영역 설정)"""
                font_kwargs = {}
                if params.bold is not None:
                    font_kwargs["Bold"] = params.bold
                if params.italic is not None:
                    font_kwargs["Italic"] = params.italic

                if font_kwargs:
                    self.hwp.MoveParaBegin()
                    self.hwp.MoveSelParaEnd()
                    self.hwp.set_font(**font_kwargs)
                    self.hwp.Cancel()

        # 메인 로직: 파이프라인 실행
        direct_params = {
            'bold': bold,
            'italic': italic,
            'font_size': font_size,
            'font_family': font_family
        }

        coercer = BooleanCoercionStrategy()
        params = CharacterParamsBuilder.build(direct_params, kwargs, coercer)

        if not CharacterFormattingValidator.validate(params, self.log_to_main):
            return False

        position = self._retrieve_segment_position(block_id)
        if not position:
            return False

        applicator = CharacterFormattingApplicator(
            self.hwp,
            self._apply_formatting_to_current_paragraph,
            self.log_to_main
        )
        return applicator.apply(position, params, block_id)

    def _register_segment_modification(self, block_id: str, operation: str, success: bool) -> None:
        """편집 작업 완료 시 해당 세그먼트를 수정 이력에 등록

        Args:
            block_id: 대상 세그먼트 식별자
            operation: 수행된 작업 유형
            success: 작업 성공 플래그
        """
        # 조건 체크를 단일 표현식으로 결합
        modification_ops = frozenset({"replace_", "append_"})
        is_trackable = success and any(operation.startswith(pfx) for pfx in modification_ops)

        if not is_trackable:
            return

        # 블록 ID 정규화 및 등록
        normalized_id = str(block_id) if block_id is not None else ""
        tracking_set = getattr(self, "_edited_blocks", None)

        if tracking_set is not None and normalized_id:
            tracking_set.add(normalized_id)

    def _record_operation_log(
        self,
        index: int,
        operation: str,
        block_id: str,
        position: Optional[Tuple[int, int, int]],
        params: Dict,
        success: bool,
    ) -> None:
        """작업 실행 이력을 메모리와 디스크에 이중 기록

        프로세스 비정상 종료 시에도 복구 가능하도록 즉시 디스크 플러시.
        """
        # 로그 엔트리 구성 (순서 변경으로 직렬화 결과 동일)
        entry_data = self._build_log_entry(
            seq=index,
            op_name=operation,
            target_id=block_id,
            coord=position,
            op_params=params,
            result=success,
        )

        # 1차: 메모리 버퍼에 추가
        self.execution_logs.append(entry_data)

        # 2차: 영속 스토리지에 기록 (비동기 안전)
        self._flush_log_entry_to_disk(entry_data)

    def _build_log_entry(
        self,
        seq: int,
        op_name: str,
        target_id: str,
        coord: Optional[Tuple[int, int, int]],
        op_params: Dict,
        result: bool,
    ) -> Dict:
        """로그 엔트리 딕셔너리 구성"""
        return {
            "index": seq,
            "operation": op_name,
            "block_id": target_id,
            "position": coord,
            "params": op_params,
            "success": result,
        }

    def _flush_log_entry_to_disk(self, entry: Dict) -> None:
        """로그 엔트리를 임시 파일에 즉시 기록

        디렉토리가 없으면 생성하고, JSONL 형식으로 추가 기록.
        """
        log_dest = getattr(self, "_temp_log_path", None)
        if not log_dest:
            return

        from pathlib import Path

        try:
            # pathlib으로 디렉토리 보장
            dest_path = Path(log_dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # JSONL 형식으로 append
            serialized = json.dumps(entry, ensure_ascii=False)
            with dest_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")

        except (OSError, IOError, json.JSONEncodeError):
            # 디스크 기록 실패는 무시 (메모리 버퍼는 유지됨)
            pass

    def _is_development_mode(self) -> bool:
        """개발 환경 여부 판별

        Returns:
            NODE_ENV가 "development"로 설정되어 있으면 True
        """
        env_key = "NODE_ENV"
        dev_indicator = "development"
        current_value = os.environ.get(env_key, "")
        return current_value == dev_indicator

    def reset_session_state(self) -> None:
        """Clear per-run caches to keep memory usage stable."""
        self.execution_logs = []
        self.ai_raw_response = ""
        self.user_instruction = ""
        self.last_insert_info = None
        self.saved_caret_pos = None
        self._table_prev_group_id = None
        self._table_group_op_count = 0
        self._table_prev_block_id = None
        self._table_groups_initialized.clear()
        self._edited_blocks.clear()

    def _persist_execution_summary(self, success_count: int, failed_count: int) -> None:
        """실행 결과 저장 (Builder + Strategy + Template Method)"""
        if not self._is_development_mode():
            return

        from dataclasses import dataclass
        from typing import List, Dict
        from abc import ABC, abstractmethod

        @dataclass
        class ReportContext:
            """리포트 컨텍스트"""
            success_count: int
            failed_count: int
            model_provider: str
            ai_response: str
            execution_logs: List[Dict]

        class DirectoryManager:
            """디렉토리 관리"""
            def __init__(self, base_path: str):
                self.base_path = base_path

            def ensure_directory(self) -> str:
                """디렉토리 생성 보장"""
                experiments_dir = os.path.abspath(
                    os.path.join(os.path.dirname(self.base_path), "..", "experiments")
                )
                if not os.path.exists(experiments_dir):
                    os.makedirs(experiments_dir)
                return os.path.join(experiments_dir, "execution_report.md")

        class MarkdownBuilder:
            """Markdown 빌더"""
            def __init__(self):
                self.lines: List[str] = []

            def add_line(self, line: str) -> 'MarkdownBuilder':
                """줄 추가"""
                self.lines.append(line)
                return self

            def add_lines(self, lines: List[str]) -> 'MarkdownBuilder':
                """여러 줄 추가"""
                self.lines.extend(lines)
                return self

            def build(self) -> str:
                """Markdown 문자열 생성"""
                return "\n".join(self.lines)

        class SectionGenerator(ABC):
            """섹션 생성 전략 (추상)"""
            @abstractmethod
            def generate(self, context: ReportContext) -> List[str]:
                """섹션 생성"""
                pass

        class HeaderSectionGenerator(SectionGenerator):
            """헤더 섹션 생성"""
            def generate(self, context: ReportContext) -> List[str]:
                return [
                    "# AI Document Processing Execution Report",
                    f"\n**Timestamp**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"**Model**: {context.model_provider.upper()}",
                    f"**Results**: ✅ Success: {context.success_count} | ❌ Failed: {context.failed_count}",
                ]

        class AIResponseSectionGenerator(SectionGenerator):
            """AI 응답 섹션 생성"""
            def generate(self, context: ReportContext) -> List[str]:
                response = context.ai_response if context.ai_response else "(No AI response recorded)"
                return [
                    "\n## 🤖 AI Response (Raw)\n",
                    response,
                ]

        class ExecutionLogSectionGenerator(SectionGenerator):
            """실행 로그 섹션 생성"""
            def generate(self, context: ReportContext) -> List[str]:
                lines = ["\n## 🔧 Execution Logs\n"]
                for log in context.execution_logs:
                    lines.extend(self._format_log_entry(log))
                return lines

            def _format_log_entry(self, log: Dict) -> List[str]:
                """로그 항목 포맷팅"""
                status_icon = "✅" if log["success"] else "❌"
                entry_lines = [
                    f"\n### {log['index']}. {log['operation']} - {status_icon}",
                    f"- **Block ID**: {log['block_id']}",
                ]

                if log["position"]:
                    entry_lines.append(
                        f"- **Position**: L:{log['position'][0]}, P:{log['position'][1]}, C:{log['position'][2]}"
                    )

                if log["params"]:
                    entry_lines.append("- **Parameters**:")
                    for key, value in log["params"].items():
                        value_display = value.replace("\n", "\\n") if isinstance(value, str) else value
                        entry_lines.append(f"  - `{key}`: {value_display}")

                entry_lines.append(f"- **Result**: {'Success' if log['success'] else 'Failed'}")
                return entry_lines

        class StatisticsSectionGenerator(SectionGenerator):
            """통계 섹션 생성"""
            def generate(self, context: ReportContext) -> List[str]:
                total = max(len(context.execution_logs), 1)
                lines = [
                    "\n## 📊 Summary Statistics\n",
                    f"- Total Operations: {len(context.execution_logs)}",
                    f"- Successful: {context.success_count} ({context.success_count / total * 100:.1f}%)",
                    f"- Failed: {context.failed_count} ({context.failed_count / total * 100:.1f}%)",
                    "\n### Operations Breakdown",
                ]
                lines.extend(self._generate_breakdown(context.execution_logs))
                return lines

            def _generate_breakdown(self, logs: List[Dict]) -> List[str]:
                """Operation 타입별 breakdown"""
                operation_counts = {}
                for log in logs:
                    op = log["operation"]
                    if op not in operation_counts:
                        operation_counts[op] = {"success": 0, "failed": 0}
                    if log["success"]:
                        operation_counts[op]["success"] += 1
                    else:
                        operation_counts[op]["failed"] += 1

                breakdown_lines = []
                for op, counts in sorted(operation_counts.items()):
                    total = counts["success"] + counts["failed"]
                    breakdown_lines.append(
                        f"- **{op}**: {total} total ({counts['success']} success, {counts['failed']} failed)"
                    )
                return breakdown_lines

        class FileWriter:
            """파일 쓰기"""
            @staticmethod
            def write(filepath: str, content: str):
                """파일에 내용 쓰기"""
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

        class StateCleanup:
            """상태 초기화"""
            def __init__(self, modifier):
                self.modifier = modifier

            def cleanup(self):
                """상태 초기화"""
                self.modifier.execution_logs = []
                self.modifier.ai_raw_response = ""
                self.modifier.user_instruction = ""
                if hasattr(self.modifier, "_edited_blocks"):
                    self.modifier._edited_blocks.clear()

        class ReportPersistencePipeline:
            """리포트 저장 파이프라인"""
            def __init__(self, modifier):
                self.modifier = modifier
                self.dir_manager = DirectoryManager(__file__)
                self.generators = [
                    HeaderSectionGenerator(),
                    AIResponseSectionGenerator(),
                    ExecutionLogSectionGenerator(),
                    StatisticsSectionGenerator(),
                ]

            def execute(self, context: ReportContext) -> str:
                """파이프라인 실행"""
                filepath = self.dir_manager.ensure_directory()
                builder = MarkdownBuilder()

                for generator in self.generators:
                    builder.add_lines(generator.generate(context))

                content = builder.build()
                FileWriter.write(filepath, content)

                StateCleanup(self.modifier).cleanup()
                return filepath

        # Pipeline execution
        try:
            context = ReportContext(
                success_count=success_count,
                failed_count=failed_count,
                model_provider=self.model_provider,
                ai_response=self.ai_raw_response,
                execution_logs=self.execution_logs,
            )
            pipeline = ReportPersistencePipeline(self)
            filepath = pipeline.execute(context)
            self.log_to_main(f"\n📄 실행 결과가 저장되었습니다: {filepath}")

        except Exception as e:
            self.log_to_main(f"❌ 실행 결과 저장 실패: {e}")
            import traceback
            traceback.print_exc()

    # ========================================================================
    # Legacy compatibility bridge (test_hwp_editor / old callers)
    # ========================================================================

    def _has_style_params(self, **kwargs) -> bool:
        """Legacy alias for style-parameter detection."""
        return self._contains_style_parameters(**kwargs)

    def _parse_html_elements(self, text: str) -> List[Dict]:
        """Legacy alias for structural markup parsing."""
        return self._parse_markup_elements(text)

    def _parse_table_html(self, table_html: str) -> List[str]:
        """Legacy alias for table markup parsing."""
        return self._parse_table_markup(table_html)

    def _parse_list_html(self, list_html: str) -> List[str]:
        """Legacy alias for list markup parsing."""
        return self._parse_list_markup(list_html)

    def _insert_with_style(self, text: str) -> None:
        """Legacy markdown-style insertion for old callers/tests."""
        raw_text = "" if text is None else str(text)

        token_pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
        chunks = token_pattern.split(raw_text)

        for chunk in chunks:
            if not chunk:
                continue
            is_bold = chunk.startswith("**") and chunk.endswith("**") and len(chunk) >= 4
            is_italic = (
                chunk.startswith("*")
                and chunk.endswith("*")
                and len(chunk) >= 3
                and not is_bold
            )
            content = chunk[2:-2] if is_bold else (chunk[1:-1] if is_italic else chunk)
            if not content:
                continue

            try:
                if is_bold:
                    self.hwp.set_font(Bold=True)
                elif is_italic:
                    self.hwp.set_font(Italic=True)
            except Exception:
                pass

            self.hwp.insert_text(content)

            try:
                if is_bold:
                    self.hwp.set_font(Bold=False)
                elif is_italic:
                    self.hwp.set_font(Italic=False)
            except Exception:
                pass

    def _apply_style_to_current_paragraph(self, **kwargs) -> bool:
        """Legacy alias for paragraph formatting helper."""
        try:
            self._apply_formatting_to_current_paragraph(**kwargs)
            return True
        except Exception:
            return False

    def replace_paragraph(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """Legacy paragraph replacement path."""
        try:
            if not self.segment_registry:
                return False
            position = self.segment_registry.get_position(str(block_id))
            if not position:
                return False
            # 서식 명시 안 됐으면 기존 서식 백업 → text 교체 → 복원.
            preserve_shape = kwargs.get("font_size") is None and kwargs.get("font_family") is None
            saved_shape = self._backup_charshape_at_block(block_id) if preserve_shape else None

            self.hwp.set_pos(*position)
            self.hwp.find(self.segment_registry.get_text(str(block_id)) or "")
            self._insert_with_style(new_text if new_text is not None else "")

            if saved_shape:
                self._restore_charshape_at_block(block_id, saved_shape)
            return True
        except Exception:
            return False

    def append_paragraph(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """표준 문단 추가 API (이전 위치 호환)."""
        return self._append_paragraph_impl(block_id, new_text, **kwargs)

    def delete_paragraph(self, block_id: str) -> bool:
        """Legacy alias for paragraph delete."""
        return self.remove_paragraph(block_id)

    def replace_list(self, block_id: str, new_text: str = None, **kwargs) -> bool:
        """Legacy alias for list replacement."""
        return self.replace_list_content(block_id, new_text, **kwargs)

    def delete_cell_content(self, block_id: str) -> bool:
        """Legacy alias for clearing a cell."""
        return self.clear_cell_content(block_id)

    def delete_table_row(self, block_id: str) -> bool:
        """Legacy alias for row deletion."""
        return self.remove_table_row(block_id)

    def find_and_replace_in_paragraph(
        self,
        block_id: str,
        old_text: str = None,
        new_text: str = None,
        **kwargs,
    ) -> bool:
        """Legacy paragraph-level find/replace (single-pass compatibility path)."""
        try:
            position = self._retrieve_segment_position(block_id)
            if not position:
                return False
            self.hwp.set_pos(*position)
            if old_text:
                found = self.hwp.find(old_text)
                if found is False:
                    return False
            if new_text is not None:
                self._insert_styled_content(str(new_text))
            return True
        except Exception:
            return False

    def find_and_replace_all(self, old_text: str, new_text: str) -> bool:
        """Legacy global find/replace (single-pass compatibility path)."""
        try:
            if hasattr(self.hwp, "MoveDocBegin"):
                self.hwp.MoveDocBegin()
            if old_text:
                found = self.hwp.find(old_text)
                if found is False:
                    return False
            if new_text is not None:
                self._insert_styled_content(str(new_text))
            return True
        except Exception:
            return False

    def apply_para_style(self, block_id: str, **kwargs) -> bool:
        """Legacy alias for paragraph formatting."""
        return self.apply_paragraph_formatting(block_id, **kwargs)

    def pre_edit_process(self, operation: str, block_id: str, **kwargs) -> bool:
        """Legacy pre-processing entry point used by old tests/callers."""
        op = str(operation or "").strip()
        if not op:
            return False

        if op == "find_and_replace_in_paragraph":
            new_text = kwargs.get("new_text")
            if isinstance(new_text, str):
                normalized = self._normalize_newlines(new_text)
                if "\n" in normalized:
                    return False
                kwargs["new_text"] = normalized

        if "new_text" in kwargs and isinstance(kwargs.get("new_text"), str):
            kwargs["new_text"] = self._normalize_newlines(kwargs["new_text"])
            if "<table" in kwargs["new_text"] or "<list" in kwargs["new_text"]:
                try:
                    self._parse_html_elements(kwargs["new_text"])
                except Exception:
                    pass

        operation_map = {
            "replace_paragraph": self.replace_paragraph,
            "append_paragraph": self.append_paragraph,
            "delete_paragraph": self.delete_paragraph,
            "replace_list": self.replace_list,
            "replace_cell_content": self.replace_cell_content,
            "delete_cell_content": self.delete_cell_content,
            "append_table_row": self.append_table_row,
            "delete_table_row": self.delete_table_row,
            "find_and_replace_in_paragraph": self.find_and_replace_in_paragraph,
            "find_and_replace_all": self.find_and_replace_all,
            "apply_para_style": self.apply_para_style,
        }

        handler = operation_map.get(op)
        if handler is None:
            return False

        try:
            if op == "find_and_replace_all":
                return bool(handler(kwargs.get("old_text"), kwargs.get("new_text")))
            return bool(handler(block_id, **kwargs))
        except Exception:
            return False

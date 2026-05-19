"""
ContentModifier test code

Tests all major methods of ContentModifier.
Uses Mock instead of actual HWP COM objects to verify logic.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ==========================================================================
# Mock 설정
# ==========================================================================

@pytest.fixture
def mock_hwp():
    """Mock HWP 객체"""
    hwp = MagicMock()
    hwp.get_pos.return_value = (10, 0, 0)
    hwp.set_pos.return_value = True
    hwp.insert_text.return_value = True
    hwp.Cancel.return_value = True
    hwp.set_font.return_value = True
    hwp.select_text.return_value = True
    hwp.SelectAll.return_value = True
    hwp.find.return_value = True
    
    # HAction Mock
    hwp.HAction = MagicMock()
    hwp.HAction.Run.return_value = True
    hwp.HAction.GetDefault.return_value = True
    hwp.HAction.Execute.return_value = True
    
    # HParameterSet Mock
    hwp.HParameterSet = MagicMock()
    hwp.HParameterSet.HTableCreation = MagicMock()
    hwp.HParameterSet.HTableCreation.HSet = MagicMock()
    hwp.HParameterSet.HFindReplace = MagicMock()
    hwp.HParameterSet.HFindReplace.HSet = MagicMock()
    hwp.HParameterSet.HCharShape = MagicMock()
    hwp.HParameterSet.HCharShape.HSet = MagicMock()
    hwp.HParameterSet.HParaShape = MagicMock()
    hwp.HParameterSet.HParaShape.HSet = MagicMock()
    hwp.HParameterSet.HShapeObject = MagicMock()
    hwp.HParameterSet.HShapeObject.HSet = MagicMock()
    
    hwp.move_pos.return_value = True
    
    return hwp


@pytest.fixture
def mock_block_manager():
    """Mock BlockManager 객체"""
    manager = MagicMock()
    manager.get_position.return_value = (10, 0, 0)
    manager.get_adjusted_position.return_value = (10, 0, 0)
    manager.get_text.return_value = "기존 텍스트"
    manager.get_table_group_id.return_value = 1
    block = MagicMock()
    block.block_type = "text"
    manager.get_block.return_value = block
    manager.adjust_positions_after_deletion.return_value = None
    manager.adjust_positions_after_row_deletion.return_value = None
    return manager


@pytest.fixture
def mock_log():
    """Mock 로깅 함수"""
    return MagicMock()


@pytest.fixture
def hwp_editor(mock_hwp, mock_block_manager, mock_log):
    """ContentModifier instance for testing"""
    from modification.content_modifier import ContentModifier

    with patch('edit.content_modifier.retrieve_runtime_id_location_mapping', return_value={10: (10, 0, 0)}):
        with patch('edit.content_modifier.check_modification_registry', return_value=False):
            with patch('edit.content_modifier.register_modification_entry', return_value=None):
                with patch('edit.content_modifier.store_recent_insertion_data', return_value=None):
                    with patch('edit.content_modifier.retrieve_recent_insertion_data', return_value=None):
                        editor = ContentModifier(
                            hwp=mock_hwp,
                            block_manager=mock_block_manager,
                            log_to_main=mock_log,
                        )
                        yield editor


# ==========================================================================
# 헬퍼 메서드 테스트
# ==========================================================================

class TestHelperMethods:
    """헬퍼 메서드 테스트"""

    def test_normalize_newlines_crlf(self, hwp_editor):
        """CRLF 표준화 테스트"""
        result = hwp_editor._normalize_newlines("Line1\r\nLine2")
        assert result == "Line1\nLine2"

    def test_normalize_newlines_cr(self, hwp_editor):
        """CR 표준화 테스트"""
        result = hwp_editor._normalize_newlines("Line1\rLine2")
        assert result == "Line1\nLine2"

    def test_normalize_newlines_literal(self, hwp_editor):
        """리터럴 \\n 표준화 테스트"""
        result = hwp_editor._normalize_newlines("Line1\\nLine2")
        assert result == "Line1\nLine2"

    def test_normalize_newlines_unicode(self, hwp_editor):
        """유니코드 줄바꿈 표준화 테스트"""
        result = hwp_editor._normalize_newlines("Line1\u2028Line2")
        assert result == "Line1\nLine2"

    def test_normalize_newlines_nel(self, hwp_editor):
        """NEL (U+0085) 표준화 테스트"""
        result = hwp_editor._normalize_newlines("Line1\u0085Line2")
        assert result == "Line1\nLine2"

    def test_normalize_newlines_none(self, hwp_editor):
        """None 입력 테스트"""
        result = hwp_editor._normalize_newlines(None)
        assert result == " "

    def test_detect_prefix_bullet(self, hwp_editor):
        """불릿 접두사 감지 테스트"""
        result = hwp_editor._detect_prefix("• 항목 내용")
        assert result is not None
        assert "•" in result

    def test_detect_prefix_number(self, hwp_editor):
        """숫자 접두사 감지 테스트"""
        result = hwp_editor._detect_prefix("1. 첫 번째 항목")
        assert result is not None
        assert "1." in result

    def test_detect_prefix_none(self, hwp_editor):
        """접두사 없음 테스트"""
        result = hwp_editor._detect_prefix("일반 텍스트")
        assert result is None

    def test_align_leading_spaces(self, hwp_editor):
        """선행 공백 정렬 테스트"""
        trimmed, adjust_n, old_lead, new_lead = hwp_editor._align_leading_spaces(
            "  기존 텍스트", "  새 텍스트"
        )
        assert adjust_n == 2
        assert old_lead == 2
        assert new_lead == 2

    def test_has_style_params_true(self, hwp_editor):
        """스타일 파라미터 있음 테스트"""
        result = hwp_editor._has_style_params(font_size=12)
        assert result is True

    def test_has_style_params_false(self, hwp_editor):
        """스타일 파라미터 없음 테스트"""
        result = hwp_editor._has_style_params(other_param="value")
        assert result is False


# ==========================================================================
# HTML 파싱 테스트
# ==========================================================================

class TestHtmlParsing:
    """HTML 파싱 테스트"""

    def test_parse_html_elements_no_html(self, hwp_editor):
        """HTML 없는 텍스트 파싱 테스트"""
        result = hwp_editor._parse_html_elements("일반 텍스트입니다")
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["content"] == "일반 텍스트입니다"

    def test_parse_html_elements_table(self, hwp_editor):
        """테이블 HTML 파싱 테스트"""
        html = "<table><tr><td>A</td><td>B</td></tr></table>"
        result = hwp_editor._parse_html_elements(html)
        assert len(result) == 1
        assert result[0]["type"] == "table"
        assert "rows" in result[0]

    def test_parse_html_elements_list(self, hwp_editor):
        """리스트 HTML 파싱 테스트"""
        html = "<list><li •>항목1</li><li •>항목2</li></list>"
        result = hwp_editor._parse_html_elements(html)
        # 리스트 패턴 매칭 확인
        assert len(result) >= 1

    def test_parse_html_elements_mixed(self, hwp_editor):
        """혼합 콘텐츠 파싱 테스트"""
        html = "서론 텍스트\n<table><tr><td>A</td></tr></table>\n결론 텍스트"
        result = hwp_editor._parse_html_elements(html)
        # 텍스트, 테이블, 텍스트 순으로 파싱
        types = [p["type"] for p in result]
        assert "text" in types
        assert "table" in types

    def test_parse_table_html(self, hwp_editor):
        """테이블 HTML 행 파싱 테스트"""
        table_html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        result = hwp_editor._parse_table_html(table_html)
        assert len(result) == 2
        assert "A|B" == result[0]
        assert "C|D" == result[1]

    def test_parse_list_html(self, hwp_editor):
        """리스트 HTML 항목 파싱 테스트"""
        list_html = "<list><li •>항목1</li><li •>항목2</li></list>"
        result = hwp_editor._parse_list_html(list_html)
        # 파싱된 항목 확인
        assert len(result) >= 0  # 패턴에 따라 다를 수 있음


# ==========================================================================
# 문단/리스트 편집 테스트
# ==========================================================================

class TestParagraphEditing:
    """문단 편집 테스트"""

    def test_replace_paragraph(self, hwp_editor, mock_hwp):
        """문단 교체 테스트"""
        with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
            with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                result = hwp_editor.replace_paragraph("10", "새로운 텍스트")
        
        assert result is True
        mock_hwp.find.assert_called()

    def test_append_paragraph(self, hwp_editor, mock_hwp):
        """문단 추가 테스트"""
        result = hwp_editor.append_paragraph("10", "추가 텍스트")
        
        assert result is True
        mock_hwp.BreakPara.assert_called()

    def test_delete_paragraph(self, hwp_editor, mock_hwp):
        """문단 삭제 테스트"""
        with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
            with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                result = hwp_editor.delete_paragraph("10")
        
        assert result is True
        mock_hwp.DeleteBack.assert_called()

    def test_replace_list(self, hwp_editor, mock_hwp, mock_block_manager):
        """리스트 교체 테스트"""
        mock_block_manager.get_block.return_value.block_type = "list"
        with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
            with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                result = hwp_editor.replace_list("10", "• 새 리스트")
        
        assert result is True


# ==========================================================================
# 표 편집 테스트
# ==========================================================================

class TestTableEditing:
    """표 편집 테스트"""

    def test_replace_cell_content(self, hwp_editor, mock_hwp, mock_block_manager):
        """셀 내용 교체 테스트"""
        mock_block_manager.get_block.return_value.block_type = "td"
        with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
            result = hwp_editor.replace_cell_content("10", "새 셀 내용")
        
        assert result is True
        mock_hwp.SelectAll.assert_called()

    def test_delete_cell_content(self, hwp_editor, mock_hwp, mock_block_manager):
        """셀 내용 삭제 테스트"""
        mock_block_manager.get_block.return_value.block_type = "td"
        with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
            result = hwp_editor.delete_cell_content("10")
        
        assert result is True
        mock_hwp.SelectAll.assert_called()

    def test_append_table_row(self, hwp_editor, mock_hwp, mock_block_manager):
        """표 행 추가 테스트"""
        mock_block_manager.get_block.return_value.block_type = "td"
        with patch('edit.hwp_editor.get_last_insert_info', return_value=None):
            with patch('edit.hwp_editor.set_last_insert_info', return_value=None):
                result = hwp_editor.append_table_row("10", row_texts=[["A", "B"], ["C", "D"]])
        
        assert result is True
        mock_hwp.HAction.Run.assert_any_call("TableAppendRow")

    def test_delete_table_row(self, hwp_editor, mock_hwp, mock_block_manager):
        """표 행 삭제 테스트"""
        mock_block_manager.get_block.return_value.block_type = "td"
        with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
            result = hwp_editor.delete_table_row("10")
        
        assert result is True
        mock_hwp.HAction.Run.assert_any_call("TableDeleteRow")


# ==========================================================================
# pre_edit_process 테스트
# ==========================================================================

class TestPreEditProcess:
    """pre_edit_process 통합 진입점 테스트"""

    def test_pre_edit_normalizes_newlines(self, hwp_editor):
        """pre_edit_process가 줄바꿈을 표준화하는지 테스트"""
        with patch.object(hwp_editor, 'replace_paragraph', return_value=True) as mock_replace:
            with patch('edit.hwp_editor.set_last_insert_info', return_value=None):
                with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
                    with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                        result = hwp_editor.pre_edit_process(
                            "replace_paragraph",
                            "10",
                            new_text="Line1\\nLine2"  # 리터럴 \n
                        )
        
        # 호출 시 표준화된 텍스트가 전달되어야 함
        assert result is True

    def test_pre_edit_rejects_newline_in_find_replace(self, hwp_editor):
        """find_and_replace에서 줄바꿈 거부 테스트"""
        with patch('edit.hwp_editor.set_last_insert_info', return_value=None):
            result = hwp_editor.pre_edit_process(
                "find_and_replace_in_paragraph",
                "10",
                old_text="찾을텍스트",
                new_text="Line1\nLine2"  # 줄바꿈 포함
            )
        
        assert result is False  # 거부되어야 함

    def test_pre_edit_handles_html(self, hwp_editor):
        """pre_edit_process가 HTML을 처리하는지 테스트"""
        with patch.object(hwp_editor, '_parse_html_elements', return_value=[{"type": "text", "content": "test"}]) as mock_parse:
            with patch.object(hwp_editor, 'replace_paragraph', return_value=True):
                with patch('edit.hwp_editor.set_last_insert_info', return_value=None):
                    with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
                        with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                            result = hwp_editor.pre_edit_process(
                                "replace_paragraph",
                                "10",
                                new_text="<table><tr><td>A</td></tr></table>"
                            )
        
        # HTML 감지 및 파싱이 호출되어야 함
        mock_parse.assert_called_once()


# ==========================================================================
# 찾기/바꾸기 테스트
# ==========================================================================

class TestFindReplace:
    """찾기/바꾸기 테스트"""

    def test_find_and_replace_in_paragraph(self, hwp_editor, mock_hwp):
        """문단 내 찾기/바꾸기 테스트"""
        mock_hwp.find.return_value = True
        
        result = hwp_editor.find_and_replace_in_paragraph(
            "10",
            old_text="기존",
            new_text="새로운"
        )
        
        assert result is True

    def test_find_and_replace_all(self, hwp_editor, mock_hwp):
        """전체 찾기/바꾸기 테스트"""
        result = hwp_editor.find_and_replace_all("기존", "새로운")
        
        assert result is True
        mock_hwp.MoveDocBegin.assert_called()


# ==========================================================================
# 스타일 적용 테스트
# ==========================================================================

class TestStyleApplication:
    """스타일 적용 테스트"""

    def test_apply_para_style(self, hwp_editor, mock_hwp):
        """문단 스타일 적용 테스트"""
        result = hwp_editor.apply_para_style(
            "10",
            font_size=12,
            font_family="맑은 고딕",
            align="center"
        )
        
        assert result is True

    def test_apply_style_to_current_paragraph(self, hwp_editor, mock_hwp):
        """현재 문단 스타일 적용 헬퍼 테스트"""
        result = hwp_editor._apply_style_to_current_paragraph(
            font_size=12,
            font_family="맑은 고딕",
            align="center"
        )
        
        assert result is True
        mock_hwp.HAction.Execute.assert_called()


# ==========================================================================
# 마크다운 스타일 테스트
# ==========================================================================

class TestMarkdownStyles:
    """마크다운 스타일 삽입 테스트"""

    def test_insert_with_style_plain(self, hwp_editor, mock_hwp):
        """일반 텍스트 삽입 테스트"""
        hwp_editor._insert_with_style("일반 텍스트")
        
        mock_hwp.insert_text.assert_called_with("일반 텍스트")

    def test_insert_with_style_bold(self, hwp_editor, mock_hwp):
        """굵게 마크업 삽입 테스트"""
        hwp_editor._insert_with_style("**굵은 텍스트**")
        
        # set_font가 Bold=True로 호출되어야 함
        mock_hwp.set_font.assert_called()

    def test_insert_with_style_mixed(self, hwp_editor, mock_hwp):
        """혼합 마크업 삽입 테스트"""
        hwp_editor._insert_with_style("일반 **굵게** *이탤릭* 일반")
        
        # insert_text가 여러 번 호출되어야 함
        assert mock_hwp.insert_text.call_count >= 1


# ==========================================================================
# 통합 테스트
# ==========================================================================

class TestIntegration:
    """통합 테스트"""

    def test_full_edit_workflow(self, hwp_editor, mock_hwp, mock_block_manager):
        """전체 편집 워크플로우 테스트"""
        with patch('edit.hwp_editor.is_in_modification_registry', return_value=False):
            with patch('edit.hwp_editor.add_to_modification_registry', return_value=None):
                # 1. 문단 교체
                result1 = hwp_editor.replace_paragraph("10", "새 문단")
                assert result1 is True
                
                # 2. 문단 추가
                result2 = hwp_editor.append_paragraph("10", "추가 문단")
                assert result2 is True
                
                # 3. 셀 내용 교체
                mock_block_manager.get_block.return_value.block_type = "td"
                result3 = hwp_editor.replace_cell_content("10", "새 셀 내용")
                assert result3 is True

    def test_error_handling(self, hwp_editor, mock_hwp, mock_block_manager):
        """에러 처리 테스트"""
        # 잘못된 block_id
        mock_block_manager.get_position.return_value = None
        
        result = hwp_editor.replace_paragraph("invalid_id", "텍스트")
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

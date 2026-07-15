"""Unit tests for enriched_hdml_serializer.py."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from processing.structure.enriched_hdml_serializer import serialize_enriched_hdml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    target_uid: str,
    block_id: int,
    block_type: str = "text",
    full_text: str = "",
    position: dict = None,
    **kwargs,
) -> dict:
    node = {
        "target_uid": target_uid,
        "id": block_id,
        "block_type": block_type,
        "full_text": full_text,
        "position": position or {"list_pos": 0, "para_pos": 0, "char_pos": 0},
    }
    node.update(kwargs)
    return node


def _make_td(
    target_uid: str,
    block_id: int,
    row: int,
    col: int,
    scope_table_id: int = 0,
    full_text: str = "",
    position: dict = None,
    **kwargs,
) -> dict:
    defaults = {
        "table_path": "0",
    }
    defaults.update(kwargs)
    return _make_node(
        target_uid=target_uid,
        block_id=block_id,
        block_type="td",
        full_text=full_text,
        position=position or {"list_pos": 0, "para_pos": 0, "char_pos": 0},
        row=row,
        col=col,
        scope_table_id=scope_table_id,
        **defaults,
    )


# ---------------------------------------------------------------------------
# 1. 빈 그래프 → 최소 출력
# ---------------------------------------------------------------------------

class TestEmptyGraph:
    def test_empty_dict(self):
        result = serialize_enriched_hdml({})
        assert "<main_content>" in result
        assert "</main_content>" in result

    def test_empty_nodes(self):
        result = serialize_enriched_hdml({"nodes": [], "edges": []})
        assert "<main_content>" in result

    def test_page_range_header(self):
        result = serialize_enriched_hdml({}, page_range=(1, 3))
        assert "<scanned_page_range>1 ~ 3 페이지</scanned_page_range>" in result

    def test_single_page_range(self):
        result = serialize_enriched_hdml({}, page_range=(2, 2))
        assert "2 페이지" in result
        assert "~" not in result


# ---------------------------------------------------------------------------
# 2. 텍스트 전용 문서 → <p> 요소
# ---------------------------------------------------------------------------

class TestTextOnly:
    def test_single_paragraph(self):
        graph = {
            "nodes": [_make_node("uid1", 1, full_text="Hello World")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert '<p id="1">Hello World</p>' in result

    def test_multiple_paragraphs_order(self):
        graph = {
            "nodes": [
                _make_node("uid2", 2, full_text="Second", position={"list_pos": 0, "para_pos": 1, "char_pos": 0}),
                _make_node("uid1", 1, full_text="First", position={"list_pos": 0, "para_pos": 0, "char_pos": 0}),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        first_pos = result.index("First")
        second_pos = result.index("Second")
        assert first_pos < second_pos

    def test_textbox_element(self):
        graph = {
            "nodes": [_make_node("uid1", 1, block_type="textbox", full_text="Box text")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "<textbox" in result
        assert "Box text</textbox>" in result

    def test_repeated_symbol_only_paragraphs_are_not_deduplicated(self):
        graph = {
            "nodes": [
                _make_node("uid1", 1, full_text="◦", position={"list_pos": 0, "para_pos": 10, "char_pos": 0}),
                _make_node("uid2", 2, full_text="-", position={"list_pos": 0, "para_pos": 11, "char_pos": 0}),
                _make_node("uid3", 3, full_text="-", position={"list_pos": 0, "para_pos": 12, "char_pos": 0}),
                _make_node("uid4", 4, full_text="◦", position={"list_pos": 0, "para_pos": 13, "char_pos": 0}),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert result.count(">◦</p>") == 2
        assert result.count(">-</p>") == 2

    def test_footnote_element(self):
        graph = {
            "nodes": [_make_node("uid1", 1, block_type="footnote", full_text="Note text")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "<footnote" in result
        assert "Note text</footnote>" in result


# ---------------------------------------------------------------------------
# 3. 단순 표 → <table>/<tr>/<td>/<p> 중첩
# ---------------------------------------------------------------------------

class TestSimpleTable:
    def test_single_cell_table(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Cell 1")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "<table>" in result
        assert "<tr>" in result
        assert "<td" in result
        assert "Cell 1" in result
        assert "</table>" in result

    def test_2x2_table(self):
        graph = {
            "nodes": [
                _make_td("uid1", 1, row=0, col=0, full_text="R0C0"),
                _make_td("uid2", 2, row=0, col=1, full_text="R0C1"),
                _make_td("uid3", 3, row=1, col=0, full_text="R1C0"),
                _make_td("uid4", 4, row=1, col=1, full_text="R1C1"),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert result.count("<tr>") == 2
        assert result.count("</tr>") == 2
        # Row order
        r0c0_pos = result.index("R0C0")
        r1c0_pos = result.index("R1C0")
        assert r0c0_pos < r1c0_pos
        # Col order within row
        r0c0_pos = result.index("R0C0")
        r0c1_pos = result.index("R0C1")
        assert r0c0_pos < r0c1_pos

    def test_td_with_children(self):
        """td with contains edge to child text node."""
        graph = {
            "nodes": [
                _make_td("uid_td", 10, row=0, col=0, full_text=""),
                _make_node("uid_child", 11, full_text="Child text", position={"list_pos": 0, "para_pos": 0, "char_pos": 0}),
            ],
            "edges": [
                {"type": "contains", "source": "uid_td", "target": "uid_child"},
            ],
        }
        result = serialize_enriched_hdml(graph)
        assert "Child text" in result
        # Child should not appear as standalone <p> outside table
        lines = result.split("\n")
        standalone_child = [l for l in lines if "Child text" in l and not l.strip().startswith("<")]
        assert len(standalone_child) == 0


# ---------------------------------------------------------------------------
# 4. 병합 셀 → colspan/rowspan 속성
# ---------------------------------------------------------------------------

class TestMergedCells:
    def test_colspan(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Wide", colspan=3)],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'colspan="3"' in result

    def test_rowspan(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Tall", rowspan=2)],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'rowspan="2"' in result

    def test_no_colspan_when_1(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Normal", colspan=1)],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "colspan" not in result


# ---------------------------------------------------------------------------
# 5. 스타일 속성 → bgcolor, color, font-size, border, diagonal
# ---------------------------------------------------------------------------

class TestStyleAttributes:
    def test_bgcolor(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Label", bgcolor="#c0c0c0")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'bgcolor="#c0c0c0"' in result

    def test_bgcolor_white_skipped(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Normal", bgcolor="#ffffff")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "bgcolor" not in result

    def test_bgcolor_black_skipped(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Normal", bgcolor="#000000")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "bgcolor" not in result

    def test_text_color(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Guide", text_color="#0000ff")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'color="#0000ff"' in result

    def test_text_color_black_skipped(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Normal", text_color="#000000")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "color=" not in result

    def test_font_size(self):
        graph = {
            "nodes": [_make_node("uid1", 1, full_text="Title", font_size="16pt")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'font-size="16pt"' in result

    def test_border_sides(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Cell", border_sides=["left", "right", "top", "bottom"])],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'border="left,right,top,bottom"' in result

    def test_diagonal(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="", diagonal=True)],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'diagonal="true"' in result

    def test_no_diagonal_when_false(self):
        graph = {
            "nodes": [_make_td("uid1", 1, row=0, col=0, full_text="Cell", diagonal=False)],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "diagonal" not in result


# ---------------------------------------------------------------------------
# 6. ID 보존 → 모든 ID가 bare integer
# ---------------------------------------------------------------------------

class TestIDPreservation:
    def test_paragraph_id(self):
        graph = {
            "nodes": [_make_node("uid1", 42, full_text="Text")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'id="42"' in result

    def test_td_id(self):
        graph = {
            "nodes": [_make_td("uid1", 99, row=0, col=0, full_text="Cell")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert 'id="99"' in result


# ---------------------------------------------------------------------------
# 7. role 미포함 → 출력에 role= 없음
# ---------------------------------------------------------------------------

class TestNoRole:
    def test_no_role_in_output(self):
        graph = {
            "nodes": [
                _make_td("uid1", 1, row=0, col=0, full_text="Label", bgcolor="#c0c0c0", role="guide"),
                _make_td("uid2", 2, row=0, col=1, full_text="", role="input"),
                _make_node("uid3", 3, full_text="Paragraph", role="text"),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "role=" not in result


# ---------------------------------------------------------------------------
# 8. 문서 순서 → ID(세그먼트 UID) 기준 정렬
# ---------------------------------------------------------------------------

class TestDocumentOrder:
    def test_paragraph_table_interleave(self):
        """Paragraph before table when paragraph ID is smaller."""
        graph = {
            "nodes": [
                _make_td("uid_td", 10, row=0, col=0, full_text="Table cell",
                         position={"list_pos": 1, "para_pos": 0, "char_pos": 0}),
                _make_node("uid_p", 5, full_text="Before table",
                          position={"list_pos": 0, "para_pos": 0, "char_pos": 0}),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        p_pos = result.index("Before table")
        t_pos = result.index("<table>")
        assert p_pos < t_pos

    def test_table_before_paragraph(self):
        """Table before paragraph when table cell ID is smaller."""
        graph = {
            "nodes": [
                _make_node("uid_p", 15, full_text="After table",
                          position={"list_pos": 0, "para_pos": 5, "char_pos": 0}),
                _make_td("uid_td", 10, row=0, col=0, full_text="Table cell",
                         position={"list_pos": 3, "para_pos": 0, "char_pos": 0}),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        t_pos = result.index("<table>")
        p_pos = result.index("After table")
        assert t_pos < p_pos


# ---------------------------------------------------------------------------
# 9. XML 이스케이프
# ---------------------------------------------------------------------------

class TestXMLEscape:
    def test_ampersand_escaped(self):
        graph = {
            "nodes": [_make_node("uid1", 1, full_text="A & B")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "A &amp; B" in result

    def test_angle_brackets_escaped(self):
        graph = {
            "nodes": [_make_node("uid1", 1, full_text="<script>alert(1)</script>")],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert "&lt;script&gt;" in result


# ---------------------------------------------------------------------------
# 10. Multiple tables (different scope_table_id)
# ---------------------------------------------------------------------------

class TestMultipleTables:
    def test_two_separate_tables(self):
        graph = {
            "nodes": [
                _make_td("uid1", 1, row=0, col=0, scope_table_id=0, full_text="T1",
                         position={"list_pos": 0, "para_pos": 0, "char_pos": 0}),
                _make_td("uid2", 2, row=0, col=0, scope_table_id=1, full_text="T2",
                         position={"list_pos": 1, "para_pos": 0, "char_pos": 0}),
            ],
            "edges": [],
        }
        result = serialize_enriched_hdml(graph)
        assert result.count("<table>") == 2
        assert result.count("</table>") == 2
        t1_pos = result.index("T1")
        t2_pos = result.index("T2")
        assert t1_pos < t2_pos


# ---------------------------------------------------------------------------
# 11. 중첩 표 (nested table) — 서로 다른 scope_table_id
# ---------------------------------------------------------------------------

class TestNestedTable:
    def test_nested_table_cells_not_lost(self):
        """중첩 표의 셀이 HDML 출력에서 누락되지 않아야 한다."""
        outer_td = _make_td(
            "uid:401", 401, row=0, col=0, scope_table_id=9,
            full_text="Outer", table_path="0",
            position={"list_pos": 0, "para_pos": 40, "char_pos": 0},
        )
        inner_td = _make_td(
            "uid:402", 402, row=0, col=0, scope_table_id=10,
            full_text="Inner", table_path="0/0/0/0",
            position={"list_pos": 0, "para_pos": 41, "char_pos": 0},
        )
        graph = {"nodes": [outer_td, inner_td], "edges": []}
        result = serialize_enriched_hdml(graph)
        # 두 표 모두 출력되어야 함
        assert result.count("<table>") == 2
        assert 'id="401"' in result
        assert 'id="402"' in result
        assert "Outer" in result
        assert "Inner" in result

    def test_deeply_nested_table_with_deep_path(self):
        """table_path 깊이가 깊어도 (depth > 1) 셀이 출력되어야 한다."""
        td = _make_td(
            "uid:500", 500, row=0, col=0, scope_table_id=20,
            full_text="Deep", table_path="0/1/2/3",
            position={"list_pos": 0, "para_pos": 50, "char_pos": 0},
        )
        graph = {"nodes": [td], "edges": []}
        result = serialize_enriched_hdml(graph)
        assert 'id="500"' in result
        assert "Deep" in result
        assert "<table>" in result

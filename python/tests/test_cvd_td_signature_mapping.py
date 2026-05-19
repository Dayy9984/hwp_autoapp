import os
import re
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from processing.extraction.cvd_extractor import CVDExtractor, clean_table_html


def test_clean_table_html_emits_table_path_and_span_meta():
    html = '<table><tr><td>A</td><td colspan="2">B</td></tr></table>'
    between = [
        {"type": "paragraph", "pos": (0, 0, 0), "text": ""},
        {"type": "paragraph", "pos": (1, 1, 1), "text": "A"},
        {"type": "paragraph", "pos": (2, 1, 2), "text": "B"},
        {"type": "paragraph", "pos": (3, 1, 3), "text": "TAIL"},
    ]

    _, _, td_metas = clean_table_html(html, [], between, [])

    assert len(td_metas) == 2
    assert td_metas[0]["table_path"] == (0,)
    assert td_metas[0]["row"] == 0 and td_metas[0]["col"] == 0
    assert td_metas[1]["table_path"] == (0,)
    assert td_metas[1]["colspan"] == 2


def test_cvd_td_mapping_uses_row_col_not_meta_order():
    ex = CVDExtractor(None)
    ex.extracted_elements = [
        {
            "type": "table",
            "html": "<table><tr><td>X</td><td>Y</td></tr></table>",
            "td_metas": [
                {
                    "type": "td",
                    "pos": (0, 11, 1),
                    "row": 0,
                    "col": 1,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0,),
                    "inner_metas": [{"type": "paragraph", "pos": (0, 11, 1), "text": "Y"}],
                },
                {
                    "type": "td",
                    "pos": (0, 10, 1),
                    "row": 0,
                    "col": 0,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0,),
                    "inner_metas": [{"type": "paragraph", "pos": (0, 10, 1), "text": "X"}],
                },
            ],
        }
    ]

    result = ex._extract_content_and_id_to_pos_from_extracted_elements()
    assert result is not None
    cvd, id_map = result

    td_ids = re.findall(r'<td id="(\d+)"', cvd)
    assert len(td_ids) == 2
    assert id_map[int(td_ids[0])] == (0, 10, 1)
    assert id_map[int(td_ids[1])] == (0, 11, 1)


def test_cvd_nested_table_path_flow_keeps_two_td_signatures():
    ex = CVDExtractor(None)
    ex.extracted_elements = [
        {
            "type": "table",
            "html": "<table><tr><td>O<table><tr><td>I</td></tr></table></td></tr></table>",
            "td_metas": [
                {
                    "type": "td",
                    "pos": (1, 1, 2),
                    "row": 0,
                    "col": 0,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0, 0, 0, 0),
                    "inner_metas": [{"type": "paragraph", "pos": (1, 1, 2), "text": "I"}],
                },
                {
                    "type": "td",
                    "pos": (0, 1, 1),
                    "row": 0,
                    "col": 0,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0,),
                    "inner_metas": [
                        {"type": "paragraph", "pos": (0, 1, 1), "text": "O"},
                        {
                            "type": "nested_table",
                            "pos": (1, 1, 1),
                            "table_path": (0, 0, 0, 0),
                            "html": "<table><tr><td>I</td></tr></table>",
                        },
                    ],
                },
            ],
        }
    ]

    result = ex._extract_content_and_id_to_pos_from_extracted_elements()
    assert result is not None
    cvd, id_map = result
    assert cvd.count("data-td-sig=") == 2

    td_ids = re.findall(r'<td id="(\d+)"', cvd)
    assert len(td_ids) == 2
    assert id_map[int(td_ids[0])] == (0, 1, 1)
    assert id_map[int(td_ids[1])] == (1, 1, 2)

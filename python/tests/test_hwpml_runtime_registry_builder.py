from processing.structure.hwpml_runtime_registry_builder import (
    build_segment_registry_from_extractor,
)


class _FakeExtractor:
    def __init__(self):
        self.global_id = 1
        self.id_to_pos = {}
        self.cell_style_map = {
            (0, 0, 0): {
                "bgcolor": "#ffcc00",
                "border_sides": ["left", "right", "top", "bottom"],
                "border_complete": True,
                "diagonal": True,
                "diagonal_flags": ["slash"],
            }
        }
        self.extracted_elements = [
            {
                "type": "table",
                "index": 0,
                "td_metas": [
                    {
                        "pos": (10, 1, 0),
                        "row": 0,
                        "col": 0,
                        "rowspan": 1,
                        "colspan": 1,
                        "table_path": (0,),
                        "inner_metas": [
                            {"type": "paragraph", "pos": (10, 2, 0), "text": "셀 본문"},
                            {"type": "list", "pos": (10, 3, 0), "text": "항목", "heading_text": "•"},
                        ],
                    }
                ],
            },
            {"type": "paragraph", "pos": (20, 1, 0), "text": "문단"},
            {"type": "textbox", "pos": (30, 1, 0), "text": "텍스트박스"},
        ]

    def _map_id_to_pos(self, pos):
        sid = self.global_id
        self.global_id += 1
        self.id_to_pos[sid] = tuple(pos)
        return sid

    def _build_td_signature(self, table_index, meta):
        return f"tdv1:{table_index}:{meta.get('row')}:{meta.get('col')}"

    def _build_paragraph_signature(self, pos, text):
        return f"pv1:{pos[0]}:{len(text or '')}"

    def _get_style_attrs_from_pos(self, pos):
        return f' data-page="{int(pos[0]) // 10 + 1}" color="#112233" font-size="11pt"'


def test_build_runtime_registry_from_extractor_without_cvd_text():
    extractor = _FakeExtractor()
    registry, id_to_pos = build_segment_registry_from_extractor(extractor)

    assert registry is not None
    assert len(id_to_pos) >= 5

    td_blocks = [seg for seg in registry.segments.values() if seg.segment_type == "td"]
    assert len(td_blocks) == 1
    td = td_blocks[0]
    assert td.td_sig == "tdv1:0:0:0"
    assert td.attrs.get("data-row") == "0"
    assert td.attrs.get("data-col") == "0"
    assert td.attrs.get("bgcolor") == "#ffcc00"
    assert td.attrs.get("data-diagonal") == "1"
    assert td.table_path == "0"

    text_blocks = [seg for seg in registry.segments.values() if seg.segment_type == "text"]
    assert any(seg.text == "셀 본문" and seg.p_sig for seg in text_blocks)
    assert any(seg.text == "문단" and seg.p_sig for seg in text_blocks)

    list_blocks = [seg for seg in registry.segments.values() if seg.segment_type == "list"]
    assert len(list_blocks) == 1
    assert list_blocks[0].attrs.get("heading-text") == "•"

    textbox_blocks = [seg for seg in registry.segments.values() if seg.segment_type == "textbox"]
    assert len(textbox_blocks) == 1

    # list_pos 기반 그룹 인덱스가 재구성되어야 한다.
    td_group = registry.get_table_group_id(str(td.id))
    assert td_group is not None
    assert registry.get_table_group_id_by_list_pos(td.list_pos) == td_group

from processing.structure.hwpml_direct_graph_builder import build_document_graph_from_hwpml
from processing.structure.segment_registry import ContentSegment


class _FakeBlockManager:
    def __init__(self, segments):
        self.segments = {str(seg.id): seg for seg in segments}


def test_hwpml_direct_graph_uses_runtime_ids_and_cell_roles():
    # list_pos differentiates containers: table cells share one list_pos
    # (e.g. 0) while standalone body paragraphs use a different list_pos (1).
    seg_td_1 = ContentSegment(
        segment_id="101",
        position=(0, 10, 0),
        text="",
        segment_type="td",
        table_group_id=7,
        td_sig="td-a",
        p_sig="p-a",
        table_path="1/1",
    )
    seg_td_2 = ContentSegment(
        segment_id="102",
        position=(0, 11, 0),
        text="",
        segment_type="td",
        table_group_id=7,
        td_sig="td-b",
        p_sig="p-b",
        table_path="1/1",
    )
    seg_text = ContentSegment(
        segment_id="201",
        position=(1, 0, 0),
        text="기존 문단",
        segment_type="text",
    )
    manager = _FakeBlockManager([seg_td_1, seg_td_2, seg_text])

    hwpml_text = """
<HWPML>
  <BORDERFILLLIST>
    <BORDERFILL Id="10" Diagonal="1" />
    <BORDERFILL Id="11"><WINBRUSH FaceColor="255" /></BORDERFILL>
  </BORDERFILLLIST>
  <BODY>
    <SECTION>
      <P>
        <TEXT>
          <TABLE>
            <ROW>
              <CELL BorderFill="10"><PARALIST><P><TEXT><CHAR>첫째</CHAR></TEXT></P></PARALIST></CELL>
              <CELL BorderFill="11"><PARALIST><P><TEXT><CHAR>둘째</CHAR></TEXT></P></PARALIST></CELL>
            </ROW>
          </TABLE>
        </TEXT>
      </P>
      <P><TEXT><CHAR>본문 문단</CHAR></TEXT></P>
    </SECTION>
  </BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(2, 6),
        page_by_pos={(0, 10, 0): 3, (0, 11, 0): 3, (1, 0, 0): 4},
    )

    nodes = graph.get("nodes") or []
    assert len(nodes) == 3
    assert [node["id"] for node in nodes] == [101, 102, 201]

    td_nodes = [node for node in nodes if node.get("block_type") == "td"]
    assert len(td_nodes) == 2
    assert td_nodes[0]["diagonal"] is True
    assert td_nodes[1]["bgcolor"] == "#0000ff"
    assert td_nodes[0]["row"] == 0 and td_nodes[0]["col"] == 0
    assert td_nodes[1]["row"] == 0 and td_nodes[1]["col"] == 1
    assert td_nodes[0]["page"] == 3 and td_nodes[1]["page"] == 3

    text_nodes = [node for node in nodes if node.get("block_type") == "text"]
    assert len(text_nodes) == 1
    assert text_nodes[0]["page"] == 4
    assert "본문 문단" in (text_nodes[0].get("preview_text") or "")


def test_hwpml_direct_graph_keeps_full_text_without_preview_truncation():
    long_text = "가" * 320

    seg_td = ContentSegment(
        segment_id="301",
        position=(0, 30, 0),
        text="",
        segment_type="td",
        table_group_id=5,
        td_sig="td-long",
        p_sig="p-long",
        table_path="1/1",
        attrs={"data-row": "0", "data-col": "0"},
    )
    manager = _FakeBlockManager([seg_td])

    hwpml_text = f"""
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE><ROW>
      <CELL><PARALIST><P><TEXT><CHAR>{long_text}</CHAR></TEXT></P></PARALIST></CELL>
    </ROW></TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(1, 5),
    )

    nodes = graph.get("nodes") or []
    assert len(nodes) == 1
    node = nodes[0]
    assert node.get("block_type") == "td"
    assert node.get("row") == 0 and node.get("col") == 0
    assert node.get("preview_text") == long_text
    assert node.get("full_text") == long_text


def test_hwpml_direct_graph_table_path_and_col_index_match_cvd_rules():
    seg_outer = ContentSegment(
        segment_id="401",
        position=(0, 40, 0),
        text="",
        segment_type="td",
        table_group_id=9,
        td_sig="td-outer",
        p_sig="p-outer",
        table_path="0",
        attrs={"data-row": "0", "data-col": "0", "data-table-path": "0"},
    )
    seg_inner = ContentSegment(
        segment_id="402",
        position=(0, 41, 0),
        text="",
        segment_type="td",
        table_group_id=10,
        td_sig="td-inner",
        p_sig="p-inner",
        table_path="0/0/0/0",
        attrs={"data-row": "0", "data-col": "0", "data-table-path": "0/0/0/0"},
    )
    seg_colspan_a = ContentSegment(
        segment_id="403",
        position=(0, 42, 0),
        text="",
        segment_type="td",
        table_group_id=11,
        td_sig="td-c1",
        p_sig="p-c1",
        table_path="1",
        attrs={"data-row": "0", "data-col": "0", "data-table-path": "1"},
    )
    seg_colspan_b = ContentSegment(
        segment_id="404",
        position=(0, 43, 0),
        text="",
        segment_type="td",
        table_group_id=11,
        td_sig="td-c2",
        p_sig="p-c2",
        table_path="1",
        attrs={"data-row": "0", "data-col": "1", "data-table-path": "1"},
    )
    manager = _FakeBlockManager([seg_outer, seg_inner, seg_colspan_a, seg_colspan_b])

    hwpml_text = """
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE>
      <ROW>
        <CELL>
          <PARALIST><P><TEXT><CHAR>O</CHAR></TEXT></P></PARALIST>
          <PARALIST><P><TEXT><TABLE><ROW><CELL><PARALIST><P><TEXT><CHAR>I</CHAR></TEXT></P></PARALIST></CELL></ROW></TABLE></TEXT></P></PARALIST>
        </CELL>
      </ROW>
    </TABLE></TEXT></P>
    <P><TEXT><TABLE>
      <ROW>
        <CELL ColSpan="2"><PARALIST><P><TEXT><CHAR>A</CHAR></TEXT></P></PARALIST></CELL>
        <CELL><PARALIST><P><TEXT><CHAR>B</CHAR></TEXT></P></PARALIST></CELL>
      </ROW>
    </TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(1, 5),
    )
    nodes = graph.get("nodes") or []
    by_id = {node["id"]: node for node in nodes}

    assert by_id[401]["table_path"] == "0"
    assert by_id[401]["row"] == 0 and by_id[401]["col"] == 0
    assert by_id[402]["table_path"] == "0/0/0/0"
    assert by_id[402]["row"] == 0 and by_id[402]["col"] == 0

    # CVD 규칙과 동일: colspan이 있어도 col은 '셀 순번' 기반
    assert by_id[403]["table_path"] == "1"
    assert by_id[403]["col"] == 0 and by_id[403]["colspan"] == 2
    assert by_id[404]["table_path"] == "1"
    assert by_id[404]["col"] == 1


def test_hwpml_direct_graph_prefers_cvd_table_path_attr_over_registry_group_path():
    seg_td = ContentSegment(
        segment_id="501",
        position=(0, 50, 0),
        text="",
        segment_type="td",
        table_group_id=31,
        td_sig="td-path",
        p_sig="p-path",
        table_path="31/32",  # SegmentRegistry group path (legacy topology)
        attrs={
            "data-row": "0",
            "data-col": "0",
            "data-table-path": "0/0/1/0",  # CVD semantic table path (expected)
        },
    )
    manager = _FakeBlockManager([seg_td])

    hwpml_text = """
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE><ROW>
      <CELL><PARALIST><P><TEXT><CHAR>X</CHAR></TEXT></P></PARALIST></CELL>
    </ROW></TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(2, 6),
    )
    nodes = graph.get("nodes") or []
    assert len(nodes) == 1
    assert nodes[0]["id"] == 501
    assert nodes[0]["table_path"] == "0/0/1/0"


def test_hwpml_direct_graph_prefers_cvd_span_attrs_over_raw_hint():
    seg_td = ContentSegment(
        segment_id="502",
        position=(0, 51, 0),
        text="",
        segment_type="td",
        table_group_id=32,
        td_sig="td-span",
        p_sig="p-span",
        table_path="2",
        attrs={
            "data-row": "0",
            "data-col": "0",
            "data-rowspan": "1",
            "data-colspan": "3",
            "data-table-path": "2",
        },
    )
    manager = _FakeBlockManager([seg_td])

    # HWPML 쪽 힌트는 colspan=1이지만, CVD attrs가 colspan=3이면 attrs를 우선해야 함
    hwpml_text = """
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE><ROW>
      <CELL><PARALIST><P><TEXT><CHAR>X</CHAR></TEXT></P></PARALIST></CELL>
    </ROW></TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(2, 6),
    )
    nodes = graph.get("nodes") or []
    assert len(nodes) == 1
    assert nodes[0]["id"] == 502
    assert nodes[0]["rowspan"] == 1
    assert nodes[0]["colspan"] == 3


def test_hwpml_direct_graph_keeps_style_fields_from_cvd_attrs():
    seg_td = ContentSegment(
        segment_id="503",
        position=(0, 52, 0),
        text="",
        segment_type="td",
        table_group_id=33,
        td_sig="td-style",
        p_sig="p-style",
        table_path="3",
        attrs={
            "data-row": "0",
            "data-col": "0",
            "data-table-path": "3",
            "bgcolor": "#ffcc00",
            "border": "left,right,top,bottom",
            "data-diagonal": "1",
            "data-diagonal-flags": "slash,backslash",
            "color": "#112233",
            "font-size": "11pt",
        },
    )
    manager = _FakeBlockManager([seg_td])

    hwpml_text = """
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE><ROW>
      <CELL><PARALIST><P><TEXT><CHAR>X</CHAR></TEXT></P></PARALIST></CELL>
    </ROW></TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(2, 6),
    )
    nodes = graph.get("nodes") or []
    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == 503
    assert node["bgcolor"] == "#ffcc00"
    assert node["text_color"] == "#112233"
    assert node["font_size"] == "11pt"
    assert node["border_sides"] == ["left", "right", "top", "bottom"]
    assert node["diagonal"] is True
    assert set(node["diagonal_flags"]) == {"slash", "backslash"}


def test_hwpml_direct_graph_uses_inline_cell_style_fallback_without_borderfill():
    seg_td = ContentSegment(
        segment_id="504",
        position=(0, 53, 0),
        text="",
        segment_type="td",
        table_group_id=34,
        td_sig="td-inline-style",
        p_sig="p-inline-style",
        table_path="4",
        attrs={
            "data-row": "0",
            "data-col": "0",
            "data-table-path": "4",
        },
    )
    manager = _FakeBlockManager([seg_td])

    hwpml_text = """
<HWPML>
  <BODY><SECTION>
    <P><TEXT><TABLE><ROW>
      <CELL BgColor="255" Border="left,right,top,bottom" Data-Diagonal="1" Data-Diagonal-Flags="slash,backslash" Color="#334455" FontSize="10pt">
        <PARALIST><P><TEXT><CHAR>X</CHAR></TEXT></P></PARALIST>
      </CELL>
    </ROW></TABLE></TEXT></P>
  </SECTION></BODY>
</HWPML>
""".strip()

    graph = build_document_graph_from_hwpml(
        hwpml_text=hwpml_text,
        block_manager=manager,
        page_range=(2, 6),
    )
    nodes = graph.get("nodes") or []
    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == 504
    assert node["bgcolor"] == "#0000ff"
    assert node["text_color"] == "#334455"
    assert node["font_size"] == "10pt"
    assert node["border_sides"] == ["bottom", "left", "right", "top"]
    assert node["border_complete"] is True
    assert node["diagonal"] is True
    assert set(node["diagonal_flags"]) == {"backslash", "slash"}

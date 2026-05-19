import os
import sys
from dataclasses import dataclass


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hwp_com_process import DocumentProcessor
from processing.extraction.cvd_extractor import CVDExtractor
from processing.structure.segment_registry import SegmentRegistry


@dataclass
class _Seg:
    id: int
    block_type: str = "td"
    table_group_id: int = 1
    td_sig: str | None = None
    p_sig: str | None = None
    table_path: str | None = None
    list_pos: int | None = None
    para_pos: int = 0
    text: str = ""
    attrs: dict | None = None


class _BlockManager:
    def __init__(self, segments: list[_Seg]):
        self.segments = {str(seg.id): seg for seg in segments}

    def get_block(self, block_id: str):
        return self.segments.get(str(block_id))

    def get_table_group_id(self, block_id: str):
        seg = self.get_block(block_id)
        return seg.table_group_id if seg else None

    def get_cell_text(self, list_pos: int):
        for seg in self.segments.values():
            if seg.list_pos == list_pos:
                return seg.text
        return ""


def _make_processor() -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor._target_uid_to_id = {}
    processor._id_to_target_uid = {}
    return processor


def test_extract_signature_meta_parses_nested_json_and_scope():
    processor = _make_processor()
    meta = processor._extract_signature_meta(
        delta_data={"id": 1, "scope_table_id": "3"},
        metadata={"meta": '{"td_sig_v1": "tdv1:a", "p_sig_v1": "pv1:b", "table_path": "1/2"}'},
    )

    assert meta["scope_table_id"] == 3
    assert meta["td_sig_v1"] == "tdv1:a"
    assert meta["p_sig_v1"] == "pv1:b"
    assert meta["table_path"] == "1/2"


def test_extract_target_contract_collects_target_uid_and_meta():
    processor = _make_processor()
    contract = processor._extract_target_contract(
        delta_data={
            "id": 10,
            "target_uid": "td-10",
            "scope_table_id": "1",
            "td_sig_v1": "tdv1:new",
            "table_path": "0/0",
        },
        metadata={},
    )

    assert contract["target_uid"] == "td-10"
    assert contract["meta"]["scope_table_id"] == 1
    assert contract["meta"]["td_sig_v1"] == "tdv1:new"
    assert contract["meta"]["table_path"] == "0/0"


def test_validate_target_contract_accepts_exact_match():
    processor = _make_processor()
    processor._target_uid_to_id = {"td-20": 20}
    processor._id_to_target_uid = {"20": "td-20"}

    manager = _BlockManager([_Seg(id=20, td_sig="tdv1:new", p_sig="pv1:cell", table_path="0/0")])
    ok, reason = processor._validate_target_contract(
        block_manager=manager,
        element_id=20,
        contract={
            "target_uid": "td-20",
            "meta": {
                "block_type": "td",
                "scope_table_id": 1,
                "td_sig_v1": "tdv1:new",
                "table_path": "0/0",
            },
        },
    )

    assert ok is True
    assert reason is None


def test_validate_target_contract_rejects_target_uid_mismatch():
    processor = _make_processor()
    processor._target_uid_to_id = {"td-20": 20}
    processor._id_to_target_uid = {"10": "td-10"}

    ok, reason = processor._validate_target_contract(
        block_manager=_BlockManager([_Seg(id=10, td_sig="tdv1:old", table_path="0/0")]),
        element_id=10,
        contract={"target_uid": "td-20"},
    )

    assert ok is False
    assert reason == "target_uid_mismatch"


def test_validate_target_contract_rejects_when_block_not_found():
    processor = _make_processor()
    ok, reason = processor._validate_target_contract(
        block_manager=_BlockManager([]),
        element_id=999,
        contract={"meta": {"td_sig_v1": "tdv1:any"}},
    )

    assert ok is False
    assert reason == "target_block_not_found"


def test_validate_target_contract_rejects_signature_mismatch_without_remap():
    processor = _make_processor()
    manager = _BlockManager(
        [
            _Seg(id=10, td_sig="tdv1:old", table_path="0/0"),
            _Seg(id=20, td_sig="tdv1:new", table_path="0/0"),
        ]
    )

    ok, reason = processor._validate_target_contract(
        block_manager=manager,
        element_id=10,
        contract={"meta": {"td_sig_v1": "tdv1:new", "table_path": "0/0"}},
    )

    assert ok is False
    assert reason == "td_sig_mismatch"


def test_validate_target_contract_checks_block_type_and_scope():
    processor = _make_processor()
    manager = _BlockManager([_Seg(id=10, block_type="text", table_group_id=2, p_sig="pv1:t1", table_path="0/1")])

    ok, reason = processor._validate_target_contract(
        block_manager=manager,
        element_id=10,
        contract={"meta": {"block_type": "td"}},
    )
    assert ok is False
    assert reason == "block_type_mismatch"

    ok, reason = processor._validate_target_contract(
        block_manager=manager,
        element_id=10,
        contract={"meta": {"scope_table_id": 1}},
    )
    assert ok is False
    assert reason == "scope_table_id_mismatch"


def _build_registry_with_signatures() -> SegmentRegistry:
    ex = CVDExtractor(None)
    ex.extracted_elements = [
        {
            "type": "table",
            "html": "<table>\n  <tr>\n    <td>A</td>\n    <td>B</td>\n  </tr>\n</table>",
            "td_metas": [
                {
                    "type": "td",
                    "pos": (1, 1, 1),
                    "row": 0,
                    "col": 0,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0,),
                    "inner_metas": [{"type": "paragraph", "pos": (1, 1, 1), "text": "A"}],
                },
                {
                    "type": "td",
                    "pos": (2, 1, 1),
                    "row": 0,
                    "col": 1,
                    "rowspan": 1,
                    "colspan": 1,
                    "table_path": (0,),
                    "inner_metas": [{"type": "paragraph", "pos": (2, 1, 1), "text": "B"}],
                },
            ],
        }
    ]
    extracted = ex._extract_content_and_id_to_pos_from_extracted_elements()
    assert extracted is not None
    return SegmentRegistry(extracted)


def test_registry_preserves_td_and_p_signatures():
    registry = _build_registry_with_signatures()

    td_segments = [seg for seg in registry.segments.values() if seg.segment_type == "td"]
    assert len(td_segments) == 2
    assert all(seg.td_sig for seg in td_segments)
    assert all(seg.table_group_id is not None for seg in td_segments)
    assert all(seg.table_path for seg in td_segments)

    text_segments = [seg for seg in registry.segments.values() if seg.segment_type in ("text", "list")]
    assert any(seg.p_sig for seg in text_segments)


def test_validator_accepts_matching_signature_snapshot_with_registry():
    processor = _make_processor()
    registry = _build_registry_with_signatures()
    td_segment = next(seg for seg in registry.segments.values() if seg.segment_type == "td")
    processor._target_uid_to_id = {f"td-{td_segment.id}": int(td_segment.id)}
    processor._id_to_target_uid = {str(td_segment.id): f"td-{td_segment.id}"}

    ok, reason = processor._validate_target_contract(
        block_manager=registry,
        element_id=int(td_segment.id),
        contract={
            "target_uid": f"td-{td_segment.id}",
            "meta": {
                "block_type": "td",
                "scope_table_id": td_segment.table_group_id,
                "td_sig_v1": td_segment.td_sig,
                "table_path": td_segment.table_path,
            },
        },
    )

    assert ok is True
    assert reason is None

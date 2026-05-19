import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hwp_com_process import DocumentProcessor


class _Seg:
    def __init__(self, seg_id: int, block_type: str = "td"):
        self.id = seg_id
        self.block_type = block_type
        self.segment_type = block_type
        self.table_group_id = 1
        self.td_sig = "tdv1:test"
        self.p_sig = "pv1:test"
        self.table_path = "0/0"


class _BlockManager:
    def __init__(self):
        self._seg = _Seg(1, "td")

    def get_block(self, block_id: str):
        return self._seg if str(block_id) == "1" else None

    def get_table_group_id(self, block_id: str):
        seg = self.get_block(block_id)
        return seg.table_group_id if seg else None


class _Editor:
    def __init__(self):
        self.segment_registry = _BlockManager()
        self.calls = []

    def replace_cell_content(self, block_id: str, new_text: str, **kwargs):
        self.calls.append(("replace_cell_content", block_id, new_text, kwargs))
        return True

    def replace_paragraph(self, block_id: str, new_text: str, **kwargs):
        self.calls.append(("replace_paragraph", block_id, new_text, kwargs))
        return True

    def replace_paragraph_content(self, block_id: str, new_text: str, **kwargs):
        self.calls.append(("replace_paragraph_content", block_id, new_text, kwargs))
        return True


def _make_processor() -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor._active_context_id = None
    processor._diff_mode_enabled = False
    processor._allowed_element_ids = [1]
    processor._target_uid_to_id = {"td-1": 1}
    processor._id_to_target_uid = {"1": "td-1"}
    processor._send_progress = lambda *args, **kwargs: None
    processor._ensure_content_modifier = lambda: _Editor()
    return processor


def test_execute_delta_allows_when_target_contract_missing():
    processor = _make_processor()
    result = processor.execute_delta(
        {
            "method_type": "replace_paragraph",
            "id": 1,
            "new_text": "A",
        }
    )
    assert result["success"] is True
    assert result["edited"] is True
    assert result.get("skipped") is not True


def test_execute_delta_allows_when_target_uid_missing():
    processor = _make_processor()
    result = processor.execute_delta(
        {
            "method_type": "replace_cell_content",
            "id": 1,
            "new_text": "A",
            "metadata": {"td_sig_v1": "tdv1:test"},
        }
    )
    assert result["success"] is True
    assert result["edited"] is True
    assert result.get("skipped") is not True


def test_execute_delta_allows_when_meta_contract_missing():
    processor = _make_processor()
    result = processor.execute_delta(
        {
            "method_type": "replace_cell_content",
            "id": 1,
            "target_uid": "td-1",
            "new_text": "A",
        }
    )
    assert result["success"] is True
    assert result["edited"] is True
    assert result.get("skipped") is not True

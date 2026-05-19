import os
import sys


PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)


from services.local_file_reader_service import LocalFileReaderService


def test_search_with_file_name_returns_snippet_not_full_text(monkeypatch):
    reader = LocalFileReaderService(user_data_path="C:\\dummy")
    full_text = ("앞부분 " * 120) + "기술 스택" + (" 뒷부분" * 160)
    file_info = {
        "file_id": "f1",
        "file_name": "sample.txt",
        "display_name": "sample.txt",
        "scope": "project",
        "abs_path": "C:\\dummy\\sample.txt",
    }

    monkeypatch.setattr(reader, "get_available_files", lambda project_id, chat_id=None: [file_info])
    monkeypatch.setattr(reader, "read_file_text", lambda abs_path: full_text)
    monkeypatch.setattr(reader, "_save_text_length", lambda file_id, text_length: None)

    result = reader.search(["기술 스택"], project_id="p1", file_name="sample.txt")

    assert result["success"] is True
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["source"] == "sample.txt"
    assert hit["content"] != full_text
    assert len(hit["content"]) < len(full_text)
    assert hit["start_char"] > 0
    assert hit["end_char"] < len(full_text)
    assert hit["truncated_before"] is True
    assert hit["truncated_after"] is True


def test_search_with_file_name_falls_back_to_whitespace_insensitive_match(monkeypatch):
    reader = LocalFileReaderService(user_data_path="C:\\dummy")
    full_text = "서론\n창업 아이템 개요\n본문"
    file_info = {
        "file_id": "f1",
        "file_name": "sample.txt",
        "display_name": "sample.txt",
        "scope": "project",
        "abs_path": "C:\\dummy\\sample.txt",
    }

    monkeypatch.setattr(reader, "get_available_files", lambda project_id, chat_id=None: [file_info])
    monkeypatch.setattr(reader, "read_file_text", lambda abs_path: full_text)
    monkeypatch.setattr(reader, "_save_text_length", lambda file_id, text_length: None)

    result = reader.search(["창업아이템 개요"], project_id="p1", file_name="sample.txt")

    assert result["success"] is True
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert "창업 아이템 개요" in hit["content"]


def test_read_chunk_caps_length_at_1000(monkeypatch):
    reader = LocalFileReaderService(user_data_path="C:\\dummy")
    full_text = "A" * 5000
    file_info = {
        "file_id": "f1",
        "file_name": "sample.txt",
        "display_name": "sample.txt",
        "scope": "project",
        "abs_path": "C:\\dummy\\sample.txt",
    }

    monkeypatch.setattr(reader, "get_available_files", lambda project_id, chat_id=None: [file_info])
    monkeypatch.setattr(reader, "read_file_text", lambda abs_path: full_text)
    monkeypatch.setattr(reader, "_save_text_length", lambda file_id, text_length: None)

    result = reader.read_chunk("sample.txt", project_id="p1", offset=100, length=5000)

    assert result["success"] is True
    assert result["length"] == 2000
    assert result["returned_chars"] == 2000
    assert result["start_char"] == 100
    assert result["end_char"] == 2100


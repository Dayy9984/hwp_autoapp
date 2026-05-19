"""
TXT 파일 읽기 모듈

텍스트 파일을 읽어서 문자열로 반환합니다.
서버 분리 시 이 문자열을 그대로 API로 전송하면 됩니다.
"""

import os
from pathlib import Path
from typing import Optional


class TxtReader:
    """TXT 파일 읽기 클래스"""

    # 지원하는 인코딩 목록 (순서대로 시도)
    ENCODINGS = ['utf-8', 'cp949', 'euc-kr', 'utf-16', 'latin-1']

    @staticmethod
    def read(file_path: str, max_chars: Optional[int] = None) -> dict:
        """
        TXT 파일을 읽어서 문자열로 반환

        Args:
            file_path: 파일 경로
            max_chars: 최대 문자 수 제한 (None이면 전체)

        Returns:
            {
                "success": bool,
                "content": str,  # 파일 내용
                "file_name": str,
                "file_size": int,
                "encoding": str,  # 감지된 인코딩
                "truncated": bool,  # 잘렸는지 여부
                "error": str  # 에러 시
            }
        """
        path = Path(file_path)

        # 파일 존재 확인
        if not path.exists():
            return {
                "success": False,
                "error": f"파일을 찾을 수 없습니다: {file_path}"
            }

        # 파일 확장자 확인
        if path.suffix.lower() not in {'.txt', '.md'}:
            return {
                "success": False,
                "error": f"TXT/MD 파일이 아닙니다: {path.suffix}"
            }

        file_size = path.stat().st_size
        content = None
        detected_encoding = None

        # 여러 인코딩 시도
        for encoding in TxtReader.ENCODINGS:
            try:
                with open(path, 'r', encoding=encoding) as f:
                    content = f.read()
                detected_encoding = encoding
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if content is None:
            return {
                "success": False,
                "error": "파일 인코딩을 감지할 수 없습니다"
            }

        # 최대 문자 수 제한
        truncated = False
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        return {
            "success": True,
            "content": content,
            "file_name": path.name,
            "file_size": file_size,
            "encoding": detected_encoding,
            "truncated": truncated
        }

    @staticmethod
    def read_with_line_limit(file_path: str, max_lines: int = 1000) -> dict:
        """
        TXT 파일을 줄 수 제한으로 읽기

        Args:
            file_path: 파일 경로
            max_lines: 최대 줄 수

        Returns:
            read()와 동일한 형식 + line_count 추가
        """
        result = TxtReader.read(file_path)

        if not result["success"]:
            return result

        lines = result["content"].split('\n')
        total_lines = len(lines)

        if total_lines > max_lines:
            result["content"] = '\n'.join(lines[:max_lines])
            result["truncated"] = True

        result["line_count"] = min(total_lines, max_lines)
        result["total_lines"] = total_lines

        return result

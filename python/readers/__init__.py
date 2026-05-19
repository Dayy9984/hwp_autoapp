"""
파일 읽기 모듈

로컬 파일을 읽어서 문자열로 반환합니다.
나중에 서버로 전송할 때 이 문자열을 그대로 보내면 됩니다.
"""

from .txt_reader import TxtReader
from .excel_reader import ExcelReader

__all__ = ['TxtReader', 'ExcelReader']

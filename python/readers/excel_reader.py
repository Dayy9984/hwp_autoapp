"""
Excel 파일 읽기 모듈

엑셀 파일의 내용을 텍스트로 추출합니다.
서버 분리 시 이 문자열을 그대로 API로 전송하면 됩니다.
"""

from pathlib import Path
from typing import Optional


class ExcelReader:
    """Excel 파일 읽기 클래스"""

    @staticmethod
    def read(file_path: str, sheet_name: Optional[str] = None) -> dict:
        """
        Excel 파일을 읽어서 텍스트로 반환

        Args:
            file_path: 파일 경로
            sheet_name: 시트 이름 (None이면 첫 번째 시트)

        Returns:
            {
                "success": bool,
                "content": str,  # 셀 내용 (탭/줄바꿈 구분)
                "file_name": str,
                "sheet_name": str,
                "row_count": int,
                "col_count": int,
                "error": str  # 에러 시
            }
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {
                "success": False,
                "error": "openpyxl 라이브러리가 설치되지 않았습니다"
            }

        path = Path(file_path)

        # 파일 존재 확인
        if not path.exists():
            return {
                "success": False,
                "error": f"파일을 찾을 수 없습니다: {file_path}"
            }

        # 파일 확장자 확인
        if path.suffix.lower() not in ['.xlsx', '.xls', '.xlsm']:
            return {
                "success": False,
                "error": f"Excel 파일이 아닙니다: {path.suffix}"
            }

        try:
            # 읽기 전용으로 열기
            wb = load_workbook(file_path, read_only=True, data_only=True)

            # 시트 선택
            if sheet_name:
                if sheet_name not in wb.sheetnames:
                    return {
                        "success": False,
                        "error": f"시트를 찾을 수 없습니다: {sheet_name}"
                    }
                ws = wb[sheet_name]
            else:
                ws = wb.active
                sheet_name = ws.title

            # 셀 내용 추출
            rows = []
            row_count = 0
            col_count = 0

            for row in ws.iter_rows():
                row_values = []
                for cell in row:
                    value = cell.value
                    if value is not None:
                        row_values.append(str(value))
                    else:
                        row_values.append("")

                # 빈 행이 아니면 추가
                if any(v.strip() for v in row_values):
                    rows.append('\t'.join(row_values))
                    row_count += 1
                    col_count = max(col_count, len(row_values))

            wb.close()

            content = '\n'.join(rows)

            return {
                "success": True,
                "content": content,
                "file_name": path.name,
                "sheet_name": sheet_name,
                "row_count": row_count,
                "col_count": col_count
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Excel 파일 읽기 오류: {str(e)}"
            }

    @staticmethod
    def get_sheet_names(file_path: str) -> dict:
        """
        Excel 파일의 시트 목록 반환

        Returns:
            {
                "success": bool,
                "sheets": list[str],
                "error": str
            }
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {
                "success": False,
                "error": "openpyxl 라이브러리가 설치되지 않았습니다"
            }

        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": "파일을 찾을 수 없습니다"}

        try:
            wb = load_workbook(file_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            return {"success": True, "sheets": sheets}
        except Exception as e:
            return {"success": False, "error": str(e)}

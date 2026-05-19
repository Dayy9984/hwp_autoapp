"""
Win32com 타입 라이브러리 캐시 삭제 스크립트
HWP 버전 전환 시 실행
"""
import os
import shutil
import tempfile

def clear_win32com_cache():
    """win32com gen_py 캐시 삭제"""
    gen_py_path = os.path.join(tempfile.gettempdir(), "gen_py")

    if os.path.exists(gen_py_path):
        try:
            shutil.rmtree(gen_py_path)
            print(f"✅ Win32com 캐시 삭제 완료: {gen_py_path}")
        except Exception as e:
            print(f"❌ 캐시 삭제 실패: {e}")
    else:
        print(f"ℹ️ 캐시 디렉토리 없음: {gen_py_path}")

if __name__ == "__main__":
    clear_win32com_cache()
    print("\n다시 애플리케이션을 실행하세요.")

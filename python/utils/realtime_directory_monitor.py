"""
실시간 디렉토리 모니터링 스크립트
문서 diff/undo 기능 관련 Python 코드 캡처

실시간으로 특정 패턴의 디렉토리를 감시하고,
새로운 파일이 생성되면 자동으로 백업합니다.
"""

import os
import glob
import time
import shutil
from datetime import datetime


class DirectoryMonitor:
    """실시간 디렉토리 모니터링 클래스"""

    def __init__(self, log_callback=None):
        """
        DirectoryMonitor 초기화

        Args:
            log_callback: 로그 콜백 함수 (선택사항)
        """
        self.log_callback = log_callback or print
        self.discovered_folders = set()
        self.processed_file_count = 0

    def log(self, level: str, message: str):
        """로그 출력"""
        self.log_callback(f"[{level}] {message}")

    def start_monitoring(self, temp_dir: str = None, pattern_prefix: str = "document-diff-"):
        """
        디렉토리 모니터링 시작

        Args:
            temp_dir: 모니터링할 임시 디렉토리 (기본값: 시스템 TEMP)
            pattern_prefix: 모니터링할 디렉토리 패턴 프리픽스
        """
        # TEMP 디렉토리 경로
        if temp_dir is None:
            temp_dir = os.environ.get('TEMP') or os.path.join(
                os.path.expanduser('~'), 'AppData', 'Local', 'Temp'
            )

        directory_pattern = os.path.join(temp_dir, f"{pattern_prefix}*")

        self.log("INFO", "디렉토리 실시간 모니터링 시작...")
        self.log("INFO", f"패턴: {directory_pattern}")
        self.log("INFO", "document-diff 기능을 사용하세요...")
        self.log("INFO", "Ctrl+C로 중지\n")

        # 초기 폴더 발견
        self.discovered_folders = set(glob.glob(directory_pattern))
        self.log("INFO", f"초기 폴더 수: {len(self.discovered_folders)}")

        try:
            while True:
                current_folders = set(glob.glob(directory_pattern))
                new_folder_paths = current_folders - self.discovered_folders

                if new_folder_paths:
                    for folder_path in new_folder_paths:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        self.log("INFO", f"\n[NEW] {folder_path}")

                        # 폴더 안의 모든 파일 확인
                        for root, dirs, files in os.walk(folder_path):
                            for file_name in files:
                                file_path = os.path.join(root, file_name)
                                try:
                                    file_size = os.path.getsize(file_path)
                                except OSError:
                                    file_size = 0

                                # Python 파일이거나 크기가 있는 파일만 복사
                                if file_name.endswith('.py') or file_size > 0:
                                    dest = os.path.join('.', f'diff_{timestamp}_{file_name}')
                                    try:
                                        shutil.copy2(file_path, dest)
                                        self.processed_file_count += 1
                                        self.log("INFO", f"   [OK] {file_name} ({file_size} bytes) -> {dest}")
                                    except Exception as e:
                                        self.log("ERROR", f"   [ERR] {file_name}: {e}")

                        self.discovered_folders.add(folder_path)

                time.sleep(0.5)

        except KeyboardInterrupt:
            self.log("INFO", f"\n\n[*] 모니터링 중지")
            self.log("INFO", f"[*] 총 {self.processed_file_count}개 파일 처리됨")
            return self.processed_file_count


def main():
    """메인 실행 함수"""
    monitor = DirectoryMonitor()
    monitor.start_monitoring()


if __name__ == "__main__":
    main()

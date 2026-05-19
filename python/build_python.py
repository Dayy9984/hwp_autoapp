"""
Inserty Desktop - Python Build (Nuitka)

64-bit 단일 빌드 - Windows COM 마샬링으로 32-bit HWP 호환

사용법:
    uv run python build_python.py
"""

import os
import sys
import shutil
from pathlib import Path
from typing import Optional
from importlib.util import find_spec

# 버퍼링 없이 즉시 출력
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ============================================================
# 설정
# ============================================================

ROOT_DIR = Path(__file__).parent
DIST_DIR = ROOT_DIR / "dist"


def resolve_file_path_checker_dll() -> Optional[Path]:
    """Nuitka 빌드에 포함할 FilePathCheckerModule DLL 경로 탐색."""
    env_path = os.environ.get("INSERTY_FILEPATHCHECKER_DLL")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        if candidate.is_dir():
            for dll_name in ("FilePathCheckerModule.dll", "FilePathCheckerModuleExample.dll"):
                dll_path = candidate / dll_name
                if dll_path.is_file():
                    return dll_path.resolve()

    try:
        spec = find_spec("pyhwpx")
        if spec and spec.origin:
            package_dir = Path(spec.origin).resolve().parent
            for dll_name in ("FilePathCheckerModule.dll", "FilePathCheckerModuleExample.dll"):
                dll_path = package_dir / dll_name
                if dll_path.is_file():
                    return dll_path.resolve()
    except Exception:
        pass

    fallback_dirs = (
        ROOT_DIR,
        ROOT_DIR.parent,
        Path.cwd(),
        Path(sys.executable).resolve().parent,
    )
    for base_dir in fallback_dirs:
        for dll_name in ("FilePathCheckerModule.dll", "FilePathCheckerModuleExample.dll"):
            dll_path = base_dir / dll_name
            if dll_path.is_file():
                return dll_path.resolve()

    return None

# ============================================================
# 빌드할 프로세스 정의 (64-bit only)
# ============================================================

PROCESSES = [
    {
        "name": "HWP COM Process",
        "entry": "hwp_com_process.py",
        "output": "inserty_python",
        "modules": ["engine", "utilities", "utils", "processing", "modification", "llm", "readers", "api", "services", "session", "parsing"],
    },
    {
        "name": "HWP Window Monitor",
        "entry": "hwp_window_monitor.py",
        "output": "hwp_window_monitor",
        "modules": [],
    },
    {
        "name": "Agent Process (LLM/RAG)",
        "entry": "agent_process.py",
        "output": "inserty_agent",
        # lazy import는 Nuitka가 정적 추적 못함 — 명시 필수 (pyhwpx도 _convert_hwp_to_docx에서 사용)
        "modules": [
            "llm", "utils", "services", "processing", "engine",
            "pyhwpx", "pptx", "PyPDF2", "openpyxl", "docx",
        ],
    },
    {
        "name": "File Reader Process (File/CVD)",
        "entry": "file_reader_process.py",
        "output": "inserty_file_reader",
        # engine: services/cvd_service.py top-level import
        # pptx: file_reader_process.py:163 lazy import — Nuitka 추적 실패
        # pyhwpx: services/cvd_service.py:171 lazy import
        # PyPDF2: services/cvd_service.py extract_pdf 사용
        # openpyxl: 엑셀 readExcelFile
        # docx: readDocFile
        "modules": [
            "readers", "services", "utils", "processing", "engine",
            "pyhwpx", "pptx", "PyPDF2", "openpyxl", "docx",
        ],
    },
]

# ============================================================
# 빌드 단계
# ============================================================

def step_1_clean():
    """이전 빌드 삭제"""
    print("=" * 60)
    print("단계 1: 이전 빌드 삭제")
    print("=" * 60)

    if DIST_DIR.exists():
        print(f"  삭제 중: {DIST_DIR}")
        shutil.rmtree(DIST_DIR)

    DIST_DIR.mkdir(exist_ok=True)
    print("삭제 완료\n")

def step_2_build_with_nuitka(process_config):
    """Nuitka로 단일 프로세스 빌드"""
    name = process_config["name"]
    entry = process_config["entry"]
    output = process_config["output"]
    modules = process_config["modules"]

    print("=" * 60)
    print(f"{name} 빌드 시작")
    print("=" * 60)

    main_file = ROOT_DIR / entry
    output_dir = DIST_DIR / output

    if not main_file.exists():
        print(f"메인 파일 없음: {main_file}")
        return False

    print(f"  Python: {sys.executable}")
    print(f"  Entry: {main_file}")

    # 미사용 대형 패키지 제외 (빌드 시간 절약)
    # ⚠️ 실제 사용하는 패키지(openai, httpx, pydantic, openpyxl, pyhwpx, win32com 등)는
    #    절대 여기 넣지 말 것 — Nuitka standalone이 자동으로 dist에 번들링해야 함
    # ⚠️ pyhwpx/core.py가 top-level에서 numpy/pandas/PIL을 import — NOFOLLOW 절대 금지
    NOFOLLOW_PACKAGES = [
        # ML/AI 프레임워크 (미설치 또는 미사용)
        "torch", "torchvision", "torchaudio", "functorch",
        "transformers", "tokenizers", "safetensors",
        "tensorflow", "keras", "onnx", "onnxruntime",
        "triton", "flash_attn", "xformers",
        # 데이터 과학 (실제 미사용 대형 라이브러리만)
        "scipy", "sklearn", "matplotlib", "sympy", "networkx",
        # 레거시 서비스 의존성 (rag_service/ocr_service — 모두 try-except)
        "lightrag", "llama_index", "llama_cloud",
        "cv2", "pytesseract", "pdf2image", "pypdfium2",
        # 테스트 프레임워크
        "pytest", "unittest", "doctest", "_pytest",
        # 개발 도구 (런타임 불필요)
        "pygments", "IPython", "ipykernel",
        # ⚠️ 다음 패키지들은 NOFOLLOW 금지 (pyhwpx top-level deps):
        #   numpy, pandas, PIL, pyperclip
        # ⚠️ docx도 NOFOLLOW 금지 — file_reader_process.py가 사용
        # ⚠️ PyPDF2, pptx도 NOFOLLOW 금지 — cvd_service.extract_pdf, readPptFile에서 사용
    ]

    # Nuitka 명령 구성
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--remove-output",

        # 최적화
        "--lto=yes",
        "--assume-yes-for-downloads",

        # 진행률 표시 + 병렬 컴파일
        "--show-progress",
        f"--jobs={os.cpu_count() or 4}",

        # 대용량 패키지 제외 (C++ 컴파일 안 함, 런타임 import만)
        "--no-deployment-flag=excluded-module-usage",
    ]

    # 대용량 패키지를 컴파일에서 제외
    for pkg in NOFOLLOW_PACKAGES:
        cmd.append(f"--nofollow-import-to={pkg}")

    # 프로젝트 모듈 명시적 포함
    for module in modules:
        cmd.append(f"--include-package={module}")

    file_path_checker_dll = resolve_file_path_checker_dll()
    if file_path_checker_dll:
        cmd.append(
            f"--include-data-files={file_path_checker_dll}=FilePathCheckerModule.dll"
        )
        print(f"  보안모듈 DLL 포함: {file_path_checker_dll}")
    else:
        print("  경고: FilePathCheckerModule DLL을 찾지 못했습니다.")

    # 컴파일러 지정 (Windows)
    if sys.platform == "win32":
        cmd.append("--mingw64")

    # 출력 설정
    cmd.extend([
        f"--output-dir={output_dir}",
        f"--output-filename={output}.exe",
        "--windows-console-mode=attach",
        str(main_file)
    ])

    print(f"  실행: nuitka ...")

    # PATH에서 32-bit MinGW 제거 (64-bit MinGW 자동 다운로드 유도)
    original_path = os.environ.get('PATH', '')
    paths = original_path.split(os.pathsep)
    paths = [p for p in paths if 'mingw' not in p.lower() or 'mingw64' in p.lower()]

    # 수정된 환경 변수 준비
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join(paths)
    # CC 환경 변수 제거 (Nuitka가 컴파일러를 자동 감지하도록)
    env.pop('CC', None)
    env.pop('CXX', None)

    try:
        # subprocess.run()으로 명시적 환경 변수 전달 (TTY 상속으로 progress bar 표시)
        import subprocess
        result = subprocess.run(
            cmd,
            env=env,
            check=False
        )
        return_code = result.returncode

        if return_code == 0:
            # --standalone은 .dist 폴더에 출력함 → 상위로 이동
            entry_base = Path(entry).stem
            dist_folder = output_dir / f"{entry_base}.dist"
            if dist_folder.exists():
                print(f"  .dist 폴더 발견: {dist_folder}")
                print(f"  내용을 상위로 이동 중...")
                for item in dist_folder.iterdir():
                    dest = output_dir / item.name
                    if dest.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(dest))
                dist_folder.rmdir()
                print(f"  이동 완료: {output_dir}")
            else:
                print(f"  경고: .dist 폴더 없음 - {dist_folder}")
            print(f"{name} 빌드 완료\n")
            return True
        else:
            print(f"{name} 빌드 실패\n")
            return False
    finally:
        # PATH는 원본 환경을 수정하지 않았으므로 복원 불필요
        pass

def main():
    """메인 빌드 프로세스"""
    print("\n")
    print("=" * 60)
    print("Inserty Desktop - Python Build")
    print("   Nuitka (4 Processes, 64-bit)")
    print("=" * 60)
    print("\n")
    print("64-bit 단일 빌드 - Windows COM 마샬링으로 32-bit HWP 호환")
    print("\n")

    # 1. Clean
    step_1_clean()

    # 2. Nuitka 빌드 (4개 프로세스)
    results = {}
    for process in PROCESSES:
        success = step_2_build_with_nuitka(process)
        results[process["name"]] = success

    # 3. Summary
    print("=" * 60)
    print("빌드 결과")
    print("=" * 60)

    success_count = 0
    failed_count = 0

    for name, success in results.items():
        if success:
            status = "성공"
            success_count += 1
        else:
            status = "실패"
            failed_count += 1
        print(f"  {name}: {status}")

    print(f"\n요약: 성공 {success_count}, 실패 {failed_count}")

    if failed_count == 0:
        print(f"\n빌드 완료!")
        print(f"\n출력 파일:")
        for process in PROCESSES:
            exe_path = DIST_DIR / process['output'] / f"{process['output']}.exe"
            print(f"  {exe_path}")
        return 0
    else:
        print("\n일부 빌드 실패")
        return 1

if __name__ == "__main__":
    sys.exit(main())

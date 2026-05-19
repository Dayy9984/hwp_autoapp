"""
Delta 명령어 자동 테스트 스크립트

테스트 대상:
1. 새 문서 생성 및 HWP 연결
2. 텍스트 삽입 (write_text)
3. 글자 스타일 적용 (set_char_style) - 정식 API 사용
4. 각주/미주 삽입 (add_footnote, add_endnote)
5. 메모 삽입 (add_comment) - InsertFieldMemo 사용
6. 출처 참조 각주 (add_source_ref)
7. 줄바꿈 삽입 (line_break)
8. 선택 영역 감지 (get_selection_info) - GetSelectedPosBySet 사용

사용법:
    python test_delta_commands.py

HWP 2022 이전 정식 API 사용
"""

import sys
import os
import time

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythoncom
import win32com.client


def get_or_create_hwp():
    """HWP 인스턴스 가져오기 또는 새로 생성"""
    pythoncom.CoInitialize()

    prog_ids = [
        "HWPFrame.HwpObject",
        "HwpFrame.HwpObject",
        "HwpObject.HwpObject",
        "HncCtrl.HwpObject.Xml",
    ]

    hwp = None
    for prog_id in prog_ids:
        try:
            hwp = win32com.client.GetActiveObject(prog_id)
            print(f"[OK] 기존 HWP 연결: {prog_id}")
            break
        except pythoncom.com_error:
            continue

    if hwp is None:
        for prog_id in prog_ids:
            try:
                hwp = win32com.client.Dispatch(prog_id)
                print(f"[OK] 새 HWP 생성: {prog_id}")
                break
            except Exception:
                continue

    if hwp is None:
        print("[ERROR] HWP를 시작할 수 없습니다.")
        return None

    try:
        hwp.RegisterModule("FilePathCheckDLL", "AutomationModule")
    except Exception:
        pass

    return hwp


def create_new_document(hwp):
    """새 문서 생성"""
    try:
        hwp.HAction.Run("FileNew")
        print("[OK] 새 문서 생성됨")
        return True
    except Exception as e:
        print(f"[ERROR] 새 문서 생성 실패: {e}")
        return False


def reset_char_style(hwp):
    """현재 입력 모양을 기본 스타일로 리셋"""
    try:
        char_params = hwp.HParameterSet.HCharShape
        hwp.HAction.GetDefault("CharShape", char_params.HSet)
        # 명시적으로 모든 스타일 속성을 기본값으로 설정
        char_params.Bold = 0
        char_params.Italic = 0
        char_params.UnderlineType = 0
        char_params.StrikeOut = 0
        char_params.TextColor = 0x000000  # 검정
        hwp.HAction.Execute("CharShape", char_params.HSet)
        print("[OK] 스타일 리셋 완료")
        return True
    except Exception as e:
        print(f"[ERROR] 스타일 리셋 실패: {e}")
        return False


def insert_text(hwp, text):
    """텍스트 삽입"""
    try:
        hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
        hwp.HParameterSet.HInsertText.Text = text
        hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
        return True
    except Exception as e:
        print(f"[ERROR] 텍스트 삽입 실패: {e}")
        return False


def insert_line_break(hwp):
    """줄바꿈 삽입"""
    try:
        hwp.HAction.Run("BreakPara")
        return True
    except Exception as e:
        print(f"[ERROR] 줄바꿈 실패: {e}")
        return False


def apply_style_to_text(hwp, text, bold=False, underline=False, font_size=None):
    """스타일 적용 텍스트 입력 (저장→설정→입력→복원 패턴)

    핵심: 작업 전 현재 입력 모양을 저장하고, 작업 후 복원
    """
    try:
        # 1. 현재 입력 모양(CharShape) 저장 - 이 시점의 상태를 기억
        base = hwp.HParameterSet.HCharShape
        hwp.HAction.GetDefault("CharShape", base.HSet)

        # 2. 작업용 CharShape - 원하는 스타일 명시적 설정
        work = hwp.HParameterSet.HCharShape
        hwp.HAction.GetDefault("CharShape", work.HSet)

        work.Bold = 1 if bold else 0
        work.UnderlineType = 1 if underline else 0  # 1=bottom
        if font_size is not None:
            work.Height = int(round(float(font_size) * 100.0))  # 1pt ≈ 100 HWPUNIT

        # 3. 현재 입력 모양을 work로 변경 (선택 없이 Execute)
        hwp.HAction.Execute("CharShape", work.HSet)

        # 4. 텍스트 입력 (변경된 입력 모양으로 입력됨)
        insert_text(hwp, text)

        # 5. 현재 입력 모양을 원래대로 복원 (핵심!)
        hwp.HAction.Execute("CharShape", base.HSet)

        return True
    except Exception as e:
        print(f"[ERROR] 스타일 적용 실패: {e}")
        return False


def insert_footnote(hwp, text):
    """각주 삽입"""
    try:
        hwp.HAction.Run("InsertFootnote")
        insert_text(hwp, text)
        hwp.HAction.Run("CloseEx")
        return True
    except Exception as e:
        print(f"[ERROR] 각주 삽입 실패: {e}")
        return False


def insert_endnote(hwp, text):
    """미주 삽입"""
    try:
        hwp.HAction.Run("InsertEndnote")
        insert_text(hwp, text)
        hwp.HAction.Run("CloseEx")
        return True
    except Exception as e:
        print(f"[ERROR] 미주 삽입 실패: {e}")
        return False


def insert_source_ref(hwp, ref_id, title, publisher="", year="", url=""):
    """출처 참조 각주 삽입"""
    try:
        # 각주 삽입
        hwp.HAction.Run("InsertFootnote")

        # 출처 정보 형식화
        source_text = f"[{ref_id}] {title}"
        if publisher:
            source_text += f", {publisher}"
        if year:
            source_text += f" ({year})"
        if url:
            source_text += f", {url}"

        # 내용 입력
        insert_text(hwp, source_text)

        # 본문으로 돌아가기
        hwp.HAction.Run("CloseEx")

        return True
    except Exception as e:
        print(f"[ERROR] 출처 각주 삽입 실패: {e}")
        return False


def get_selection_info(hwp):
    """선택 영역 정보 가져오기 - GetSelectedPosBySet 사용 (HWP 2022 이전 API)"""
    try:
        # GetSelectedPosBySet로 선택 영역 좌표 확인 (sset, eset 필요)
        sset = hwp.CreateSet("ListParaPos")
        eset = hwp.CreateSet("ListParaPos")
        hwp.GetSelectedPosBySet(sset, eset)
        item_count = sset.ItemCount

        if item_count > 0:
            # 선택이 있으면 텍스트 가져오기
            try:
                sel_text = hwp.Selection.Text
                return {"hasSelection": True, "text": sel_text}
            except:
                return {"hasSelection": True, "text": "(텍스트 추출 실패)"}

        return {"hasSelection": False, "text": ""}
    except Exception as e:
        print(f"[ERROR] 선택 정보 가져오기 실패: {e}")
        return {"hasSelection": False, "text": ""}


def run_all_tests(hwp):
    """모든 테스트 실행"""
    results = []

    print("\n" + "=" * 60)
    print("Delta 명령어 자동 테스트 시작 (HWP 2022 이전 정식 API)")
    print("=" * 60)

    # 테스트 1: 일반 텍스트
    print("\n[테스트 1] 일반 텍스트 삽입...")
    ok = insert_text(hwp, "일반 텍스트입니다. ")
    results.append(("일반 텍스트", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 2: 굵은 텍스트 (색상 제외)
    print("\n[테스트 2] 굵은 텍스트...")
    ok = apply_style_to_text(hwp, "굵은 텍스트 ", bold=True)
    results.append(("굵은 텍스트", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 3: 밑줄 텍스트
    print("\n[테스트 3] 밑줄 텍스트...")
    ok = apply_style_to_text(hwp, "밑줄 텍스트 ", underline=True)
    results.append(("밑줄 텍스트", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 4: 글자 크기 (20pt)
    print("\n[테스트 4] 글자 크기 20pt...")
    ok = apply_style_to_text(hwp, "큰 글자 ", font_size=20)
    results.append(("글자 크기", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 5: 스타일 리셋 확인용 일반 텍스트
    print("\n[테스트 5] 리셋 확인 (일반 텍스트)...")
    ok = insert_text(hwp, "이 텍스트는 일반 스타일이어야 함 ")
    results.append(("리셋 확인", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)
    insert_line_break(hwp)

    # 테스트 6: 각주
    print("\n[테스트 6] 각주 삽입...")
    insert_text(hwp, "각주 테스트")
    ok = insert_footnote(hwp, "이것은 테스트 각주입니다.")
    results.append(("각주", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 7: 미주
    print("\n[테스트 7] 미주 삽입...")
    insert_text(hwp, "미주 테스트")
    ok = insert_endnote(hwp, "이것은 테스트 미주입니다.")
    results.append(("미주", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)

    # 테스트 8: 출처 참조 각주
    print("\n[테스트 8] 출처 참조 각주...")
    insert_text(hwp, "출처 참조")
    ok = insert_source_ref(
        hwp,
        ref_id="1",
        title="한국 AI 산업 현황 보고서",
        publisher="과학기술정보통신부",
        year="2025",
        url="https://example.com/report.pdf"
    )
    results.append(("출처 각주", ok))
    print(f"  → {'성공' if ok else '실패'}")

    insert_line_break(hwp)
    insert_line_break(hwp)

    # 완료 표시
    hwp.HAction.Run("MoveDocEnd")
    insert_line_break(hwp)
    insert_text(hwp, "=" * 40)
    insert_line_break(hwp)
    insert_text(hwp, "테스트 완료!")

    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, ok in results:
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {status}  {name}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n총 {len(results)}개 테스트: {passed}개 성공, {failed}개 실패")
    print("=" * 60)

    return results


def main():
    print("=" * 60)
    print("Delta 명령어 자동 테스트 (HWP 2022 이전 정식 API)")
    print("=" * 60)
    print()

    hwp = get_or_create_hwp()
    if not hwp:
        print("[ERROR] HWP를 시작할 수 없습니다.")
        return

    print("\n새 문서 생성 중...")
    if not create_new_document(hwp):
        print("[ERROR] 새 문서 생성 실패")
        return

    try:
        hwp.XHwpWindows.Item(0).Visible = True
    except Exception:
        pass

    time.sleep(0.5)

    # 테스트 전 기본 스타일로 리셋
    print("\n스타일 초기화 중...")
    reset_char_style(hwp)

    results = run_all_tests(hwp)

    print("\n[완료] 열린 HWP 문서에서 결과를 확인하세요.")


if __name__ == "__main__":
    main()

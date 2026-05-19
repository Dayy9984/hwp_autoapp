"""
Track Changes 색상 설정 테스트

목표:
- Insert(삽입): "색만", 녹색
- Delete(삭제): "취소선", 빨강
- Format(서식 변경): "색만", 녹색

사용법:
1. HWP 문서를 먼저 열어둘 것
2. python tests/test_track_changes_color.py 실행
3. 출력된 ParameterSet Item 목록 확인
4. 실제 키 이름을 확인하여 코드 업데이트
"""

import sys
import os
import pytest

# 프로젝트 루트를 sys.path에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 수동 점검용 테스트: 기본 pytest 실행에서는 제외
pytestmark = pytest.mark.skip(reason="manual integration test (requires live HWP COM instance)")

try:
    import pythoncom
    pythoncom.CoInitialize()
except ImportError:
    print("⚠️ pythoncom을 찾을 수 없습니다. pywin32 설치 필요")
    sys.exit(1)

from engine.connection.rot_access import ROTAccessManager
from processing.extraction.hwp_raw_wrapper import HwpRawWrapper


def test_track_change_parameter_items():
    """TrackChangeOption ParameterSet의 Item 목록 조회"""
    print("=" * 60)
    print("Track Changes 색상 설정 테스트")
    print("=" * 60)

    # 1. 열려있는 HWP 문서 가져오기
    print("\n1. HWP 문서 연결 중...")
    raw_hwp = ROTAccessManager.get_first_hwp_instance()

    if not raw_hwp:
        print("❌ 열려있는 HWP 문서가 없습니다.")
        print("   HWP 문서를 먼저 열어주세요.")
        return False

    print("✅ HWP 문서 연결 성공")

    # HwpRawWrapper로 래핑
    print("   HwpRawWrapper로 래핑 중...")
    hwp = HwpRawWrapper(raw_hwp)
    print("✅ HwpRawWrapper 래핑 완료")

    try:
        # 2. CreateSet으로 ParameterSet 생성 시도
        print("\n2. CreateSet으로 ParameterSet 생성 시도...")

        pset = None
        pset_method = None

        # 방법 1: CreateSet 사용
        try:
            print("   방법 1: CreateSet('HTrackChange') 시도...")
            pset = hwp.CreateSet("HTrackChange")
            if pset:
                print("✅ CreateSet('HTrackChange') 성공")
                pset_method = "CreateSet"
        except Exception as e:
            print(f"   ❌ CreateSet 실패: {e}")

        # 방법 2: HParameterSet.HTrackChange 직접 접근
        if not pset:
            try:
                print("   방법 2: HParameterSet.HTrackChange 직접 접근 시도...")
                pset = hwp.HParameterSet.HTrackChange
                print("✅ HParameterSet.HTrackChange 접근 성공")
                pset_method = "HParameterSet"
            except Exception as e:
                print(f"   ❌ HParameterSet 접근 실패: {e}")

        # 방법 3: 다른 ParameterSet 이름 시도
        if not pset:
            for name in ["TrackChange", "HTrackChangeOption", "TrackChangeOption"]:
                try:
                    print(f"   방법 3: CreateSet('{name}') 시도...")
                    pset = hwp.CreateSet(name)
                    if pset:
                        print(f"✅ CreateSet('{name}') 성공")
                        pset_method = f"CreateSet({name})"
                        break
                except Exception as e:
                    print(f"   ❌ CreateSet('{name}') 실패: {e}")

        if not pset:
            print("\n❌ 모든 방법으로 ParameterSet 생성 실패")
            print("   매크로 기록으로 확인이 필요합니다.")
            return False

        # 3. TrackChangeOption 기본값 로드
        print("\n3. TrackChangeOption 기본값 로드 중...")
        try:
            result = hwp.HAction.GetDefault("TrackChangeOption", pset.HSet)
            print(f"✅ GetDefault 성공 (result: {result})")
        except Exception as e:
            print(f"❌ GetDefault 실패: {e}")
            return False

        # 4. ParameterSet Item 목록 조회
        print("\n4. ParameterSet Item 목록 조회:")
        print("-" * 60)

        # 예상 Item 키들 (버전에 따라 다를 수 있음)
        possible_keys = [
            # 표시 방식
            "InsertMarkType", "InsertMark", "InsertType",
            "DeleteMarkType", "DeleteMark", "DeleteType",
            "FormatMarkType", "FormatMark", "FormatType",
            # 색상
            "InsertColor", "InsertColour", "InsertTextColor",
            "DeleteColor", "DeleteColour", "DeleteTextColor",
            "FormatColor", "FormatColour", "FormatTextColor",
            # 기타
            "ChangeColor", "ModifyColor",
        ]

        found_keys = []
        for key in possible_keys:
            try:
                value = pset.Item(key)
                found_keys.append(key)
                print(f"✅ {key:25s} = {value}")
            except Exception:
                pass

        if not found_keys:
            print("⚠️ 예상 키를 찾을 수 없습니다.")
            print("   ParameterSet의 모든 속성을 조회합니다...")

            # pset의 모든 속성 조회
            try:
                pset_attrs = dir(pset)
                print(f"\n   사용 가능한 속성: {pset_attrs}")
            except Exception as e:
                print(f"   속성 조회 실패: {e}")

        # 5. 테스트: 색상 설정 시도
        print("\n5. 색상 설정 테스트 (실행하지 않음, 코드만 표시):")
        print("-" * 60)
        print("# 녹색 (BGR): 0x0000FF00")
        print("# 빨강 (BGR): 0x000000FF")
        print()
        print("pset.SetItem('InsertMarkType', 0)  # 0=색만")
        print("pset.SetItem('DeleteMarkType', 2)  # 2=취소선")
        print("pset.SetItem('FormatMarkType', 0)  # 0=색만")
        print()
        print("pset.SetItem('InsertColor', 0x0000FF00)  # 녹색")
        print("pset.SetItem('DeleteColor', 0x000000FF)  # 빨강")
        print("pset.SetItem('FormatColor', 0x0000FF00)  # 녹색")
        print()
        print("hwp.HAction.Execute('TrackChangeOption', pset.HSet)")

        print("\n" + "=" * 60)
        print("테스트 완료!")
        print("=" * 60)

        if found_keys:
            print(f"\n✅ 발견된 키: {', '.join(found_keys)}")
            print("   위 키들을 사용하여 hwp_raw_wrapper.py에 메서드를 추가할 수 있습니다.")
        else:
            print("\n⚠️ 키를 찾지 못했습니다.")
            print("   매크로 기록으로 정확한 키를 확인하세요.")

        return True

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    test_track_change_parameter_items()

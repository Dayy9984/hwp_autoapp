"""
에러 복구 핸들러

편집 트랜잭션 관리 및 롤백 기능 제공
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.engine.connection.document_connector import HwpConnector


class ErrorHandler:
    """에러 복구 전략

    편집 작업의 트랜잭션을 관리하고,
    실패 시 이전 상태로 롤백하는 기능을 제공합니다.

    복구 전략:
    1. 트랜잭션 시작 시 문서 상태 백업
    2. 롤백 시 Undo를 반복하며 백업 상태와 비교
    3. 백업 상태에 도달하면 중지

    Note:
        HWP는 연속된 편집을 하나의 "편집 세션"으로 묶어 Undo 1회로 모두 취소할 수 있습니다.
        따라서 edit_count 기반 Undo 반복은 정확하지 않습니다.

    Attributes:
        hwp: HwpConnector 인스턴스
        _edit_count: 현재 트랜잭션 내 편집 횟수 (참고용)
        _in_transaction: 트랜잭션 진행 중 여부
        _backup_text: 트랜잭션 시작 시 문서 상태 백업
    """

    def __init__(self, hwp_connector: 'HwpConnector'):
        """에러 핸들러 초기화

        Args:
            hwp_connector: HwpConnector 인스턴스
        """
        self.hwp = hwp_connector
        self._edit_count = 0
        self._in_transaction = False
        self._backup_text = None

    def start_transaction(self) -> None:
        """편집 트랜잭션 시작

        새로운 트랜잭션을 시작하고 현재 문서 상태를 백업합니다.
        """
        self._edit_count = 0
        self._in_transaction = True

        # 현재 문서 상태 백업
        self._backup_text = self.hwp.get_text() or ""

    def record_edit(self) -> None:
        """편집 작업 기록

        트랜잭션 내에서 편집 작업이 수행될 때마다 호출하여
        롤백 시 되돌릴 작업 수를 추적합니다.
        """
        if self._in_transaction:
            self._edit_count += 1

    def commit(self) -> None:
        """편집 확정

        트랜잭션을 정상 종료하고 편집 카운트를 초기화합니다.
        """
        self._edit_count = 0
        self._in_transaction = False

    def rollback(self) -> bool:
        """편집 롤백

        백업된 문서 상태로 복원합니다.

        복구 전략:
        1. Undo를 반복 실행
        2. 각 Undo 후 현재 문서 상태와 백업 상태 비교
        3. 백업 상태에 도달하면 중지
        4. 최대 20회 Undo 시도 (무한 루프 방지)

        Returns:
            bool: 롤백 성공 여부
        """
        if self._backup_text is None:
            print("백업 상태가 없습니다", file=sys.stderr)
            return False

        try:
            max_undo_attempts = 20
            undo_count = 0

            # Undo를 반복하며 백업 상태 복원 시도
            while undo_count < max_undo_attempts:
                # 현재 문서 상태 확인
                current_text = self.hwp.get_text() or ""

                # 백업 상태에 도달했는지 확인
                if current_text == self._backup_text:
                    # 롤백 성공
                    self._edit_count = 0
                    self._in_transaction = False
                    self._backup_text = None
                    return True

                # Undo 실행
                try:
                    self.hwp._hwp.Undo()
                    undo_count += 1

                    # Undo 후 상태 확인
                    text_after_undo = self.hwp.get_text() or ""

                    # Undo가 너무 많이 되돌린 경우 (백업보다 적어진 경우)
                    # Redo 1회로 복원 시도 (HWP는 Undo/Redo를 세션 단위로 묶음)
                    if len(text_after_undo) < len(self._backup_text):
                        try:
                            self.hwp._hwp.Redo()
                            current_text = self.hwp.get_text() or ""

                            # Redo 후 백업 상태와 비교
                            if current_text == self._backup_text:
                                # 롤백 성공
                                self._edit_count = 0
                                self._in_transaction = False
                                self._backup_text = None
                                return True
                        except Exception:
                            pass

                        # Redo로도 정확히 복원 실패 - 트랜잭션 종료
                        # HWP의 편집 세션 그룹화로 인해 정확한 복원이 불가능한 경우
                        self._edit_count = 0
                        self._in_transaction = False
                        self._backup_text = None
                        return False

                except Exception as e:
                    print(f"Undo 실패 (시도 {undo_count}): {e}", file=sys.stderr)
                    # Undo 실패 시에도 백업 상태 확인
                    current_text = self.hwp.get_text() or ""
                    if current_text == self._backup_text:
                        self._edit_count = 0
                        self._in_transaction = False
                        self._backup_text = None
                        return True
                    return False

            # 최대 시도 횟수 초과
            print(f"롤백 실패: {max_undo_attempts}회 Undo 후에도 백업 상태에 도달하지 못했습니다", file=sys.stderr)
            current_text = self.hwp.get_text() or ""
            print(f"현재 상태: {repr(current_text[:100])}", file=sys.stderr)
            print(f"백업 상태: {repr(self._backup_text[:100])}", file=sys.stderr)

            # 실패해도 트랜잭션 종료
            self._edit_count = 0
            self._in_transaction = False
            self._backup_text = None
            return False

        except Exception as e:
            print(f"롤백 실패: {e}", file=sys.stderr)
            self._edit_count = 0
            self._in_transaction = False
            self._backup_text = None
            return False

    def get_edit_count(self) -> int:
        """현재 트랜잭션 내 편집 횟수 조회

        Returns:
            int: 편집 횟수
        """
        return self._edit_count

    def is_in_transaction(self) -> bool:
        """트랜잭션 진행 중 여부 확인

        Returns:
            bool: 트랜잭션 진행 중이면 True
        """
        return self._in_transaction

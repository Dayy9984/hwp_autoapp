"""
편집 세션 관리

전체 워크플로우를 통합하는 세션 관리자
"""

from typing import List, Dict, Optional
from python.engine.connection.document_connector import HwpConnector
from python.document.view_generator import ViewGenerator
from python.document.view_structures import DocumentView
from python.edit.position_registry import PositionRegistry
from python.edit.document_editor import DocumentEditor
from python.llm.delta_parser import DeltaParser
from python.llm.command_executor import CommandExecutor


class EditSession:
    """문서 편집 세션

    HWP 문서 편집의 전체 워크플로우를 관리합니다.

    워크플로우:
    1. initialize() - HWP 연결 및 컴포넌트 초기화
    2. generate_document_view() - DocumentView 생성 및 PositionRegistry 등록
    3. process_llm_response() - LLM 응답 파싱 및 편집 실행

    Attributes:
        connector: HwpConnector 인스턴스
        generator: ViewGenerator 인스턴스
        editor: DocumentEditor 인스턴스
        registry: PositionRegistry 인스턴스
        view: 현재 DocumentView
    """

    def __init__(self, connector: Optional[HwpConnector] = None):
        """세션 초기화

        Args:
            connector: 기존 HwpConnector 인스턴스 (선택사항)
                      None이면 새로운 인스턴스 생성

        모든 컴포넌트를 None으로 초기화합니다.
        실제 초기화는 initialize() 메서드에서 수행됩니다.
        """
        self.connector = connector if connector is not None else HwpConnector()
        self.generator: Optional[ViewGenerator] = None
        self.editor: Optional[DocumentEditor] = None
        self.registry: Optional[PositionRegistry] = None
        self.view: Optional[DocumentView] = None
        self._owns_connector = (connector is None)  # 자체 생성한 connector인지 추적

    def initialize(self) -> bool:
        """세션 초기화

        HWP 연결을 수행하고 모든 컴포넌트를 초기화합니다.

        Returns:
            bool: 초기화 성공 여부
        """
        # HWP 연결 (자체 생성한 connector인 경우에만)
        if self._owns_connector:
            if not self.connector.connect():
                return False
        else:
            # 외부에서 받은 connector는 이미 연결되어 있다고 가정
            if not self.connector.check_alive():
                return False

        # 컴포넌트 초기화
        self.generator = ViewGenerator(self.connector)
        self.registry = PositionRegistry()
        self.editor = DocumentEditor(self.connector, self.registry)

        return True

    def generate_document_view(self) -> Optional[DocumentView]:
        """DocumentView 생성

        현재 HWP 문서에서 DocumentView를 생성하고
        모든 요소를 PositionRegistry에 등록합니다.

        Returns:
            Optional[DocumentView]: 생성된 DocumentView 또는 None (실패 시)
        """
        if not self.generator:
            return None

        # DocumentView 생성
        self.view = self.generator.generate_view()

        if not self.view or not self.registry:
            return None

        # PositionRegistry 초기화
        for elem in self.view.elements:
            self.registry.register(
                elem.element_id,
                elem.position,
                elem.content
            )

        return self.view

    def process_llm_response(self, response: str) -> List[Dict]:
        """LLM 응답 처리

        LLM의 델타 응답을 파싱하여 순차적으로 실행합니다.

        Args:
            response: LLM 델타 응답 문자열

        Returns:
            List[Dict]: 각 델타의 실행 결과
                [{"delta": {...}, "success": True/False}, ...]
        """
        if not self.editor or not self.registry:
            return []

        # 델타 파싱
        parser = DeltaParser()
        deltas = parser.parse(response)

        # 명령 실행기 생성
        executor = CommandExecutor(self.editor, self.registry)

        # 각 델타 실행
        results = []
        for delta in deltas:
            success = executor.execute(delta)
            results.append({
                "delta": delta,
                "success": success
            })

        return results

    def close(self) -> None:
        """세션 종료

        HWP 연결을 종료하고 모든 리소스를 정리합니다.
        """
        # 자체 생성한 connector인 경우에만 disconnect
        if self.connector and self._owns_connector:
            self.connector.disconnect()

        # 컴포넌트 초기화
        self.generator = None
        self.editor = None
        self.registry = None
        self.view = None

    def is_initialized(self) -> bool:
        """초기화 상태 확인

        Returns:
            bool: 세션이 초기화되었으면 True
        """
        return (
            self.connector is not None and
            self.generator is not None and
            self.editor is not None and
            self.registry is not None
        )

    def __enter__(self):
        """컨텍스트 매니저 진입

        Returns:
            EditSession: 현재 인스턴스
        """
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료

        Args:
            exc_type: 예외 타입
            exc_val: 예외 값
            exc_tb: 예외 트레이스백
        """
        self.close()

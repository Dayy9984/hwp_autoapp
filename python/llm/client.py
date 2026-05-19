"""LLM 클라이언트 추상화 - 나중에 서버 API로 교체 가능"""

from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """LLM 응답 결과"""
    success: bool
    commands: list  # 편집 명령 리스트
    message: Optional[str] = None  # 거절 또는 에러 메시지
    token_usage: Optional[dict] = None  # {"input": int, "output": int, "total": int}


class LLMClient(ABC):
    """LLM 클라이언트 인터페이스"""

    @abstractmethod
    async def generate_commands(self, html: str, prompt: str) -> LLMResponse:
        """
        문서 HTML과 사용자 프롬프트를 받아 편집 명령 생성

        Args:
            html: 문서의 HTML 표현 (ID가 포함된)
            prompt: 사용자의 편집 요청

        Returns:
            LLMResponse: 편집 명령 또는 에러
        """
        pass


# 현재 활성화된 클라이언트 타입
_client_type = "openai"  # "openai" | "server"
_client_instance: Optional[LLMClient] = None


def set_client_type(client_type: str):
    """클라이언트 타입 설정 (openai 또는 server)"""
    global _client_type, _client_instance
    _client_type = client_type
    _client_instance = None  # 다음 호출 시 새로 생성


def get_llm_client(api_key: Optional[str] = None) -> LLMClient:
    """현재 설정된 LLM 클라이언트 반환"""
    global _client_instance

    if _client_instance is None:
        if _client_type == "openai":
            from .openai_client import OpenAIClient
            if not api_key:
                raise ValueError("OPENAI_API_KEY_REQUIRED")
            _client_instance = OpenAIClient(api_key=api_key)
        elif _client_type == "server":
            # TODO: 서버 클라이언트 구현 후 추가
            # from .server_client import ServerClient
            # _client_instance = ServerClient()
            raise NotImplementedError("Server client not implemented yet")
        else:
            raise ValueError(f"Unknown client type: {_client_type}")

    return _client_instance

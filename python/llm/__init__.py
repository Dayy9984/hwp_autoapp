# LLM 클라이언트
# 기존 패턴과 2022-호환 규칙에 맞춤

# LLM 클라이언트는 외부 의존성이 있을 수 있으므로 선택적 import
try:
    from .client import LLMClient, get_llm_client
    from .openai_client import OpenAIClient
    from .streaming_client import OpenAIStreamingClient
    _llm_available = True
except ImportError:
    _llm_available = False
    LLMClient = None
    OpenAIClient = None
    OpenAIStreamingClient = None
    get_llm_client = None

__all__ = []

if _llm_available:
    __all__.extend([
        "LLMClient",
        "OpenAIClient",
        "OpenAIStreamingClient",
        "get_llm_client"
    ])

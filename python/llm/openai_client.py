"""
OpenAI API 직접 호출 클라이언트 (레거시)

**DEPRECATED**: 이 모듈은 레거시입니다.
현재는 streaming_client.py의 OpenAIStreamingClient를 사용합니다.

Note: Legacy chat.completions code is commented out.
All active calls use Responses API.
"""

import sys
import json
from typing import Optional
from openai import OpenAI

from .client import LLMClient, LLMResponse
from utils.logger import debug, log_llm_interaction


# 시스템 프롬프트 (레거시 호환)
SYSTEM_PROMPT = """당신은 한글(HWP) 문서 편집 AI입니다.
사용자의 요청에 따라 문서를 편집하는 도구를 호출합니다.
도구 호출 시 block_id는 문서에서 제공된 ID를 그대로 사용하세요.

## 양식 영역 보호 규칙
- 기울임체(Italic) 텍스트는 예시/안내 문구일 가능성이 높으니 삭제 후 해당 내용을 참고해서 작성하세요.
- 색상이 있는 텍스트는 편집하지 마세요
- 테두리가 없거나 배경색이 있는 표 셀은 디자인 요소일 수 있으므로 주의
"""



class OpenAIClient(LLMClient):
    """OpenAI API를 직접 호출하는 클라이언트"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-5.1",
        reasoning_effort: str = "medium",  # "low", "medium", "high"
        verbosity: str = "high"
    ):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY_REQUIRED")

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.client = OpenAI(api_key=self.api_key)

    async def generate_commands(self, html: str, prompt: str) -> LLMResponse:
        """OpenAI API로 편집 명령 생성"""
        response_raw = {}
        commands_raw = []

        try:
            # 사용자 메시지 구성
            user_message = f"""[문서 HTML]
{html}

[사용자 요청]
{prompt}"""

            debug(f"LLM 호출: model={self.model}, html_len={len(html)}, prompt={prompt[:50]}...")

            # Legacy chat.completions code (commented out for full Responses API transition)
            # response = self.client.chat.completions.create(
            #     model=self.model,
            #     messages=[
            #         {"role": "system", "content": SYSTEM_PROMPT},
            #         {"role": "user", "content": user_message}
            #     ],
            #     tools=OPENAI_TOOLS,
            #     tool_choice="auto"
            # )

            # Responses API 호출
            response = self.client.responses.create(
                model=self.model,
                input=user_message,
                instructions=SYSTEM_PROMPT,
                reasoning={"effort": self.reasoning_effort},
                text={"verbosity": self.verbosity},
                max_output_tokens=12000
            )

            # 토큰 사용량 (Responses API)
            token_usage = None
            if getattr(response, "usage", None):
                token_usage = {
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                    "total": response.usage.total_tokens
                }
                debug(f"LLM 응답: tokens={token_usage}")

            # 응답 텍스트 추출 (Responses API)
            output_text = getattr(response, "output_text", "") or ""
            if not output_text and getattr(response, "output", None):
                for item in response.output:
                    if getattr(item, "type", "") == "output_text":
                        output_text += getattr(item, "text", "") or ""
                    elif getattr(item, "type", "") == "message":
                        for content in getattr(item, "content", []) or []:
                            if getattr(content, "type", "") in ("output_text", "text"):
                                output_text += getattr(content, "text", "") or ""

            response_raw = {
                "response_id": getattr(response, "id", None),
                "output_text_preview": output_text[:500] if output_text else ""
            }

            # Responses API는 tool 호출 기반이 아니므로, JSON 형태일 때만 명령 파싱 시도
            commands_raw = []
            parsed = None
            if output_text:
                try:
                    parsed = json.loads(output_text)
                except json.JSONDecodeError:
                    parsed = None

            if isinstance(parsed, dict) and isinstance(parsed.get("commands"), list):
                commands_raw = parsed.get("commands", [])
                debug(f"LLM 응답(JSON) 명령: {len(commands_raw)}개")

                # 명령 형식 변환 (압축형 → 딕셔너리형)
                converted = self._convert_commands(commands_raw)

                # LLM 상호작용 로깅
                log_llm_interaction(
                    prompt=prompt,
                    html=html,
                    response_raw=response_raw,
                    commands=converted,
                    success=True
                )

                return LLMResponse(
                    success=True,
                    commands=converted,
                    token_usage=token_usage
                )

            # JSON 명령이 없으면 메시지로 반환
            message = output_text or "요청을 처리할 수 없습니다."

            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw=response_raw,
                commands=[],
                success=False,
                error=message
            )

            return LLMResponse(
                success=False,
                commands=[],
                message=message,
                token_usage=token_usage
            )

        except Exception as e:
            error_msg = f"LLM 호출 실패: {str(e)}"
            debug(error_msg)

            # LLM 상호작용 로깅
            log_llm_interaction(
                prompt=prompt,
                html=html,
                response_raw=response_raw,
                commands=[],
                success=False,
                error=error_msg
            )

            return LLMResponse(
                success=False,
                commands=[],
                message=error_msg
            )

    def _convert_commands(self, compact_commands: list) -> list:
        """압축형 명령을 딕셔너리형으로 변환"""
        commands = []

        for cmd in compact_commands:
            if not cmd or len(cmd) < 2:
                continue

            action_type = cmd[0]

            if action_type == "E":  # Edit
                if len(cmd) >= 3:
                    commands.append({
                        "action": "edit_document",
                        "id": int(cmd[1]),
                        "content": str(cmd[2]).replace("\n", "<br/>")
                    })

            elif action_type == "A":  # Append
                if len(cmd) >= 3:
                    rows = cmd[2]
                    # rows를 문자열 형태로 변환 (셀은 | 로 구분)
                    row_strings = []
                    for row in rows:
                        if isinstance(row, list):
                            row_strings.append("|".join(
                                str(cell).replace("\n", "<br/>") for cell in row
                            ))
                        else:
                            row_strings.append(str(row))

                    commands.append({
                        "action": "append_table_row",
                        "id": int(cmd[1]),
                        "rows": row_strings
                    })

            elif action_type == "F":  # Find and Replace (기존 패턴)
                if len(cmd) >= 4:
                    commands.append({
                        "action": "find_replace",
                        "id": int(cmd[1]),
                        "old_text": str(cmd[2]),
                        "new_text": str(cmd[3])
                    })

        return commands

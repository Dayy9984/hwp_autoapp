"""
Agent Child Process - LLM API 호출 전담 프로세스

Main Process와 stdin/stdout JSON-RPC로 통신하며 LLM API 호출을 격리합니다.
멀티 프로세스 아키텍처로 안정성과 복구 능력을 향상시킵니다.

**격리 목적:**
- LLM API 크래시 시 Main Process 및 HWP COM Process 영향 방지
- 빠른 복구 (< 1초 Agent 재시작)
- 메모리 격리 (LLM 메모리 누수가 HWP에 영향 없음)

사용법:
    uv run python agent_process.py

통신 프로토콜:
    - stdin으로 JSON 요청 수신
    - stdout으로 JSON 응답 및 Delta 이벤트 전송
    - 각 메시지는 줄바꿈으로 구분

메서드:
    - ping: 연결 확인
    - stream_llm: LLM 스트리밍 호출 및 Delta 전송
    - cancel: 현재 스트리밍 취소
    - quit: 프로세스 종료
"""

import sys
import json
import traceback
import threading
from typing import Optional, Dict, Any
import os

# LLM 클라이언트 import
from llm.streaming_client import (
    get_streaming_client,
    StreamingCommand,
    StreamingResult,
    set_rag_context  # v7.2: OpenAI file_search 기반 RAG
)


class AgentProcess:
    """Agent Child Process - LLM API 호출 전담

    격리된 LLM 처리 프로세스로 Delta 스트리밍 방식을 지원합니다.
    Main Process와 JSON-RPC로 통신하며 Delta 이벤트를 실시간 전송합니다.

    Attributes:
        streaming_client: OpenAI 스트리밍 클라이언트
        current_stream_id: 현재 실행 중인 스트림 ID (취소용)
    """

    def __init__(self):
        self.streaming_client = None
        self.current_stream_id: Optional[int] = None
        self._stdout_lock = threading.Lock()

        # 베타 trace: env 에서 license_token + device_id 읽어 등록
        try:
            import os as _os_beta
            from services.beta_trace import set_credentials
            token = _os_beta.environ.get("INSERTYAI_LICENSE_TOKEN", "")
            device_id = _os_beta.environ.get("INSERTYAI_DEVICE_ID", "")
            if token:
                set_credentials(token, device_id)
                print(f"[AgentProcess] beta_trace creds registered (device={device_id[:8]}...)", file=sys.stderr)
        except Exception as _e:
            print(f"[AgentProcess] beta_trace creds failed: {_e}", file=sys.stderr)

        # 시작 로그
        print("[AgentProcess] Initialized", file=sys.stderr)

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """요청 처리 라우터

        Args:
            request: JSON-RPC 요청 {"id": int, "method": str, "params": dict}

        Returns:
            응답 딕셔너리 {"id": int, "result": any} 또는 {"id": int, "error": str}
        """
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        try:
            if method == "ping":
                return {"id": request_id, "result": {"pong": True}}

            elif method == "beta:set_creds":
                # Electron 이 라이센스 verify 토큰 + device_id 등록 / 갱신
                try:
                    from services.beta_trace import set_credentials
                    result = set_credentials(
                        params.get("token", ""),
                        params.get("device_id", ""),
                    )
                    return {"id": request_id, "result": result}
                except Exception as e:
                    return {"id": request_id, "result": {"ok": False, "error": str(e)}}

            elif method == "stream_llm":
                # LLM 스트리밍 호출 (Delta 전송) - 비동기 처리로 cancel 요청을 받을 수 있게 함
                stream_thread = threading.Thread(
                    target=self._run_stream_llm,
                    args=(request_id, params),
                    daemon=True
                )
                stream_thread.start()
                return None

            elif method == "cancel":
                # 현재 스트리밍 취소
                cancelled = self._cancel_stream()
                return {"id": request_id, "result": {"cancelled": cancelled}}

            elif method == "quit":
                # 프로세스 종료
                return {"id": request_id, "result": {"quitting": True}}

            else:
                return {
                    "id": request_id,
                    "error": f"Unknown method: {method}"
                }

        except Exception as e:
            print(f"[AgentProcess] Error handling request: {e}", file=sys.stderr)
            return {
                "id": request_id,
                "error": str(e),
                "trace": traceback.format_exc()
            }

    def _run_stream_llm(self, request_id: int, params: Dict[str, Any]) -> None:
        """stream_llm 실행 스레드

        스트리밍 진행 중에도 stdin 루프가 cancel을 처리할 수 있도록 분리 실행.
        """
        try:
            result = self._stream_llm(request_id, params)
            self._send_response({"id": request_id, "result": result})
        except Exception as e:
            self._send_response({
                "id": request_id,
                "error": str(e),
                "trace": traceback.format_exc()
            })

    def _stream_llm(self, request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """LLM 스트리밍 호출 및 Delta 전송

        Delta 이벤트를 실시간으로 Main Process에 전송하여 즉각적인 문서 편집을 수행합니다.
    v5.3: File Search Tool Use 지원 - LLM이 필요할 때 File Search를 호출합니다.

        Args:
            request_id: 요청 ID
            params: {
                "html": str,
                "prompt": str,
                "use_delta": bool,
                "use_html": bool,
                "compact_mode": bool,
                "rag_context": {  # v5.3: File Search Tool Use
                    "project_id": str,
                    "chat_id": str,
                    "user_data_path": str,
                    "openai_api_key": str,
                    "file_search_model": str,
                    "embedding_model": str
                },
                "model": str,
                "prompt_custom_rules": str,
                "prompt_custom_enabled": bool,
                "prompt_full_override": str
            }

        Returns:
            {"success": bool, "commands": int, "messages": list[str]}
        """
        html = params.get("html", "")
        prompt = params.get("prompt", "")
        use_delta = params.get("use_delta", True)  # 기본적으로 Delta 모드
        use_html = params.get("use_html", False)
        compact_mode = params.get("compact_mode", True)
        prompt_custom_rules = params.get("prompt_custom_rules")
        prompt_custom_enabled = params.get("prompt_custom_enabled", False)
        prompt_full_override = params.get("prompt_full_override")
        model = params.get("model")

        # Codex OAuth 모드
        codex_mode = params.get("codex_mode", False)
        codex_account_id = params.get("codex_account_id", "")
        print(f"[AgentProcess] codex_mode={codex_mode}, codex_account_id={'set' if codex_account_id else 'empty'}", file=sys.stderr)

        # v7.2: RAG 컨텍스트 (OpenAI file_search 기반)
        rag_context = params.get("rag_context", {})
        enable_rag = bool(rag_context.get("project_id") or rag_context.get("chat_id"))
        openai_api_key = params.get("openai_api_key") or rag_context.get("openai_api_key")

        if not codex_mode and not openai_api_key:
            raise ValueError("OPENAI_API_KEY_REQUIRED")

        if not html or not prompt:
            raise ValueError("html and prompt are required")

        # 라이센스 검증 — 서버 발급 JWT 의 exp 검사 (Layer 3 defense in depth).
        # Frontend gate / IPC handler 가 우회되어도 여기서 차단. Nuitka 빌드 후 우회 매우 어려움.
        # 토큰은 set_credentials() 통해 module-level _LICENSE_TOKEN 에 저장됨 (beta_trace.py).
        try:
            from services.beta_trace import _LICENSE_TOKEN as _lt  # type: ignore
            import services.beta_trace as _bt  # 매 호출 시 최신 값 read
            token = _bt._LICENSE_TOKEN or ""
            if not token:
                raise ValueError("LICENSE_REQUIRED")
            # JWT payload 의 exp 만료 검사 (서명은 Supabase 가 발급 시점에 검증)
            import base64 as _b64, json as _json, time as _time
            try:
                payload_b64 = token.split(".")[1]
                padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
                payload = _json.loads(_b64.urlsafe_b64decode(padded))
                exp = int(payload.get("exp", 0))
                if exp and exp < int(_time.time()):
                    raise ValueError("LICENSE_EXPIRED")
            except (ValueError, IndexError, KeyError) as e:
                if str(e) in ("LICENSE_EXPIRED", "LICENSE_REQUIRED"):
                    raise
                raise ValueError("LICENSE_INVALID")
        except ImportError:
            raise ValueError("LICENSE_REQUIRED")

        # v7.2: RAG 컨텍스트 설정 (OpenAI file_search 기반)
        if enable_rag:
            file_search_model = rag_context.get("file_search_model")
            if file_search_model:
                os.environ["OPENAI_FILE_SEARCH_MODEL"] = file_search_model
            embedding_model = rag_context.get("embedding_model")
            if embedding_model:
                os.environ["OPENAI_EMBEDDING_MODEL"] = embedding_model
            print(
                f"[AgentProcess] Setting RAG context: project={rag_context.get('project_id')}, chat={rag_context.get('chat_id')}",
                file=sys.stderr,
            )
            set_rag_context(
                project_id=rag_context.get("project_id"),
                chat_id=rag_context.get("chat_id"),
                user_data_path=rag_context.get("user_data_path"),
                openai_api_key=openai_api_key,
                codex_mode=codex_mode,
            )

        # 스트림 ID 설정 (취소용)
        self.current_stream_id = request_id

        self.streaming_client = get_streaming_client(
            openai_api_key,
            codex_mode=codex_mode,
            codex_account_id=codex_account_id,
        )
        if model:
            self.streaming_client.model = model

        # Delta 카운터
        deltas_sent = 0
        commands_count = 0
        command_seq = 0
        messages_collected = []

        def on_command(cmd: StreamingCommand):
            """명령이 파싱될 때마다 호출 - Delta 이벤트 전송"""
            nonlocal deltas_sent
            nonlocal commands_count
            nonlocal command_seq
            nonlocal messages_collected
            command_seq += 1

            metadata = dict(cmd.metadata or {})
            metadata["command_seq"] = command_seq

            # Delta 이벤트 생성
            delta_event = {
                "type": "delta",
                "request_id": request_id,
                "action": cmd.action,
                "id": cmd.id,
                "content": cmd.content,
                "message": cmd.message,
                "metadata": metadata,
                "rows": cmd.rows
            }

            # Main Process로 Delta 전송 (stdout)
            self._send_delta(delta_event)
            deltas_sent += 1

            # 통계 수집
            if cmd.action == "message":
                text = cmd.message or cmd.content
                if text:
                    messages_collected.append(text)
            else:
                commands_count += 1
                # 베타 trace: LLM 생성 명령 D1 기록 (applied=0 = 생성만, 적용 전)
                try:
                    from services.beta_trace import HwpTraceSession, _LICENSE_TOKEN, _DEVICE_ID
                    if _LICENSE_TOKEN:
                        # agent process 는 별도 process 라 hwp_com_process 의 session 과 분리
                        # request_id 를 session_id 로 재사용 (LLM 호출 단위 추적)
                        s = HwpTraceSession(_LICENSE_TOKEN, _DEVICE_ID, "", None)
                        s.session_id = f"agent-{request_id}"
                        s.block_cmd(
                            command=str(cmd.action or "unknown"),
                            target_id=str(cmd.id) if cmd.id is not None else None,
                            args={
                                "content_len": len(str(cmd.content or "")),
                                "message_len": len(str(cmd.message or "")),
                                "rows_count": len(cmd.rows) if cmd.rows else 0,
                                **{k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool, type(None)))},
                            },
                            applied=0,
                        )
                        s.flush()
                except Exception:
                    pass

        def on_rag_search_callback(stage: str, message: str):
            """RAG 검색 상태 변경 시 호출 - Progress 이벤트 전송 (v7.2)

            Args:
                stage: 'search' (검색 시작) 또는 'complete' (검색 완료)
                message: 표시할 메시지
            """
            progress_event = {
                "type": "progress",
                "event": "rag:search",
                "data": {
                    "stage": stage,
                    "message": message
                }
            }
            self._send_delta(progress_event)

        def on_tool_validation_error_callback(tool_name: str, reason: str):
            """v7.10 tool validation 실패를 진행 이벤트로 전달."""
            progress_event = {
                "type": "progress",
                "event": "tool:validation_error",
                "data": {
                    "tool_name": tool_name,
                    "reason": reason,
                }
            }
            self._send_delta(progress_event)

        try:
            # LLM 스트리밍 호출
            print(
                f"[AgentProcess] Starting LLM stream: use_delta={use_delta}, use_html={use_html}, compact_mode={compact_mode}, enable_rag={enable_rag}",
                file=sys.stderr,
            )

            result: StreamingResult = self.streaming_client.generate_commands_streaming(
                html=html,
                prompt=prompt,
                on_command=on_command,
                on_file_search=on_rag_search_callback if enable_rag else None,
                on_tool_validation_error=on_tool_validation_error_callback if use_delta else None,
                use_delta=use_delta,
                use_html=use_html,
                compact_mode=compact_mode,
                enable_file_search=enable_rag,
                prompt_custom_rules=prompt_custom_rules,
                prompt_custom_enabled=bool(prompt_custom_enabled),
                prompt_full_override=prompt_full_override,
            )

            print(f"[AgentProcess] LLM stream completed: deltas_sent={deltas_sent}, success={result.success}", file=sys.stderr)

            final_messages = messages_collected
            if not final_messages and isinstance(result.messages, list):
                final_messages = [msg for msg in result.messages if isinstance(msg, str) and msg.strip()]

            return {
                "success": result.success,
                "commands": commands_count,
                "messages": final_messages,
                "deltas_sent": deltas_sent,
                "token_usage": result.token_usage
            }

        except Exception as e:
            print(f"[AgentProcess] LLM stream error: {e}", file=sys.stderr)
            raise

        finally:
            self.current_stream_id = None

    def _cancel_stream(self) -> bool:
        """현재 진행 중인 스트리밍 취소

        Returns:
            bool: 취소 성공 여부 (실행 중인 스트림이 있었으면 True)
        """
        if self.current_stream_id and self.streaming_client:
            print(f"[AgentProcess] Cancelling stream {self.current_stream_id}", file=sys.stderr)
            self.streaming_client.cancel()
            self.current_stream_id = None
            return True
        else:
            print(f"[AgentProcess] No active stream to cancel", file=sys.stderr)
            return False

    def _send_delta(self, delta: Dict[str, Any]):
        """Main Process로 Delta 이벤트 전송

        stdout으로 JSON 라인 전송 (Main Process가 실시간 수신)

        Args:
            delta: Delta 이벤트 딕셔너리
        """
        try:
            json_line = json.dumps(delta, ensure_ascii=False, default=str)
            with self._stdout_lock:
                print(json_line, flush=True)  # stdout으로 전송
        except Exception as e:
            print(f"[AgentProcess] Error sending delta: {e}", file=sys.stderr)

    def _send_response(self, response: Dict[str, Any]):
        """Main Process로 응답 전송

        Args:
            response: 응답 딕셔너리
        """
        try:
            json_line = json.dumps(response, ensure_ascii=False, default=str)
            with self._stdout_lock:
                print(json_line, flush=True)
        except Exception as e:
            print(f"[AgentProcess] Error sending response: {e}", file=sys.stderr)

    def run(self):
        """메인 루프 - stdin에서 요청을 읽고 처리

        JSON-RPC 프로토콜로 Main Process와 통신합니다.
        각 요청은 줄바꿈으로 구분됩니다.
        """
        print("[AgentProcess] Ready, waiting for requests...", file=sys.stderr)

        for line in sys.stdin:
            try:
                line = line.strip()
                if not line:
                    continue

                # JSON 파싱
                request = json.loads(line)

                # 요청 처리
                response = self.handle_request(request)

                # 응답 전송
                if response is not None:
                    self._send_response(response)

                # quit 메서드면 종료
                if request.get("method") == "quit":
                    print("[AgentProcess] Quitting...", file=sys.stderr)
                    break

            except json.JSONDecodeError as e:
                print(f"[AgentProcess] JSON decode error: {e}, line: {line}", file=sys.stderr)
                continue

            except Exception as e:
                print(f"[AgentProcess] Unexpected error: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                continue


if __name__ == "__main__":
    # UTF-8 인코딩 설정
    if sys.platform == 'win32':
        import io
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

    # Agent Process 실행
    agent = AgentProcess()
    agent.run()

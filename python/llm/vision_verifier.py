# -*- coding: utf-8 -*-
"""비전 기반 자동작성 검증 호출 (스펙 §5.4).

편집된 문서를 렌더한 PNG 들을 OpenAI Responses API(image input)에 넘겨,
"사람처럼 라벨↔칸 관계로" 각 요청 항목이 올바른 칸에 실제로 써졌는지 독립 판독한다.

핵심 원칙(스펙 부록):
  - 정답지는 유저의 자연어 의도. AI 의 op 목록(id 번호)이 아니다.
  - 비전은 내부 id/번호를 무시하고 라벨-칸 관계로 판단해 AI 의 칸 지정 오류까지 잡는다.
  - 결과는 유저에게 절대 노출 안 함 — 내부 로그 수집용.

설계 제약:
  - 절대 raise 하지 않는다. 어떤 실패도 {"items": [], "error": <str>} 로 degrade.
  - client 주입 가능(테스트용 fake client). 미주입 시 streaming_client 가 쓰는
    동일 OpenAI Responses API client 를 구성한다(codex 모드 포함).
"""

import base64
import json
import re
import sys
from typing import Any, Optional

# §5.4 출력 JSON 스키마 강제 + 사람-라벨 판독 지시.
_PROMPT_TEMPLATE = """[이미지: 편집된 양식 전체 (페이지별)]

유저 요청(자연어, = 검증의 정답지):
{user_intent}

참고용 — AI 가 적용했다고 보고한 편집 op 목록 (정답 아님, 완전성 체크 보조용):
{op_list}

참고용 — 편집 전 문서 구조 요약 (불변식 대조용; 없으면 "(없음)"):
{structure_summary}

지시:
사람이 양식을 눈으로 읽듯, 라벨과 입력칸의 실제 위치 관계로만 판단하라.
내부 번호/태그/id 는 완전히 무시하라. 유저가 요청한 각 항목에 대해:
  - 해당 라벨의 입력칸에 요청한 값이 실제로 들어가 있는가? (correct)
  - 엉뚱한 칸에 들어갔는가? (wrong_location)
  - 위치는 맞지만 값이 틀렸는가? (wrong_content)
  - 비어 있는가 / 누락되었는가? (missing)
  - 건드리면 안 될 곳을 수정했는가? (over_edit)
  - 참고자료가 있는데 못 찾아 못 채웠는가? (rag_retrieval_fail)
  - 찾았지만 잘못 해석/요약했는가? (rag_interpret_fail)
  - 확신이 안 서면 uncertain (단정 금지).

불변식(정답 없이도 검증):
  - scope_respected: 선택 범위 밖을 편집하지 않았는가
  - structure_preserved: 표 구조/행렬을 변경하지 않았는가
  - labels_preserved: 라벨 셀/placeholder/안내문을 삭제하지 않았는가

반드시 아래 JSON 한 개만 출력하라. 코드펜스/설명 텍스트 금지:
{{
  "items": [
    {{"requested": "<요청 항목>", "verdict": "correct|wrong_location|wrong_content|missing|over_edit|rag_retrieval_fail|rag_interpret_fail|uncertain",
      "found_value": "<발견된 값 또는 null>", "location_ok": true, "content_ok": true,
      "actual_desc": "<실제 어디에 어떻게 들어갔는지>", "confidence": 0.0}}
  ],
  "invariants": {{"scope_respected": true, "structure_preserved": true, "labels_preserved": true}},
  "scores": {{"location": 0.0, "content": 0.0, "preservation": 0.0, "scope": 0.0}},
  "overall": {{"correct": 0, "wrong_location": 0, "wrong_content": 0, "missing": 0,
    "over_edit": 0, "rag_retrieval_fail": 0, "rag_interpret_fail": 0, "uncertain": 0}}
}}"""


def _build_default_client() -> Any:
    """streaming_client 가 쓰는 것과 동일한 OpenAI Responses API client 를 구성.

    api key / codex 모드는 streaming_client._rag_context 에 저장된 값을 재사용.
    구성 불가(키 없음 등) 시 None 반환 → 호출부에서 error dict 로 degrade.
    """
    try:
        from openai import OpenAI
        from llm import streaming_client as sc
        ctx = getattr(sc, "_rag_context", {}) or {}
        api_key = (ctx.get("openai_api_key") or "").strip()
        codex_mode = bool(ctx.get("codex_mode", False))
        codex_account_id = ctx.get("codex_account_id") or ""
        if not api_key:
            return None
        if codex_mode:
            return OpenAI(
                api_key=api_key,
                base_url="https://chatgpt.com/backend-api/codex",
                default_headers={
                    "ChatGPT-Account-Id": codex_account_id,
                    "OpenAI-Beta": "responses=v1",
                    "OpenAI-Originator": "codex",
                },
            )
        return OpenAI(api_key=api_key)
    except Exception as e:
        print(f"[vision_verifier] client build failed: {e}", file=sys.stderr)
        return None


def _is_codex_client(client: Any) -> bool:
    """True if ``client`` targets the codex backend (chatgpt.com/backend-api/codex).

    The codex backend requires ``stream=True`` and an ``instructions`` field on
    responses.create — a plain (non-stream) call returns HTTP 400. We detect it
    from the client's base_url so the same call site works for both API-key and
    codex (OAuth) clients.
    """
    try:
        base = str(getattr(client, "base_url", "") or "")
        return "chatgpt.com/backend-api/codex" in base
    except Exception:
        return False


def _stream_output_text(stream: Any) -> str:
    """Collect output text from a streaming Responses API iterator (codex)."""
    text = ""
    try:
        for event in stream:
            delta = getattr(event, "delta", None)
            if isinstance(delta, str):
                text += delta
                continue
            # Some SDK builds emit a terminal event carrying the full response.
            resp = getattr(event, "response", None)
            if resp is not None:
                full = getattr(resp, "output_text", "") or ""
                if full and len(full) > len(text):
                    text = full
    except Exception as e:
        print(f"[vision_verifier] stream read failed: {e}", file=sys.stderr)
    finally:
        closer = getattr(stream, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    return text


def _extract_output_text(response: Any) -> str:
    """Responses API 응답에서 텍스트 추출 (openai_client.py 패턴 재사용)."""
    output_text = getattr(response, "output_text", "") or ""
    if output_text:
        return output_text
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") == "output_text":
            output_text += getattr(item, "text", "") or ""
        elif getattr(item, "type", "") == "message":
            for content in getattr(item, "content", None) or []:
                if getattr(content, "type", "") in ("output_text", "text"):
                    output_text += getattr(content, "text", "") or ""
    return output_text


def _parse_json(text: str) -> Optional[dict]:
    """모델 출력에서 JSON 객체를 파싱. 코드펜스/잡음 섞여도 첫 {...} 블록 시도."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # 코드펜스/설명 텍스트가 섞인 경우 첫 번째 { ... } 블록 추출 시도.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def verify_vision(
    images: "list[bytes]",
    user_intent: str,
    op_list: list,
    structure_summary: Optional[str],
    model: str,
    client: Any = None,
) -> dict:
    """편집된 문서 렌더 이미지를 비전 모델로 독립 판독한다.

    Args:
        images: 페이지별 PNG bytes 리스트.
        user_intent: 유저의 자연어 지시 (검증 정답지).
        op_list: AI 가 적용한 op 목록 (완전성 체크 보조용, 정답 아님).
        structure_summary: 편집 전 구조 요약 (불변식 대조용; None 가능).
        model: 비전 모델 이름.
        client: OpenAI Responses API client (주입 가능; None 이면 기본 구성).

    Returns:
        {"items": [...], "invariants": {...}, "scores": {...}, "overall": {...}}.
        실패/파싱 실패 시 {"items": [], "error": <str>}. 절대 raise 안 함.
    """
    try:
        if client is None:
            client = _build_default_client()
        if client is None:
            return {"items": [], "error": "no_client"}

        try:
            op_repr = json.dumps(op_list, ensure_ascii=False, default=str)[:4000]
        except Exception:
            op_repr = str(op_list)[:4000]

        prompt_text = _PROMPT_TEMPLATE.format(
            user_intent=user_intent or "(없음)",
            op_list=op_repr or "[]",
            structure_summary=structure_summary or "(없음)",
        )

        content: list[dict] = []
        for png in images or []:
            if not png:
                continue
            b64 = base64.b64encode(png).decode("ascii")
            content.append({
                "type": "input_image",
                "image_url": f"data:image/png;base64,{b64}",
            })
        content.append({"type": "input_text", "text": prompt_text})

        input_payload = [{"type": "message", "role": "user", "content": content}]

        if _is_codex_client(client):
            # Codex backend: requires instructions + stream=True (else HTTP 400).
            stream = client.responses.create(
                model=model,
                instructions=(
                    "당신은 한국어 양식 문서를 사람처럼 판독하는 비전 검증기다. "
                    "지시에 따라 JSON 한 개만 출력하라."
                ),
                input=input_payload,
                store=False,
                stream=True,
            )
            text = _stream_output_text(stream)
        else:
            response = client.responses.create(model=model, input=input_payload)
            text = _extract_output_text(response)
        parsed = _parse_json(text)
        if not isinstance(parsed, dict):
            return {"items": [], "error": "parse"}

        return {
            "items": parsed.get("items", []) or [],
            "invariants": parsed.get("invariants", {}) or {},
            "scores": parsed.get("scores", {}) or {},
            "overall": parsed.get("overall", {}) or {},
        }
    except Exception as e:
        print(f"[vision_verifier] verify_vision failed: {e}", file=sys.stderr)
        return {"items": [], "error": str(e)}

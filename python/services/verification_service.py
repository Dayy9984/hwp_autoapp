# -*- coding: utf-8 -*-
"""검증 오케스트레이션 (스펙 §4, §5.5).

편집된 HWP 를 렌더 → 비전 독립 판독 → 카운트/점수/불변식 집계 → beta_trace 전송.

설계 제약(결정 4):
  - 백그라운드 스레드에서 실행 — 본 편집/UI 절대 차단 0.
  - 전 과정 try/except, 절대 raise 안 함. 어떤 실패도 silent (telemetry only).
  - 유저에게 어떤 검증 결과/오류도 표시하지 않음 — 내부 로그 수집만.
"""

import sys
import uuid

from services.hwp_renderer import render_doc_to_pngs
from services.beta_trace import get_session

try:
    from llm.vision_verifier import verify_vision
except Exception:  # pragma: no cover - import degrade
    def verify_vision(*a, **k):  # type: ignore
        return {"items": [], "error": "vision_verifier_import_failed"}


# verdict → verify_results.*_count 컬럼명 매핑 (스펙 §6.1).
_VERDICT_COUNT_KEYS = {
    "correct": "correct_count",
    "wrong_location": "wrong_location_count",
    "wrong_content": "wrong_content_count",
    "missing": "missing_count",
    "over_edit": "over_edit_count",
    "rag_retrieval_fail": "rag_retrieval_fail_count",
    "rag_interpret_fail": "rag_interpret_fail_count",
    "uncertain": "uncertain_count",
}


def _inv_flag(value) -> "int | None":
    """불변식 bool → 1(통과)/0(위반)/None(미검사) 매핑 (스펙 §6.1)."""
    if value is None:
        return None
    return 1 if value else 0


def _to_score(value) -> "float | None":
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_verification(hwp, *, request_id, user_intent, op_list, model, session=None,
                     max_pages=None) -> None:
    """편집 후 문서 검증 — 백그라운드 스레드 진입점. 절대 raise 안 함.

    Args:
        hwp: 라이브 HWP COM 객체(또는 save_as 호환 connector/raw hwp).
        request_id: chat 사이클 조인키.
        user_intent: 유저의 자연어 지시 (검증 정답지).
        op_list: AI 가 적용한 op 목록 (완전성 체크 보조용).
        model: 비전 모델 이름.
        session: finalize_edits 가 end_session() 이전에 캡처해 넘긴 trace 세션.
            None 이면 get_session() 으로 fallback. 캡처된 세션은 _CURRENT_SESSION 이
            nulled 된 뒤에도 자체 license_token 으로 send_trace/_post_async 전송 가능 →
            end_session() race 로 인한 telemetry 유실 방지.
        max_pages: 렌더 범위(첫 N 페이지). None 이면 전체 문서. 편집 스코프(예: 첫 페이지)
            와 동일하게 맞추면 헤비 문서(전체 PDF 익스포트 hang)도 page 단위로 안전 렌더.
    """
    try:
        # 1. 렌더 (실패/빈 list 면 abort). max_pages 로 렌더 스코프 == 편집 스코프.
        pngs = render_doc_to_pngs(hwp, max_pages=max_pages)
        if not pngs:
            return

        # 2. (선택) 편집 전 구조 요약 — 현재 미수집 → None.
        structure_summary = None

        # 3. 비전 독립 판독.
        verdict = verify_vision(
            images=pngs, user_intent=user_intent, op_list=op_list or [],
            structure_summary=structure_summary, model=model,
        )

        # 4. 집계 → result dict.
        items = verdict.get("items", []) or []
        scores = verdict.get("scores", {}) or {}
        invariants = verdict.get("invariants", {}) or {}

        result = {
            "verify_id": uuid.uuid4().hex,
            "request_id": request_id,
            "page_count": len(pngs),
            "item_count": len(items),
            "model": model,
            "items": items,
        }

        # verdict 별 카운트 (item 들로부터 파생).
        for col in _VERDICT_COUNT_KEYS.values():
            result[col] = 0
        confidences = []
        for it in items:
            verdict_name = it.get("verdict")
            col = _VERDICT_COUNT_KEYS.get(verdict_name)
            if col:
                result[col] += 1
            conf = _to_score(it.get("confidence"))
            if conf is not None:
                confidences.append(conf)
        if confidences:
            result["avg_confidence"] = sum(confidences) / len(confidences)

        # 점수 (비전 scores 에서).
        location = _to_score(scores.get("location"))
        if location is not None:
            result["location_score"] = location
        content = _to_score(scores.get("content"))
        if content is not None:
            result["content_score"] = content
        preservation = _to_score(scores.get("preservation"))
        if preservation is not None:
            result["preservation_score"] = preservation
        scope = _to_score(scores.get("scope"))
        if scope is not None:
            result["scope_score"] = scope

        # 불변식 (비전 invariants → 1/0/None).
        result["inv_scope_respected"] = _inv_flag(invariants.get("scope_respected"))
        result["inv_structure_preserved"] = _inv_flag(invariants.get("structure_preserved"))
        result["inv_labels_preserved"] = _inv_flag(invariants.get("labels_preserved"))

        # 5. 전송 (집계 → verify, 렌더 이미지 → upload_render).
        #    동의 게이팅은 Worker 가 license_key 로 판정 — 클라는 항상 전송 시도.
        #    캡처된 session 우선 — finalize_edits 의 end_session() race 회피.
        #    get_session() 재조회 금지(이미 _CURRENT_SESSION=None 일 수 있음).
        s = session if session is not None else get_session()
        if s:
            s.verify(result)
            s.upload_render(request_id, pngs)
    except Exception as e:
        print(f"[verification_service] run_verification failed: {e}", file=sys.stderr)

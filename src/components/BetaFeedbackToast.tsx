// 베타 피드백 inline 카드 — chat 메시지 스트림 끝에 자연스럽게 노출.
//
// 사용자 요구:
//   - 우상단 fixed toast 가 아닌, chat message 영역 inline 카드
//   - 텍스트: accept = "문서 작성 결과는 만족스러운가요?" / reject = "문서 작성에 어떠한 문제가 있나요?"
//   - 닫기 버튼 + 카드 클릭 시 Tally 폼 모달 노출
//   - 한 사이클 (chat 한 번) 끝나야 노출

import { MessageSquare, X } from 'lucide-react'
import { useBetaSurveyStore } from '../stores/beta-survey-store'
import { TallyEmbedModal } from './modals/TallyEmbedModal'

export function BetaFeedbackInlineCard() {
  const pendingToast = useBetaSurveyStore((s) => s.pendingToast)
  const dismissToast = useBetaSurveyStore((s) => s.dismissToast)
  const openModalFromToast = useBetaSurveyStore((s) => s.openModalFromToast)

  if (!pendingToast) return null

  const headline = pendingToast.kind === 'accept'
    ? '문서 작성 결과는 만족스러운가요?'
    : '문서 작성에 어떠한 문제가 있나요?'

  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-2 mb-2 rounded-xl border border-border bg-bg-secondary px-3 py-2.5 flex items-start gap-2.5"
    >
      <MessageSquare size={14} className="text-accent flex-shrink-0 mt-0.5" />
      <button
        onClick={openModalFromToast}
        className="flex-1 text-left text-[13px] text-text leading-snug bg-transparent border-0 cursor-pointer p-0"
      >
        {headline}
        <span className="ml-2 text-accent text-[12px] font-medium">피드백 →</span>
      </button>
      <button
        onClick={dismissToast}
        aria-label="닫기"
        className="text-text-tertiary hover:text-text bg-transparent border-0 cursor-pointer p-0.5 flex-shrink-0"
      >
        <X size={13} />
      </button>
    </div>
  )
}

// Tally 폼 모달 — 별도로 chat 영역 밖 (App 레벨) 에 mount 되어야 함.
export function BetaFeedbackModal() {
  const activeModal = useBetaSurveyStore((s) => s.activeModal)
  const closeModal = useBetaSurveyStore((s) => s.closeModal)

  if (!activeModal) return null

  return (
    <TallyEmbedModal
      formKey={activeModal.formKey}
      title={activeModal.title}
      onClose={closeModal}
    />
  )
}

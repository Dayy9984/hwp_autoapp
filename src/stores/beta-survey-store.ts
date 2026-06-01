// 베타 피드백 toast 트리거 관리.
//
// 사용자 의도:
//   - 전체 승인/거절 직후 작은 toast(우상단) 노출 → 클릭 시 Tally 폼 열림.
//   - 부분(선택) 승인/거절은 toast 도 띄우지 않음 (사용자 작업 중간 방해 X).
//   - 풀스크린 자동 모달은 금지. 사용자 능동 클릭만 폼 노출.

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { TallyFormKey } from '../components/modals/TallyEmbedModal'

export interface FeedbackToast {
  kind: 'accept' | 'reject'
  formKey: TallyFormKey
  title: string
}

interface BetaSurveyState {
  acceptCount: number
  rejectCount: number
  lastAcceptToastAt: number
  lastRejectToastAt: number
  lastWeeklyAt: number
  // 상단 toast 로 노출할 잠재 피드백 — null 이면 아무 것도 안 보임.
  pendingToast: FeedbackToast | null
  // 사용자가 toast 클릭 시 실제로 띄울 Tally 폼.
  activeModal: { formKey: TallyFormKey; title: string } | null

  recordAccept: () => void
  recordReject: () => void
  dismissToast: () => void
  openModalFromToast: () => void
  // 사용자 능동 클릭 (사이드바 피드백 메뉴 등) — toast 우회하여 즉시 Tally 폼 노출.
  openModal: (formKey: TallyFormKey, title: string) => void
  closeModal: () => void
  shouldShowWeekly: () => boolean
  markWeeklyShown: () => void
}

const HOUR_MS = 3600 * 1000
const DAY_MS = 24 * HOUR_MS

export const useBetaSurveyStore = create<BetaSurveyState>()(
  persist(
    (set, get) => ({
      acceptCount: 0,
      rejectCount: 0,
      lastAcceptToastAt: 0,
      lastRejectToastAt: 0,
      lastWeeklyAt: 0,
      pendingToast: null,
      activeModal: null,

      // 전체 승인 (mode==='all') 직후 매번 toast 노출. (cooldown 제거)
      recordAccept: () => {
        const s = get()
        set({
          acceptCount: s.acceptCount + 1,
          pendingToast: { kind: 'accept', formKey: 'satisfaction_accept', title: '결과 만족도' },
          lastAcceptToastAt: Date.now(),
        })
      },

      // 전체 거절 (mode==='all') 직후 매번 toast 노출. (cooldown 제거)
      recordReject: () => {
        const s = get()
        set({
          rejectCount: s.rejectCount + 1,
          pendingToast: { kind: 'reject', formKey: 'satisfaction_reject', title: '어떤 점이 문제였나요?' },
          lastRejectToastAt: Date.now(),
        })
      },

      dismissToast: () => set({ pendingToast: null }),

      openModalFromToast: () => {
        const t = get().pendingToast
        if (!t) return
        set({
          pendingToast: null,
          activeModal: { formKey: t.formKey, title: t.title },
        })
      },

      openModal: (formKey, title) =>
        set({ activeModal: { formKey, title }, pendingToast: null }),

      closeModal: () => set({ activeModal: null }),

      shouldShowWeekly: () => Date.now() - get().lastWeeklyAt > 7 * DAY_MS,
      markWeeklyShown: () => set({ lastWeeklyAt: Date.now() }),
    }),
    {
      name: 'inserty-beta-survey',
      // 카운트와 cooldown 만 persist. toast/modal 같은 휘발성 상태는 빼서 재시작 시 강제로 안 뜨도록.
      partialize: (s) => ({
        acceptCount: s.acceptCount,
        rejectCount: s.rejectCount,
        lastAcceptToastAt: s.lastAcceptToastAt,
        lastRejectToastAt: s.lastRejectToastAt,
        lastWeeklyAt: s.lastWeeklyAt,
      }),
    }
  )
)

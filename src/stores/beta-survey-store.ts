// 베타 설문 트리거 관리.
// - acceptCount 가 3 의 배수에 도달하면 satisfaction_accept 모달 노출
// - rejectCount 가 증가할 때마다 satisfaction_reject 모달 노출
// - weekly: 마지막 노출 후 7일 경과 시 weekly_usability 모달 노출
// - 사용자 dismiss/skip 도 기록하여 과노출 방지

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { TallyFormKey } from '../components/modals/TallyEmbedModal'

interface BetaSurveyState {
  acceptCount: number
  rejectCount: number
  lastAcceptModalAt: number  // unix ms
  lastRejectModalAt: number
  lastWeeklyAt: number
  currentModal: { formKey: TallyFormKey; title: string } | null

  // mutations
  recordAccept: () => void
  recordReject: () => void
  openModal: (formKey: TallyFormKey, title: string) => void
  closeModal: () => void
  shouldShowWeekly: () => boolean
  markWeeklyShown: () => void
}

const DAY_MS = 24 * 3600 * 1000

export const useBetaSurveyStore = create<BetaSurveyState>()(
  persist(
    (set, get) => ({
      acceptCount: 0,
      rejectCount: 0,
      lastAcceptModalAt: 0,
      lastRejectModalAt: 0,
      lastWeeklyAt: 0,
      currentModal: null,

      recordAccept: () => {
        const s = get()
        const next = s.acceptCount + 1
        set({ acceptCount: next })
        // 3회마다 + 마지막 노출 후 6시간 이상 경과 시
        const cooldown = 6 * 3600 * 1000
        if (next % 3 === 0 && Date.now() - s.lastAcceptModalAt > cooldown) {
          set({
            currentModal: { formKey: 'satisfaction_accept', title: '결과 만족도' },
            lastAcceptModalAt: Date.now(),
          })
        }
      },

      recordReject: () => {
        const s = get()
        const next = s.rejectCount + 1
        set({ rejectCount: next })
        // 매번 노출하되 1시간 쿨다운
        const cooldown = 1 * 3600 * 1000
        if (Date.now() - s.lastRejectModalAt > cooldown) {
          set({
            currentModal: { formKey: 'satisfaction_reject', title: '어떤 점이 문제였나요?' },
            lastRejectModalAt: Date.now(),
          })
        }
      },

      openModal: (formKey, title) => set({ currentModal: { formKey, title } }),
      closeModal: () => set({ currentModal: null }),

      shouldShowWeekly: () => Date.now() - get().lastWeeklyAt > 7 * DAY_MS,
      markWeeklyShown: () => set({ lastWeeklyAt: Date.now() }),
    }),
    { name: 'inserty-beta-survey' }
  )
)

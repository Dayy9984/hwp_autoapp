import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface TrackChangesState {
  // UI 상태
  pending: boolean
  selectionCount: number
  contextVisible: boolean
  showToast: boolean
  toastMessage: string

  // Actions
  updateContext: (context: { pending: boolean; selectionCount: number; contextVisible: boolean }) => void
  setShowToast: (show: boolean, message?: string) => void
  reset: () => void
}

const DEFAULT_TOAST_MESSAGE = '현재 커서에 변경이 없습니다. 변경표시를 클릭 후 다시 시도하세요.'

// pending 만 persist — 앱 재시작 후에도 staged HWP 변경이 있으면 버튼이 다시 노출되어야 함.
// (메시지 metadata.editCount 는 DB persist 됨 — 짝맞춰서 frontend 도 영속 필요.)
// 휘발성 필드 (selectionCount/showToast 등) 는 partialize 로 제외.
export const useTrackChangesStore = create<TrackChangesState>()(
  persist(
    (set) => ({
      pending: false,
      selectionCount: 0,
      contextVisible: false,
      showToast: false,
      toastMessage: '',

      updateContext: (context) =>
        set({
          pending: context.pending,
          selectionCount: context.selectionCount,
          contextVisible: context.contextVisible,
        }),

      setShowToast: (show, message = DEFAULT_TOAST_MESSAGE) =>
        set({
          showToast: show,
          toastMessage: message,
        }),

      reset: () =>
        set({
          pending: false,
          selectionCount: 0,
          contextVisible: false,
          showToast: false,
          toastMessage: '',
        }),
    }),
    {
      name: 'inserty-track-changes',
      partialize: (s) => ({ pending: s.pending, contextVisible: s.contextVisible }),
    }
  )
)

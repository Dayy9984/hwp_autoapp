import { create } from 'zustand'

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

export const useTrackChangesStore = create<TrackChangesState>((set) => ({
  // Initial state
  pending: false,
  selectionCount: 0,
  contextVisible: false,
  showToast: false,
  toastMessage: '',

  // Actions
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
}))

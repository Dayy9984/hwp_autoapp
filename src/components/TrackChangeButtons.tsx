import { useEffect, useRef, useState } from 'react'
import { useTrackChangesStore } from '../stores/track-changes-store'
import { useChatStore } from '../stores/chat-store'
import { Check, X, CheckSquare, XSquare } from 'lucide-react'
import { useBetaSurveyStore } from '../stores/beta-survey-store'
import { IS_BETA } from '../config/beta'
import { track } from '../lib/telemetry'

const normalizeMatchText = (value: string) =>
  value
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[^0-9a-zA-Z\u3131-\uD79D]/g, '')

const extractSelectionText = (ops: any[] | undefined): string | null => {
  if (!Array.isArray(ops)) return null
  const selected = ops.find((op) => op.kind === '선택텍스트')
  if (!selected?.summary || typeof selected.summary !== 'string') return null
  return selected.summary
}

const summarizeDelta = (delta: any): string => {
  const operation = delta?.metadata?.operation || delta?.action || 'edit'
  const idPart = delta?.id !== undefined ? `id=${delta.id}` : ''
  const header = [operation, idPart].filter(Boolean).join(' ')
  const detailSource =
    delta?.metadata?.old_text ||
    (Array.isArray(delta?.rows) ? delta.rows.join(' ') : '') ||
    delta?.content ||
    ''
  const detail = typeof detailSource === 'string' && detailSource.trim()
    ? `: "${detailSource.substring(0, 80)}"`
    : ''
  return `${header}${detail}`
}

const matchRejectedCommands = (deltas: any[] | undefined, selectedText: string | null) => {
  if (!Array.isArray(deltas) || !selectedText) return []
  const selectedNorm = normalizeMatchText(selectedText)
  if (selectedNorm.length < 4) return []

  const matched: { kind: string; summary: string }[] = []
  const seen = new Set<string>()

  for (const delta of deltas) {
    const candidates: string[] = []
    if (typeof delta?.content === 'string') candidates.push(delta.content)
    if (typeof delta?.metadata?.old_text === 'string') candidates.push(delta.metadata.old_text)
    if (Array.isArray(delta?.rows)) {
      delta.rows.forEach((row: any) => {
        if (typeof row === 'string') candidates.push(row)
      })
    }

    let isMatch = false
    for (const candidate of candidates) {
      const candidateNorm = normalizeMatchText(candidate)
      if (candidateNorm.length < 4) continue
      if (selectedNorm.includes(candidateNorm) || candidateNorm.includes(selectedNorm)) {
        isMatch = true
        break
      }
    }

    if (isMatch) {
      const summary = summarizeDelta(delta)
      if (!seen.has(summary)) {
        seen.add(summary)
        matched.push({ kind: '명령', summary })
      }
    }
  }

  return matched
}

interface TrackChangeButtonsProps {
  isEmbedded?: boolean
}

export function TrackChangeButtons({ isEmbedded = false }: TrackChangeButtonsProps) {
  const {
    pending,
    showToast,
    toastMessage,
    updateContext,
    setShowToast,
    reset,
  } = useTrackChangesStore()

  const { getCurrentChat, getDiffModeEnabled } = useChatStore()
  const currentChat = getCurrentChat()
  const diffModeEnabled = currentChat ? getDiffModeEnabled(currentChat.id) : true

  // v4.1.6: 적용된 편집 명령어가 있는지 확인
  const hasExecutedEdits = (() => {
    if (!currentChat) return false
    const lastAssistantMsg = currentChat.messages
      .slice()
      .reverse()
      .find((msg) => msg.role === 'assistant')
    const metadata = lastAssistantMsg?.metadata
    const editCount = typeof metadata?.editCount === 'number' ? metadata.editCount : 0
    if (editCount > 0) return true
    const deltas = metadata?.executedDeltas
    if (!Array.isArray(deltas) || deltas.length === 0) return false
    // 편집 관련 명령어만 필터링 (message 제외)
    return deltas.some((d) => d.action !== 'message' && d.action !== 'thinking')
  })()
  const [isProcessing, setIsProcessing] = useState(false)
  const cachePromiseRef = useRef<Promise<any> | null>(null)

  const resolveDocKey = async (): Promise<string | undefined> => {
    const chat = useChatStore.getState().getCurrentChat()
    if (chat?.boundDocKey) {
      return chat.boundDocKey
    }

    const fallbackDocKey = useChatStore.getState().lastActiveDocKey
    if (fallbackDocKey) {
      if (chat?.id) {
        useChatStore.getState().updateChatBoundDocKey(chat.id, fallbackDocKey)
      }
      return fallbackDocKey
    }

    try {
      const result = await window.electronAPI.doc.getActiveKey()
      if (result?.docKey) {
        useChatStore.getState().setLastActiveDocKey(result.docKey)
        if (chat?.id) {
          useChatStore.getState().updateChatBoundDocKey(chat.id, result.docKey)
        }
        return result.docKey
      }
    } catch (err) {
      console.error('[TrackChangeButtons] getActiveKey failed:', err)
    }

    return undefined
  }

  // diffModeEnabled 가 true → false 로 "실제 전환" 될 때만 리셋.
  // mount 시 첫 effect fire 에서 diffModeEnabled 가 잠깐 false (store hydrate 직전) 였다가
  // true 로 안정화되면 reset 이 잘못 발사되어 pending 도 휘발 → 버튼이 깜빡 사라지는 race 방지.
  const prevDiffModeRef = useRef<boolean | null>(null)
  useEffect(() => {
    const prev = prevDiffModeRef.current
    prevDiffModeRef.current = diffModeEnabled
    // 최초 mount 시 skip (prev === null)
    if (prev === null) return
    // 실제 true → false 전환일 때만 reset
    if (prev === true && diffModeEnabled === false) {
      reset()
    }
  }, [diffModeEnabled, reset])

  // Toast 자동 숨김 (3초 후)
  useEffect(() => {
    if (showToast) {
      const timeoutId = setTimeout(() => {
        setShowToast(false)
      }, 3000)

      return () => clearTimeout(timeoutId)
    }
  }, [showToast, setShowToast])

  const cacheSelection = () => {
    cachePromiseRef.current = window.electronAPI.trackChanges.cacheSelection().catch((err) => {
      console.error('[TrackChangeButtons] cacheSelection failed:', err)
    })
  }

  const awaitCachedSelection = async () => {
    if (cachePromiseRef.current) {
      await cachePromiseRef.current
      cachePromiseRef.current = null
    }
  }

  const handleApplyAll = async () => {
    setIsProcessing(true)
    try {
      const result = await window.electronAPI.trackChanges.applyAll()
      console.log('[TrackChangeButtons] ApplyAll result:', result)

      if (IS_BETA && result?.success) {
        track('delta_accepted', { mode: 'all' })
        useBetaSurveyStore.getState().recordAccept()
      }

      if (result.autoComplete) {
        reset()
      } else {
        const context = await window.electronAPI.trackChanges.getContext()
        updateContext({
          pending: context.pending,
          selectionCount: context.selectionCount,
          contextVisible: context.contextVisible
        })
      }
    } catch (err) {
      console.error('[TrackChangeButtons] ApplyAll failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleRejectAll = async () => {
    setIsProcessing(true)
    try {
      const currentChat = useChatStore.getState().getCurrentChat()
      const chatId = currentChat?.id
      const docKey = await resolveDocKey()

      const result = await window.electronAPI.trackChanges.rejectAll({ docKey, chatId })
      console.log('[TrackChangeButtons] RejectAll result:', result)

      if (result.fact?.mismatch) {
        setShowToast(true, '문서가 변경되었습니다. 다시 시도해주세요.')
        return
      }

      if (IS_BETA && result.fact?.success) {
        track('delta_rejected', { mode: 'all' })
        useBetaSurveyStore.getState().recordReject()
      }

      if (result.fact?.success && chatId && docKey) {
        const explain = {
          ...(result.explain ?? { rejectedOps: [], reason: '' }),
          rejectedOps: []
        }
        useChatStore.getState().enqueuePendingRejection(chatId, docKey, {
          fact: {
            ...result.fact,
            requiresFullRegen: result.fact.requiresFullRegen ?? true,
            uncertain: false,
          },
          explain
        })
      }

      if (result.autoComplete) {
        reset()
      } else {
        const context = await window.electronAPI.trackChanges.getContext()
        updateContext({
          pending: context.pending,
          selectionCount: context.selectionCount,
          contextVisible: context.contextVisible
        })
      }
    } catch (err) {
      console.error('[TrackChangeButtons] RejectAll failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleApplySelected = async () => {
    setIsProcessing(true)
    try {
      await awaitCachedSelection()
      const result = await window.electronAPI.trackChanges.applySelected()
      console.log('[TrackChangeButtons] ApplySelected result:', result)

      // 부분(선택) 승인은 telemetry 만 보내고 피드백 toast 는 띄우지 않음
      // (사용자 작업 중간 흐름 방해 X).
      if (IS_BETA && !result?.showToast) {
        track('delta_accepted', { mode: 'selected' })
      }

      if (result.showToast) {
        setShowToast(true)
      }
    } catch (err) {
      console.error('[TrackChangeButtons] ApplySelected failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleRejectSelected = async () => {
    setIsProcessing(true)
    try {
      await awaitCachedSelection()
      const currentChat = useChatStore.getState().getCurrentChat()
      const chatId = currentChat?.id
      const docKey = await resolveDocKey()

      const result = await window.electronAPI.trackChanges.rejectSelected({ docKey, chatId })
      console.log('[TrackChangeButtons] RejectSelected result:', result)

      if (result.fact?.mismatch) {
        setShowToast(true, '문서가 변경되었습니다. 다시 시도해주세요.')
        return
      }

      // 부분(선택) 거절도 telemetry 만, 피드백 toast 는 띄우지 않음.
      if (IS_BETA && result.fact?.success) {
        track('delta_rejected', { mode: 'selected' })
      }

      if (result.showToast) {
        setShowToast(true)
      }

      if (result.fact?.success && chatId && docKey) {
        const selectedText = extractSelectionText(result.explain?.rejectedOps)
        const executedDeltas = currentChat?.messages
          ?.slice()
          .reverse()
          .find((msg) => msg.role === 'assistant' && msg.metadata?.executedDeltas)?.metadata
          ?.executedDeltas
        const matchedOps = matchRejectedCommands(executedDeltas, selectedText)

        const fact = matchedOps.length > 0
          ? {
              ...result.fact,
              requiresFullRegen: result.fact.requiresFullRegen ?? false,
              uncertain: false,
            }
          : result.fact
        const explain = {
          ...(result.explain ?? { rejectedOps: [], reason: '' }),
          rejectedOps: matchedOps.length > 0 ? matchedOps : (result.explain?.rejectedOps ?? [])
        }
        useChatStore.getState().enqueuePendingRejection(chatId, docKey, {
          fact,
          explain
        })
      }

    } catch (err) {
      console.error('[TrackChangeButtons] RejectSelected failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  // v4.1.6: 안내 토스트는 pending=false여도 노출
  if (!diffModeEnabled || !hasExecutedEdits || (!pending && !showToast)) {
    return null
  }

  const isActionDisabled = isProcessing || !pending

  if (isEmbedded) {
    return (
      <div className="flex flex-col gap-3 p-4 bg-bg rounded-2xl w-full h-full justify-center items-center">
        {showToast && (
          <div className="absolute -top-12 left-1/2 -translate-x-1/2 bg-yellow-100 border border-yellow-400 text-yellow-800 px-4 py-2 rounded text-sm shadow-md whitespace-nowrap z-50">
            {toastMessage}
          </div>
        )}

        <div className="flex gap-2 justify-center w-full">
          <button
            onClick={handleApplyAll}
            disabled={isActionDisabled}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-green-600 text-white rounded-xl shadow-md hover:bg-green-700 hover:shadow-lg hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-bold text-base"
          >
            <CheckSquare size={20} />
            <span>전체 승인</span>
          </button>
          <button
            onClick={handleRejectAll}
            disabled={isActionDisabled}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-red-600 text-white rounded-xl shadow-md hover:bg-red-700 hover:shadow-lg hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-bold text-base"
          >
            <XSquare size={20} />
            <span>전체 거절</span>
          </button>
        </div>

        <div className="flex flex-col w-full gap-2 animate-fade-in">
          <div className="flex gap-2 justify-center w-full">
            <button
              onClick={handleApplySelected}
              onMouseDown={cacheSelection}
              disabled={isActionDisabled}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg shadow hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition font-medium"
            >
              <Check size={18} />
              <span>부분 승인</span>
            </button>
            <button
              onClick={handleRejectSelected}
              onMouseDown={cacheSelection}
              disabled={isActionDisabled}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-orange-600 text-white rounded-lg shadow hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition font-medium"
            >
              <X size={18} />
              <span>부분 거절</span>
            </button>
          </div>
          <p className="text-center text-xs opacity-70" style={{ color: 'var(--text-secondary)' }}>
            💡 변경표시를 드래그로 선택 후 부분 승인/거절하세요.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-50"
      style={{
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        backdropFilter: 'blur(2px)'
      }}
    >
      <div
        className="flex flex-col gap-3 p-4"
        style={{
          backgroundColor: 'var(--bg)',
          borderTop: '2px solid var(--border)',
          maxHeight: '200px'
        }}
      >
        {showToast && (
          <div className="bg-yellow-100 border border-yellow-400 text-yellow-800 px-4 py-2 rounded text-sm mb-2">
            {toastMessage}
          </div>
        )}

        <div className="flex gap-2 justify-center">
          <button
            onClick={handleApplyAll}
            disabled={isActionDisabled}
            className="flex items-center gap-2 px-6 py-3 bg-green-600 text-white rounded-lg shadow-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition font-medium"
          >
            <CheckSquare size={20} />
            <span>전체 승인</span>
          </button>
          <button
            onClick={handleRejectAll}
            disabled={isActionDisabled}
            className="flex items-center gap-2 px-6 py-3 bg-red-600 text-white rounded-lg shadow-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition font-medium"
          >
            <XSquare size={20} />
            <span>전체 거절</span>
          </button>
        </div>

        <div className="flex gap-2 justify-center">
          <button
            onClick={handleApplySelected}
            onMouseDown={cacheSelection}
            disabled={isActionDisabled}
            className="flex items-center gap-2 px-5 py-2 bg-blue-600 text-white rounded-lg shadow hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            <Check size={18} />
            <span>부분 승인</span>
          </button>
          <button
            onClick={handleRejectSelected}
            onMouseDown={cacheSelection}
            disabled={isActionDisabled}
            className="flex items-center gap-2 px-5 py-2 bg-orange-600 text-white rounded-lg shadow hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            <X size={18} />
            <span>부분 거절</span>
          </button>
        </div>
        <p className="text-center text-xs opacity-70" style={{ color: 'var(--text-secondary)' }}>
          💡 변경표시를 드래그로 선택 후 부분 승인/거절하세요.
        </p>
      </div>
    </div>
  )
}

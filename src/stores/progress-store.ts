/**
 * Progress Store - UI 진행 상태 관리 (v4.1.4 UI/UX)
 * 
 * 채팅 영역과 분리된 진행 상태 표시를 위한 스토어
 */

import { create } from 'zustand'

export type ProgressStage = 'idle' | 'scan' | 'thinking' | 'editing' | 'done'

export interface EditLogItem {
    type: string
    content: string
    timestamp: number
}

export interface ThinkingItem {
    content: string
    timestamp: number
    durationMs?: number
    durationLabel?: string
}

export interface ProgressState {
    // 상태
    isActive: boolean
    currentStage: ProgressStage
    stageProgress: number // 0-100
    stageMessage: string

    // v4.1.5: 현재 진행 중인 메시지 ID (프로그레스 패널 위치 추적용)
    activeMessageId: string | null

    // Thinking 로그 (접기/펼치기 UI용)
    thinkingItems: ThinkingItem[]
    thinkingExpanded: boolean

    // v4.1.6: Thinking 완료 상태 (message 표시 타이밍 제어용)
    isThinkingComplete: boolean

    // v4.1.6: 대기 중인 메시지 (thinking 완료 후 표시)
    pendingMessage: string | null

    // 편집 로그
    editLog: EditLogItem[]
    totalEdits: number

    // Actions
    startProgress: (messageId?: string) => void
    setStage: (stage: ProgressStage, message?: string, progress?: number) => void
    addThinking: (content: string, duration?: { durationMs?: number; durationLabel?: string }) => void
    toggleThinking: () => void
    addEdit: (type: string, content: string) => void
    completeProgress: () => void
    reset: () => void

    // v4.1.6: Thinking 완료 표시
    markThinkingComplete: () => void
    // v4.1.6: 대기 메시지 설정/해제
    setPendingMessage: (message: string | null) => void
    consumePendingMessage: () => string | null
}

export const useProgressStore = create<ProgressState>((set, get) => ({
    // Initial state
    isActive: false,
    currentStage: 'idle',
    stageProgress: 0,
    stageMessage: '',
    activeMessageId: null,
    thinkingItems: [],
    thinkingExpanded: false,
    isThinkingComplete: false,
    pendingMessage: null,
    editLog: [],
    totalEdits: 0,

    // Actions
    startProgress: (messageId?: string) => set({
        isActive: true,
        currentStage: 'scan',
        stageProgress: 0,
        stageMessage: '문서 스캔 중...',
        activeMessageId: messageId || null,
        thinkingItems: [],
        thinkingExpanded: false,
        isThinkingComplete: false,
        pendingMessage: null,
        editLog: [],
        totalEdits: 0
    }),

    setStage: (stage, message, progress) => set(state => {
        // v4.1.6: thinking → editing/done 전환 시 thinking 완료로 표시
        const wasThinking = state.currentStage === 'thinking'
        const isLeavingThinking = wasThinking && (stage === 'editing' || stage === 'done')

        return {
            currentStage: stage,
            stageMessage: message ?? state.stageMessage,
            stageProgress: progress ?? (stage === 'done' ? 100 : state.stageProgress),
            isThinkingComplete: isLeavingThinking ? true : state.isThinkingComplete
        }
    }),

    addThinking: (content, duration) => set(state => ({
        thinkingItems: [
            ...state.thinkingItems,
            {
                content,
                timestamp: Date.now(),
                durationMs: duration?.durationMs,
                durationLabel: duration?.durationLabel
            }
        ].slice(-10)
    })),

    toggleThinking: () => set(state => ({
        thinkingExpanded: !state.thinkingExpanded
    })),

    addEdit: (type, content) => set(state => ({
        editLog: [...state.editLog, { type, content, timestamp: Date.now() }].slice(-20),
        totalEdits: state.totalEdits + 1
    })),

    completeProgress: () => set({
        currentStage: 'done',
        stageProgress: 100,
        stageMessage: '완료',
        isThinkingComplete: true
    }),

    reset: () => set({
        isActive: false,
        currentStage: 'idle',
        stageProgress: 0,
        stageMessage: '',
        activeMessageId: null,
        thinkingItems: [],
        thinkingExpanded: false,
        isThinkingComplete: false,
        pendingMessage: null,
        editLog: [],
        totalEdits: 0
    }),

    // v4.1.6: Thinking 완료 표시
    markThinkingComplete: () => set({ isThinkingComplete: true }),

    // v4.1.6: 대기 메시지 관리
    setPendingMessage: (message) => set({ pendingMessage: message }),

    consumePendingMessage: () => {
        const message = get().pendingMessage
        if (message) {
            set({ pendingMessage: null })
        }
        return message
    }
}))

/**
 * 채팅 이력 수집 유틸리티 (v4.1.4)
 * AI 프롬프트에 전달할 대화 컨텍스트 생성
 */

import { Chat, RejectResult } from '../stores/chat-store'

const MAX_RECENT_MESSAGES = 5

function sanitizeHistoryContent(raw: string): string {
    const text = String(raw ?? '').trim()
    if (!text) return ''
    const cleanedLines = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => !!line)
    return cleanedLines.join(' ').trim()
}

export interface ConversationContext {
    // 최근 대화 이력 (최근 N개)
    historyText: string
    // 구조화 거절 컨텍스트 (있으면)
    rejectionText: string | null
    // 플래그
    hasHistory: boolean
    hasRejection: boolean
}

/**
 * 대화 이력을 수집하여 AI 프롬프트용 텍스트로 변환
 */
export function collectConversationContext(
    chat: Chat,
    pendingRejections: RejectResult[] | null
): ConversationContext {
    const result: ConversationContext = {
        historyText: '',
        rejectionText: null,
        hasHistory: false,
        hasRejection: false
    }

    // 1. 대화 이력 수집 (현재 전송 중 메시지는 호출 측에서 제외)
    const messages = chat.messages
    if (messages.length > 0) {
        const recentMessages = messages.slice(-MAX_RECENT_MESSAGES)

        let historyParts: string[] = []

        for (let i = recentMessages.length - 1; i >= 0; i--) {
            const msg = recentMessages[i]
            const roleLabel = msg.role === 'user' ? '사용자' : 'AI'
            const sanitized = sanitizeHistoryContent(msg.content)
            if (!sanitized) continue
            historyParts.unshift(`${roleLabel}: ${sanitized}`)
        }

        if (historyParts.length > 0) {
            result.historyText = historyParts.join('\n')
            result.hasHistory = true
        }
    }

    // 2. 거절 정보 수집 (최근 전체거절 우선)
    if (pendingRejections && pendingRejections.length > 0) {
        const normalized = pendingRejections.filter((item) => !!item?.fact)
        const latestAll = [...normalized]
            .reverse()
            .find((item) => item.fact.rejectionType === 'all')
        const latest = latestAll ?? normalized[normalized.length - 1]

        if (latest) {
            const rejectionType = latest.fact.rejectionType === 'all' ? 'all' : 'partial'
            const requiresFullRegen = latest.fact.requiresFullRegen ?? (rejectionType === 'all')
            const rejectedCount = Number(latest.fact.rejectedCount ?? 0)
            const reason = String(latest.explain?.reason ?? '').trim()
            const ops = Array.isArray(latest.explain?.rejectedOps) ? latest.explain.rejectedOps : []
            const samples = ops
                .slice(0, 3)
                .map((op) => `${op.kind ?? '변경'}${op.summary ? `: ${op.summary}` : ''}`)
                .join(' | ')

            const lines: string[] = [
                `rejection_type: ${rejectionType}`,
                `requires_full_regen: ${requiresFullRegen ? 'true' : 'false'}`,
                `rejected_count: ${rejectedCount}`,
            ]
            if (reason) lines.push(`reason: ${reason}`)
            if (samples) lines.push(`rejected_samples: ${samples}`)

            result.rejectionText = lines.join('\n')
            result.hasRejection = true
        }
    }

    return result
}

/**
 * AI 프롬프트에 삽입할 전체 컨텍스트 문자열 생성
 */
export function buildContextPromptSection(context: ConversationContext): string {
    const sections: string[] = []

    if (context.hasHistory) {
        sections.push(`<PREVIOUS_CONVERSATION>
${context.historyText}
</PREVIOUS_CONVERSATION>`)
    }

    if (context.hasRejection) {
        sections.push(`<REJECTION_CONTEXT>
${context.rejectionText}
</REJECTION_CONTEXT>`)
    }

    return sections.join('\n\n')
}

/**
 * ProgressPanel - 진행 상태 표시 패널
 * 
 * 단계별 진행 상황을 체크리스트 형태로 표시
 */

import { useProgressStore, ProgressStage } from '../stores/progress-store'
import { ChevronDown, ChevronRight, Loader2, Check, FileSearch, Brain, Edit3, FileText } from 'lucide-react'
import { useMemo, useEffect, useRef, useState } from 'react'

const STAGE_ORDER: ProgressStage[] = ['scan', 'thinking', 'editing', 'done']

interface ProgressPanelProps {
    snapshot?: {
        thinkingItems?: {
            content: string
            timestamp: number
            durationMs?: number
            durationLabel?: string
        }[]
        stageMessage?: string
        isDone?: boolean
        thinkingDurationSeconds?: number
    }
}

// 타이핑 효과 컴포넌트 (ChatCanvas와 공유하면 좋지만 일단 중복 정의)
function ThinkingLine({
    text,
    animate,
    onComplete,
    onTick
}: {
    text: string
    animate: boolean
    onComplete?: () => void
    onTick?: () => void
}) {
    const [displayed, setDisplayed] = useState(animate ? '' : text)
    const onCompleteRef = useRef(onComplete)
    const onTickRef = useRef(onTick)

    useEffect(() => {
        onCompleteRef.current = onComplete
    }, [onComplete])

    useEffect(() => {
        onTickRef.current = onTick
    }, [onTick])

    useEffect(() => {
        if (!animate) {
            setDisplayed(text)
            return
        }

        let i = 0
        setDisplayed('')
        const timer = setInterval(() => {
            i++
            setDisplayed(text.substring(0, i))
            onTickRef.current?.()
            if (i >= text.length) {
                clearInterval(timer)
                onCompleteRef.current?.()
            }
        }, 15) // v4.1.6: 10ms → 15ms로 조정 (thinking 타이핑 속도)
        return () => clearInterval(timer)
    }, [text, animate])

    return <span className="whitespace-pre-wrap">{displayed}</span>
}

export function ProgressPanel({ snapshot }: ProgressPanelProps) {
    const store = useProgressStore()

    // 스냅샷이 있으면 그것을 사용, 없으면 스토어 상태 사용
    const isActive = snapshot ? true : store.isActive
    const currentStage = snapshot?.isDone ? 'done' : store.currentStage
    const thinkingItems = snapshot?.thinkingItems || store.thinkingItems
    const stageMessage = snapshot?.stageMessage || store.stageMessage
    const snapshotDuration = snapshot?.thinkingDurationSeconds

    // Thinking 확장 상태는 로컬로 관리 (스냅샷일 경우)
    const [localExpanded, setLocalExpanded] = useState(false)
    const isExpanded = snapshot ? localExpanded : store.thinkingExpanded
    const toggleThinking = snapshot ? () => setLocalExpanded(!localExpanded) : store.toggleThinking
    const detailsRef = useRef<HTMLDivElement>(null)

    // 시간 계산
    const thinkingDuration = useMemo(() => {
        if (typeof snapshotDuration === 'number' && snapshotDuration > 0) {
            return snapshotDuration
        }
        const totalDurationMs = thinkingItems.reduce((sum, item) => (
            sum + (typeof item.durationMs === 'number' ? item.durationMs : 0)
        ), 0)
        if (totalDurationMs > 0) {
            return Math.max(1, Math.round(totalDurationMs / 1000))
        }
        if (thinkingItems.length < 2) return null
        const start = thinkingItems[0].timestamp
        const end = thinkingItems[thinkingItems.length - 1].timestamp
        // 타임스탬프가 유효한지 확인
        if (!start || !end || isNaN(start) || isNaN(end) || end <= start) return null
        return Math.max(1, Math.round((end - start) / 1000))
    }, [thinkingItems, snapshotDuration])

    // 현재 단계가 어느 순서에 있는지 확인
    const currentStepIndex = STAGE_ORDER.indexOf(currentStage === 'idle' ? 'scan' : currentStage)

    // 각 단계별 상태 계산 helper
    const getStepStatus = (stepStage: ProgressStage) => {
        const stepIndex = STAGE_ORDER.indexOf(stepStage)
        if (currentStepIndex > stepIndex) return 'done'
        if (currentStepIndex === stepIndex) return 'active'
        return 'pending'
    }
    const thinkingStatus = getStepStatus('thinking')

    // Thinking 애니메이션 상태 추적 (실시간일 때만 적용)
    const lastAnimatedAtRef = useRef(0)
    const prevExpandedRef = useRef(isExpanded)

    useEffect(() => {
        if (!snapshot && isExpanded && !prevExpandedRef.current) {
            const latestTimestamp = thinkingItems.reduce((max, item) => (
                typeof item.timestamp === 'number' ? Math.max(max, item.timestamp) : max
            ), 0)
            if (latestTimestamp > lastAnimatedAtRef.current) {
                lastAnimatedAtRef.current = latestTimestamp
            }
        }
        prevExpandedRef.current = isExpanded
    }, [isExpanded, snapshot, thinkingItems])

    useEffect(() => {
        if (snapshot) return
        if (!isExpanded || thinkingStatus !== 'active') return
        if (thinkingItems.length === 0) return
        const target = detailsRef.current
        if (!target) return
        requestAnimationFrame(() => {
            if (detailsRef.current) {
                detailsRef.current.scrollTop = detailsRef.current.scrollHeight
            }
        })
    }, [snapshot, isExpanded, thinkingStatus, thinkingItems.length])

    if (!isActive) return null

    return (
        <div className="flex flex-col gap-1 mb-6 animate-in fade-in slide-in-from-top-2 p-1">
            {/* 1. 한글 문서 분석 단계 */}
            <StepItem
                status={getStepStatus('scan')}
                icon={FileSearch}
                label="한글 문서 분석"
                doneLabel="한글 문서 분석 완료"
                activeLabel="한글 문서 분석 중..."
            />

            {/* 2. Thinking 단계 (Expandable) */}
            <StepItem
                status={thinkingStatus}
                icon={Brain}
                label="생각"
                doneLabel={thinkingDuration ? `${thinkingDuration}초 동안 생각 완료` : "생각 완료"}
                activeLabel="생각 중..."
                isExpandable={true}
                isExpanded={isExpanded}
                onToggle={toggleThinking}
                details={thinkingItems.length > 0 ? (
                    <div
                        ref={detailsRef}
                        className="mt-2 pl-2 space-y-1.5 max-h-60 overflow-y-auto thin-scrollbar pr-2"
                    >
                        {thinkingItems.map((item, i) => {
                            const itemTimestamp = typeof item.timestamp === 'number' ? item.timestamp : 0
                            const durationLabel = item.durationLabel
                                ?? (typeof item.durationMs === 'number' && item.durationMs > 0
                                    ? `${Math.max(1, Math.round(item.durationMs / 1000))}s`
                                    : null)
                            const shouldAnimate = !snapshot
                                && isExpanded
                                && thinkingStatus === 'active'
                                && i === thinkingItems.length - 1
                                && itemTimestamp > lastAnimatedAtRef.current

                            return (
                                <div key={i} className="text-sm opacity-80 flex items-start gap-2">
                                <span className="mt-1.5 w-1 h-1 rounded-full bg-current opacity-50 flex-shrink-0" />
                                <span className="leading-relaxed">
                                    <ThinkingLine
                                        text={item.content}
                                        animate={shouldAnimate}
                                        onTick={shouldAnimate ? () => {
                                            if (detailsRef.current) {
                                                detailsRef.current.scrollTop = detailsRef.current.scrollHeight
                                            }
                                        } : undefined}
                                        onComplete={() => {
                                            if (itemTimestamp > lastAnimatedAtRef.current) {
                                                lastAnimatedAtRef.current = itemTimestamp
                                            }
                                        }}
                                    />
                                    {durationLabel && (
                                        <span className="ml-2 text-xs opacity-60">({durationLabel})</span>
                                    )}
                                </span>
                                </div>
                            )
                        })}
                    </div>
                ) : null}
            />

            {/* 3. 편집 단계 */}
            <StepItem
                status={getStepStatus('editing')}
                icon={Edit3}
                label="문서 편집"
                doneLabel="문서 편집 완료"
                activeLabel="문서 편집 중..."
                details={stageMessage && getStepStatus('editing') === 'active' ? (
                    <div className="mt-1 pl-2 text-sm opacity-70">
                        {stageMessage}
                    </div>
                ) : null}
            />
        </div>
    )
}

interface StepItemProps {
    status: 'pending' | 'active' | 'done'
    icon: any
    label: string
    activeLabel?: string
    doneLabel?: string
    isExpandable?: boolean
    isExpanded?: boolean
    onToggle?: () => void
    details?: React.ReactNode
}

function StepItem({
    status,
    icon: Icon,
    label,
    activeLabel,
    doneLabel,
    isExpandable,
    isExpanded,
    onToggle,
    details
}: StepItemProps) {
    if (status === 'pending') return null // 대기 중인 항목은 숨김 (원하면 보이게 할 수 있음)

    const isDone = status === 'done'
    const isActive = status === 'active'

    // 표시할 텍스트 결정
    let displayText = label
    if (isDone && doneLabel) displayText = doneLabel
    if (isActive && activeLabel) displayText = activeLabel

    return (
        <div className="group">
            <div
                className={`
                    flex items-center gap-3 py-2 px-1 rounded transition-colors
                    ${isExpandable ? 'cursor-pointer hover:bg-black/5 dark:hover:bg-white/5' : ''}
                `}
                onClick={isExpandable ? onToggle : undefined}
            >
                {/* 왼쪽 아이콘 영역 (확장 화살표 또는 아이콘) */}
                <div className="flex-shrink-0 w-5 h-5 flex items-center justify-center text-muted-foreground">
                    {isExpandable ? (
                        isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />
                    ) : (
                        <Icon size={18} className={isActive ? "animate-pulse" : ""} />
                    )}
                </div>

                {/* 중앙 텍스트 */}
                <div className="flex-1 font-medium text-[15px]" style={{ color: isActive ? 'var(--foreground)' : 'var(--muted-foreground)' }}>
                    {displayText}
                </div>

                {/* 오른쪽 상태 아이콘 */}
                <div className="flex-shrink-0 w-6 flex items-center justify-end">
                    {isDone ? (
                        <Check size={18} className="text-green-500" />
                    ) : isActive ? (
                        <Loader2 size={16} className="animate-spin text-blue-500" />
                    ) : null}
                </div>
            </div>

            {/* 상세 내용 (Thinking 로그 등) */}
            {isExpanded && details && (
                <div className="ml-2 pl-4 border-l-2 border-slate-100 dark:border-slate-800 mb-2 animate-in fade-in slide-in-from-top-1 text-muted-foreground">
                    {details}
                </div>
            )}
        </div>
    )
}

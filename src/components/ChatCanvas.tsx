import { useEffect, useRef, useState, useCallback } from 'react'

import { useChatStore, Message, ChatAttachment } from '../stores/chat-store'
import { useFolderStore } from '../stores/folder-store'

import { useUIStore } from '../stores/ui-store'

import { useProgressStore } from '../stores/progress-store'

import { ProgressPanel } from './ProgressPanel'

import { Copy, Check, Plus, FileText, File, FileSpreadsheet } from 'lucide-react'


// 타이핑 효과 컴포넌트 (v4.1.6: 속도 조정 15ms → 25ms)

function Typewriter({ text, speed = 25, onComplete }: { text: string; speed?: number; onComplete?: () => void }): JSX.Element {

  const [displayedText, setDisplayedText] = useState('')

  const indexRef = useRef(0)



  useEffect(() => {

    if (!text) {

      setDisplayedText('')

      return

    }



    indexRef.current = 0

    setDisplayedText('')



    const timer = setInterval(() => {

      if (indexRef.current < text.length) {

        indexRef.current += 1

        setDisplayedText(text.substring(0, indexRef.current))

      } else {

        clearInterval(timer)

        onComplete?.()

      }

    }, speed)



    return () => clearInterval(timer)

  }, [text])



  return <span className="whitespace-pre-wrap">{displayedText}</span>

}



const formatAttachmentSize = (size?: number) => {
  if (typeof size !== 'number' || Number.isNaN(size) || size <= 0) return null
  return `${(size / 1024).toFixed(1)} KB`
}

const resolveAttachmentStyle = (attachment: ChatAttachment) => {
  const name = attachment.name?.toLowerCase() ?? ''
  const type = attachment.type?.toLowerCase() ?? ''
  const isPDF = type === 'pdf' || name.endsWith('.pdf')
  const isHwp = type === 'hwp' || name.endsWith('.hwp') || name.endsWith('.hwpx')
  const isExcel = type === 'excel' || name.endsWith('.xls') || name.endsWith('.xlsx') || name.endsWith('.xlsm')

  let IconComp = FileText
  let iconColor = 'text-text-secondary'
  let borderColor = 'border-border'

  if (isPDF) {
    IconComp = File
    iconColor = 'text-red-500'
    borderColor = 'border-red-200 dark:border-red-900/30'
  } else if (isHwp) {
    IconComp = FileText
    iconColor = 'text-blue-500'
    borderColor = 'border-blue-200 dark:border-blue-900/30'
  } else if (isExcel) {
    IconComp = FileSpreadsheet
    iconColor = 'text-green-600'
    borderColor = 'border-green-200 dark:border-green-900/30'
  }

  return { IconComp, iconColor, borderColor }
}

function MessageAttachments({ attachments, align }: { attachments: ChatAttachment[]; align: 'start' | 'end' }) {
  if (!attachments || attachments.length === 0) return null

  return (
    <div className={`mt-2 flex w-full ${align === 'end' ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex gap-2 overflow-x-auto pb-1 thin-scrollbar ${align === 'end' ? 'justify-end' : ''}`}>
        {attachments.map((attachment) => {
          const { IconComp, iconColor, borderColor } = resolveAttachmentStyle(attachment)
          const sizeLabel = formatAttachmentSize(attachment.size)
          return (
            <div
              key={attachment.id}
              className={`relative flex-shrink-0 w-48 p-3 rounded-xl border bg-bg-secondary ${borderColor}`}
            >
              <div className="flex items-start gap-3">
                <div className={`p-2 rounded-lg bg-bg ${iconColor} shadow-sm border border-border/50`}>
                  <IconComp size={20} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm text-text truncate mb-0.5" title={attachment.name}>
                    {attachment.name}
                  </div>
                  {sizeLabel && (
                    <div className="text-xs text-text-tertiary">
                      {sizeLabel}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface MessageBubbleProps {
  message: Message

  isLast: boolean

  isProgressOngoing: boolean

  onAnimationComplete?: () => void

}



function MessageBubble({ message, isLast, isProgressOngoing, onAnimationComplete }: MessageBubbleProps) {

  const isUser = message.role === 'user'

  const [copied, setCopied] = useState(false)
  const attachments = message.metadata?.attachments ?? []
  const hasAttachments = attachments.length > 0


  // v4.1.5: 타이핑 효과 조건 개선

  // - Assistant 메시지이고

  // - 마지막 메시지이고

  // - 진행 중이 아니고

  // - 애니메이션이 아직 완료되지 않았을 때만 타이핑 효과

  const animationAlreadyCompleted = message.metadata?.animationCompleted === true

  const shouldUseTypewriter = !isUser && isLast && !isProgressOngoing && !animationAlreadyCompleted



  const handleCopy = async () => {

    try {

      await navigator.clipboard.writeText(message.content)

      setCopied(true)

      setTimeout(() => setCopied(false), 2000)

    } catch (err) {

      console.error('Failed to copy:', err)

    }

  }



  return (

    <div className={`flex ${isUser ? 'justify-end' : 'justify-start w-full'} group animate-slide-up mb-6`}>

      <div className={`flex flex-col ${isUser ? 'items-end max-w-[80%]' : 'items-start max-w-[95%] w-full'}`}>



        {/* Header Name */}

        <div className={`text-sm font-bold mb-2 ${isUser ? 'text-accent mr-1' : 'text-text-secondary ml-1'}`}>

          {isUser ? '나' : 'Inserty AI'}

        </div>



        {/* Message Box */}

        <div className="relative w-full">

          {isUser ? (

            // User Message: White/Standard Box (Inserty Style Applied Here)

            <div className="relative">
            <div className={`

                px-5 py-4 rounded-2xl rounded-tr-none shadow-sm border border-border/60

                bg-white text-zinc-900 text-[15px] leading-relaxed

                ml-auto table

            `}>

              <span className="whitespace-pre-wrap">{message.content}</span>

            </div>
            {hasAttachments && (
              <MessageAttachments attachments={attachments} align="end" />
            )}
            {message.content && (
              <button
                onClick={handleCopy}
                className="absolute -bottom-6 left-0 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-all hover:bg-bg-tertiary text-text-tertiary hover:text-text"
                title={copied ? '복사됨' : '복사하기'}
              >
                {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}
              </button>
            )}
            </div>
          ) : (

            // Assistant Message: No Box (Plain Text)

            <div className={`

                px-1 py-1 w-full text-text text-[15px] leading-relaxed

                bg-transparent

            `}>

              {shouldUseTypewriter ? (

                <Typewriter text={message.content} onComplete={onAnimationComplete} />

              ) : (

                <span className="whitespace-pre-wrap">{message.content}</span>

              )}



              {/* Copy Button */}

              {message.content && (

                <button

                  onClick={handleCopy}

                  className="absolute -bottom-6 right-0 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-all hover:bg-bg-tertiary text-text-tertiary hover:text-text"

                  title={copied ? '복사됨' : '복사하기'}

                >

                  {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}

                  {/* <span className="text-xs ml-1">복사</span> */}

                </button>

              )}
              {hasAttachments && (
                <MessageAttachments attachments={attachments} align="start" />
              )}


            </div>

          )}

        </div>

      </div>

    </div>

  )

}



export function ChatCanvas() {

  const { getCurrentChat, currentChatId, progressMessage, updateMessageMetadata } = useChatStore()

  const { getChatFolder } = useFolderStore()

  const { openModal } = useUIStore()

  const { isActive: isProgressActive, activeMessageId, currentStage } = useProgressStore()

  const scrollRef = useRef<HTMLDivElement>(null)

  const isProgressOngoing = isProgressActive && currentStage !== 'done'

  // v4.1.6: Smart Auto-scroll - 사용자가 위로 스크롤하면 자동 스크롤 비활성화
  const isUserScrolledUpRef = useRef(false)
  const lastScrollTopRef = useRef(0)

  // v4.1.5: 애니메이션 완료 시 메타데이터 업데이트

  const handleAnimationComplete = useCallback((messageId: string) => {

    if (currentChatId) {

      updateMessageMetadata(currentChatId, messageId, { animationCompleted: true })

    }

  }, [currentChatId, updateMessageMetadata])

  // v4.1.6: 스크롤 이벤트 핸들러 - 사용자 스크롤 위치 추적
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight
    const computedStyle = window.getComputedStyle(scrollRef.current)
    const bottomPadding = Number.parseFloat(computedStyle.paddingBottom || '0') || 0
    const reenableDistance = Math.max(180, bottomPadding + 48)

    // 하단 근처에 도달하면 방향과 무관하게 자동 스크롤 복귀
    if (distanceFromBottom <= reenableDistance) {
      isUserScrolledUpRef.current = false
    } else if (scrollTop < lastScrollTopRef.current - 2) {
      // 상단 방향으로 스크롤하면 자동 스크롤 일시 중단
      isUserScrolledUpRef.current = true
    }

    lastScrollTopRef.current = scrollTop
  }, [])

  const chat = currentChatId ? getCurrentChat() : null

  const currentFolder = currentChatId ? getChatFolder(currentChatId) : null

  // 새 메시지/채팅 전환 시 스크롤 상태 초기화
  useEffect(() => {
    isUserScrolledUpRef.current = false
    lastScrollTopRef.current = 0
  }, [currentChatId])

  // 메시지 개수 변경 시 강제 스크롤 (새 메시지 전송)
  useEffect(() => {
    if (!chat) return
    isUserScrolledUpRef.current = false
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: 'smooth'
        })
      }
    })
  }, [chat?.messages.length])

  // ResizeObserver로 컨테이너 내부 콘텐츠 높이 변화를 능동 감지
  // → 스트리밍 텍스트 갱신, Typewriter 점진적 추가, ProgressPanel thinking 추가 등
  //   모든 DOM 높이 증가에 자동 스크롤 (사용자가 위로 스크롤한 경우 존중)
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    // 첫 번째 자식 요소(내부 콘텐츠 wrapper)를 관찰 — 이 요소의 크기 변화가 곧 콘텐츠 grow
    const inner = container.firstElementChild
    if (!inner) return

    const ro = new ResizeObserver(() => {
      if (isUserScrolledUpRef.current) return
      // smooth보다 auto가 스트리밍에 적합 (연속 호출 시 충돌 방지)
      container.scrollTo({
        top: container.scrollHeight,
        behavior: 'auto'
      })
    })
    ro.observe(inner)
    return () => ro.disconnect()
  }, [currentChatId])



  if (!currentChatId) {

    return (

      <div className="flex-1 flex flex-col items-center justify-center animate-fade-in bg-bg-secondary">

        <div className="text-center">

          <h2 className="text-3xl font-serif font-medium mb-3 text-text">Inserty</h2>

          <p className="text-text-secondary">새 채팅을 시작하여 문서를 작성해보세요.</p>

        </div>

      </div>

    )

  }



  return (

    <div className="flex-1 flex flex-col overflow-hidden min-h-0 bg-bg-secondary">

      {/* Folder Header */}

      {currentFolder && (

        <div className="flex items-center justify-between px-6 py-3 border-b border-border bg-bg/80 backdrop-blur-sm z-10 flex-none">

          <h1 className="text-lg font-serif font-medium text-text" style={{ fontFamily: "'Georgia', 'NanumMyeongjo', 'Nanum Myeongjo', serif" }}>{currentFolder.name}</h1>

          <button

            onClick={() => openModal('add-folder-file', { folderId: currentFolder.id })}

            className="px-3 py-1.5 text-xs font-medium text-white bg-accent hover:bg-accent-dark rounded-lg transition-colors duration-200 shadow-sm flex items-center gap-1.5"

          >

            <Plus size={14} />

            파일 추가

          </button>

        </div>

      )}



      {/* Chat List Area */}

      <div

        ref={scrollRef}

        onScroll={handleScroll}
        data-testid="chat-scroll"

        className="flex-1 overflow-y-auto min-h-0 p-6 scroll-smooth pb-80"

      >

        <div className="max-w-3xl mx-auto space-y-6">

          {chat?.messages.map((message, index) => {

            const isLast = index === chat.messages.length - 1



            // 저장된 Thinking 데이터 확인 (과거 기록용)

            const progressSnapshot = message.metadata?.progressState as {

              thinkingItems?: unknown[]

              stageMessage?: string

              isDone?: boolean

              thinkingDurationSeconds?: number

            } | undefined

            const metadataThinking = Array.isArray(message.metadata?.thinking)

              ? message.metadata?.thinking

              : []

            const progressThinking = Array.isArray(progressSnapshot?.thinkingItems)

              ? progressSnapshot?.thinkingItems

              : []

            const snapshotThinkingRaw = (

              metadataThinking.length >= progressThinking.length

                ? metadataThinking

                : progressThinking

            ) as unknown[]

            const snapshotThinking = Array.isArray(snapshotThinkingRaw)

              ? snapshotThinkingRaw

                .map((item) => {

                  if (typeof item === 'string') {

                    return { content: item, timestamp: 0 }

                  }

                  if (item && typeof item === 'object' && typeof (item as any).content === 'string') {

                    const timestamp = typeof (item as any).timestamp === 'number' ? (item as any).timestamp : 0

                    const durationMs = typeof (item as any).durationMs === 'number' ? (item as any).durationMs : undefined
                    const durationLabel = typeof (item as any).durationLabel === 'string' ? (item as any).durationLabel : undefined

                    return { content: (item as any).content, timestamp, durationMs, durationLabel }

                  }

                  return null

                })

                .filter((item): item is { content: string; timestamp: number; durationMs?: number; durationLabel?: string } => !!item)

              : []



            // v4.1.5: 현재 활성화된 진행 상태인지 확인

            // activeMessageId가 있으면 해당 메시지에서만, 없으면 마지막 메시지에서 표시

            const isCurrentProgress = isProgressActive && (

              activeMessageId ? message.id === activeMessageId : isLast

            )



            // ProgressPanel을 렌더링해야 하는지

            // 1. 현재 진행 중이거나

            // 2. 과거 Thinking 기록이 있을 때

            const hasProgressSnapshot = !!progressSnapshot

            const showProgress = isCurrentProgress || hasProgressSnapshot || snapshotThinking.length > 0

            const isProgressPlaceholder = message.metadata?.progressPlaceholder || message.content === '처리 중...'

            const shouldHideMessage = isProgressPlaceholder && isCurrentProgress && isProgressOngoing



            const messageNode = shouldHideMessage ? null : (

              <MessageBubble

                message={message}

                isLast={isLast}

                isProgressOngoing={isProgressOngoing}

                onAnimationComplete={() => handleAnimationComplete(message.id)}

              />

            )



            const progressNode = showProgress ? (

              <div className="mt-3 mb-6 px-1">

                {isCurrentProgress ? (

                  // 현재 진행 중: Global Store 사용

                  <ProgressPanel />

                ) : (

                  // 과거 기록: Snapshot 데이터 사용

                  <ProgressPanel snapshot={{

                    thinkingItems: snapshotThinking,

                    isDone: progressSnapshot?.isDone ?? true,

                    stageMessage: progressSnapshot?.stageMessage ?? '완료',

                    thinkingDurationSeconds: progressSnapshot?.thinkingDurationSeconds

                  }} />

                )}

              </div>

            ) : null



            return (

              <div key={message.id}>

                {progressNode}

                {messageNode}

              </div>

            )

          })}

        </div>

      </div>

    </div>

  )

}


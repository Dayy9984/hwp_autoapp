import { useState, useEffect, useRef, KeyboardEvent, DragEvent } from 'react'
import { useChatStore, ThinkingSnapshotItem } from '../stores/chat-store'
import { useDocumentStore, UploadedFile, SelectionInfo } from '../stores/document-store'
import { useUIStore } from '../stores/ui-store'
import { useSettingsStore } from '../stores/settings-store'
import { useTrackChangesStore } from '../stores/track-changes-store'
import { useProgressStore } from '../stores/progress-store'
import { useFolderStore } from '../stores/folder-store'
import { DocumentSelector } from './DocumentSelector'
import { SelectionPreview } from './SelectionPreview'
import { DocumentIconRenderer } from './icons/DocumentIconRenderer'
import { TextFileIcon, ExcelIcon, CloseIcon } from './icons'
import { DEFAULT_CHAT_PROJECT_ID } from '../constants/rag'
import {
  Paperclip,
  ArrowUp,
  X,
  Square,
  FileText,
  FileSpreadsheet,
  File,
  ChevronDown,
  FileDiff,
  GitCompare,
  Zap,
  Eye,
  Plus,
  Search
} from 'lucide-react'
import { TrackChangeButtons } from './TrackChangeButtons'
import { collectConversationContext } from '../utils/chat-history'

const { refreshPageInfo } = useDocumentStore.getState()

// v4.1.4: ID 생성 함수
const generateId = () => Math.random().toString(36).substring(2, 15)

// 첨부 파일 진행률 단일 파이프라인(0~100) 매핑
const FILE_PIPELINE_UPLOAD_MAX = 45
const FILE_PIPELINE_INDEX_MIN = FILE_PIPELINE_UPLOAD_MAX + 1

const clampPercent = (value: number) => Math.max(0, Math.min(Math.round(value), 100))

const mapUploadProgressToPipeline = (ratio: number) => {
  const normalized = Math.max(0, Math.min(ratio, 1))
  return clampPercent(normalized * FILE_PIPELINE_UPLOAD_MAX)
}

const mapIndexProgressToPipeline = (progress: number, hasUploadStage: boolean) => {
  const normalized = Math.max(0, Math.min(progress, 100))
  if (!hasUploadStage) return clampPercent(normalized)
  const weighted = FILE_PIPELINE_UPLOAD_MAX + (normalized / 100) * (100 - FILE_PIPELINE_UPLOAD_MAX)
  return clampPercent(weighted)
}

// 진행 상황 상태
interface ProgressState {
  stage: string
  message: string
  edits: { type: string; content: string }[]
  history: string[]
}

interface ChatInputProps {
  folderId?: string
}

export function ChatInput({ folderId }: ChatInputProps = {}) {
  const [input, setInput] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [progress, setProgress] = useState<ProgressState | null>(null)
  const [fileProgress, setFileProgress] = useState<Record<string, { progress: number; message?: string }>>({})
  const {
    currentChatId, createChat, addMessage, updateLastMessage,
    getDiffModeEnabled, setProgressMessage, getCurrentChat,
    isLoading, setIsLoading,
  } = useChatStore()
  const diffModeEnabled = currentChatId ? getDiffModeEnabled(currentChatId) : true
  const {
    selectedDocument,
    uploadedFiles = [],
    uploadTxtFile,
    uploadExcelFile,
    uploadPdfFile,
    uploadHwpFile,
    uploadDocFile,
    uploadPptFile,
    removeUploadedFile,
    clearUploadedFiles,
    selectionInfo,
    setSelectionInfo,
    refreshSelectionInfo
  } = useDocumentStore()
  const { openPopover, navigateToView, currentView, theme, openModal } = useUIStore()
  const { openaiApiKey, connectionMode } = useSettingsStore()
  const { pending, showToast } = useTrackChangesStore()
  const { getChatFolder } = useFolderStore()
  const { isActive: isProgressActive, currentStage: progressStage } = useProgressStore()
  const currentChatForUI = getCurrentChat()
  const hasExecutedEdits = (() => {
    if (!currentChatForUI) return false
    const lastAssistantMsg = currentChatForUI.messages
      .slice()
      .reverse()
      .find((msg) => msg.role === 'assistant')
    const metadata = lastAssistantMsg?.metadata
    const editCount = typeof metadata?.editCount === 'number' ? metadata.editCount : 0
    if (editCount > 0) return true
    const deltas = metadata?.executedDeltas
    if (!Array.isArray(deltas) || deltas.length === 0) return false
    return deltas.some((d) => d.action !== 'message' && d.action !== 'thinking')
  })()
  const shouldShowTrackChanges = diffModeEnabled && !isLoading && hasExecutedEdits && (pending || showToast)
  const showCancelButton = isLoading || (isProgressActive && progressStage !== 'done')

  const progressMessageIdRef = useRef<string | null>(null)
  const dragCounterRef = useRef(0)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const thinkingItemsRef = useRef<ThinkingSnapshotItem[]>([])
  const uploadedFilesRef = useRef<UploadedFile[]>([])
  const progressCleanupRef = useRef<Record<string, number>>({})
  const uploadStageSeenRef = useRef<Set<string>>(new Set())
  const pendingRagDeleteRef = useRef<Set<string>>(new Set())
  const isPausedRef = useRef(false) // 선택 영역 감지 일시 중단 추적

  const makePendingDeleteKey = (chatId: string, fileId: string) => `${chatId}::${fileId}`

  const resolveRagProjectId = (chatId?: string) => {
    if (folderId) return folderId
    if (chatId) {
      const chatFolder = getChatFolder(chatId)
      if (chatFolder?.id) return chatFolder.id
    }
    return DEFAULT_CHAT_PROJECT_ID
  }

  const getMessageMetadata = (chatId: string, messageId: string) => {
    const chat = useChatStore.getState().chats.find((c) => c.id === chatId)
    return chat?.messages.find((msg) => msg.id === messageId)?.metadata
  }

  const ensureOpenAiKey = async () => {
    // Codex(ChatGPT) 모드: 인증 상태 사전 체크
    if (connectionMode === 'codex') {
      const api = (window as unknown as { electronAPI?: any }).electronAPI
      if (!api?.codex?.status) return true
      try {
        const s = await api.codex.status()
        if (!s.installed || !s.authenticated) {
          window.dispatchEvent(new CustomEvent('beta:codex-needs-setup'))
          return false
        }
      } catch { /* fall through */ }
      return true
    }
    if (openaiApiKey && openaiApiKey.trim()) return true
    const confirmed = window.confirm('OpenAI API 키가 필요합니다. 설정의 모델 및 AI 탭에서 키를 등록할까요?')
    if (confirmed) {
      openModal('settings', { tab: 'ai' })
    }
    return false
  }

  const toUserSafeErrorMessage = (raw?: string) => {
    const text = (raw || '').trim()
    if (!text) return '요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.'
    if (text.includes('OPENAI_API_KEY_REQUIRED')) {
      return 'OpenAI API 키가 필요합니다. 설정에서 API 키를 등록해주세요.'
    }
    if (/Error code:\s*\d+/i.test(text) || /invalid_api_key|timeout|connection/i.test(text)) {
      return 'AI 요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
    }
    return text
  }

  const normalizeDurationValue = (value: unknown) => {
    if (value === null || value === undefined) return {}
    if (typeof value === 'number' && Number.isFinite(value)) {
      const durationMs = value >= 1000 ? Math.round(value) : Math.round(value * 1000)
      const durationLabel = value >= 1000 ? `${Math.round(value / 1000)}s` : `${value}s`
      return { durationMs, durationLabel }
    }
    if (typeof value === 'string') {
      const trimmed = value.trim()
      if (!trimmed) return {}
      const msMatch = trimmed.match(/^(\d+(?:\.\d+)?)\s*ms$/i)
      if (msMatch) {
        const durationMs = Math.round(Number(msMatch[1]))
        return { durationMs, durationLabel: trimmed }
      }
      const sMatch = trimmed.match(/^(\d+(?:\.\d+)?)\s*s$/i)
      if (sMatch) {
        const durationMs = Math.round(Number(sMatch[1]) * 1000)
        return { durationMs, durationLabel: trimmed }
      }
      const numericValue = Number(trimmed)
      if (Number.isFinite(numericValue)) {
        const durationMs = Math.round(numericValue * 1000)
        return { durationMs, durationLabel: `${trimmed}s` }
      }
      return { durationLabel: trimmed }
    }
    return {}
  }

  const buildThinkingItem = (content: string, durationValue?: unknown): ThinkingSnapshotItem => {
    const { durationMs, durationLabel } = normalizeDurationValue(durationValue)
    return {
      content,
      timestamp: Date.now(),
      durationMs,
      durationLabel
    }
  }

  const finalizeThinkingItems = (endTimeMs: number) => {
    if (thinkingItemsRef.current.length === 0) return []
    const source = thinkingItemsRef.current
    const next = source.map((item, index) => {
      if (typeof item.durationMs === 'number' && item.durationMs > 0) return item
      const start = item.timestamp
      const nextTimestamp = source[index + 1]?.timestamp ?? endTimeMs
      if (typeof start !== 'number' || typeof nextTimestamp !== 'number') return item
      if (nextTimestamp <= start) return item
      const durationMs = nextTimestamp - start
      const durationLabel = item.durationLabel ?? `${Math.max(1, Math.round(durationMs / 1000))}s`
      return { ...item, durationMs, durationLabel }
    })
    thinkingItemsRef.current = next
    return next
  }

  const getThinkingDurationSeconds = (items: ThinkingSnapshotItem[], endTimeMs: number) => {
    const totalDurationMs = items.reduce((sum, item) => (
      sum + (typeof item.durationMs === 'number' ? item.durationMs : 0)
    ), 0)
    if (totalDurationMs > 0) {
      return Math.max(1, Math.round(totalDurationMs / 1000))
    }
    const firstTimestamp = items[0]?.timestamp
    if (typeof firstTimestamp === 'number' && endTimeMs > firstTimestamp) {
      return Math.max(1, Math.round((endTimeMs - firstTimestamp) / 1000))
    }
    return 0
  }

  const buildProgressSnapshot = (fallbackMessage: string) => {
    const progressState = useProgressStore.getState()
    const now = Date.now()
    const thinkingItems = finalizeThinkingItems(now)
    const thinkingDuration = getThinkingDurationSeconds(thinkingItems, now)

    return {
      thinkingItems,
      stageMessage: progressState.stageMessage || fallbackMessage,
      isDone: true,
      thinkingDurationSeconds: thinkingDuration
    }
  }

  const clearFileProgress = (fileId: string) => {
    const timer = progressCleanupRef.current[fileId]
    if (timer) {
      clearTimeout(timer)
      delete progressCleanupRef.current[fileId]
    }
    setFileProgress((prev) => {
      if (!prev[fileId]) return prev
      const next = { ...prev }
      delete next[fileId]
      return next
    })
  }

  const scheduleFileProgressCleanup = (fileId: string) => {
    const existing = progressCleanupRef.current[fileId]
    if (existing) {
      clearTimeout(existing)
    }
    progressCleanupRef.current[fileId] = window.setTimeout(() => {
      clearFileProgress(fileId)
    }, 1500)
  }

  const indexChatFileInRag = async (chatId: string, file: UploadedFile) => {
    if (!(await ensureOpenAiKey())) {
      return
    }
    const pendingDeleteKey = makePendingDeleteKey(chatId, file.id)
    try {
      const hasUploadStage = uploadStageSeenRef.current.has(file.id)
      const stageStartProgress = hasUploadStage ? FILE_PIPELINE_INDEX_MIN : 1
      const stageStartMessage = hasUploadStage ? '2/2 벡터 인덱싱 준비 중...' : '인덱싱 준비 중...'
      setFileProgress((prev) => ({
        ...prev,
        [file.id]: {
          progress: Math.max(prev[file.id]?.progress ?? 0, stageStartProgress),
          message: stageStartMessage
        }
      }))
      const projectId = resolveRagProjectId(chatId)
      console.log('[ChatInput] RAG 인덱싱 시작:', file.name)
      const ragResult = await window.electronAPI.rag.indexChatFile({
        projectId,
        chatId,
        fileId: file.id,
        textContent: file.content,
        fileName: file.name,
        filePath: file.path,
      })

      if (ragResult.success) {
        // 명시적 제거 요청이 있었던 파일만 인덱싱 완료 시점에 삭제한다.
        // (clearUploadedFiles 같은 UI 정리는 삭제 의도가 아님)
        if (pendingRagDeleteRef.current.has(pendingDeleteKey)) {
          pendingRagDeleteRef.current.delete(pendingDeleteKey)
          console.log('[ChatInput] 제거 대기 파일 인덱싱 완료 후 RAG 삭제:', file.name)
          window.electronAPI.rag.deleteFile({
            projectId,
            chatId,
            fileId: file.id
          }).catch(err => console.error('[ChatInput] RAG 파일 삭제 실패:', err))
          clearFileProgress(file.id)
          return
        }

        console.log('[ChatInput] RAG 인덱싱 완료:', file.name)
        setFileProgress((prev) => ({
          ...prev,
          [file.id]: { progress: 100, message: '인덱싱 완료' }
        }))
        scheduleFileProgressCleanup(file.id)
      } else {
        console.error('[ChatInput] RAG 인덱싱 실패:', ragResult.error)
        pendingRagDeleteRef.current.delete(pendingDeleteKey)
        clearFileProgress(file.id)
      }
    } catch (err) {
      console.error('[ChatInput] RAG 인덱싱 오류:', err)
      pendingRagDeleteRef.current.delete(pendingDeleteKey)
      clearFileProgress(file.id)
    }
  }

  const indexChatFilesInRag = async (chatId: string, files: UploadedFile[]) => {
    if (files.length === 0) return
    for (const file of files) {
      await indexChatFileInRag(chatId, file)
    }
  }

  // 페이지 정보 이벤트 기반 갱신 (polling 제거 - chat 요청 blocking 방지)
  useEffect(() => {
    // 초기 로드
    refreshPageInfo()

    // HWP 창 바인딩 이벤트 시 갱신
    const handleWindowBound = () => {
      refreshPageInfo()
    }

    window.electronAPI.onHwpWindowBound(handleWindowBound)

    // v4.1.4: 활성 문서 변경 이벤트 구독 (v4.2: 활성 문서 힌트 전달)
    const unsubscribeDocChange = window.electronAPI.onActiveDocChanged((docKey, activeDocHint) => {
      if (docKey) {
        useChatStore.getState().setLastActiveDocKey(docKey)
      }
      void refreshPageInfo(activeDocHint)
    })

    return () => {
      // cleanup
      unsubscribeDocChange()
    }
  }, [])

  // HWP 선택 영역 변경 이벤트 수신 (hwp_window_monitor에서 1초마다 감지)
  useEffect(() => {
    const unsubscribe = window.electronAPI.onHwpSelectionChanged((data: any) => {
      // hwp_window_monitor에서 직접 전송된 선택 영역 정보
      if (data && typeof data === 'object') {
        const info: SelectionInfo = {
          hasSelection: data.hasSelection || false,
          selectedText: data.selectedText || '',
          selectedTextFull: data.selectedTextFull || '',
          selectionType: (data.selectionType as SelectionInfo['selectionType']) || 'cursor',
          isTableSelection: data.isTableSelection || false,
          position: data.position,
          filename: data.filename,
        }
        setSelectionInfo(info)
      }
    })

    return () => {
      unsubscribe()
    }
  }, [setSelectionInfo])

  // 파이프라인 완료 시 선택 영역 감지 재개
  useEffect(() => {
    if (!isLoading && !shouldShowTrackChanges && isPausedRef.current) {
      window.electronAPI.selection.resumeDetection()
      isPausedRef.current = false
    }
  }, [isLoading, shouldShowTrackChanges])

  useEffect(() => {
    uploadedFilesRef.current = uploadedFiles
    const activeIds = new Set(uploadedFiles.map((file) => file.id))
    for (const trackedFileId of Array.from(uploadStageSeenRef.current)) {
      if (!activeIds.has(trackedFileId)) {
        uploadStageSeenRef.current.delete(trackedFileId)
      }
    }
    setFileProgress((prev) => {
      let changed = false
      const next: Record<string, { progress: number; message?: string }> = {}
      for (const [fileId, info] of Object.entries(prev)) {
        if (activeIds.has(fileId)) {
          next[fileId] = info
        } else {
          changed = true
          const timer = progressCleanupRef.current[fileId]
          if (timer) {
            clearTimeout(timer)
            delete progressCleanupRef.current[fileId]
          }
        }
      }
      return changed ? next : prev
    })
  }, [uploadedFiles])

  const uploadFileByPath = async (filePath: string): Promise<UploadedFile | null> => {
    const ext = filePath.split('.').pop()?.toLowerCase()
    if (!ext) {
      console.warn('[ChatInput] File extension missing:', filePath)
      return null
    }
    if (ext === 'doc') {
      console.warn('[ChatInput] Legacy DOC format not supported:', filePath)
      return null
    }
    if (ext === 'ppt') {
      console.warn('[ChatInput] Legacy PPT format not supported:', filePath)
      return null
    }
    if (ext === 'txt' || ext === 'md') return uploadTxtFile(filePath)
    if (['xlsx', 'xls', 'xlsm'].includes(ext)) return uploadExcelFile(filePath)
    if (ext === 'pdf') return uploadPdfFile(filePath)
    if (['hwp', 'hwpx'].includes(ext)) return uploadHwpFile(filePath)
    if (ext === 'docx') return uploadDocFile(filePath)
    if (ext === 'pptx') return uploadPptFile(filePath)
    console.warn('[ChatInput] Unsupported file type:', ext)
    return null
  }

  const getUploadFailureMessage = (filePath: string): string => {
    const ext = filePath.split('.').pop()?.toLowerCase()
    if (!ext) return '파일 확장자를 확인할 수 없습니다.'
    if (ext === 'doc') return 'DOC 형식은 지원되지 않습니다. DOCX로 변환 후 다시 시도해주세요.'
    if (ext === 'ppt') return 'PPT 형식은 지원되지 않습니다. PPTX로 변환 후 다시 시도해주세요.'
    return `파일 업로드에 실패했습니다 (.${ext}).`
  }

  const showUploadFailure = (fileName: string, filePath?: string) => {
    const reason = filePath ? getUploadFailureMessage(filePath) : '파일 경로를 확인할 수 없습니다.'
    window.alert(`[${fileName}] ${reason}`)
  }

  const persistChatFile = async (chatId: string, file: UploadedFile): Promise<boolean> => {
    if (!window.electronAPI?.invoke) {
      console.warn('[ChatInput] chatFiles:add skipped: electronAPI not available')
      return false
    }
    const ext = file.path.split('.').pop()?.toLowerCase()
    try {
      const result = await window.electronAPI.invoke('chatFiles:add', {
        chatId,
        fileId: file.id,
        fileName: file.name,
        filePath: file.path,
        extension: ext,
        size: file.size,
      })
      if (result?.success) {
        console.log('[ChatInput] DB 저장 완료:', file.name)
        return true
      }
      console.error('[ChatInput] chatFiles:add failed:', result?.error)
      return false
    } catch (err) {
      console.error('[ChatInput] chatFiles:add failed:', err)
      return false
    }
  }

  const persistUploadedFiles = async (chatId: string, files: UploadedFile[] = uploadedFiles) => {
    for (const file of files) {
      await persistChatFile(chatId, file)
    }
  }

  const removeChatFile = async (chatId: string, fileId: string): Promise<boolean> => {
    if (!window.electronAPI?.invoke) return false
    try {
      const result = await window.electronAPI.invoke('chatFiles:remove', { chatId, fileId })
      if (!result?.success) {
        console.error('[ChatInput] chatFiles:remove failed:', result?.error)
        return false
      }
      return true
    } catch (err) {
      console.error('[ChatInput] chatFiles:remove failed:', err)
      return false
    }
  }

  // 드래그 앤 드랍 핸들러 (카운터 방식으로 깜빡임 방지)
  const handleDragEnter = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (dragCounterRef.current === 1) {
      setIsDragging(true)
    }
  }

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setIsDragging(false)
    }
  }

  const handleDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current = 0
    setIsDragging(false)

    const files = e.dataTransfer.files
    if (files.length === 0) return
    console.log('[ChatInput] Drop event:', files.length)

    // v6.1: 프로젝트 컨텍스트에서는 AddFolderFileModal 열기
    if (folderId) {
      console.log('[ChatInput] 프로젝트 컨텍스트 - AddFolderFileModal 열기:', folderId)
      openModal('add-folder-file', { folderId })
      return
    }

    const remainingSlots = Math.max(0, 5 - uploadedFilesRef.current.length)
    if (remainingSlots <= 0) {
      window.alert('채팅 첨부는 최대 5개까지 가능합니다.')
      return
    }
    const droppedFiles = Array.from(files).slice(0, remainingSlots)
    if (droppedFiles.length < files.length) {
      window.alert(`채팅 첨부는 최대 5개까지 가능합니다. ${droppedFiles.length}개 파일만 추가합니다.`)
    }

    // 다중 파일 지원 (최대 5개까지)
    for (const file of droppedFiles) {
      // Electron webUtils.getPathForFile
      let filePath = window.electronAPI.file.getPathForFile(file)

      // v6.1: getPathForFile 실패 시 File API fallback
      if (!filePath) {
        console.warn('[ChatInput] getPathForFile failed, trying File API fallback:', file.name)
        try {
          // 임시 파일로 저장 후 경로 획득
          const arrayBuffer = await file.arrayBuffer()
          const result = await window.electronAPI.invoke('file:saveTempFile', {
            fileName: file.name,
            buffer: Array.from(new Uint8Array(arrayBuffer))
          })
          if (result?.success && result.path) {
            filePath = result.path
            console.log('[ChatInput] File API fallback success:', filePath)
          }
        } catch (err) {
          console.error('[ChatInput] File API fallback failed:', file.name, err)
        }
      }

      if (!filePath) {
        console.error('[ChatInput] Failed to get file path:', file.name)
        showUploadFailure(file.name)
        continue
      }

      console.log('[ChatInput] Drag upload start:', file.name)
      const uploadedFile = await uploadFileByPath(filePath)
      if (!uploadedFile) {
        console.warn('[ChatInput] Upload skipped:', file.name)
        showUploadFailure(file.name, filePath)
        continue
      }
      console.log('[ChatInput] File parsed:', uploadedFile.name)
      if (currentChatId) {
        await persistChatFile(currentChatId, uploadedFile)
      }

      // RAG 인덱싱 (채팅 스코프) - 파일 업로드 성공 시
      if (currentChatId) {
        await indexChatFileInRag(currentChatId, uploadedFile)
      }
    }
  }

  // Progress 이벤트 구독 - progress-store로 연결
  useEffect(() => {
    const {
      startProgress, setStage, addThinking, addEdit, completeProgress, reset: resetProgress
    } = useProgressStore.getState()

    const unsubscribe = window.electronAPI.chat.onProgress((event, data) => {
      console.log('[Progress]', event, data)

      if (event === 'start') {
        thinkingItemsRef.current = []
        startProgress(progressMessageIdRef.current || undefined)
        setProgress({
          stage: 'initializing',
          message: '시작 중...',
          edits: [],
          history: ['시작 중...'],
        })
        setProgressMessage('시작 중...')
      } else if (event === 'stage') {
        // stage 이벤트를 progress-store로 전달
        const stageMap: Record<string, any> = {
          'init': 'scan',
          'reading': 'scan',
          'parsing': 'scan',
          'context_ready': 'thinking',
          'thinking': 'thinking',
          'editing': 'editing',
          'finishing': 'done'
        }
        const mappedStage = stageMap[data.stage] || 'scan'
        setStage(mappedStage, data.message)

        setProgress((prev) => ({
          ...prev,
          stage: data.stage,
          message: data.message || prev?.message || '',
          edits: prev?.edits ?? [],
          history: [
            ...(prev?.history ?? []),
            ...(data.message ? [data.message] : []),
          ].slice(-5),
        }))
        setProgressMessage(data.message || '진행 중...')
      } else if (event === 'thinking') {
        // Thinking 내용을 store에 추가
        const thinkingMessage = data.content || 'AI가 분석 중...'
        const durationValue = data.duration ?? data.metadata?.time
        const thinkingItem = buildThinkingItem(thinkingMessage, durationValue)
        thinkingItemsRef.current.push(thinkingItem)
        addThinking(thinkingMessage, {
          durationMs: thinkingItem.durationMs,
          durationLabel: thinkingItem.durationLabel
        })
        setStage('thinking', thinkingMessage)

        const displayMessage = thinkingItem.durationLabel
          ? `${thinkingMessage} (${thinkingItem.durationLabel})`
          : thinkingMessage

        setProgress((prev) => ({
          ...prev,
          stage: 'thinking',
          message: displayMessage,
          edits: prev?.edits ?? [],
          history: [...(prev?.history ?? []), displayMessage].slice(-5),
        }))
        setProgressMessage(displayMessage)
      } else if (event === 'rag:search' || event === 'rag:progress') {
        // RAG 검색 진행 상황
        const ragMessage = data.message || '검색 중...'
        const ragThinking = `${ragMessage}`
        thinkingItemsRef.current.push(buildThinkingItem(ragThinking))
        addThinking(ragThinking)

        if (data.stage === 'search') {
          setStage('thinking', ragMessage)
          setProgress((prev) => ({
            ...prev,
            stage: 'thinking',
            message: ragMessage,
            edits: prev?.edits ?? [],
            history: [...(prev?.history ?? []), ragMessage].slice(-5),
          }))
          setProgressMessage(ragMessage)
        } else if (data.stage === 'complete') {
          setProgress((prev) => ({
            stage: prev?.stage ?? 'complete',
            message: ragMessage,
            edits: prev?.edits ?? [],
            history: [...(prev?.history ?? []), ragMessage].slice(-5),
          }))
          setProgressMessage(ragMessage)
        }
      } else if (event === 'file:upload:progress') {
        // 1/2 단계: 파일 추출 진행 상황 (단일 파이프라인 0~45%)
        const filePath = data.filePath
        if (!filePath) return

        // 파일 경로로 업로드 중인 파일 찾기
        const uploadingFile = uploadedFilesRef.current.find(f => f.path === filePath)
        if (uploadingFile) {
          uploadStageSeenRef.current.add(uploadingFile.id)
          const rawRatio = typeof data.progress === 'number'
            ? Math.max(0, Math.min(data.progress, 1))
            : 0
          const progressPct = mapUploadProgressToPipeline(rawRatio)
          const baseMessage = typeof data.message === 'string' && data.message.trim()
            ? data.message
            : '문서 추출 중...'
          const message = `1/2 ${baseMessage}`
          setFileProgress((prev) => {
            const currentProgress = prev[uploadingFile.id]?.progress || 0
            // 단조증가 보장: 새 progress가 현재보다 작으면 무시
            if (progressPct < currentProgress && currentProgress < 100) {
              return prev
            }
            return {
              ...prev,
              [uploadingFile.id]: {
                progress: progressPct,
                message
              }
            }
          })
        }
      } else if (event === 'fileSearch:progress') {
        const fileId = data?.fileId
        if (!fileId) return
        if (data?.chatId && currentChatId && data.chatId !== currentChatId) return
        const hasFile = uploadedFilesRef.current.some((file) => file.id === fileId)
        if (!hasFile) return
        const rawProgress = typeof data.progress === 'number'
          ? Math.max(0, Math.min(data.progress, 100))
          : 0
        const hasUploadStage = uploadStageSeenRef.current.has(fileId)
        const nextProgress = mapIndexProgressToPipeline(rawProgress, hasUploadStage)
        const baseMessage = typeof data.message === 'string' && data.message.trim()
          ? data.message
          : '인덱싱 중...'
        const message = hasUploadStage ? `2/2 ${baseMessage}` : baseMessage
        setFileProgress((prev) => {
          const currentProgress = prev[fileId]?.progress || 0
          if (nextProgress < currentProgress && currentProgress < 100) {
            return prev
          }
          return {
            ...prev,
            [fileId]: {
              progress: nextProgress,
              message
            }
          }
        })
        if (nextProgress >= 100) {
          scheduleFileProgressCleanup(fileId)
        }
      } else if (event === 'edit') {
        // 편집 이벤트를 store에 추가
        addEdit(data.type, data.content || `${data.type} 편집`)

        setProgress((prev) => {
          if (!prev) return prev
          const edits = prev.edits ?? []
          const newEdit = { type: data.type, content: data.content || `${data.type} 편집` }
          const editCount = edits.length + 1
          const summary = data.content ? `${data.type}: ${data.content}` : `${data.type} 편집`
          return {
            ...prev,
            edits: [...edits, newEdit],
            message: `편집 중... (${editCount}개 완료) · ${summary}`,
            history: [...(prev.history ?? []), summary].slice(-5),
          }
        })
        const editCount = (progress?.edits?.length ?? 0) + 1
        const summary = data.content ? `${data.type}: ${data.content}` : `${data.type} 편집`
        setProgressMessage(`편집 중... (${editCount}개 완료)\n최근: ${summary}`)
      } else if (event === 'message') {
        // AI 메시지 - v4.1.6: thinking 완료 후 표시
        const streamedText = typeof data?.text === 'string' ? data.text : ''
        if (streamedText && progressMessageIdRef.current && currentChatId) {
          const progressState = useProgressStore.getState()

          // thinking이 완료되지 않았으면 대기 메시지로 저장
          if (!progressState.isThinkingComplete) {
            useProgressStore.getState().setPendingMessage(streamedText)
            console.log('[ChatInput] Message queued - waiting for thinking to complete')
          } else {
            // thinking 완료 후 바로 표시
            updateLastMessage(currentChatId, streamedText)
          }
        }
      } else if (event === 'complete') {
        // v4.1.6: 완료 시 대기 중인 메시지 처리
        const pendingMsg = useProgressStore.getState().consumePendingMessage()
        if (pendingMsg && currentChatId) {
          console.log('[ChatInput] Processing pending message on complete')
          updateLastMessage(currentChatId, pendingMsg)
        }

        if (progressMessageIdRef.current && currentChatId) {
          // Progress 상태 캡처 (완료 전에 캡처해야 함)
          const snapshot = buildProgressSnapshot('완료')
          useChatStore.getState().updateMessageMetadata(currentChatId, progressMessageIdRef.current, {
            thinking: snapshot.thinkingItems,
            progressState: snapshot,
            progressPlaceholder: false
          })
          progressMessageIdRef.current = null
        }
        thinkingItemsRef.current = []
        completeProgress()
        setProgress(null)
        setProgressMessage(null)
      } else if (event === 'editDocument') {
        // 편집 시작/종료 (스트리밍 스타일)
        if (data.status === 'start') {
          setStage('editing', '문서 수정 중...')

          // v4.1.6: thinking 완료로 표시하고 대기 중인 메시지 처리
          useProgressStore.getState().markThinkingComplete()
          const pendingMsg = useProgressStore.getState().consumePendingMessage()
          if (pendingMsg && currentChatId) {
            console.log('[ChatInput] Processing pending message after thinking complete')
            updateLastMessage(currentChatId, pendingMsg)
          }

          setProgress((prev) => ({
            ...prev,
            stage: 'editing',
            message: '문서 수정 중...',
            edits: prev?.edits ?? [],
            history: [...(prev?.history ?? []), '문서 수정 중...'].slice(-5),
          }))
          setProgressMessage('문서 수정 중...')
        } else if (data.status === 'end') {
          // 델타 명령 완료 시 HWP Track Changes 비활성화
          void (async () => {
            try {
              await window.electronAPI.edit.setDiffMode(false)
              console.log('[ChatInput] 델타 완료 - HWP Track Changes 비활성화')
            } catch (err) {
              console.error('[ChatInput] HWP Track Changes 비활성화 실패:', err)
            }
          })()

          completeProgress()
          setProgress((prev) => ({
            ...prev,
            stage: 'finishing',
            message: '수정 완료 중...',
            edits: prev?.edits ?? [],
            history: [...(prev?.history ?? []), '수정 완료 중...'].slice(-5),
          }))
          setProgressMessage('수정 완료 중...')
        }
      } else if (event === 'error') {
        if (progressMessageIdRef.current && currentChatId) {
          // Progress 상태 캡처 (에러 발생 시)
          const snapshot = buildProgressSnapshot('에러 발생')
          useChatStore.getState().updateMessageMetadata(currentChatId, progressMessageIdRef.current, {
            thinking: snapshot.thinkingItems,
            progressState: snapshot,
            progressPlaceholder: false
          })
          progressMessageIdRef.current = null
        }
        thinkingItemsRef.current = []
        resetProgress()
        setProgress(null)
        setProgressMessage(null)
        setIsLoading(false)
      } else if (event === 'cancelled') {
        if (progressMessageIdRef.current && currentChatId) {
          // Progress 상태 캡처 (취소 시)
          const snapshot = buildProgressSnapshot('취소됨')
          useChatStore.getState().updateMessageMetadata(currentChatId, progressMessageIdRef.current, {
            thinking: snapshot.thinkingItems,
            progressState: snapshot,
            progressPlaceholder: false
          })
          progressMessageIdRef.current = null
        }
        thinkingItemsRef.current = []
        resetProgress()
        setProgress(null)
        setProgressMessage(null)
        setIsLoading(false)
      }
    })

    return () => unsubscribe()
  }, [currentChatId, updateLastMessage, progress?.edits?.length, setProgressMessage])

  useEffect(() => {
    return () => {
      Object.values(progressCleanupRef.current).forEach((timer) => clearTimeout(timer))
      progressCleanupRef.current = {}
    }
  }, [])

  const handleCancel = async () => {
    if (!showCancelButton) return
    setIsLoading(false)
    useProgressStore.getState().reset()
    setProgress(null)
    setProgressMessage(null)

    try {
      await window.electronAPI.chat.cancel()
    } catch (err) {
      console.error('[ChatInput] chat cancel failed:', err)
    }

    // 중단 시 HWP Track Changes 비활성화 (UI 응답성을 위해 비동기 분리)
    void window.electronAPI.edit.setDiffMode(false).catch((err) => {
      console.error('[ChatInput] 생성 중단 - HWP Track Changes 비활성화 실패:', err)
    })
  }

  const handleSend = async () => {
    if (!input.trim() || isLoading) return
    if (!(await ensureOpenAiKey())) return

    let chatId = currentChatId
    const uploadedFilesSnapshot = [...uploadedFiles]

    // v6.0: 프로젝트 화면에서 채팅이 선택되지 않은 경우만 새 채팅 생성
    let shouldNavigateToChat = false
    if (folderId && !chatId) {
      chatId = await createChat(folderId)
      console.log('[ChatInput] 프로젝트 화면에서 새 채팅 생성:', chatId, `(프로젝트: ${folderId})`)

      // 프로젝트 폴더 자동 펼침
      useUIStore.getState().expandFolder(folderId)

      if (uploadedFilesSnapshot.length > 0) {
        await persistUploadedFiles(chatId, uploadedFilesSnapshot)
      }

      // 채팅 화면 전환을 pauseDetection 이후로 지연 (컴포넌트 언마운트로 인한 ref 손실 방지)
      shouldNavigateToChat = true

      if (uploadedFilesSnapshot.length > 0) {
        await indexChatFilesInRag(chatId, uploadedFilesSnapshot)
      }
    }
    // 독립 채팅 화면: 현재 채팅 없으면 새로 생성
    else if (!chatId) {
      chatId = await createChat()
      console.log('[ChatInput] 새 독립 채팅 생성:', chatId)

      if (uploadedFilesSnapshot.length > 0) {
        await persistUploadedFiles(chatId, uploadedFilesSnapshot)
      }

      if (uploadedFilesSnapshot.length > 0) {
        await indexChatFilesInRag(chatId, uploadedFilesSnapshot)
      }
    }
    // else: currentChatId가 있으면 그 채팅에 계속 메시지 추가

    const userMessage = input.trim()
    setInput('')

    // 사용자 메시지 추가
    const userMessageId = addMessage(chatId, 'user', userMessage)
    if (uploadedFilesSnapshot.length > 0) {
      const attachments = uploadedFilesSnapshot.map((file) => ({
        id: file.id,
        name: file.name,
        type: file.type,
        size: typeof file.size === 'number' ? file.size : file.content.length,
      }))
      useChatStore.getState().updateMessageMetadata(chatId, userMessageId, {
        attachments,
      })
      clearUploadedFiles()
    }

    setIsLoading(true)

    // 선택 영역 감지 일시 중단 (파이프라인 시작)
    await window.electronAPI.selection.pauseDetection()
    isPausedRef.current = true

    // 프로젝트에서 새 채팅 시작한 경우, pauseDetection 이후에 뷰 전환
    // (이전에 전환하면 컴포넌트 언마운트로 isPausedRef가 초기화됨)
    if (shouldNavigateToChat) {
      navigateToView('chat')
      console.log('[ChatInput] 프로젝트 화면 → 채팅 화면 전환 (pauseDetection 이후)')
    }

    // v4.1.4: assistantMessageId를 미리 생성 (불변식 2)
    const assistantMessageId = generateId()

    try {
      // 선택된 문서 정보
      const docType = selectedDocument?.type
      const docIndex = selectedDocument?.index

      // 참조 자료들 (txt/excel 파일 내용 - 다중 파일 지원)
      let referenceContent: string | undefined
      let referenceFileName: string | undefined

      if (uploadedFilesSnapshot.length > 0) {
        // 다중 파일 내용 합치기
        referenceContent = uploadedFilesSnapshot.map(f => {
          return `[파일: ${f.name}]\n${f.content}`
        }).join('\n\n---\n\n')
        referenceFileName = uploadedFilesSnapshot.length === 1
          ? uploadedFilesSnapshot[0].name
          : `${uploadedFilesSnapshot[0].name} 외 ${uploadedFilesSnapshot.length - 1}개 파일`
      }

      // 문서도 없고 참조 자료도 없는 경우
      if (!selectedDocument && uploadedFilesSnapshot.length === 0) {
        addMessage(chatId, 'assistant', '편집할 문서를 선택하거나 참조 파일을 업로드해주세요.')
        setIsLoading(false)
        return
      }

      // HWP 외의 문서는 아직 미지원 (참조 자료만 있는 경우는 OK)
      if (selectedDocument && docType !== 'hwp') {
        addMessage(chatId, 'assistant', `현재 HWP 문서만 편집 가능합니다. (선택된 문서: ${docType})`)
        setIsLoading(false)
        return
      }

      // v4.1.4: 미리 생성한 ID로 진행 중 표시용 메시지 추가
      progressMessageIdRef.current = assistantMessageId
      addMessage(chatId, 'assistant', '처리 중...', assistantMessageId)
      useChatStore.getState().updateMessageMetadata(chatId, assistantMessageId, {
        progressPlaceholder: true
      })

      // 페이지 정보 갱신 (전송 직전에만 - 한글 포커스 문제 방지)
      await refreshPageInfo()

      // 선택 영역 정보 갱신
      await refreshSelectionInfo()

      // v4.1.4: 현재 채팅에서 거절 정보 및 이력 수집
      const chatStore = useChatStore.getState()
      let currentChat = chatStore.getCurrentChat()
      if (chatId && !currentChat?.boundDocKey) {
        const activeDoc = await window.electronAPI.doc.getActiveKey()
        if (activeDoc?.docKey) {
          chatStore.setLastActiveDocKey(activeDoc.docKey)
          chatStore.updateChatBoundDocKey(chatId, activeDoc.docKey)
          currentChat = chatStore.getCurrentChat()
        }
      }
      const boundDocKey = currentChat?.boundDocKey

      // 거절 정보 peek (consume은 성공 후)
      let rejectionInfo: any[] | undefined
      let pendingRejections: any[] | null = null
      if (chatId && boundDocKey) {
        pendingRejections = useChatStore.getState().peekPendingRejection(chatId, boundDocKey)
        if (pendingRejections && pendingRejections.length > 0) {
          rejectionInfo = pendingRejections
          console.log('[ChatInput] v4.1.4 거절 정보 전달:', rejectionInfo.length, '개')
        }
      }

      // v5.0: 현재 채팅에 연결된 프로젝트 ID 가져오기 (RAG 컨텍스트용)
      const chatFolder = useFolderStore.getState().getChatFolder(chatId)
      const projectId = folderId ?? chatFolder?.id ?? DEFAULT_CHAT_PROJECT_ID
      if (projectId) {
        console.log('[ChatInput] v5.0 프로젝트 ID:', projectId, '- RAG 컨텍스트 활성화')
      }

      // 채팅 이력 수집 (이전 메시지들)
      let conversationHistory: string | undefined
      if (currentChat && currentChat.messages.length > 1) {
        const historyMessages = [...currentChat.messages]
        if (historyMessages.length >= 2) {
          const last = historyMessages[historyMessages.length - 1]
          const secondLast = historyMessages[historyMessages.length - 2]
          if (last.role === 'assistant' && secondLast.role === 'user') {
            historyMessages.pop()
            historyMessages.pop()
          } else if (last.role === 'assistant') {
            historyMessages.pop()
          }
        }

        const historyChat = { ...currentChat, messages: historyMessages }
        const context = collectConversationContext(historyChat, pendingRejections)
        if (context.hasHistory) {
          conversationHistory = context.historyText
          console.log('[ChatInput] v4.1.4 대화 이력 전달:', conversationHistory.length, '자')
        }
      }

      // LLM 기반 문서 편집 요청 (v4.1.4: 거절 정보 및 이력 포함, v5.0: projectId 추가, v5.1: chatId 추가, v6.2: model 추가)
      const result = await window.electronAPI.chat.send(
        userMessage,
        docType,
        docIndex?.toString(),
        referenceContent,
        referenceFileName,
        undefined,
        undefined,
        diffModeEnabled,
        // v4.1.4 추가 파라미터
        assistantMessageId,
        rejectionInfo,
        conversationHistory,
        // v5.0 추가 파라미터 (RAG 컨텍스트용)
        projectId,
        // v5.1 추가 파라미터 (RAG 채팅 스코프 분리)
        chatId,  // currentChatId 대신 chatId 사용 (새 채팅 생성 시 올바른 ID 전달)
        // v6.2 추가 파라미터 (비용 계산용)
        useSettingsStore.getState().openaiDefaultModel || 'gpt-5.1'
      )

      // v4.1.4: 성공 시 consume
      if (result.success && chatId && boundDocKey && rejectionInfo) {
        useChatStore.getState().consumePendingRejection(chatId, boundDocKey)
        console.log('[ChatInput] v4.1.4 거절 정보 consume 완료')
      }


      if (result.success) {
        // 성공 응답 - 마지막 메시지 업데이트
        const responseMessages = Array.isArray((result as any).messages)
          ? (result as any).messages.filter((msg: any) => typeof msg === 'string' && msg.trim())
          : []
        const message = (
          (typeof (result as any).message === 'string' ? (result as any).message.trim() : '') ||
          (responseMessages.length > 0 ? responseMessages[responseMessages.length - 1] : '') ||
          (typeof result.edits === 'number' && result.edits > 0 ? `편집 ${result.edits}건을 적용했습니다.` : '요청을 처리했지만 적용 가능한 편집 명령이 없었습니다.')
        )
        updateLastMessage(chatId, message)

        const existingMetadata = getMessageMetadata(chatId, assistantMessageId)
        const progressState = existingMetadata?.progressState
          ? undefined
          : buildProgressSnapshot('완료')
        const snapshotThinking = thinkingItemsRef.current.length > 0
          ? finalizeThinkingItems(Date.now())
          : []

        // v4.1.4: 응답에서 docKey가 오면 boundDocKey 및 metadata 업데이트
        // (현재 IPC는 확장 전이므로 result에 docKey가 없을 수 있음)
        const resultDocKey = (result as any).docKey
        const metadataUpdate: any = {
          editCount: result.edits,
          executedDeltas: (result as any).executedDeltas,
          progressPlaceholder: false
        }
        if (snapshotThinking.length > 0) {
          metadataUpdate.thinking = snapshotThinking
        }
        if (progressState) {
          metadataUpdate.progressState = progressState
        }
        if (resultDocKey) {
          metadataUpdate.docKey = resultDocKey
          useChatStore.getState().updateChatBoundDocKey(chatId, resultDocKey)
        }
        useChatStore.getState().updateMessageMetadata(chatId, assistantMessageId, metadataUpdate)

        // Log LLM usage
        // 디버그 로그
        console.log('[ChatInput] Result:', {
          success: result.success,
          status: result.status,
          hasTokenUsage: !!result.token_usage,
          tokenUsage: result.token_usage
        })

        // token_usage 보장 (null 방어)
        const tokenUsage = result.token_usage ?? { input: 0, output: 0, total: 0 }

        console.log('[ChatInput] Token usage:', {
          input: tokenUsage.input,
          output: tokenUsage.output,
          total: tokenUsage.total,
          cost: tokenUsage.cost,
          hasCost: 'cost' in tokenUsage
        })

        // 조건 변경: token_usage 존재가 아닌 result.success 또는 token 존재 기준
        if (result.success || tokenUsage.total > 0) {
          window.electronAPI.log.usage({
            eventType: 'llm_call',
            eventData: {
              inputTokens: tokenUsage.input,
              outputTokens: tokenUsage.output,
              totalTokens: tokenUsage.total,
              edits: result.edits || 0,
              hasReference: !!referenceContent,
              diffMode: diffModeEnabled
            },
            sessionId: chatId
          }).catch(err => {
            console.error('[ChatInput] Failed to log LLM usage:', err)
          })

          // v6.1: 설정 페이지 사용량 게이지 업데이트
          const cost = tokenUsage.cost || 0

          console.log('[ChatInput] Adding usage record:', {
            tokensUsed: tokenUsage.total,
            costUsd: cost,
            eventType: 'llm_call'
          })

          useSettingsStore.getState().addUsageRecord({
            date: Date.now(),
            tokensUsed: tokenUsage.total,
            apiCalls: 1,
            feature: 'AI 채팅',
            costUsd: cost,
            eventType: 'llm_call',
            eventData: {
              inputTokens: tokenUsage.input,
              outputTokens: tokenUsage.output,
              totalTokens: tokenUsage.total,
              edits: result.edits || 0,
              hasReference: !!referenceContent,
              diffMode: diffModeEnabled
            }
          })

          // v6.2: 충전 잔액에서 사용 금액 차감
          if (cost > 0) {
            useSettingsStore.getState().deductBalance(cost)
          }
        } else {
          console.warn('[ChatInput] Skipping usage record:', {
            success: result.success,
            tokenUsageTotal: tokenUsage.total
          })
        }

        // Diff 모드에서 편집 성공 시 TrackChanges 버튼 표시
        if (diffModeEnabled) {
          const { updateContext } = useTrackChangesStore.getState()
          updateContext({ pending: true, selectionCount: 0, contextVisible: true })
        }
        progressMessageIdRef.current = null
      } else {
        // 에러 응답
        const errorMessage = toUserSafeErrorMessage(result.error)
        updateLastMessage(chatId, errorMessage)
        const existingMetadata = getMessageMetadata(chatId, assistantMessageId)
        const progressState = existingMetadata?.progressState
          ? undefined
          : buildProgressSnapshot('에러 발생')
        const snapshotThinking = thinkingItemsRef.current.length > 0
          ? finalizeThinkingItems(Date.now())
          : []
        const metadataUpdate: any = {
          progressPlaceholder: false
        }
        if (snapshotThinking.length > 0) {
          metadataUpdate.thinking = snapshotThinking
        }
        if (progressState) {
          metadataUpdate.progressState = progressState
        }
        useChatStore.getState().updateMessageMetadata(chatId, assistantMessageId, metadataUpdate)
        progressMessageIdRef.current = null
      }
    } catch (error: any) {
      console.error('Chat error:', error)
      if (chatId) {
        const safeMessage = toUserSafeErrorMessage(error?.message || '알 수 없는 오류')
        updateLastMessage(chatId, safeMessage)
        const existingMetadata = getMessageMetadata(chatId, assistantMessageId)
        if (existingMetadata) {
          const progressState = existingMetadata.progressState
            ? undefined
            : buildProgressSnapshot('에러 발생')
          const snapshotThinking = thinkingItemsRef.current.length > 0
            ? finalizeThinkingItems(Date.now())
            : []
          const metadataUpdate: any = {
            progressPlaceholder: false
          }
          if (snapshotThinking.length > 0) {
            metadataUpdate.thinking = snapshotThinking
          }
          if (progressState) {
            metadataUpdate.progressState = progressState
          }
          useChatStore.getState().updateMessageMetadata(chatId, assistantMessageId, metadataUpdate)
        }
      }
    } finally {
      setIsLoading(false)
      setProgress(null)
      setProgressMessage(null)
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div
      className={`absolute bottom-6 left-1/2 -translate-x-1/2 w-full max-w-3xl transition-all duration-300 z-10 px-4`}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div
        className={`
          relative rounded-3xl border transition-all duration-300 shadow-xl
          ${isDragging
            ? 'bg-bg-secondary/90 border-accent scale-105 ring-2 ring-accent/20'
            : 'bg-bg border-border hover:shadow-2xl'
          }
        `}
      >
        {/* 드래그 앤 드랍 안내 */}
        {isDragging && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-bg/95 rounded-3xl backdrop-blur-sm border-2 border-dashed border-accent">
            <div className="w-12 h-12 rounded-full bg-accent/10 flex items-center justify-center mb-3 text-accent animate-bounce">
              <Plus className="w-6 h-6" strokeWidth={3} />
            </div>
            <p className="text-text font-bold text-lg">파일을 여기에 놓으세요</p>
            <p className="text-text-tertiary text-sm mt-1">HWP, PDF, Word, Excel, TXT 지원</p>
          </div>
        )}

        <div className="p-4">
          {/* 업로드된 파일들 표시 (Claude Style Cards) */}
          {uploadedFiles.length > 0 && (
            <div className="mb-4 flex overflow-x-auto gap-3 pb-2 thin-scrollbar px-1">
              {uploadedFiles.map((file) => {
                  const isPDF = file.type === 'pdf' || file.name.endsWith('.pdf');
                  const progressInfo = fileProgress[file.id]
                
                // 파일 아이콘 결정 로직
                let borderColor = 'border-border'; // 기본: 회색

                if (isPDF) {
                  borderColor = 'border-red-200 dark:border-red-900/30'; // PDF: 빨강
                } else if (file.type === 'hwp' || file.name.endsWith('.hwp')) {
                  borderColor = 'border-blue-200 dark:border-blue-900/30'; // HWP: 파랑
                } else if (file.type === 'excel' || file.name.endsWith('xls') || file.name.endsWith('xlsx')) {
                  borderColor = 'border-green-200 dark:border-green-900/30'; // Excel: 초록
                }

                return (
                  <div
                    key={file.id}
                    className={`
                      relative group flex-shrink-0 w-48 p-3 rounded-xl border bg-bg-secondary
                      transition-all duration-200 hover:shadow-md ${borderColor}
                    `}
                  >
                    <div className="flex items-start gap-3">
                      <div className={`p-1.5 rounded-lg bg-bg shadow-sm border border-border/50`}>
                        <DocumentIconRenderer type={file.type || file.name.split('.').pop() || ''} size={32} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-sm text-text truncate mb-0.5" title={file.name}>
                          {file.name}
                        </div>
                        <div className="text-xs text-text-tertiary">
                          {file.content ? `${(file.content.length / 1024).toFixed(1)} KB` : '처리 중...'}
                        </div>
                        {/* 파일 처리 중이거나(progressInfo 존재) 추출 전(content 비어 있음)일 때 progress bar 표시 */}
                        {(file.content === '' || progressInfo) && (
                          <div className="mt-2">
                            <div className="h-1 bg-border rounded-full overflow-hidden">
                              <div
                                className="h-full bg-accent transition-all duration-300"
                                style={{ width: `${Math.min(progressInfo?.progress || 0, 100)}%` }}
                              />
                            </div>
                            {(progressInfo?.message || file.content === '') && (
                              <div className="text-[10px] text-text-tertiary mt-1 truncate" title={progressInfo?.message || '파일 처리 준비 중...'}>
                                {progressInfo?.message || '파일 처리 준비 중...'}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>

                    <button
                      onClick={async (e) => {
                        e.stopPropagation()
                        const chatIdForDelete = currentChatId || ''
                        const deleteKey = chatIdForDelete ? makePendingDeleteKey(chatIdForDelete, file.id) : ''
                        const progressInfo = fileProgress[file.id]
                        const isIndexing = Boolean(progressInfo) && (progressInfo.progress ?? 0) < 100

                        if (chatIdForDelete && isIndexing) {
                          pendingRagDeleteRef.current.add(deleteKey)
                        } else if (chatIdForDelete) {
                          pendingRagDeleteRef.current.delete(deleteKey)
                        }

                        // UI에서 즉시 제거 (업로드 중이어도)
                        clearFileProgress(file.id)
                        removeUploadedFile(file.id)

                        // 백그라운드에서 파일 정리
                        if (chatIdForDelete) {
                          // 채팅 파일 메타데이터 제거 (실패해도 무시)
                          removeChatFile(chatIdForDelete, file.id).catch(err =>
                            console.warn('[ChatInput] 채팅 파일 제거 실패:', err)
                          )

                          // RAG에서도 파일 삭제
                          const projectId = resolveRagProjectId(chatIdForDelete)
                          window.electronAPI.rag.deleteFile({
                            projectId,
                            chatId: chatIdForDelete,
                            fileId: file.id
                          }).then((result) => {
                            if (result?.success) {
                              if (deleteKey) {
                                pendingRagDeleteRef.current.delete(deleteKey)
                              }
                            } else if (!isIndexing && deleteKey) {
                              pendingRagDeleteRef.current.delete(deleteKey)
                            }
                          }).catch(err => {
                            console.error('[ChatInput] RAG 파일 삭제 실패:', err)
                            if (!isIndexing && deleteKey) {
                              pendingRagDeleteRef.current.delete(deleteKey)
                            }
                          })
                        } else {
                          // 채팅이 없는 상태에서 제거한 경우, 지연 삭제 플래그를 남기지 않음
                        }
                      }}
                      className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 p-1 rounded-full bg-bg hover:bg-danger-light text-text-tertiary hover:text-danger shadow-sm border border-border transition-all"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* 편집 중인 문서 선택 (작게 표시) */}
          <div className="mb-2 px-1">
            <DocumentSelector folderId={folderId} />
          </div>

          {/* 선택 영역 미리보기 */}
          {selectionInfo?.hasSelection && !isLoading && !pending && (
            <div className="mb-2 px-1">
              <SelectionPreview selectionInfo={selectionInfo} />
            </div>
          )}

          <div
            className="relative rounded-2xl border border-transparent focus-within:border-gray-200 dark:focus-within:border-zinc-700/50 transition-all"
            style={{ backgroundColor: theme === 'dark' ? 'var(--bg-secondary)' : 'transparent' }}
          >
            {shouldShowTrackChanges ? (
              <TrackChangeButtons isEmbedded />
            ) : (
              <>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onFocus={() => {
                    // HWP에서 텍스트 선택 후 입력창 포커스 시 선택 영역 갱신
                    refreshSelectionInfo()
                  }}
                  placeholder="메시지를 입력하세요... (Inserty에게 문서 작업을 요청해보세요)"
                  className="w-full bg-transparent border-none outline-none resize-none p-4 max-h-60 text-[15px] leading-relaxed placeholder:text-text-tertiary text-text"
                  style={{ minHeight: '56px' }}
                  rows={1}
                  disabled={isLoading}
                />

                <div className="flex items-center justify-between px-2 pb-2 mt-1">
                  {/* Left Controls: File Upload & Model Selector */}
                  <div className="flex items-center gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        const rect = e.currentTarget.getBoundingClientRect()
                        openPopover('file-upload', {
                          position: { top: rect.top - 120, left: rect.left },
                          onFileSelect: () => fileInputRef.current?.click(),
                        })
                      }}
                      className="p-2 rounded-lg text-text-tertiary hover:text-text hover:bg-bg-tertiary transition-colors"
                      title="파일 추가"
                    >
                      <Paperclip className="w-5 h-5" strokeWidth={2} />
                    </button>

                    {/* Model Selector (Dummy) */}
                    <div className="flex items-center gap-1 px-2 py-1.5 rounded-lg hover:bg-bg-tertiary cursor-pointer transition-colors group">
                      <span className="text-xs font-medium text-text-secondary group-hover:text-text">Inserty 1.0</span>
                      <ChevronDown className="w-3 h-3 text-text-tertiary group-hover:text-text" strokeWidth={2.5} />
                    </div>
                  </div>

                  {/* Right Controls: Diff Mode & Send */}
                  <div className="flex items-center gap-2">
                    <button
                      onClick={async () => {
                        let targetChatId = currentChatId

                        if (!targetChatId && folderId) {
                          targetChatId = await createChat(folderId)
                          useUIStore.getState().expandFolder(folderId)
                          navigateToView('chat')
                          useChatStore.getState().selectChat(targetChatId)
                        } else if (!targetChatId) {
                          targetChatId = await createChat()
                          navigateToView('chat')
                          useChatStore.getState().selectChat(targetChatId)
                        }

                        if (!targetChatId) {
                          window.alert('모드를 변경할 채팅을 먼저 선택하거나 새 채팅을 생성해주세요.')
                          return
                        }

                        const store = useChatStore.getState()
                        const nextModeEnabled = !store.getDiffModeEnabled(targetChatId)
                        store.toggleDiffMode(targetChatId, nextModeEnabled)

                        // HWP와 모드 상태 동기화는 비동기로 처리 (토글 반응성 우선)
                        void window.electronAPI.edit.setDiffMode(nextModeEnabled).catch((err) => {
                          console.error('[ChatInput] mode sync failed:', err)
                        })
                      }}
                      className={`
                          px-3 py-1.5 text-xs font-medium rounded-lg flex items-center gap-1.5 transition-colors
                          ${diffModeEnabled ? 'bg-accent/10 text-accent' : 'text-text-tertiary hover:bg-bg-tertiary'}
                        `}
                      title={diffModeEnabled ? '수정 전 확인 모드 켜짐' : '자동 수정 모드 (빠름)'}
                    >
                      {diffModeEnabled ? (
                        <>
                          <Search className="w-3.5 h-3.5" />
                          <span>확인 후 수정</span>
                        </>
                      ) : (
                        <>
                          <Zap className="w-3.5 h-3.5" fill="currentColor" />
                          <span>자동 수정</span>
                        </>
                      )}
                    </button>

                    {showCancelButton ? (
                      <button
                        onClick={handleCancel}
                        className="p-2 rounded-xl bg-accent text-white hover:bg-accent-dark shadow-md transition-all"
                        title="생성 중단"
                        aria-label="생성 중단"
                      >
                        <Square className="w-4 h-4" strokeWidth={0} fill="currentColor" />
                      </button>
                    ) : (
                      <button
                        onClick={handleSend}
                        disabled={!input.trim()}
                        aria-label="메시지 전송"
                        className={`
                          p-2 rounded-xl transition-all duration-200
                          ${!input.trim()
                          ? 'bg-bg-tertiary text-text-tertiary cursor-not-allowed'
                          : 'bg-accent text-white hover:bg-accent-dark shadow-md hover:shadow-lg hover:scale-105 active:scale-95'
                        }
                        `}
                      >
                        <ArrowUp className="w-5 h-5" strokeWidth={3} />
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}

            {/* Hidden File Input (moved outside conditional but inside parent div) */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".hwp,.hwpx,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.xlsm,.txt,.md"
              multiple
              className="hidden"
              onChange={async (e) => {
                const files = e.target.files
                if (!files || files.length === 0) return
                console.log('[ChatInput] File input change:', files.length)

                const remainingSlots = Math.max(0, 5 - uploadedFilesRef.current.length)
                if (remainingSlots <= 0) {
                  window.alert('채팅 첨부는 최대 5개까지 가능합니다.')
                  e.target.value = ''
                  return
                }
                const selectedFiles = Array.from(files).slice(0, remainingSlots)
                if (selectedFiles.length < files.length) {
                  window.alert(`채팅 첨부는 최대 5개까지 가능합니다. ${selectedFiles.length}개 파일만 추가합니다.`)
                }

                // 파일 업로드 처리 (채팅 첨부용)
                for (const file of selectedFiles) {
                  let filePath = window.electronAPI.file.getPathForFile(file)

                  // v6.1: getPathForFile 실패 시 File API fallback
                  if (!filePath) {
                    console.warn('[ChatInput] getPathForFile failed, trying File API fallback:', file.name)
                    try {
                      const arrayBuffer = await file.arrayBuffer()
                      const result = await window.electronAPI.invoke('file:saveTempFile', {
                        fileName: file.name,
                        buffer: Array.from(new Uint8Array(arrayBuffer))
                      })
                      if (result?.success && result.path) {
                        filePath = result.path
                        console.log('[ChatInput] File API fallback success:', filePath)
                      }
                    } catch (err) {
                      console.error('[ChatInput] File API fallback failed:', file.name, err)
                    }
                  }

                  if (!filePath) {
                    console.error('[ChatInput] Failed to get file path:', file.name)
                    showUploadFailure(file.name)
                    continue
                  }
                  console.log('[ChatInput] File input upload start:', file.name)
                  const uploadedFile = await uploadFileByPath(filePath)
                  if (!uploadedFile) {
                    console.warn('[ChatInput] Upload skipped:', file.name)
                    showUploadFailure(file.name, filePath)
                    continue
                  }
                  console.log('[ChatInput] File parsed:', uploadedFile.name)
                  if (currentChatId) {
                    await persistChatFile(currentChatId, uploadedFile)
                  }

                  // RAG 인덱싱 (채팅 스코프)
                  if (currentChatId) {
                    await indexChatFileInRag(currentChatId, uploadedFile)
                  }
                }
                e.target.value = ''
              }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

import { useState, useEffect, useRef } from 'react'
import { useDocumentStore, getDocumentColor, OpenDocument, UploadedFile } from '../stores/document-store'
import { useChatStore } from '../stores/chat-store'
import { useFolderStore } from '../stores/folder-store'
import { useSettingsStore } from '../stores/settings-store'
import { useUIStore } from '../stores/ui-store'
import { DocumentIconRenderer } from './icons/DocumentIconRenderer'
import { ChevronDown, RefreshCw, FileText, Check, X, File, Paperclip } from 'lucide-react'
import { DEFAULT_CHAT_PROJECT_ID } from '../constants/rag'

export function DocumentSelector({ folderId }: { folderId?: string } = {}) {
  const [isOpen, setIsOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const currentChatId = useChatStore(state => state.currentChatId)
  const { openaiApiKey, connectionMode } = useSettingsStore()
  const { openModal } = useUIStore()

  const {
    openDocuments,
    selectedDocument,
    uploadedFiles,
    isLoading,
    pageInfo,
    selectDocument,
    uploadTxtFile,
    uploadExcelFile,
    uploadPdfFile,
    uploadHwpFile,
    uploadDocFile,
    uploadPptFile,
    removeUploadedFile,
    clearUploadedFiles,
    refreshDocuments,
    refreshPageInfo,
  } = useDocumentStore()

  const resolveRagProjectId = (chatId?: string) => {
    if (folderId) return folderId
    if (chatId) {
      const chatFolder = useFolderStore.getState().getChatFolder(chatId)
      if (chatFolder?.id) return chatFolder.id
    }
    return DEFAULT_CHAT_PROJECT_ID
  }

  const ensureOpenAiKey = () => {
    // Codex(ChatGPT) 모드는 API 키 불필요
    if (connectionMode === 'codex') return true
    if (openaiApiKey && openaiApiKey.trim()) return true
    const confirmed = window.confirm('OpenAI API 키가 필요합니다. 설정의 모델 및 AI 탭에서 키를 등록할까요?')
    if (confirmed) {
      openModal('settings', { tab: 'ai' })
    }
    return false
  }

  const persistChatFile = async (file: UploadedFile): Promise<boolean> => {
    if (!currentChatId || !window.electronAPI?.invoke) {
      console.warn('[DocumentSelector] chatFiles:add skipped: no chat or ipc')
      return false
    }
    const ext = file.path.split('.').pop()?.toLowerCase()
    try {
      const result = await window.electronAPI.invoke('chatFiles:add', {
        chatId: currentChatId,
        fileId: file.id,
        fileName: file.name,
        filePath: file.path,
        extension: ext,
        size: file.size,
      })
      if (result?.success) {
        console.log('[DocumentSelector] DB saved:', file.name)
        return true
      }
      console.error('[DocumentSelector] chatFiles:add failed:', result?.error)
      return false
    } catch (err) {
      console.error('[DocumentSelector] chatFiles:add failed:', err)
      return false
    }
  }

  const removeChatFiles = async (fileIds: string[]) => {
    if (!currentChatId || !window.electronAPI?.invoke) {
      return { failed: fileIds }
    }
    const failed: string[] = []
    for (const fileId of fileIds) {
      try {
        const result = await window.electronAPI.invoke('chatFiles:remove', { chatId: currentChatId, fileId })
        if (!result?.success) {
          console.error('[DocumentSelector] chatFiles:remove failed:', result?.error)
          failed.push(fileId)
        }
      } catch (err: any) {
        console.error('[DocumentSelector] chatFiles:remove failed:', err)
        failed.push(fileId)
      }
    }
    return { failed }
  }

  // 페이지 정보 비동기 폴링 (ROT 방식으로 창 활성화 없이)
  useEffect(() => {
    refreshPageInfo()

    let isActive = true
    let timeoutId: number | null = null

    const poll = async () => {
      while (isActive) {
        await refreshPageInfo()
        if (!isActive) break
        await new Promise<void>((resolve) => {
          timeoutId = window.setTimeout(() => resolve(), 2000)
        })
      }
    }

    void poll()

    return () => {
      isActive = false
      if (timeoutId !== null) {
        clearTimeout(timeoutId)
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 팝오버 토글 (열림/닫힘)
  const handleToggle = async () => {
    if (isOpen) {
      setIsOpen(false)
    } else {
      setIsOpen(true)
      await refreshDocuments()
    }
  }

  // 외부 클릭 감지
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  // 문서 선택
  const handleSelect = async (doc: OpenDocument) => {
    try {
      await window.electronAPI.documents.select(doc.type, doc.index, doc.id)  // doc.id 추가
    } catch (err) {
      console.error('Failed to select document:', err)
    }
    selectDocument(doc)
    setIsOpen(false)
  }

  // 선택 해제
  const handleClear = async () => {
    let failedIds: string[] = []
    if (uploadedFiles.length > 0) {
      const result = await removeChatFiles(uploadedFiles.map(file => file.id))
      failedIds = result.failed
    }
    selectDocument(null)
    if (failedIds.length === 0) {
      clearUploadedFiles()
    } else {
      const failedSet = new Set(failedIds)
      for (const file of uploadedFiles) {
        if (!failedSet.has(file.id)) {
          removeUploadedFile(file.id)
        }
      }
    }
    setIsOpen(false)
  }

  // 파일 업로드 (다중 선택 지원)
  const handleUploadFile = async () => {
    const filePaths = await window.electronAPI.dialog.openFiles({
      filters: [
        { name: '참조 파일', extensions: ['hwp', 'hwpx', 'pdf', 'docx', 'pptx', 'xlsx', 'xls', 'xlsm', 'txt', 'md'] },
        { name: 'All Files', extensions: ['*'] },
      ],
    })

    if (!filePaths || filePaths.length === 0) return

    // 선택한 파일들 업로드 (최대 5개까지)
      for (const filePath of filePaths) {
        if (uploadedFiles.length >= 5) {
          console.warn('최대 5개 파일까지 업로드 가능합니다.')
          break
        }

        const ext = filePath.split('.').pop()?.toLowerCase()
        let uploadedFile: UploadedFile | null = null

        if (ext === 'txt' || ext === 'md') {
          uploadedFile = await uploadTxtFile(filePath)
        } else if (ext === 'pdf') {
          uploadedFile = await uploadPdfFile(filePath)
        } else if (ext === 'hwp' || ext === 'hwpx') {
          uploadedFile = await uploadHwpFile(filePath)
        } else if (ext === 'docx') {
          uploadedFile = await uploadDocFile(filePath)
        } else if (ext === 'pptx') {
          uploadedFile = await uploadPptFile(filePath)
        } else if (['xlsx', 'xls', 'xlsm'].includes(ext || '')) {
          uploadedFile = await uploadExcelFile(filePath)
        }

        if (!uploadedFile) continue

      await persistChatFile(uploadedFile)

      if (currentChatId) {
        try {
          if (!ensureOpenAiKey()) {
            break
          }
          const projectId = resolveRagProjectId(currentChatId)
          const ragResult = await window.electronAPI.rag.indexChatFile({
            projectId,
            chatId: currentChatId,
            fileId: uploadedFile.id,
            textContent: uploadedFile.content,
            fileName: uploadedFile.name,
          })

          if (ragResult.success) {
          }
        } catch (err) {
          console.error('[DocumentSelector] RAG 인덱싱 오류:', err)
        }
      }
    }
    setIsOpen(false)
  }

  return (
    <div className="relative flex flex-col gap-1">
      <div className="flex items-center gap-3">
        {/* 선택 버튼 */}
        {/* Modified: Removed background color (bg-transparent) to fix user feedback */}
        <button
          ref={buttonRef}
          onClick={handleToggle}
          className={`
            flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg transition-colors border
            ${isOpen ? 'bg-bg-tertiary border-accent/50' : 'bg-transparent border-transparent hover:bg-bg-tertiary'}
          `}
        >
        {selectedDocument ? (
          <>
            <DocumentIconRenderer
              type={selectedDocument.type}
              size={22}
              className={`flex-shrink-0`}
            />
            <span className="max-w-[200px] truncate font-medium text-text">{selectedDocument.name}</span>
          </>
        ) : uploadedFiles.length > 0 ? (
          <>
            <Paperclip size={18} className="text-accent flex-shrink-0" />
            <span className="max-w-[200px] truncate font-medium text-text">
              {uploadedFiles.length === 1
                ? uploadedFiles[0].name
                : `${uploadedFiles[0].name} 외 ${uploadedFiles.length - 1}개`}
            </span>
          </>
        ) : (
          <>
            <File size={18} className="text-text-tertiary flex-shrink-0" />
            <span className="text-text-secondary">문서 선택</span>
          </>
        )}
        <ChevronDown
          size={14}
          className={`text-text-tertiary transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {/* 페이지 범위 표시 */}
      {pageInfo && (
        <div className="flex items-center gap-1.5 px-2.5 py-1 bg-accent/10 text-accent rounded-full text-xs font-medium border border-accent/20">
          <FileText size={12} />
          <span>{pageInfo.startPage}~{pageInfo.endPage}p</span>
          <span className="opacity-60">/ {pageInfo.totalPages}</span>
        </div>
      )}
      </div>

      {/* 팝오버 */}
      {isOpen && (
        <div
          ref={popoverRef}
          className="absolute bottom-full left-0 mb-2 w-80 rounded-xl shadow-2xl border flex flex-col overflow-hidden animate-scale-up origin-bottom-left"
          style={{
            backgroundColor: 'var(--bg)',
            borderColor: 'var(--border)',
          }}
        >
          {/* 헤더 */}
          <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
            <span className="font-semibold text-sm text-text">열린 문서 선택</span>
            <button
              onClick={() => refreshDocuments()}
              className="p-1.5 rounded-md text-text-tertiary hover:bg-bg-tertiary hover:text-text-secondary transition-colors"
              disabled={isLoading}
              title="새로고침"
            >
              <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* 문서 목록 */}
          <div className="max-h-64 overflow-y-auto thin-scrollbar p-1">
            {isLoading && openDocuments.length === 0 ? (
              <div className="p-8 text-center text-text-tertiary text-sm flex flex-col items-center gap-2">
                <RefreshCw size={24} className="animate-spin opacity-50" />
                <span>문서 목록을 불러오는 중...</span>
              </div>
            ) : openDocuments.length === 0 ? (
              <div className="p-8 text-center text-text-tertiary text-sm">
                <p className="font-medium text-text-secondary mb-1">열린 문서가 없습니다</p>
                <p className="text-xs">한글 문서를 열어주세요</p>
              </div>
            ) : (
              <div className="space-y-0.5">
                {openDocuments.map(doc => (
                  <button
                    key={doc.id}
                    onClick={() => handleSelect(doc)}
                    className={`
                      w-full flex items-center gap-3 px-3 py-2.5 text-left rounded-lg transition-colors group
                      ${selectedDocument?.id === doc.id ? 'bg-accent/5' : 'hover:bg-bg-secondary'}
                    `}
                  >
                    <div className="p-1.5 rounded-md bg-bg shadow-sm border border-border/50 group-hover:border-border">
                      <DocumentIconRenderer
                        type={doc.type}
                        size={22}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium truncate ${selectedDocument?.id === doc.id ? 'text-accent' : 'text-text'}`}>
                        {doc.name}
                      </p>
                      <p className="text-[10px] text-text-tertiary truncate uppercase font-semibold tracking-wider">
                        {doc.type}
                      </p>
                    </div>
                    {selectedDocument?.id === doc.id && (
                      <Check size={16} className="text-accent" />
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 하단 액션 */}
          {(selectedDocument || uploadedFiles.length > 0) && (
            <div className="border-t px-2 py-2" style={{ borderColor: 'var(--border)' }}>
              <button
                onClick={handleClear}
                className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:bg-bg-secondary hover:text-danger transition-colors"
              >
                <X size={14} />
                선택 해제
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

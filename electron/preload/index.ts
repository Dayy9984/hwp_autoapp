import { ipcRenderer, contextBridge, webUtils } from 'electron'

// Expose API to Renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  // IPC 통신
  invoke: (channel: string, ...args: any[]) => ipcRenderer.invoke(channel, ...args),
  on: (channel: string, callback: (...args: any[]) => void) => {
    const subscription = (_event: any, ...args: any[]) => callback(...args)
    ipcRenderer.on(channel, subscription)
    return () => ipcRenderer.removeListener(channel, subscription)
  },

  // 앱 버전 조회
  getVersion: () => ipcRenderer.sendSync('app:getVersion'),

  // Python Bridge
  python: {
    start: () => ipcRenderer.invoke('python:start'),
    call: (method: string, params: any) => ipcRenderer.invoke('python:call', method, params),
    isReady: () => ipcRenderer.invoke('python:isReady'),
  },
  // Agent Bridge (LLM process)
  agent: {
    start: () => ipcRenderer.invoke('agent:start'),
  },

  // Codex CLI
  codex: {
    status: () => ipcRenderer.invoke('codex:status'),
    login: () => ipcRenderer.invoke('codex:login'),
  },

  // 다이얼로그
  dialog: {
    openFile: (options?: any) => ipcRenderer.invoke('dialog:openFile', options),
    openFiles: (options?: any) => ipcRenderer.invoke('dialog:openFiles', options),
  },

  // 파일 읽기 (서버 분리 대비 - 로컬에서 읽고 문자열 반환)
  file: {
    readTxt: (filePath: string, maxChars?: number) =>
      ipcRenderer.invoke('file:readTxt', filePath, maxChars),
    readExcel: (filePath: string, sheetName?: string) =>
      ipcRenderer.invoke('file:readExcel', filePath, sheetName),
    readPdf: (filePath: string) =>
      ipcRenderer.invoke('file:readPdf', filePath),
    readHwp: (filePath: string) =>
      ipcRenderer.invoke('file:readHwp', filePath),
    readDoc: (filePath: string) =>
      ipcRenderer.invoke('file:readDoc', filePath),
    readPpt: (filePath: string) =>
      ipcRenderer.invoke('file:readPpt', filePath),
    getExcelSheets: (filePath: string) =>
      ipcRenderer.invoke('file:getExcelSheets', filePath),
    // 드래그 앤 드랍된 파일의 경로 가져오기
    getPathForFile: (file: File) => webUtils.getPathForFile(file),
  },

  // 문서 관리 (Python Bridge 경유)
  documents: {
    getOpen: () => ipcRenderer.invoke('documents:getOpen'),
    select: (type: string, index: number, docId?: string) => ipcRenderer.invoke('documents:select', type, index, docId),
    getCurrentPageInfo: () => ipcRenderer.invoke('documents:getCurrentPageInfo'),
    getSelectionInfo: () => ipcRenderer.invoke('documents:getSelectionInfo'),
  },
  selection: {
    pauseDetection: () => ipcRenderer.invoke('selection:pauseDetection'),
    resumeDetection: () => ipcRenderer.invoke('selection:resumeDetection'),
  },
  doc: {
    getActiveKey: () => ipcRenderer.invoke('doc:getActiveKey'),
  },

  // HWP 창 바인딩 이벤트
  onHwpWindowBound: (callback: (data: { pid: number; hwnd: number; title: string }) => void) => {
    const subscription = (_event: any, data: any) => callback(data)
    ipcRenderer.on('hwp:windowBound', subscription)
    return () => ipcRenderer.removeListener('hwp:windowBound', subscription)
  },

  // HWP 선택 영역 변경 이벤트
  onHwpSelectionChanged: (callback: (data: any) => void) => {
    const subscription = (_event: any, data: any) => callback(data)
    ipcRenderer.on('hwp:selectionChanged', subscription)
    return () => ipcRenderer.removeListener('hwp:selectionChanged', subscription)
  },

  // HWP 바인딩 활성화 (로그인 후 호출)
  hwp: {
    enableBinding: () => ipcRenderer.invoke('hwp:enableBinding'),
    checkCompatibility: () => ipcRenderer.invoke('hwp:checkCompatibility'),
    ensureCompatibility: () => ipcRenderer.invoke('hwp:ensureCompatibility'),
    getCompatibilityStatus: () => ipcRenderer.invoke('hwp:getCompatibilityStatus'),
  },

  // Auth (logout only)
  auth: {
    logout: () => ipcRenderer.invoke('auth:logout'),
  },

  // Update (App Update Check & Download - electron-updater 기반)
  update: {
    check: () => ipcRenderer.invoke('update:check'),
    download: () => ipcRenderer.invoke('update:download'),
    install: () => ipcRenderer.invoke('update:install'),
    getCurrentVersion: () => ipcRenderer.invoke('update:getCurrentVersion'),
    startPeriodicCheck: (intervalMs?: number) => ipcRenderer.invoke('update:startPeriodicCheck', intervalMs),
    stopPeriodicCheck: () => ipcRenderer.invoke('update:stopPeriodicCheck'),
    onStatus: (callback: (status: {
      status: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
      info?: { version: string; releaseDate?: string; releaseNotes?: string }
      progress?: { percent: number; bytesPerSecond: number; total: number; transferred: number }
      error?: string
      isCritical?: boolean  // 필수 업데이트 여부
      releaseNotes?: string // 릴리스 노트
    }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, status: unknown) => callback(status as Parameters<typeof callback>[0])
      ipcRenderer.on('auto-update:status', handler)
      return () => ipcRenderer.removeListener('auto-update:status', handler)
    },
    getLastStatus: () => ipcRenderer.invoke('update:getLastStatus'),
  },

  // Log (Auth & Usage Logging)
  log: {
    auth: (params: {
      success: boolean
      reason?: string
      ipHash?: string
      userAgent?: string
      metadata?: Record<string, unknown>
    }) => ipcRenderer.invoke('log:auth', params),
    usage: (params: {
      eventType: string
      eventData?: Record<string, unknown>
      sessionId?: string
    }) => ipcRenderer.invoke('log:usage', params),
  },

  // Device (Device ID Management)
  device: {
    getId: () => ipcRenderer.invoke('device:getId'),
    getInfo: () => ipcRenderer.invoke('device:getInfo'),
    regenerateId: () => ipcRenderer.invoke('device:regenerateId'),
  },

  // Python 준비 완료 이벤트
  onPythonReady: (callback: () => void) => {
    const subscription = () => callback()
    ipcRenderer.on('python:ready', subscription)
    return () => ipcRenderer.removeListener('python:ready', subscription)
  },

  onAppStatus: (callback: (message: string, progress?: number) => void) => {
    const subscription = (_event: any, message: string, progress?: number) => callback(message, progress)
    ipcRenderer.on('app:status', subscription)
    return () => ipcRenderer.removeListener('app:status', subscription)
  },

  // Progress 이벤트 (CVD, RAG 등 모든 progress 이벤트)
  onProgress: (callback: (event: string, data: any) => void) => {
    const subscription = (_event: any, event: string, data: any) => callback(event, data)
    ipcRenderer.on('chat:progress', subscription)
    return () => ipcRenderer.removeListener('chat:progress', subscription)
  },

  // 채팅 (LLM 기반 문서 편집)
  // v4.1.4: assistantMessageId, rejectionInfo, conversationHistory 추가
  // v5.0: projectId 추가 (RAG 컨텍스트용)
  // v5.1: chatId 추가 (RAG 채팅 스코프 분리)
  // v6.2: model 추가 (비용 계산용)
  chat: {
    send: (
      prompt: string,
      docType?: string,
      docIndex?: number,
      referenceContent?: string,
      referenceFileName?: string,
      startPage?: number,
      endPage?: number,
      diffModeEnabled?: boolean,
      // v4.1.4 추가
      assistantMessageId?: string,
      rejectionInfo?: any[],
      conversationHistory?: string,
      // v5.0 추가
      projectId?: string,
      // v5.1 추가
      chatId?: string,
      // v6.2 추가
      model?: string
    ) => ipcRenderer.invoke('chat:send', prompt, docType, docIndex, referenceContent, referenceFileName, startPage, endPage, diffModeEnabled, assistantMessageId, rejectionInfo, conversationHistory, projectId, chatId, model),
    // 채팅 생성 중지
    cancel: () => ipcRenderer.invoke('chat:cancel'),
    // 실시간 진행 상황 이벤트 수신
    onProgress: (callback: (event: string, data: any) => void) => {
      const subscription = (_event: any, event: string, data: any) => callback(event, data)
      ipcRenderer.on('chat:progress', subscription)
      return () => ipcRenderer.removeListener('chat:progress', subscription)
    },
  },

  // Undo/Redo 및 Diff 모드 (TASK-006)
  edit: {
    undo: (count?: number) => ipcRenderer.invoke('edit:undo', count),
    redo: (count?: number) => ipcRenderer.invoke('edit:redo', count),
    setDiffMode: (enabled: boolean) => ipcRenderer.invoke('edit:setDiffMode', enabled),
    getDiffMode: () => ipcRenderer.invoke('edit:getDiffMode'),
    getHistory: () => ipcRenderer.invoke('edit:getHistory'),
    clearHistory: () => ipcRenderer.invoke('edit:clearHistory'),
    accept: () => ipcRenderer.invoke('edit:accept'),
    reject: () => ipcRenderer.invoke('edit:reject'),
  },

  // TrackChange 부분 승인/거절 (HWP 2022 이전)
  trackChanges: {
    getContext: () => ipcRenderer.invoke('trackChanges:getContext'),
    cacheSelection: () => ipcRenderer.invoke('trackChanges:cacheSelection'),
    applyAll: () => ipcRenderer.invoke('trackChanges:applyAll'),
    // v4.1.4: docKey 파라미터 지원 (기존 호출도 호환)
    rejectAll: (params?: { docKey?: string; chatId?: string }) =>
      ipcRenderer.invoke('trackChanges:rejectAll', params),
    applySelected: () => ipcRenderer.invoke('trackChanges:applySelected'),
    rejectSelected: (params?: { docKey?: string; chatId?: string }) =>
      ipcRenderer.invoke('trackChanges:rejectSelected', params),
  },

  // Phase 4: CVD 추출 및 Diff 생성
  cvd: {
    extractPair: (args: {
      projectId: string
      pairId: string
      templatePath: string
      filledPath: string
    }) => ipcRenderer.invoke('cvd:extractPair', args),
    generateDiff: (args: {
      projectId: string
      pairId: string
    }) => ipcRenderer.invoke('cvd:generateDiff', args),
    processTemplatePair: (args: {
      projectId: string
      pairId: string
      templatePath: string
      filledPath: string
    }) => ipcRenderer.invoke('cvd:processTemplatePair', args),
    // 진행 이벤트 수신
    onProgress: (callback: (data: { pairId: string; progress: number; message: string }) => void) => {
      const subscription = (_event: any, event: string, data: any) => {
        if (event === 'cvd:progress') {
          callback(data)
        }
      }
      ipcRenderer.on('chat:progress', subscription)
      return () => ipcRenderer.removeListener('chat:progress', subscription)
    },
  },

  // Phase 5: RAG 인덱싱/쿼리 (OpenAI 키는 로컬 설정에서 읽음)
  // Phase 5.1: 채팅 스코프 분리 (v5.1)
  rag: {
    indexPair: (args: {
      projectId: string
      pairId: string
      diffPath: string
      openaiApiKey?: string  // deprecated: 로컬 설정 사용
    }) => ipcRenderer.invoke('fileSearch:indexPair', args),
    indexFile: (args: {
      projectId: string
      fileId: string
      textContent: string
      fileName: string
      filePath?: string
      openaiApiKey?: string  // deprecated
    }) => ipcRenderer.invoke('fileSearch:indexFile', args),
    deletePair: (args: {
      projectId: string
      pairId: string
      openaiApiKey?: string  // deprecated
    }) => ipcRenderer.invoke('fileSearch:deletePair', args),
    deleteFile: (args: {
      projectId: string
      fileId: string
      chatId?: string
      openaiApiKey?: string  // deprecated
    }) => ipcRenderer.invoke('fileSearch:deleteFile', args),
    // v5.1: 채팅 스코프 분리
    indexChatFile: (args: {
      projectId: string
      chatId: string
      fileId: string
      textContent: string
      fileName: string
      filePath?: string
      openaiApiKey?: string  // deprecated
    }) => ipcRenderer.invoke('fileSearch:indexChatFile', args),
    deleteChatScope: (args: {
      projectId: string
      chatId: string
    }) => ipcRenderer.invoke('fileSearch:deleteChatScope', args),
  },

  maintenance: {
    resetLocalData: (args?: { backup?: boolean }) =>
      ipcRenderer.invoke('maintenance:resetLocalData', args),
    validateDb: () => ipcRenderer.invoke('maintenance:validateDb'),
  },

  // v4.1.4: 활성 문서 변경 이벤트 (v4.2: 활성 문서 상세 정보 포함)
  onActiveDocChanged: (callback: (docKey: string | undefined, activeDocHint?: { documentId?: number; path?: string; name?: string }) => void) => {
    const subscription = (_event: any, data: { docKey?: string; activeDocumentId?: number; activePath?: string; activeName?: string }) => {
      const hint = (data.activeDocumentId || data.activePath || data.activeName)
        ? { documentId: data.activeDocumentId, path: data.activePath, name: data.activeName }
        : undefined
      callback(data.docKey, hint)
    }
    ipcRenderer.on('doc:activeChanged', subscription)
    return () => ipcRenderer.removeListener('doc:activeChanged', subscription)
  },
})

// Splash window API (네이티브 스플래시 화면용)
contextBridge.exposeInMainWorld('electronSplash', {
  onStatusUpdate: (callback: (message: string, progress?: number) => void) => {
    const subscription = (_event: any, message: string, progress?: number) => callback(message, progress)
    ipcRenderer.on('splash:status', subscription)
    return () => ipcRenderer.removeListener('splash:status', subscription)
  },
})

// Type definitions
interface OpenDocument {
  id: string
  name: string
  path: string
  type: 'hwp' | 'word' | 'excel' | 'unknown'
  index: number
  documentId?: number
}

interface ChatResponse {
  success: boolean
  status?: string
  message?: string
  edits?: number
  error?: string
  token_usage?: {
    input: number
    output: number
    total: number
    cost?: number
  }
}

declare global {
  interface Window {
    electronAPI: {
      invoke: (channel: string, ...args: any[]) => Promise<any>
      on: (channel: string, callback: (...args: any[]) => void) => () => void
      getVersion: () => string
      python: {
        start: () => Promise<{ success: boolean }>
        call: (method: string, params: any) => Promise<any>
        isReady: () => Promise<{ ready: boolean }>
      }
      agent: {
        start: () => Promise<{ success: boolean }>
      }
      codex: {
        status: () => Promise<{ installed: boolean; authenticated?: boolean; reason?: string }>
        login: () => Promise<{ success: boolean; error?: string }>
      }
      dialog: {
        openFile: (options?: any) => Promise<string | null>
        openFiles: (options?: any) => Promise<string[]>
      }
      file: {
        readTxt: (filePath: string, maxChars?: number) => Promise<{
          success: boolean
          content?: string
          file_name?: string
          file_size?: number
          encoding?: string
          truncated?: boolean
          error?: string
        }>
        readExcel: (filePath: string, sheetName?: string) => Promise<{
          success: boolean
          content?: string
          file_name?: string
          sheet_name?: string
          row_count?: number
          col_count?: number
          error?: string
        }>
        readPdf: (filePath: string) => Promise<{
          success: boolean
          text?: string
          page_count?: number
          error?: string
        }>
        readHwp: (filePath: string) => Promise<{
          success: boolean
          text?: string
          error?: string
        }>
        readDoc: (filePath: string) => Promise<{
          success: boolean
          text?: string
          error?: string
        }>
        readPpt: (filePath: string) => Promise<{
          success: boolean
          text?: string
          error?: string
        }>
        getExcelSheets: (filePath: string) => Promise<{
          success: boolean
          sheets?: string[]
          error?: string
        }>
        getPathForFile: (file: File) => string
      }
      documents: {
        getOpen: () => Promise<{ success: boolean; documents: OpenDocument[]; error?: string }>
        select: (type: string, index: number, docId?: string) => Promise<{ success: boolean; error?: string }>
        getCurrentPageInfo: () => Promise<{
          success: boolean
          currentPage?: number
          totalPages?: number
          startPage?: number
          endPage?: number
          activeDocument?: OpenDocument
          error?: string
        }>
        getSelectionInfo: () => Promise<{
          success: boolean
          hasSelection?: boolean
          selectedText?: string
          selectedTextFull?: string
          selectionType?: string
          isTableSelection?: boolean
          position?: {
            startPara?: number
            startPos?: number
            endPara?: number
            endPos?: number
          }
          filename?: string
          error?: string
        }>
      }
      doc: {
        getActiveKey: () => Promise<{ docKey?: string }>
      }
      selection: {
        pauseDetection: () => Promise<{ success: boolean; error?: string }>
        resumeDetection: () => Promise<{ success: boolean; error?: string }>
      }
      onHwpWindowBound: (callback: (data: { pid: number; hwnd: number; title: string }) => void) => () => void
      onHwpSelectionChanged: (callback: (data: any) => void) => () => void
      onProgress: (callback: (event: string, data: any) => void) => () => void
      hwp: {
        enableBinding: () => Promise<{ success: boolean; error?: string }>
        checkCompatibility: () => Promise<{ success: boolean; status?: string; message?: string; hwpInfo?: any; error?: string }>
        ensureCompatibility: () => Promise<{ success: boolean; error?: string }>
        getCompatibilityStatus: () => Promise<{ status: string; lastResult: any }>
      }
      onPythonReady: (callback: () => void) => () => void
      onAppStatus: (callback: (message: string, progress?: number) => void) => () => void
      chat: {
        // v4.1.4: 추가 파라미터
        // v5.0: projectId 추가 (RAG 컨텍스트용)
        // v5.1: chatId 추가 (RAG 채팅 스코프 분리)
        // v6.2: model 추가 (비용 계산용)
        send: (
          prompt: string,
          docType?: string,
          docIndex?: string,
          referenceContent?: string,
          referenceFileName?: string,
          startPage?: number,
          endPage?: number,
          diffModeEnabled?: boolean,
          assistantMessageId?: string,
          rejectionInfo?: any[],
          conversationHistory?: string,
          projectId?: string,
          chatId?: string,
          model?: string
        ) => Promise<ChatResponse>
        cancel: () => Promise<{ success: boolean; error?: string }>
        onProgress: (callback: (event: string, data: any) => void) => () => void
      }
      edit: {
        undo: (count?: number) => Promise<{
          success: boolean
          undone?: number
          remaining?: number
          error?: string
        }>
        redo: (count?: number) => Promise<{
          success: boolean
          redone?: number
          error?: string
        }>
        setDiffMode: (enabled: boolean) => Promise<{
          success: boolean
          diffMode?: boolean
          error?: string
        }>
        getDiffMode: () => Promise<{
          success: boolean
          diffMode?: boolean
          error?: string
        }>
        getHistory: () => Promise<{
          success: boolean
          history?: Array<{
            id: number
            chatId: string
            editCount: number
            timestamp: string
          }>
          totalEdits?: number
          error?: string
        }>
        clearHistory: () => Promise<{
          success: boolean
          error?: string
        }>
        accept: () => Promise<{
          success: boolean
          error?: string
        }>
        reject: () => Promise<{
          success: boolean
          error?: string
        }>
      }
      trackChanges: {
        getContext: () => Promise<{
          pending: boolean
          selectionCount: number
          contextVisible: boolean
        }>
        cacheSelection: () => Promise<{
          success: boolean
          selectionCount: number
        }>
        applyAll: () => Promise<{
          success: boolean
          hasRemaining: boolean
          autoComplete: boolean
          error?: string
        }>
        // v4.1.4: RejectResult 구조 포함
        rejectAll: (params?: { docKey?: string; chatId?: string }) => Promise<{
          success: boolean
          rejectionType?: 'all' | 'partial'
          requiresFullRegen?: boolean
          hasRemaining: boolean
          autoComplete: boolean
          error?: string
          // RejectResult 필드
          fact?: {
            success: boolean
            rejectedCount: number
            rejectionType: 'all' | 'partial'
            requiresFullRegen: boolean
            uncertain: boolean
            mismatch: boolean
          }
          explain?: {
            rejectedOps: any[]
            reason: string
          }
        }>
        applySelected: () => Promise<{
          success: boolean
          processed: number
          hasRemaining: boolean
          autoComplete: boolean
          showToast: boolean
          error?: string
        }>
        rejectSelected: (params?: { docKey?: string; chatId?: string }) => Promise<{
          success: boolean
          rejectionType?: 'all' | 'partial'
          requiresFullRegen?: boolean
          processed: number
          hasRemaining: boolean
          autoComplete: boolean
          showToast: boolean
          error?: string
          fact?: {
            success: boolean
            rejectedCount: number
            rejectionType: 'all' | 'partial'
            requiresFullRegen: boolean
            uncertain: boolean
            mismatch: boolean
          }
          explain?: {
            rejectedOps: any[]
            reason: string
          }
        }>
      }
      // Phase 4: CVD 추출 및 Diff 생성
      cvd: {
        extractPair: (args: {
          projectId: string
          pairId: string
          templatePath: string
          filledPath: string
        }) => Promise<{
          success: boolean
          template_cvd_path?: string
          filled_cvd_path?: string
          error?: string
        }>
        generateDiff: (args: {
          projectId: string
          pairId: string
        }) => Promise<{
          success: boolean
          diff_path?: string
          changes_count?: number
          stats?: {
            totalFields: number
            changedFields: number
            unchangedFields: number
            addedFields: number
            modifiedFields: number
            removedFields: number
          }
          error?: string
        }>
        processTemplatePair: (args: {
          projectId: string
          pairId: string
          templatePath: string
          filledPath: string
        }) => Promise<{
          success: boolean
          diff_path?: string
          changes_count?: number
          stats?: {
            totalFields: number
            changedFields: number
            unchangedFields: number
            addedFields: number
            modifiedFields: number
            removedFields: number
          }
          error?: string
        }>
        onProgress: (callback: (data: { pairId: string; progress: number; message: string }) => void) => () => void
      }
      // Phase 5: RAG 인덱싱/쿼리 (v5.0: openaiApiKey 선택적)
      // Phase 5.1: 채팅 스코프 분리 (v5.1)
      rag: {
        indexPair: (args: {
          projectId: string
          pairId: string
          diffPath: string
          openaiApiKey?: string  // 선택적 - Python 환경변수 사용
        }) => Promise<{
          success: boolean
          indexed_count?: number
          error?: string
        }>
        indexFile: (args: {
          projectId: string
          fileId: string
          textContent: string
          fileName: string
          filePath?: string
          openaiApiKey?: string  // 선택적
        }) => Promise<{
          success: boolean
          indexed_count?: number
          error?: string
        }>
        deletePair: (args: {
          projectId: string
          pairId: string
          openaiApiKey?: string
        }) => Promise<{
          success: boolean
          error?: string
        }>
        deleteFile: (args: {
          projectId: string
          fileId: string
          chatId?: string
          openaiApiKey?: string
        }) => Promise<{
          success: boolean
          error?: string
        }>
        // v5.1: 채팅 스코프 분리
        indexChatFile: (args: {
          projectId: string
          chatId: string
          fileId: string
          textContent: string
          fileName: string
          filePath?: string
          openaiApiKey?: string
        }) => Promise<{
          success: boolean
          indexed_count?: number
          error?: string
        }>
        deleteChatScope: (args: {
          projectId: string
          chatId: string
        }) => Promise<{
          success: boolean
          deleted_path?: string
          error?: string
        }>
      }
      maintenance: {
        resetLocalData: (args?: { backup?: boolean }) => Promise<{
          success: boolean
          data?: {
            backupDir: string | null
            backedUp: string[]
            deleted: string[]
          }
          error?: {
            code: string
            message: string
          }
        }>
        validateDb: () => Promise<{
          success: boolean
          data?: {
            ok: boolean
            issues: Array<{ type: string; count: number }>
            counts: Record<string, number> | null
            foreignKeyViolations: Array<Record<string, unknown>>
          }
          error?: {
            code: string
            message: string
          }
        }>
      }
      // v4.1.4: 활성 문서 변경 이벤트 (v4.2: 활성 문서 상세 정보 포함)
      onActiveDocChanged: (callback: (docKey: string | undefined, activeDocHint?: { documentId?: number; path?: string; name?: string }) => void) => () => void
      auth: {
        logout: () => Promise<{ success: boolean }>
      }
      // Update (App Update Check & Download - electron-updater 기반)
      update: {
        check: () => Promise<{
          success: boolean
          updateAvailable?: boolean
          version?: string
          error?: string
        }>
        download: () => Promise<{
          success: boolean
          error?: string
        }>
        install: () => Promise<{
          success: boolean
        }>
        getCurrentVersion: () => Promise<{
          success: boolean
          version: string
          downloadedVersion: string | null
        }>
        startPeriodicCheck: (intervalMs?: number) => Promise<{ success: boolean }>
        stopPeriodicCheck: () => Promise<{ success: boolean }>
        onStatus: (callback: (status: {
          status: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
          info?: { version: string; releaseDate?: string; releaseNotes?: string }
          progress?: { percent: number; bytesPerSecond: number; total: number; transferred: number }
          error?: string
          isCritical?: boolean
          releaseNotes?: string
        }) => void) => () => void
        getLastStatus: () => Promise<{
          success: boolean
          status: {
            status: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
            info?: { version: string; releaseDate?: string; releaseNotes?: string }
            progress?: { percent: number; bytesPerSecond: number; total: number; transferred: number }
            error?: string
            isCritical?: boolean
            releaseNotes?: string
          } | null
        }>
      }
      // Log (Auth & Usage Logging)
      log: {
        auth: (params: {
          success: boolean
          reason?: string
          ipHash?: string
          userAgent?: string
          metadata?: Record<string, unknown>
        }) => Promise<{
          success: boolean
          error?: string
        }>
        usage: (params: {
          eventType: string
          eventData?: Record<string, unknown>
          sessionId?: string
        }) => Promise<{
          success: boolean
          error?: string
        }>
      }
      // Device (Device ID Management)
      device: {
        getId: () => Promise<{
          success: boolean
          deviceId: string
        }>
        getInfo: () => Promise<{
          success: boolean
          data: {
            deviceId: string
            platform: string
            hostname: string
            arch: string
            createdAt: string | null
          }
        }>
        regenerateId: () => Promise<{
          success: boolean
          deviceId: string
        }>
      }
    }
  }
}

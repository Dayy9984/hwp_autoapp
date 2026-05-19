import { create } from 'zustand'

export interface OpenDocument {
  id: string
  name: string
  path: string
  type: 'hwp' | 'word' | 'excel' | 'txt' | 'unknown'
  index: number  // Python에서 사용하는 문서 인덱스
  documentId?: number  // HWP DocumentID (있으면 최신 문서 판단에 사용)
  isBound?: boolean  // Python에서 현재 바인딩된 문서
  pid?: number  // HWP 프로세스 ID
  hwnd?: number  // HWP 윈도우 핸들
}

// 업로드된 파일 (txt, excel 등 - 서버 분리 대비)
export interface UploadedFile {
  id: string
  name: string
  path: string
  type: 'txt' | 'excel' | 'pdf' | 'hwp' | 'word' | 'pptx'
  content: string  // 파일 내용 (서버로 전송할 문자열)
  encoding?: string
  sheetName?: string  // Excel용
  size?: number
}

// 페이지 정보
interface PageInfo {
  currentPage: number
  totalPages: number
  startPage: number
  endPage: number
}

// 선택 영역 정보
export interface SelectionInfo {
  hasSelection: boolean
  selectedText: string           // 미리보기용 (최대 200자)
  selectedTextFull: string       // 전체 텍스트 (LLM 전송용)
  selectionType: 'cursor' | 'text' | 'table' | 'page'
  isTableSelection: boolean
  position?: {
    startPara?: number
    startPos?: number
    endPara?: number
    endPos?: number
  }
  filename?: string
}

// 최대 업로드 파일 수
const MAX_UPLOADED_FILES = 5
const hashString = async (value: string): Promise<string> => {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) {
    console.warn('crypto.subtle unavailable; falling back to timestamp id')
    return `${Date.now()}_${Math.random().toString(16).slice(2)}`
  }
  const data = new TextEncoder().encode(value)
  const digest = await subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

const buildFileId = async (
  prefix: string,
  filePath: string,
  fileName: string,
  size?: number
): Promise<string> => {
  const nonce = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}_${Math.random().toString(16).slice(2)}`
  const base = `${prefix}:${fileName}:${filePath}:${size ?? ''}:${nonce}`
  const hash = await hashString(base)
  return `${prefix}_${hash}`
}
const pickLatestHwp = (documents: OpenDocument[]) => {
  const hwpDocs = documents.filter(doc => doc?.type === 'hwp')
  if (!hwpDocs.length) return null

  const withDocId = hwpDocs.filter(doc => typeof doc.documentId === 'number')
  if (withDocId.length) {
    return withDocId.reduce((latest, doc) => {
      const latestId = typeof latest.documentId === 'number' ? latest.documentId : -1
      const docId = typeof doc.documentId === 'number' ? doc.documentId : -1
      return docId > latestId ? doc : latest
    })
  }

  return hwpDocs[hwpDocs.length - 1]
}

const normalizePath = (value?: string | null) => (value || '').replace(/\\/g, '/').toLowerCase()

const findUpdatedSelection = (
  current: OpenDocument | null,
  documents: OpenDocument[]
): OpenDocument | null => {
  if (!current) return null
  const currentPath = normalizePath(current.path)
  const currentName = (current.name || '').toLowerCase()
  const currentDocId = typeof current.documentId === 'number' ? current.documentId : null

  if (currentDocId !== null) {
    const byDocId = documents.find((doc) => doc.documentId === currentDocId)
    if (byDocId) return byDocId
  }

  return documents.find((doc) => {
    if (doc.id === current.id) return true
    if (currentPath && normalizePath(doc.path) === currentPath) return true
    if (currentName && (doc.name || '').toLowerCase() === currentName) return true
    return false
  }) ?? null
}

interface DocumentState {
  // 열린 문서 목록
  openDocuments: OpenDocument[]
  // 현재 선택된 문서 (편집 대상)
  selectedDocument: OpenDocument | null
  // 업로드된 파일들 (txt, excel - 서버 분리 대비, 최대 5개)
  uploadedFiles: UploadedFile[]
  // 로딩 상태
  isLoading: boolean
  // 마지막 갱신 시간
  lastUpdated: number | null
  // 현재 페이지 정보
  pageInfo: PageInfo | null
  // 선택 영역 정보
  selectionInfo: SelectionInfo | null

  // Actions
  setOpenDocuments: (documents: OpenDocument[]) => void
  selectDocument: (document: OpenDocument | null) => void
  addUploadedFile: (file: UploadedFile) => boolean
  removeUploadedFile: (fileId: string) => void
  uploadTxtFile: (filePath: string) => Promise<UploadedFile | null>
  uploadExcelFile: (filePath: string, sheetName?: string) => Promise<UploadedFile | null>
  uploadPdfFile: (filePath: string) => Promise<UploadedFile | null>
  uploadHwpFile: (filePath: string) => Promise<UploadedFile | null>
  uploadDocFile: (filePath: string) => Promise<UploadedFile | null>
  uploadPptFile: (filePath: string) => Promise<UploadedFile | null>
  clearUploadedFiles: () => void
  refreshDocuments: () => Promise<void>
  setLoading: (loading: boolean) => void
  refreshPageInfo: (activeDocHint?: { documentId?: number; path?: string; name?: string }) => Promise<void>
  // 선택 영역 관리
  setSelectionInfo: (info: SelectionInfo | null) => void
  refreshSelectionInfo: () => Promise<void>
  clearSelectionInfo: () => void
  // HWP 연결 해제 시 상태 초기화
  clearDocumentState: () => void
}

export const useDocumentStore = create<DocumentState>((set, get) => ({
  openDocuments: [],
  selectedDocument: null,
  uploadedFiles: [],
  isLoading: false,
  lastUpdated: null,
  pageInfo: null,
  selectionInfo: null,

  setOpenDocuments: (documents) => {
    const current = get().selectedDocument
    // 현재 선택된 문서가 새 목록에 있으면 업데이트
    const updated = current ? findUpdatedSelection(current, documents) : null
    // 없으면 최신 HWP 문서로 자동 선택
    const selected = updated || pickLatestHwp(documents)
    set({
      openDocuments: documents,
      selectedDocument: selected,
      lastUpdated: Date.now()
    })
  },

  selectDocument: (document) => {
    set({ selectedDocument: document })
  },

  // 파일 추가 (최대 5개)
  addUploadedFile: (file) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return false
    }
    // 중복 체크 (같은 경로)
    if (current.some(f => f.path === file.path)) {
      console.warn('이미 추가된 파일입니다.')
      return false
    }
    set({ uploadedFiles: [...current, file] })
    return true
  },

  // 파일 제거
  removeUploadedFile: (fileId) => {
    set({ uploadedFiles: get().uploadedFiles.filter(f => f.id !== fileId) })
  },

  // TXT 파일 업로드 (서버 분리 대비 - 로컬에서 읽어서 문자열로 저장)
  uploadTxtFile: async (filePath: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.file.readTxt(filePath)
      if (result.success && result.content) {
        const name = result.file_name || filePath.split(/[/\\]/).pop() || 'unknown.txt'
        const id = await buildFileId('txt', filePath, name, result.file_size)
        const file: UploadedFile = {
          id,
          name,
          path: filePath,
          type: 'txt',
          content: result.content,
          encoding: result.encoding,
          size: result.file_size,
        }
        set({ uploadedFiles: [...get().uploadedFiles, file] })
        return file
      }
      console.error('Failed to read txt file:', result.error)
      return null
    } catch (err) {
      console.error('Failed to upload txt file:', err)
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  // Excel 파일 업로드 (서버 분리 대비)
  uploadExcelFile: async (filePath: string, sheetName?: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.file.readExcel(filePath, sheetName)
      if (result.success && result.content) {
        const name = result.file_name || filePath.split(/[/\\]/).pop() || 'unknown.xlsx'
        const fileSize = (result as any).file_size ?? 0
        const id = await buildFileId('excel', filePath, name, fileSize)
        const file: UploadedFile = {
          id,
          name,
          path: filePath,
          type: 'excel',
          content: result.content,
          sheetName: result.sheet_name,
          size: fileSize,
        }
        set({ uploadedFiles: [...get().uploadedFiles, file] })
        return file
      }
      console.error('Failed to read excel file:', result.error)
      return null
    } catch (err) {
      console.error('Failed to upload excel file:', err)
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  // PDF 파일 업로드 (서버 분리 대비)
  uploadPdfFile: async (filePath: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.file.readPdf(filePath)
      if (result.success) {
        const extractedText = typeof result.text === 'string' ? result.text : ''
        if (!extractedText.trim()) {
          console.warn('[DocumentStore] PDF text is empty (possibly image-based PDF without OCR dependencies):', filePath)
        }
        const name = filePath.split(/[/\\]/).pop() || 'unknown.pdf'
        const id = await buildFileId('pdf', filePath, name)
        const file: UploadedFile = {
          id,
          name,
          path: filePath,
          type: 'pdf',
          content: extractedText,
        }
        set({ uploadedFiles: [...get().uploadedFiles, file] })
        return file
      }
      console.error('Failed to read pdf file:', result.error)
      return null
    } catch (err) {
      console.error('Failed to upload pdf file:', err)
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  // HWP/HWPX 파일 업로드 (서버 분리 대비)
  uploadHwpFile: async (filePath: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    // 1. 파일 객체를 먼저 생성하여 UI에 즉시 표시
    const name = filePath.split(/[/\\]/).pop() || 'unknown.hwp'
    const id = await buildFileId('hwp', filePath, name)
    const tempFile: UploadedFile = {
      id,
      name,
      path: filePath,
      type: 'hwp',
      content: '', // 빈 content (나중에 업데이트)
    }
    set({ uploadedFiles: [...get().uploadedFiles, tempFile], isLoading: true })

    // 2. 백그라운드에서 CVD 추출 및 content 로드
    try {
      const result = await window.electronAPI.file.readHwp(filePath)
      if (result.success && result.text) {
        // 3. content 업데이트
        const updatedFile: UploadedFile = {
          ...tempFile,
          content: result.text,
        }
        set({
          uploadedFiles: get().uploadedFiles.map(f =>
            f.id === id ? updatedFile : f
          )
        })
        return updatedFile
      }
      // 실패 시 임시 파일 제거
      console.error('Failed to read hwp file:', result.error)
      set({ uploadedFiles: get().uploadedFiles.filter(f => f.id !== id) })
      return null
    } catch (err) {
      console.error('Failed to upload hwp file:', err)
      set({ uploadedFiles: get().uploadedFiles.filter(f => f.id !== id) })
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  // DOCX 파일 업로드 (서버 분리 대비)
  uploadDocFile: async (filePath: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.file.readDoc(filePath)
      if (result.success && result.text) {
        const name = filePath.split(/[/\\]/).pop() || 'unknown.docx'
        const id = await buildFileId('doc', filePath, name)
        const file: UploadedFile = {
          id,
          name,
          path: filePath,
          type: 'word',
          content: result.text,
        }
        set({ uploadedFiles: [...get().uploadedFiles, file] })
        return file
      }
      console.error('Failed to read doc file:', result.error)
      return null
    } catch (err) {
      console.error('Failed to upload doc file:', err)
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  // PPTX 파일 업로드 (서버 분리 대비)
  uploadPptFile: async (filePath: string) => {
    const current = get().uploadedFiles
    if (current.length >= MAX_UPLOADED_FILES) {
      console.warn(`최대 ${MAX_UPLOADED_FILES}개 파일까지 업로드 가능합니다.`)
      return null
    }
    if (current.some(f => f.path === filePath)) {
      console.warn('이미 추가된 파일입니다.')
      return null
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.file.readPpt(filePath)
      if (result.success && result.text) {
        const name = filePath.split(/[/\\]/).pop() || 'unknown.pptx'
        const id = await buildFileId('ppt', filePath, name)
        const file: UploadedFile = {
          id,
          name,
          path: filePath,
          type: 'pptx',
          content: result.text,
        }
        set({ uploadedFiles: [...get().uploadedFiles, file] })
        return file
      }
      console.error('Failed to read pptx file:', result.error)
      return null
    } catch (err) {
      console.error('Failed to upload pptx file:', err)
      return null
    } finally {
      set({ isLoading: false })
    }
  },

  clearUploadedFiles: () => {
    set({ uploadedFiles: [] })
  },

  setLoading: (loading) => {
    set({ isLoading: loading })
  },

  refreshDocuments: async () => {
    // ✅ RAG 인덱싱 중이면 스킵 (Python blocking 방지)
    const { isActive } = await import('./progress-store').then(m => m.useProgressStore.getState())
    if (isActive) {
      console.log('[DocumentStore] Skipping refreshDocuments during RAG indexing')
      return
    }

    set({ isLoading: true })
    try {
      const result = await window.electronAPI.documents.getOpen()
      if (result.success) {
        const currentSelected = get().selectedDocument
        const updates: Partial<DocumentState> = {
          openDocuments: result.documents,
          lastUpdated: Date.now(),
        }

        // 1순위: isBound=true인 문서 (Python에서 실제로 바인딩된 문서)
        const boundDoc = result.documents.find((doc: OpenDocument) => doc.isBound === true)
        if (boundDoc) {
          updates.selectedDocument = boundDoc
        } else {
          // 2순위: 기존 선택된 문서 유지
          const refreshedSelection = findUpdatedSelection(currentSelected, result.documents)
          if (refreshedSelection) {
            updates.selectedDocument = refreshedSelection
          } else {
            // 3순위: 최신 HWP 문서
            const latestHwp = pickLatestHwp(result.documents)
            if (latestHwp) {
              updates.selectedDocument = latestHwp
            } else if (currentSelected) {
              updates.selectedDocument = null
            }
          }
        }

        set(updates)
      }
    } catch (err) {
      console.error('Failed to refresh documents:', err)
    } finally {
      set({ isLoading: false })
    }
  },

  // 현재 페이지 정보 갱신
  refreshPageInfo: async (activeDocHint?: { documentId?: number; path?: string; name?: string }) => {
    try {
      const [pageResult, openResult] = await Promise.all([
        window.electronAPI.documents.getCurrentPageInfo(),
        window.electronAPI.documents.getOpen(),
      ])
      const updates: Partial<DocumentState> = {}
      const currentSelected = get().selectedDocument

      if (pageResult.success) {
        updates.pageInfo = {
          currentPage: pageResult.currentPage!,
          totalPages: pageResult.totalPages!,
          startPage: pageResult.startPage!,
          endPage: pageResult.endPage!,
        }
      } else {
        updates.pageInfo = null
      }

      if (openResult.success) {
        updates.openDocuments = openResult.documents
        updates.lastUpdated = Date.now()

        const activeDocPath = pageResult.success ? pageResult.activeDocument?.path : undefined
        const activeDocName = pageResult.success ? pageResult.activeDocument?.name : undefined
        const activeDocId = pageResult.success ? pageResult.activeDocument?.documentId : undefined
        const normalizedActivePath = normalizePath(activeDocPath)
        const normalizedActiveName = (activeDocName || '').toLowerCase()
        const activeDocByPageInfo = openResult.documents.find((doc: OpenDocument) => {
          const byDocId = typeof activeDocId === 'number' && doc.documentId === activeDocId
          const byPath = normalizedActivePath.length > 0 && normalizePath(doc.path) === normalizedActivePath
          const byName = normalizedActiveName.length > 0 && (doc.name || '').toLowerCase() === normalizedActiveName
          return byDocId || byPath || byName
        })

        // 0순위: 폴링에서 전달받은 activeDocHint로 매칭 (COM 객체 불일치 문제 우회)
        let hintMatch: OpenDocument | undefined
        if (activeDocHint) {
          hintMatch = openResult.documents.find((doc: OpenDocument) => {
            if (typeof activeDocHint.documentId === 'number' && doc.documentId === activeDocHint.documentId) return true
            if (activeDocHint.path && normalizePath(activeDocHint.path) === normalizePath(doc.path)) return true
            if (activeDocHint.name && (doc.name || '').toLowerCase() === activeDocHint.name.toLowerCase()) return true
            return false
          })
        }

        // 1순위: 페이지 정보의 활성 문서
        if (hintMatch) {
          updates.selectedDocument = hintMatch
        } else if (activeDocByPageInfo) {
          updates.selectedDocument = activeDocByPageInfo
        } else {
          // 2순위: isBound=true인 문서 (Python에서 실제로 바인딩된 문서)
          const boundDoc = openResult.documents.find((doc: OpenDocument) => doc.isBound === true)
          if (boundDoc) {
            updates.selectedDocument = boundDoc
          } else {
            // 3순위: 기존 선택된 문서 유지
            const refreshedSelection = findUpdatedSelection(currentSelected, openResult.documents)
            if (refreshedSelection) {
              updates.selectedDocument = refreshedSelection
            } else {
              // 4순위: 최신 HWP 문서
              const latestHwp = pickLatestHwp(openResult.documents)
              if (latestHwp) {
                updates.selectedDocument = latestHwp
              } else if (currentSelected) {
                updates.selectedDocument = null
              }
            }
          }
        }
      }

      set(updates)
    } catch (err) {
      console.error('Failed to get page info:', err)
      set({ pageInfo: null })
    }
  },

  // 선택 영역 정보 설정
  setSelectionInfo: (info) => {
    set({ selectionInfo: info })
  },

  // 선택 영역 정보 갱신
  refreshSelectionInfo: async () => {
    try {
      const result = await window.electronAPI.documents.getSelectionInfo()
      if (result.success) {
        const info: SelectionInfo = {
          hasSelection: result.hasSelection || false,
          selectedText: result.selectedText || '',
          selectedTextFull: result.selectedTextFull || '',
          selectionType: (result.selectionType as SelectionInfo['selectionType']) || 'cursor',
          isTableSelection: result.isTableSelection || false,
          position: result.position,
          filename: result.filename,
        }
        set({ selectionInfo: info })
      } else {
        set({ selectionInfo: null })
      }
    } catch (err) {
      console.error('Failed to get selection info:', err)
      set({ selectionInfo: null })
    }
  },

  // 선택 영역 정보 초기화
  clearSelectionInfo: () => {
    set({ selectionInfo: null })
  },

  // HWP 연결 해제 시 모든 문서 상태 초기화
  clearDocumentState: () => {
    set({
      openDocuments: [],
      selectedDocument: null,
      pageInfo: null,
      selectionInfo: null,
      lastUpdated: null,
    })
  },
}))

// 문서 타입별 아이콘 (React component로 변경됨)
// 이제 getDocumentIconComponent를 사용하세요
export function getDocumentIcon(type: OpenDocument['type'] | 'txt'): 'document' | 'word' | 'excel' | 'text' | 'clipboard' {
  switch (type) {
    case 'hwp':
      return 'document'
    case 'word':
      return 'word'
    case 'excel':
      return 'excel'
    case 'txt':
      return 'text'
    default:
      return 'clipboard'
  }
}

// 문서 타입별 색상
export function getDocumentColor(type: OpenDocument['type'] | 'txt'): string {
  switch (type) {
    case 'hwp':
      return 'text-blue-600'
    case 'word':
      return 'text-blue-500'
    case 'excel':
      return 'text-green-600'
    case 'txt':
      return 'text-gray-600'
    default:
      return 'text-gray-600'
  }
}

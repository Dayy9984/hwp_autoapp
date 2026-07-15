import { useState, useEffect, useRef } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useFolderStore } from '../../stores/folder-store'
import { Upload, FilePlus, Trash2, X, File, AlertCircle, Loader2, CheckCircle2, Plus } from 'lucide-react'
import { DocumentIconRenderer } from '../icons/DocumentIconRenderer'
import type { OpenDocument } from '../../stores/document-store'

interface UploadedFile {
  id: string
  path: string
  name: string
  status: 'uploading' | 'indexing' | 'ready' | 'failed'
  errorMessage?: string
  progress?: number
  progressMessage?: string
}

interface TemplatePairUpload {
  id: string
  templatePath: string | null
  referencePath: string | null
  templateName: string | null
  referenceName: string | null
  status: 'uploading' | 'extracting' | 'indexing' | 'ready' | 'failed'
  errorMessage?: string
  progress?: number
  progressMessage?: string
}

const MAX_TOTAL_FILES = 10
const MAX_TEMPLATE_PAIRS = 5
// 가짜 프로그레스 설정: 매 간격마다 증가량
const FAKE_PROGRESS_INTERVAL = 500  // ms
const FAKE_PROGRESS_INCREMENT = 2   // %

interface BackendUploadedFile {
  id: string
  path: string
  name: string
  originalPath?: string
  type?: string
  size?: number
  addedAt?: number
  indexStatus?: {
    status: 'pending' | 'indexing' | 'ready' | 'failed'
    message?: string
    progress?: number
  }
}

interface BackendErrorInfo {
  code?: string
  message?: string
}

const normalizePathKey = (value?: string | null): string =>
  (value || '').replace(/\\/g, '/').toLowerCase()

const takeFromQueue = <T,>(queue: Map<string, T[]>, key: string): T | undefined => {
  if (!key) return undefined
  const list = queue.get(key)
  if (!list || list.length === 0) return undefined
  const item = list.shift()
  if (!list.length) queue.delete(key)
  return item
}

const reconcileUploadedFiles = (
  prev: UploadedFile[],
  placeholders: UploadedFile[],
  backendFiles: BackendUploadedFile[],
  failedFiles: Array<{ name: string; errorMessage: string }> = []
): UploadedFile[] => {
  const placeholderIds = new Set(placeholders.map((file) => file.id))
  const queueByOriginalPath = new Map<string, BackendUploadedFile[]>()
  const queueByName = new Map<string, BackendUploadedFile[]>()
  // 백엔드가 반환한 실패 파일 — name 기준으로 errorMessage 매칭
  const failedByName = new Map<string, string[]>()

  for (const backendFile of backendFiles) {
    const originalPathKey = normalizePathKey(backendFile.originalPath)
    if (originalPathKey) {
      const list = queueByOriginalPath.get(originalPathKey)
      if (list) list.push(backendFile)
      else queueByOriginalPath.set(originalPathKey, [backendFile])
    }

    const nameKey = backendFile.name || ''
    if (nameKey) {
      const list = queueByName.get(nameKey)
      if (list) list.push(backendFile)
      else queueByName.set(nameKey, [backendFile])
    }
  }

  for (const f of failedFiles) {
    const list = failedByName.get(f.name)
    if (list) list.push(f.errorMessage)
    else failedByName.set(f.name, [f.errorMessage])
  }

  return prev.map((file) => {
    if (!placeholderIds.has(file.id)) return file

    const pathKey = normalizePathKey(file.path)
    const match = takeFromQueue(queueByOriginalPath, pathKey) || takeFromQueue(queueByName, file.name)
    if (!match) {
      // 백엔드가 명시적으로 보고한 실패 메시지 우선
      const failedQueue = failedByName.get(file.name)
      const errorMessage = (failedQueue && failedQueue.length > 0)
        ? failedQueue.shift()!
        : '업로드 결과 매핑 실패'
      return {
        ...file,
        status: 'failed',
        errorMessage,
      }
    }

    return {
      ...file,
      id: match.id,
      path: match.path || file.path,
      name: match.name || file.name,
      status: match.indexStatus?.status === 'ready' ? 'ready' as const : 'indexing' as const,
      errorMessage: undefined,
    }
  })
}

const markUploadedFilesFailed = (
  prev: UploadedFile[],
  placeholders: UploadedFile[],
  errorMessage: string
): UploadedFile[] => {
  const placeholderIds = new Set(placeholders.map((file) => file.id))
  return prev.map((file) =>
    placeholderIds.has(file.id)
      ? { ...file, status: 'failed', errorMessage }
      : file
  )
}

const resolveProjectUploadErrorMessage = (error?: BackendErrorInfo): string => {
  const code = error?.code || ''
  if (code === 'MAX_FILES_EXCEEDED') {
    return `최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`
  }
  if (code === 'MAX_PAIRS_EXCEEDED') {
    return `양식쌍은 최대 ${MAX_TEMPLATE_PAIRS}쌍까지 추가할 수 있습니다.`
  }
  return error?.message || '업로드 실패'
}

export function AddFolderFileModal() {
  const { activeModal, modalData, closeModal } = useUIStore()
  // ✅ Zustand selector 패턴 사용 - store 변경 시 자동 구독
  const folderId = modalData?.folderId as string | undefined
  const folder = useFolderStore(
    (state) => folderId ? state.folders.find(f => f.id === folderId) ?? null : null
  )
  const { addProjectFile, addTemplatePair, removeProjectFile, removeTemplatePair, updateFileIndexStatus, updatePairIndexStatus } = useFolderStore()
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([])
  const [templatePairs, setTemplatePairs] = useState<TemplatePairUpload[]>([])
  const [isDragging, setIsDragging] = useState(false)
  // 양식쌍 드래그 상태 (pairId를 키로 사용)
  const [templateDragging, setTemplateDragging] = useState<Record<string, boolean>>({})
  const [referenceDragging, setReferenceDragging] = useState<Record<string, boolean>>({})

  const isOpen = activeModal === 'add-folder-file'

  // 실제로 파일이 선택된 양식쌍만 카운트 (빈 폼은 제외)
  const totalFileCount = uploadedFiles.length +
    (templatePairs.filter(p => p.templatePath && p.referencePath).length * 2)
  const canAddMore = totalFileCount < MAX_TOTAL_FILES
  const hasProcessing = uploadedFiles.some(f => f.status === 'uploading' || f.status === 'indexing') ||
    templatePairs.some(p => p.status === 'uploading' || p.status === 'extracting' || p.status === 'indexing')

  // ⭐ 가짜 프로그레스 애니메이션 (부드러운 차오르는 효과)
  const fakeProgressTimerRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    // 진행 중인 항목이 있으면 타이머 시작
    if (hasProcessing) {
      fakeProgressTimerRef.current = setInterval(() => {
        // 일반 파일 프로그레스 증가
          setUploadedFiles(prev => prev.map(f => {
            const cap = f.status === 'indexing' ? 90 : 95
            if ((f.status === 'uploading' || f.status === 'indexing') && (f.progress || 0) < cap) {
              const current = f.progress || 0
              let increment = FAKE_PROGRESS_INCREMENT

              // 60% 이상(Merging)이면 속도 늦춤
              if (current > 60) increment = 0.5
              // 80% 이상이면 더 늦춤
              if (current > 80) increment = 0.1

              return { ...f, progress: Math.min(current + increment, cap) }
            }
            return f
          }))

        // 양식쌍 프로그레스 증가
          setTemplatePairs(prev => prev.map(p => {
            const cap = (p.status === 'extracting' || p.status === 'indexing') ? 90 : 95
            if ((p.status === 'uploading' || p.status === 'extracting' || p.status === 'indexing') && (p.progress || 0) < cap) {
              const current = p.progress || 0

              // 기본 증가량
              let increment = FAKE_PROGRESS_INCREMENT

              // HDML 추출('extracting')은 더 빠르게 증가 (5%씩)
              if (p.status === 'extracting') {
                increment = 5
              }

              // 60% 이상이면 속도 늦춤 (RAG Merging 등 고려)
              if (current > 60) increment = Math.min(increment, 0.5)
              // 80% 이상이면 더 늦춤
              if (current > 80) increment = Math.min(increment, 0.1)

              return { ...p, progress: Math.min(current + increment, cap) }
            }
            return p
          }))
      }, FAKE_PROGRESS_INTERVAL)
    } else {
      // 진행 중인 항목 없으면 타이머 정리
      if (fakeProgressTimerRef.current) {
        clearInterval(fakeProgressTimerRef.current)
        fakeProgressTimerRef.current = null
      }
    }

    return () => {
      if (fakeProgressTimerRef.current) {
        clearInterval(fakeProgressTimerRef.current)
      }
    }
  }, [hasProcessing])

  // 프로젝트가 바뀔 때 기존 파일 로드 및 상태 초기화
  useEffect(() => {
    if (folder) {
      // 기존 프로젝트 파일을 uploadedFiles에 로드
      const existingFiles = (folder.projectFiles || []).map(file => ({
        id: file.id,
        name: file.name,
        path: file.path,
        status: file.indexStatus?.status === 'ready' ? 'ready' as const :
          file.indexStatus?.status === 'indexing' ? 'indexing' as const :
            file.indexStatus?.status === 'pending' ? 'indexing' as const :  // pending도 indexing으로 표시
              file.indexStatus?.status === 'failed' ? 'failed' as const : 'indexing' as const,  // 기본값도 indexing
        errorMessage: file.indexStatus?.message
      }))
      setUploadedFiles(existingFiles)
      console.log('[AddFolderFileModal] Loaded existing files:', existingFiles.length)
    } else {
      setUploadedFiles([])
    }

    // ✅ folder-store의 TemplatePair 타입에 맞게 직접 접근
    // ⭐ FIX: 진행 중인 양식쌍은 로컬 상태 유지 (UI 깜빡임 방지)
    if (folder && folder.templatePairs && folder.templatePairs.length > 0) {
      setTemplatePairs(prev => {
        // 현재 진행 중인 양식쌍 ID 목록 (uploading, extracting, indexing)
        const inProgressIds = prev
          .filter(p => p.status === 'uploading' || p.status === 'extracting' || p.status === 'indexing')
          .map(p => p.id)

        // 스토어에서 가져온 양식쌍
        // ⭐ 양쪽 형식 모두 지원: folder-store 형식 (templateName) + manifest 형식 (templateFile.name)
        const storePairs = folder.templatePairs.map((pair: any) => ({
          id: pair.id,
          templatePath: pair.templatePath || pair.templateFile?.relPath || null,
          referencePath: pair.referencePath || pair.filledFile?.relPath || null,
          templateName: pair.templateName || pair.templateFile?.name || null,
          referenceName: pair.referenceName || pair.filledFile?.name || null,
          status: pair.indexStatus?.status === 'ready' ? 'ready' as const :
            pair.indexStatus?.status === 'indexing' ? 'indexing' as const :
              pair.indexStatus?.status === 'pending' ? 'indexing' as const :
                pair.extractStatus?.status === 'indexing' ? 'extracting' as const :
                  pair.extractStatus?.status === 'pending' ? 'extracting' as const :
                    pair.indexStatus?.status === 'failed' ? 'failed' as const : 'extracting' as const,
          errorMessage: pair.indexStatus?.message
        }))

        // 진행 중인 양식쌍은 로컬 상태 유지, 나머지는 스토어에서 가져옴
        const mergedPairs = storePairs.map(storePair => {
          const localPair = prev.find(p => p.id === storePair.id)
          // 로컬에서 진행 중이면 로컬 상태 유지 (파일명 등 보존)
          if (localPair && inProgressIds.includes(storePair.id)) {
            return localPair
          }
          return storePair
        })

        // 스토어에 없지만 로컬에서 진행 중인 양식쌍 추가 (새로 업로드 중인 것)
        const localOnlyPairs = prev.filter(p =>
          inProgressIds.includes(p.id) && !storePairs.find(sp => sp.id === p.id)
        )

        return [...mergedPairs, ...localOnlyPairs]
      })
      console.log('[AddFolderFileModal] Loaded/merged template pairs')
    } else if (!folder?.templatePairs?.length) {
      // 양식쌍이 없을 때만 초기화 (진행 중인 것이 없을 때)
      setTemplatePairs(prev => {
        const hasInProgress = prev.some(p =>
          p.status === 'uploading' || p.status === 'extracting' || p.status === 'indexing'
        )
        if (hasInProgress) {
          return prev // 진행 중인 것 있으면 유지
        }
        return [{
          id: `${Date.now()}-${Math.random()}`,
          templatePath: null,
          referencePath: null,
          templateName: null,
          referenceName: null,
          status: 'ready' as const,
        }]
      })
    }
  }, [folderId, folder])

  // 모달이 열릴 때 기본 양식쌍 폼이 없으면 추가
  useEffect(() => {
    if (isOpen && templatePairs.length === 0) {
      setTemplatePairs([{
        id: `${Date.now()}-${Math.random()}`,
        templatePath: null,
        referencePath: null,
        templateName: null,
        referenceName: null,
        status: 'ready' as const,
      }])
    }
  }, [isOpen, templatePairs.length])

  // Progress 이벤트 리스너
  useEffect(() => {
    if (!isOpen) return

    const handleProgress = (event: string, data: any) => {
      // console.log('[AddFolderFileModal] Progress event:', event, data)

      // HDML 진행 상태
      if (event === 'hdml:progress' && data.pairId) {
        setTemplatePairs(prev => prev.map(p =>
          p.id === data.pairId
            ? {
              ...p,
              status: 'extracting' as const,
              // ⭐ 실제 값이 현재보다 크면 사용, 아니면 유지
              progress: typeof data.progress === 'number'
                ? Math.max(p.progress || 0, Math.round(data.progress))
                : p.progress,
              progressMessage: data.message
            }
            : p
        ))
      }

      // 양식쌍 인덱싱 완료
      if (event === 'pair:indexComplete' && data.pairId) {
        console.log('[AddFolderFileModal] Pair indexing completed:', data.pairId)

        // 모달 로컬 상태 업데이트
        setTemplatePairs(prev => prev.map(p =>
          p.id === data.pairId
            ? { ...p, status: 'ready' as const }
            : p
        ))

        // folder-store도 업데이트 (영구 저장)
        if (folderId) {
          updatePairIndexStatus(folderId, data.pairId, { status: 'ready' as const })
        }
      }

      // Template Pair 업로드 실패 (HDML 추출/Diff 생성 실패)
      if (event === 'pair:uploadFailed' && data.pairId) {
        console.error('[AddFolderFileModal] Template pair upload failed:', data.pairId, data.error)
        console.log('[AddFolderFileModal] Current templatePairs before removal:', templatePairs)

        // ⭐ CRITICAL: folder store에서도 제거 (manifest에서는 backend에서 이미 제거됨)
        if (folderId) {
          removeTemplatePair(folderId, data.pairId)
        }

        // UI 모달 상태에서 pair 제거
        setTemplatePairs(prev => {
          const filtered = prev.filter(p => p.id !== data.pairId)
          console.log('[AddFolderFileModal] Removed pair from modal state, remaining count:', filtered.length)
          return filtered
        })

        // 에러 메시지 표시
        alert(`양식쌍 업로드 실패\n${data.error}`)
      }
    }

    const unsubscribe = window.electronAPI.onProgress(handleProgress)
    return () => unsubscribe()
  }, [isOpen])

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !hasProcessing) {
        closeModal()
      }
    }

    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [isOpen, hasProcessing])

  const getFileExtension = (fileName: string | undefined): string => {
    if (!fileName) return ''
    return fileName.split('.').pop()?.toLowerCase() || ''
  }

  const handleUploadClick = async () => {
    if (!folderId) {
      alert('프로젝트 ID가 없습니다.')
      return
    }

    if (!canAddMore) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`)
      return
    }

    const result = await window.electronAPI.invoke('dialog:selectFiles', { type: 'reference' })
    if (!result.success || !result.filePaths || result.filePaths.length === 0) {
      return
    }

    const remainingSlots = Math.max(0, MAX_TOTAL_FILES - totalFileCount)
    if (remainingSlots <= 0) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`)
      return
    }

    const selectedPaths = result.filePaths.slice(0, remainingSlots)
    if (selectedPaths.length < result.filePaths.length) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다. ${selectedPaths.length}개 파일만 추가됩니다.`)
    }
    if (selectedPaths.length === 0) {
      return
    }

    // 선택된 파일들을 uploading 상태로 추가
    const newFiles: UploadedFile[] = selectedPaths.map((filePath: string) => {
      const fileName = filePath.split(/[/\\]/).pop() || filePath
      return {
        id: `${Date.now()}-${Math.random()}`,
        path: filePath,
        name: fileName,
        status: 'uploading',
      }
    })

    setUploadedFiles(prev => [...prev, ...newFiles])

    // 즉시 업로드 시작
    try {
      const uploadResult = await window.electronAPI.invoke('project:uploadFiles', {
        projectId: folderId,
        filePaths: selectedPaths,
      })

      if (uploadResult.success && uploadResult.data) {
        const backendFiles: BackendUploadedFile[] = Array.isArray(uploadResult.data.uploadedFiles)
          ? uploadResult.data.uploadedFiles
          : []
        // 부분 실패 파일 (UNSUPPORTED_EXTENSION, FILE_TOO_LARGE 등) — 개별 마킹
        const failedFiles: Array<{ name: string; errorCode: string; errorMessage: string }> =
          Array.isArray(uploadResult.data.failedFiles) ? uploadResult.data.failedFiles : []

        // folder-store에 추가 (Backend 형식 → Frontend 형식 변환)
        backendFiles.forEach((file) => {
          addProjectFile(folderId, {
            id: file.id,
            path: file.path,
            name: file.name,
            type: file.type || 'reference',
            size: file.size || 0,
            indexStatus: file.indexStatus || { status: 'pending' as const },
            createdAt: file.addedAt || Date.now()  // Backend addedAt → Frontend createdAt
          })
        })

        console.log('[AddFolderFileModal] 일반 파일 업로드 완료, 성공:', backendFiles.length, '실패:', failedFiles.length)

        // Backend ID/경로로 플레이스홀더 교체 (indexStatus가 ready면 즉시 완료)
        // 실패한 파일은 backend가 보고한 errorMessage로 마킹
        setUploadedFiles((prev) => reconcileUploadedFiles(prev, newFiles, backendFiles, failedFiles))
      } else {
        const backendMessage = resolveProjectUploadErrorMessage(uploadResult.error)
        if (uploadResult.error?.code === 'MAX_FILES_EXCEEDED' || uploadResult.error?.code === 'MAX_PAIRS_EXCEEDED') {
          alert(backendMessage)
        }
        setUploadedFiles((prev) =>
          markUploadedFilesFailed(prev, newFiles, backendMessage)
        )
      }
    } catch (error) {
      console.error('File upload error:', error)
      setUploadedFiles((prev) => markUploadedFilesFailed(prev, newFiles, '업로드 중 오류 발생'))
    }
  }

  const handleRemoveFile = (fileId: string) => {
    setUploadedFiles(prev => prev.filter(f => f.id !== fileId))
  }

  // 업로드된 일반 파일 삭제
  const handleDeleteProjectFile = async (fileId: string) => {
    if (!folderId) return

    // failed/uploading 상태는 백엔드 DB에 없음 → 로컬 state에서만 제거
    const target = uploadedFiles.find(f => f.id === fileId)
    if (target && target.status !== 'ready') {
      setUploadedFiles(prev => prev.filter(f => f.id !== fileId))
      return
    }

    try {
      const result = await window.electronAPI.invoke('project:removeFile', {
        projectId: folderId,
        fileId
      })

      // NOT_FOUND는 이미 삭제됐거나 DB에 없는 케이스 — 정상 종료로 처리
      const errorCode = result?.error?.code
      if (!result?.success && errorCode !== 'NOT_FOUND') {
        throw new Error(result?.error?.message || '삭제 실패')
      }

      // 백엔드 삭제 성공/NOT_FOUND 모두 로컬 state에서 제거
      removeProjectFile(folderId, fileId)
      setUploadedFiles(prev => prev.filter(f => f.id !== fileId))
      console.log('[AddFolderFileModal] 일반 파일 삭제 완료, fileId:', fileId)
      if (result?.ragDelete) {
        const hasWarnings = Array.isArray(result.ragDelete.warnings) && result.ragDelete.warnings.length > 0
        const openaiMissing = result.ragDelete.openai_file_deleted === false
        if (result.ragDelete.success === false || hasWarnings || openaiMissing) {
          console.warn('[AddFolderFileModal] RAG 삭제 경고:', result.ragDelete.error || result.ragDelete.warnings)
          const detailMessage = result.ragDelete.error
            || (hasWarnings ? result.ragDelete.warnings.join(' / ') : '')
            || '자세한 내용은 로그를 확인하세요.'
          alert(`RAG 삭제 확인 필요: ${detailMessage}`)
        }
      }
    } catch (err) {
      console.error('Failed to delete project file:', err)
      alert('파일 삭제에 실패했습니다.')
    }
  }

  // 업로드된 양식쌍 삭제
  const handleDeleteTemplatePair = async (pairId: string) => {
    if (!folderId) return

    // failed/uploading 상태는 백엔드 DB에 없음 → 로컬 state에서만 제거
    const target = uploadedFiles.find(f => f.id === pairId)
    if (target && target.status !== 'ready') {
      setUploadedFiles(prev => prev.filter(f => f.id !== pairId))
      return
    }

    try {
      const result = await window.electronAPI.invoke('project:removeTemplatePair', {
        projectId: folderId,
        pairId
      })

      // NOT_FOUND는 이미 삭제된 상태 — 정상 종료로 처리
      const errorCode = (result as { error?: { code?: string } })?.error?.code
      if (result && (result as { success?: boolean }).success === false && errorCode !== 'NOT_FOUND') {
        throw new Error((result as { error?: { message?: string } }).error?.message || '삭제 실패')
      }

      removeTemplatePair(folderId, pairId)
      setUploadedFiles(prev => prev.filter(f => f.id !== pairId))
      console.log('[AddFolderFileModal] 양식쌍 삭제 완료, pairId:', pairId)
    } catch (err) {
      console.error('Failed to delete template pair:', err)
      alert('양식쌍 삭제에 실패했습니다.')
    }
  }

  const handleAddTemplatePair = () => {
    if (templatePairs.length >= MAX_TEMPLATE_PAIRS) {
      alert(`양식쌍은 최대 ${MAX_TEMPLATE_PAIRS}쌍까지 추가할 수 있습니다.`)
      return
    }

    if (totalFileCount + 2 > MAX_TOTAL_FILES) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`)
      return
    }

    setTemplatePairs(prev => [...prev, {
      id: `pair-${Date.now()}-${Math.random()}`,
      templatePath: null,
      referencePath: null,
      templateName: null,
      referenceName: null,
      status: 'ready' as const,
    }])
  }

  const handleRemoveTemplatePair = (pairId: string) => {
    setTemplatePairs(prev => prev.filter(p => p.id !== pairId))
  }

  // 드래그 드롭 핸들러
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    if (!folderId) return
    if (!canAddMore) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`)
      return
    }

    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return

    // ✅ Electron webUtils.getPathForFile() 사용
    const resolvedPaths = files
      .map(f => window.electronAPI.file.getPathForFile(f))
      .filter((path): path is string => typeof path === 'string' && path.length > 0)

    if (resolvedPaths.length === 0) {
      alert('파일 경로를 확인할 수 없습니다. 파일 선택 버튼으로 다시 시도해주세요.')
      return
    }

    const remainingSlots = Math.max(0, MAX_TOTAL_FILES - totalFileCount)
    if (remainingSlots <= 0) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다 (양식쌍 1개 = 2개 계산)`)
      return
    }

    const filePaths = resolvedPaths.slice(0, remainingSlots)
    if (filePaths.length < resolvedPaths.length) {
      alert(`최대 ${MAX_TOTAL_FILES}개까지 업로드 가능합니다. ${filePaths.length}개 파일만 추가됩니다.`)
    }

    // 파일들을 uploading 상태로 즉시 추가
    const newFiles: UploadedFile[] = filePaths.map((filePath) => {
      const fileName = filePath.split(/[/\\]/).pop() || filePath
      return {
        id: `${Date.now()}-${Math.random()}`,
        path: filePath,
        name: fileName,
        status: 'uploading',
      }
    })

    setUploadedFiles(prev => [...prev, ...newFiles])

    // 즉시 업로드 시작
    try {
      const uploadResult = await window.electronAPI.invoke('project:uploadFiles', {
        projectId: folderId,
        filePaths,
      })

      if (uploadResult.success && uploadResult.data) {
        const backendFiles: BackendUploadedFile[] = Array.isArray(uploadResult.data.uploadedFiles)
          ? uploadResult.data.uploadedFiles
          : []

        // folder-store에 추가 (Backend 형식 → Frontend 형식 변환)
        backendFiles.forEach((file) => {
          addProjectFile(folderId, {
            id: file.id,
            path: file.path,
            name: file.name,
            type: file.type || 'reference',
            size: file.size || 0,
            indexStatus: file.indexStatus || { status: 'pending' as const },
            createdAt: file.addedAt || Date.now()  // Backend addedAt → Frontend createdAt
          })
        })

        console.log('[AddFolderFileModal] 드래그 드롭 파일 업로드 완료, 개수:', backendFiles.length)

        // Backend ID/경로로 플레이스홀더 교체 (indexStatus가 ready면 즉시 완료)
        setUploadedFiles((prev) => reconcileUploadedFiles(prev, newFiles, backendFiles))
      } else {
        const backendMessage = resolveProjectUploadErrorMessage(uploadResult.error)
        if (uploadResult.error?.code === 'MAX_FILES_EXCEEDED' || uploadResult.error?.code === 'MAX_PAIRS_EXCEEDED') {
          alert(backendMessage)
        }
        setUploadedFiles((prev) =>
          markUploadedFilesFailed(prev, newFiles, backendMessage)
        )
      }
    } catch (error) {
      console.error('Drag drop upload error:', error)
      setUploadedFiles((prev) => markUploadedFilesFailed(prev, newFiles, '업로드 중 오류 발생'))
    }
  }

  const handleTemplateDrop = (e: React.DragEvent, pairId: string) => {
    e.preventDefault()
    e.stopPropagation()

    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return

    const file = files[0]
    const fileName = file.name
    // ✅ Electron webUtils.getPathForFile() 사용
    const filePath = window.electronAPI.file.getPathForFile(file)

    setTemplatePairs(prev => prev.map(p =>
      p.id === pairId ? { ...p, templatePath: filePath, templateName: fileName } : p
    ))

    // 템플릿과 참조가 모두 선택되었으면 자동 업로드
    const pair = templatePairs.find(p => p.id === pairId)
    if (pair?.referencePath) {
      uploadTemplatePair(pairId, filePath, pair.referencePath)
    }
  }

  const handleReferenceDrop = (e: React.DragEvent, pairId: string) => {
    e.preventDefault()
    e.stopPropagation()

    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return

    const file = files[0]
    const fileName = file.name
    // ✅ Electron webUtils.getPathForFile() 사용
    const filePath = window.electronAPI.file.getPathForFile(file)

    setTemplatePairs(prev => prev.map(p =>
      p.id === pairId ? { ...p, referencePath: filePath, referenceName: fileName } : p
    ))

    // 템플릿과 참조가 모두 선택되었으면 자동 업로드
    const pair = templatePairs.find(p => p.id === pairId)
    if (pair?.templatePath) {
      uploadTemplatePair(pairId, pair.templatePath, filePath)
    }
  }

  const handleTemplateFileSelect = async (pairId: string) => {
    const result = await window.electronAPI.invoke('dialog:selectFiles', { type: 'template' })
    if (result.success && result.filePaths && result.filePaths.length > 0) {
      const filePath = result.filePaths[0]
      const fileName = filePath.split(/[/\\]/).pop() || filePath

      setTemplatePairs(prev => prev.map(p =>
        p.id === pairId ? { ...p, templatePath: filePath, templateName: fileName } : p
      ))

      // 템플릿과 참조가 모두 선택되었으면 자동 업로드
      const pair = templatePairs.find(p => p.id === pairId)
      if (pair?.referencePath) {
        uploadTemplatePair(pairId, filePath, pair.referencePath)
      }
    }
  }

  const handleReferenceFileSelect = async (pairId: string) => {
    const result = await window.electronAPI.invoke('dialog:selectFiles', { type: 'template' })
    if (result.success && result.filePaths && result.filePaths.length > 0) {
      const filePath = result.filePaths[0]
      const fileName = filePath.split(/[/\\]/).pop() || filePath

      setTemplatePairs(prev => prev.map(p =>
        p.id === pairId ? { ...p, referencePath: filePath, referenceName: fileName } : p
      ))

      // 템플릿과 참조가 모두 선택되었으면 자동 업로드
      const pair = templatePairs.find(p => p.id === pairId)
      if (pair?.templatePath) {
        uploadTemplatePair(pairId, pair.templatePath, filePath)
      }
    }
  }

  const uploadTemplatePair = async (pairId: string, templatePath: string, referencePath: string) => {
    if (!folderId) return

    setTemplatePairs(prev => prev.map(p =>
      p.id === pairId ? { ...p, status: 'uploading' as const } : p
    ))

    try {
      const result = await window.electronAPI.invoke('project:addTemplatePair', {
        projectId: folderId,
        templatePath,
        filledPath: referencePath,
      })

      if (result.success && result.data) {
        // ⭐ 백엔드 형식 → folder-store 형식으로 변환
        const backendPair = result.data.pair
        const storePair = {
          id: backendPair.id,
          templatePath: backendPair.templateFile?.relPath || templatePath,
          referencePath: backendPair.filledFile?.relPath || referencePath,
          templateName: backendPair.templateFile?.name || templatePath.split(/[/\\]/).pop() || '',
          referenceName: backendPair.filledFile?.name || referencePath.split(/[/\\]/).pop() || '',
          diffPath: backendPair.diffPath,
          extractStatus: backendPair.extractStatus || { status: 'pending' as const },
          indexStatus: backendPair.indexStatus || { status: 'pending' as const },
          createdAt: backendPair.createdAt || Date.now()
        }

        addTemplatePair(folderId, storePair)
        console.log('[AddFolderFileModal] 양식쌍 업로드 완료, pairId:', pairId, '→', storePair.id)

        // ⭐ CRITICAL FIX: Backend ID로 교체 + 파일명 보존 (이벤트 수신을 위해 필수)
        const backendPairId = backendPair.id

        // HDML 추출 및 RAG 인덱싱 진행 중이므로 'extracting' 상태로 변경
        // 완료 시 'pair:indexComplete' 이벤트로 'ready'로 변경
        setTemplatePairs(prev => prev.map(p =>
          p.id === pairId ? {
            ...p,
            id: backendPairId,
            status: 'extracting' as const,
            // 파일명 보존 (로컬에서 이미 설정됨)
            templateName: p.templateName || storePair.templateName,
            referenceName: p.referenceName || storePair.referenceName,
          } : p
        ))
      } else {
        const backendMessage = resolveProjectUploadErrorMessage(result.error)
        if (result.error?.code === 'MAX_FILES_EXCEEDED' || result.error?.code === 'MAX_PAIRS_EXCEEDED') {
          alert(backendMessage)
        }
        setTemplatePairs(prev => prev.map(p =>
          p.id === pairId ? {
            ...p,
            status: 'failed' as const,
            errorMessage: backendMessage
          } : p
        ))
      }
    } catch (error) {
      console.error('Template pair upload error:', error)
      setTemplatePairs(prev => prev.map(p =>
        p.id === pairId ? {
          ...p,
          status: 'failed' as const,
          errorMessage: '업로드 중 오류 발생'
        } : p
      ))
    }
  }

  const getFileIconType = (fileName: string): OpenDocument['type'] | 'txt' | 'pdf' => {
    const extension = getFileExtension(fileName)
    switch (extension) {
      case 'hwp':
      case 'hwpx':
        return 'hwp'
      case 'pdf':
        return 'pdf'
      case 'docx':
        return 'word'
      case 'xls':
      case 'xlsx':
      case 'xlsm':
        return 'excel'
      case 'ppt':
      case 'pptx':
        return 'word' // PowerPoint - Office 계열로 표시
      case 'txt':
      case 'md':
        return 'txt' // Markdown도 텍스트 아이콘 사용
      default:
        return 'txt'
    }
  }

  const getStatusText = (status: string): string => {
    switch (status) {
      case 'uploading': return '업로드 중...'
      case 'extracting': return 'HDML 추출 중...'
      case 'indexing': return '인덱싱 중...'
      case 'ready': return '완료'
      case 'failed': return '실패'
      default: return ''
    }
  }

  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'uploading':
      case 'extracting':
      case 'indexing':
        return 'var(--accent)'
      case 'ready':
        return 'var(--success)'
      case 'failed':
        return 'var(--danger)'
      default:
        return 'var(--text-secondary)'
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={() => !hasProcessing && closeModal()}
        aria-hidden="true"
      />

      <div className="fixed z-50 pointer-events-none w-full max-w-3xl px-4 flex justify-center">
        <div
          className="w-full pointer-events-auto max-h-[90vh] overflow-hidden rounded-2xl shadow-2xl border border-border flex flex-col animate-scale-up"
          onClick={(e) => e.stopPropagation()}
          style={{ backgroundColor: 'var(--bg)' }}
        >
          {/* Header */}
          <div className="px-6 py-4 border-b border-border flex items-center justify-between flex-shrink-0 bg-bg-secondary">
            <div>
              <h2 className="text-lg font-serif font-bold text-text">
                파일 추가
              </h2>
              <p className="text-xs mt-1 text-text-secondary">
                총 {totalFileCount}/10개 (양식쌍 1개 = 2개 계산)
              </p>
            </div>
            <button
              onClick={() => !hasProcessing && closeModal()}
              disabled={hasProcessing}
              className="p-2 rounded-lg text-text-secondary hover:bg-bg-tertiary transition-colors disabled:opacity-50"
            >
              <X size={20} />
            </button>
          </div>

          <div className="p-6 space-y-8 overflow-y-auto flex-1">
            {/* 일반 파일 업로드 섹션 */}
            <section>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <div className="p-1.5 rounded-md bg-accent/10 text-accent">
                    <File size={16} />
                  </div>
                  <h3 className="text-sm font-bold text-text">일반 파일</h3>
                </div>
                <button
                  onClick={handleUploadClick}
                  disabled={hasProcessing}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-accent-dark transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                >
                  <FilePlus size={14} />
                  파일 선택
                </button>
              </div>

              {uploadedFiles.length === 0 ? (
                <div
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  className={`
                    rounded-xl border-2 border-dashed transition-all duration-200 flex flex-col items-center justify-center py-12
                    ${isDragging
                      ? 'border-accent bg-accent/5'
                      : 'border-border bg-bg-secondary hover:bg-bg-tertiary'
                    }
                  `}
                >
                  <div className={`p-4 rounded-full mb-3 ${isDragging ? 'bg-accent/10 text-accent' : 'bg-bg text-text-tertiary'}`}>
                    <Upload size={32} />
                  </div>
                  <div className="text-sm font-medium text-text mb-1">
                    파일을 드래그 앤 드롭하세요
                  </div>
                  <div className="text-xs text-text-secondary">
                    한글(HWP/HWPX), PDF, Word(DOCX), Excel(XLS/XLSX/XLSM), PowerPoint(PPTX), Markdown(MD), TXT 파일 지원 (최대 50MB)
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  {uploadedFiles.map((uploadedFile) => (
                    <div
                      key={uploadedFile.id}
                      className="flex items-center gap-3 p-3 rounded-xl border border-border bg-bg-secondary group hover:border-accent/30 transition-colors"
                    >
                      <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-bg shadow-sm">
                        <DocumentIconRenderer
                          type={getFileIconType(uploadedFile.name)}
                          size={20}
                        />
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-text truncate" title={uploadedFile.name}>
                          {uploadedFile.name}
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <div className="text-xs font-medium" style={{ color: getStatusColor(uploadedFile.status) }}>
                            {getStatusText(uploadedFile.status)}
                          </div>
                          {(uploadedFile.status === 'uploading' || uploadedFile.status === 'indexing') && (
                            <div className="flex flex-col flex-1 max-w-[200px]">
                              <div className="flex items-center gap-2">
                                <div className="flex-1 h-1 bg-gray-200 rounded-full overflow-hidden">
                                  <div
                                    className="h-full bg-accent transition-all duration-300 ease-out"
                                    style={{ width: `${uploadedFile.progress || 0}%` }}
                                  />
                                </div>
                                <span className="text-[10px] text-text-tertiary w-6 text-right">
                                  {Math.round(uploadedFile.progress || 0)}%
                                </span>
                              </div>
                            </div>
                          )}
                        </div>
                        {(uploadedFile.status === 'indexing' && uploadedFile.progressMessage) && (
                          <div className="text-[10px] text-text-tertiary mt-0.5 truncate max-w-[250px]" title={uploadedFile.progressMessage}>
                            {uploadedFile.progressMessage}
                          </div>
                        )}
                        {uploadedFile.errorMessage && (
                          <div className="text-xs mt-1 text-danger">
                            {uploadedFile.errorMessage}
                          </div>
                        )}
                      </div>

                      {
                        uploadedFile.status === 'ready' ? (
                          <div className="flex items-center gap-2">
                            <CheckCircle2 size={18} className="text-green-500" />
                            <button
                              onClick={() => handleDeleteProjectFile(uploadedFile.id)}
                              className="p-1.5 rounded-lg text-text-tertiary hover:bg-bg hover:text-danger transition-colors"
                              title="삭제"
                            >
                              <Trash2 size={16} />
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => handleDeleteProjectFile(uploadedFile.id)}
                            className="p-1.5 rounded-lg text-text-tertiary hover:bg-bg hover:text-danger transition-colors opacity-0 group-hover:opacity-100"
                            title="취소"
                          >
                            <X size={16} />
                          </button>
                        )
                      }
                    </div>
                  ))}

                  {/* 드롭존 (파일이 있어도 작게 표시) */}
                  <div
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    className={`
                      mt-2 rounded-lg border-2 border-dashed transition-all duration-200 flex items-center justify-center py-4 cursor-pointer
                      ${isDragging
                        ? 'border-accent bg-accent/5'
                        : 'border-border bg-bg hover:bg-bg-secondary hover:border-text-tertiary'
                      }
                    `}
                    onClick={handleUploadClick}
                  >
                    <div className="flex items-center gap-2 text-text-secondary text-xs font-medium">
                      <Upload size={14} />
                      <span>추가 파일 업로드</span>
                    </div>
                  </div>
                </div>
              )}
            </section>

            {/* 양식쌍 섹션 Divider */}
            <div className="h-px bg-border/50" />

            {/* 양식쌍 섹션 */}
            <section>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <div className="p-1.5 rounded-md bg-accent/10 text-accent">
                    <File size={16} />
                  </div>
                  <div className="flex flex-col">
                    <h3 className="text-sm font-bold text-text">양식쌍 (템플릿 + 참조)</h3>
                    <span className="text-[10px] text-text-tertiary">빈 양식과 채워진 양식을 쌍으로 업로드하여 학습시킵니다.</span>
                  </div>
                </div>
                <button
                  onClick={handleAddTemplatePair}
                  disabled={hasProcessing}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:opacity-90 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 shadow-sm"
                >
                  <Plus size={14} />
                  양식쌍 추가
                </button>
              </div>

              <div className="space-y-4">
                {templatePairs.map((pair, index) => (
                  <div
                    key={pair.id}
                    className="rounded-xl border border-border bg-bg-secondary p-4 transition-all hover:shadow-sm"
                  >
                    <div className="flex flex-col mb-4 pb-3 border-b border-border/50">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold px-2 py-0.5 rounded bg-bg text-text-secondary border border-border">
                            PAIR #{index + 1}
                          </span>
                          {(pair.status === 'uploading' || pair.status === 'extracting' || pair.status === 'indexing') && (
                            <div className="flex items-center gap-1.5 bg-accent/5 px-2 py-0.5 rounded text-accent">
                              <Loader2 size={10} className="animate-spin" />
                              <span className="text-[10px] font-medium">
                                {getStatusText(pair.status)}
                              </span>
                            </div>
                          )}
                          {pair.status === 'ready' && pair.templatePath && pair.referencePath && (
                            <div className="flex items-center gap-1 text-green-600">
                              <CheckCircle2 size={12} />
                              <span className="text-[10px] font-bold">완료</span>
                            </div>
                          )}
                          {pair.status === 'failed' && (
                            <div className="flex items-center gap-1 text-danger">
                              <AlertCircle size={12} />
                              <span className="text-[10px] font-bold">실패</span>
                            </div>
                          )}
                        </div>

                        <button
                          onClick={() => {
                            if (pair.status === 'ready' && pair.templateName && pair.referenceName) {
                              handleDeleteTemplatePair(pair.id)
                            } else {
                              handleRemoveTemplatePair(pair.id)
                            }
                          }}
                          disabled={pair.status === 'uploading' || pair.status === 'extracting' || pair.status === 'indexing'}
                          className="text-text-tertiary hover:text-danger transition-colors disabled:opacity-30 p-1"
                          title="삭제"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>

                      {/* 양식쌍 프로그레스 바 및 메시지 */}
                      {(pair.status === 'uploading' || pair.status === 'extracting' || pair.status === 'indexing') && (
                        <div className="mt-2 pl-1 pr-1">
                          <div className="flex items-center gap-2 mb-1">
                            <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-accent transition-all duration-300 ease-out"
                                style={{ width: `${pair.progress || 0}%` }}
                              />
                            </div>
                            <span className="text-[10px] text-text-tertiary w-8 text-right">
                              {Math.round(pair.progress || 0)}%
                            </span>
                          </div>
                          {pair.progressMessage && (
                            <div className="text-[10px] text-text-tertiary truncate">
                              {pair.progressMessage}
                            </div>
                          )}
                        </div>
                      )}
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      {/* 템플릿 파일 드롭존 */}
                      <div className="space-y-2">
                        <label className="flex items-center gap-1 text-xs font-medium text-text-secondary">
                          <File size={12} />
                          빈 양식 (Template)
                        </label>
                        {pair.templateName ? (
                          <div className="flex items-center gap-2 p-3 rounded-lg border border-border bg-bg shadow-sm">
                            <DocumentIconRenderer type={getFileIconType(pair.templateName)} size={20} />
                            <span className="text-xs flex-1 min-w-0 truncate font-medium text-text" title={pair.templateName}>
                              {pair.templateName}
                            </span>
                          </div>
                        ) : (
                          <div
                            onDragEnter={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              setTemplateDragging(prev => ({ ...prev, [pair.id]: true }))
                            }}
                            onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
                            onDragLeave={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              setTemplateDragging(prev => ({ ...prev, [pair.id]: false }))
                            }}
                            onDrop={(e) => {
                              handleTemplateDrop(e, pair.id)
                              setTemplateDragging(prev => ({ ...prev, [pair.id]: false }))
                            }}
                            onClick={() => handleTemplateFileSelect(pair.id)}
                            className={`
                              w-full h-[60px] rounded-lg border-2 border-dashed flex flex-col items-center justify-center cursor-pointer transition-all
                              ${templateDragging[pair.id]
                                ? 'border-accent bg-accent/5'
                                : 'border-border bg-transparent hover:bg-bg hover:border-text-secondary'
                              }
                            `}
                          >
                            <span className={`text-[10px] ${templateDragging[pair.id] ? 'text-accent' : 'text-text-tertiary'}`}>
                              파일 선택 또는 드래그
                            </span>
                          </div>
                        )}
                      </div>

                      {/* 참조 파일 드롭존 */}
                      <div className="space-y-2">
                        <label className="flex items-center gap-1 text-xs font-medium text-text-secondary">
                          <FilePlus size={12} />
                          참조 파일 (Filled)
                        </label>
                        {pair.referenceName ? (
                          <div className="flex items-center gap-2 p-3 rounded-lg border border-border bg-bg shadow-sm">
                            <DocumentIconRenderer type={getFileIconType(pair.referenceName)} size={20} />
                            <span className="text-xs flex-1 min-w-0 truncate font-medium text-text" title={pair.referenceName}>
                              {pair.referenceName}
                            </span>
                          </div>
                        ) : (
                          <div
                            onDragEnter={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              setReferenceDragging(prev => ({ ...prev, [pair.id]: true }))
                            }}
                            onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
                            onDragLeave={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              setReferenceDragging(prev => ({ ...prev, [pair.id]: false }))
                            }}
                            onDrop={(e) => {
                              handleReferenceDrop(e, pair.id)
                              setReferenceDragging(prev => ({ ...prev, [pair.id]: false }))
                            }}
                            onClick={() => handleReferenceFileSelect(pair.id)}
                            className={`
                              w-full h-[60px] rounded-lg border-2 border-dashed flex flex-col items-center justify-center cursor-pointer transition-all
                              ${referenceDragging[pair.id]
                                ? 'border-accent bg-accent/5'
                                : 'border-border bg-transparent hover:bg-bg hover:border-text-secondary'
                              }
                            `}
                          >
                            <span className={`text-[10px] ${referenceDragging[pair.id] ? 'text-accent' : 'text-text-tertiary'}`}>
                              파일 선택 또는 드래그
                            </span>
                          </div>
                        )}
                      </div>
                    </div>

                    {pair.errorMessage && (
                      <div className="mt-2 p-2 rounded bg-danger/5 border border-danger/10 text-[10px] text-danger">
                        {pair.errorMessage}
                      </div>
                    )}
                  </div>
                ))}

                {templatePairs.length === 0 && (
                  <div className="text-center py-8 border border-dashed border-border rounded-xl bg-bg-secondary/50">
                    <p className="text-xs text-text-tertiary">양식쌍이 없습니다. '양식쌍 추가' 버튼을 눌러주세요.</p>
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div >
    </div >
  )
}

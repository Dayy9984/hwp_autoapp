import { useState, useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useFolderStore } from '../../stores/folder-store'
import { Pencil, Trash2, AlertTriangle } from 'lucide-react'

export function FolderOptionsPopover() {
  const { activePopover, popoverData, closePopover } = useUIStore()
  const { updateFolder, deleteFolder, getFolder } = useFolderStore()
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  const isOpen = activePopover === 'folder-options'
  const folderId = popoverData?.folderId as string | undefined

  const folder = getFolder(folderId || '')

  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [isRenaming])

  useEffect(() => {
    if (isOpen && folder) {
      setRenameValue(folder.name)
    }
  }, [isOpen, folder])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        closePopover()
        setIsRenaming(false)
        setShowDeleteConfirm(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (isRenaming) {
          setIsRenaming(false)
          setRenameValue(folder?.name || '')
        } else if (showDeleteConfirm) {
          setShowDeleteConfirm(false)
        } else {
          closePopover()
        }
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
      return () => {
        document.removeEventListener('mousedown', handleClickOutside)
        document.removeEventListener('keydown', handleEscape)
      }
    }
  }, [isOpen, closePopover, isRenaming, showDeleteConfirm, folder?.name])

  const handleRename = async () => {
    if (!folderId || !renameValue.trim()) {
      setIsRenaming(false)
      setRenameValue(folder?.name || '')
      return
    }

    // IPC를 통해 프로젝트 이름 변경 저장 (먼저 백엔드에서 저장)
    try {
      await window.electronAPI.invoke('project:rename', {
        projectId: folderId,
        name: renameValue.trim()
      })

      // 백엔드 저장 성공 시에만 Frontend store 업데이트
      updateFolder(folderId, renameValue.trim())
    } catch (err) {
      console.error('Failed to rename project:', err)
      alert('프로젝트 이름 변경에 실패했습니다.')
      setRenameValue(folder?.name || '')
      setIsRenaming(false)
      return
    }

    setIsRenaming(false)
    closePopover()
  }

  const handleDelete = async () => {
    if (!folderId) return

    // IPC를 통해 프로젝트 삭제 (먼저 백엔드에서 삭제)
    try {
      await window.electronAPI.invoke('project:delete', folderId)

      // 백엔드 삭제 성공 시에만 Frontend store 업데이트
      deleteFolder(folderId)
    } catch (err) {
      console.error('Failed to delete project:', err)
      alert('프로젝트 삭제에 실패했습니다.')
      setShowDeleteConfirm(false)
      return
    }

    closePopover()
    setShowDeleteConfirm(false)
  }

  if (!isOpen || !folderId || !folder) return null

  const position = popoverData?.position as { top: number; left: number } | undefined

  return (
    <div
      ref={popoverRef}
      className="bg-bg rounded-xl shadow-xl border border-border min-w-[220px] overflow-hidden animate-scale-up z-50 fixed"
      style={{
        top: position?.top || 0,
        left: position?.left || 0,
        backgroundColor: 'var(--bg)',
      }}
    >
      {showDeleteConfirm ? (
        <div className="p-3 w-64">
          <div className="mb-3">
            <div className="flex items-center gap-2 mb-2 text-danger">
              <AlertTriangle size={18} />
              <h3 className="font-semibold text-sm text-text">프로젝트 삭제</h3>
            </div>
            <p className="text-xs text-text-secondary leading-relaxed mb-2">
              이 프로젝트를 삭제하시겠습니까? 프로젝트 내 모든 파일, 양식쌍 데이터가 삭제됩니다.
            </p>
            <p className="text-[10px] text-danger font-medium mt-1">
              ⚠️ 이 작업은 되돌릴 수 없습니다.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowDeleteConfirm(false)}
              className="flex-1 px-3 py-2 rounded-lg text-sm text-text font-medium bg-bg-secondary hover:bg-bg-tertiary transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleDelete}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium text-white bg-danger hover:bg-danger-dark transition-colors"
            >
              삭제
            </button>
          </div>
        </div>
      ) : isRenaming ? (
        <div className="p-3 w-64">
          <div className="relative">
            <input
              ref={renameInputRef}
              type="text"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRename()
              }}
              className="w-full pl-3 pr-8 py-2 bg-bg-secondary rounded-lg text-sm text-text border border-transparent focus:bg-bg focus:border-accent focus:ring-1 focus:ring-accent outline-none"
              placeholder="프로젝트 이름"
            />
            <div className="absolute right-2 top-2 text-text-tertiary">
              <Pencil size={14} />
            </div>
          </div>
          <div className="flex gap-2 mt-2">
            <button
              onClick={() => {
                setIsRenaming(false)
                setRenameValue(folder.name)
              }}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-medium text-text-tertiary hover:bg-bg-secondary transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleRename}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-accent hover:bg-accent-dark transition-colors"
            >
              저장
            </button>
          </div>
        </div>
      ) : (
        <div className="p-1">
          <button
            onClick={() => setIsRenaming(true)}
            className="w-full px-3 py-2 rounded-lg flex items-center gap-2.5 text-sm text-text hover:bg-bg-secondary transition-colors text-left"
          >
            <Pencil size={16} className="text-text-secondary" />
            <span className="font-medium">이름 바꾸기</span>
          </button>

          <div className="my-1 border-t border-border/50" />

          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="w-full px-3 py-2 rounded-lg flex items-center gap-2.5 text-sm text-danger hover:bg-danger/10 transition-colors text-left"
          >
            <Trash2 size={16} />
            <span className="font-medium">삭제</span>
          </button>
        </div>
      )}
    </div>
  )
}

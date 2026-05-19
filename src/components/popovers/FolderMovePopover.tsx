import { useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useFolderStore } from '../../stores/folder-store'
import { Plus, Folder } from 'lucide-react'

export function FolderMovePopover() {
  const { activePopover, popoverData, closePopover, openModal } = useUIStore()
  const { folders, moveChatToFolder } = useFolderStore()
  const popoverRef = useRef<HTMLDivElement>(null)

  const isOpen = activePopover === 'folder-move'
  const chatId = popoverData?.chatId as string | undefined

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        closePopover()
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closePopover()
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
  }, [isOpen, closePopover])

  const handleCreateNewFolder = () => {
    closePopover()
    openModal('create-folder', { chatId })
  }

  const handleMoveToFolder = (folderId: string) => {
    if (!chatId) return

    moveChatToFolder(chatId, folderId)
    closePopover()
  }

  if (!isOpen || !chatId) return null

  const position = popoverData?.position as { top: number; left: number } | undefined

  return (
    <div
      ref={popoverRef}
      className="fixed z-[70] rounded-xl shadow-2xl border min-w-[240px] overflow-hidden animate-scale-up"
      style={{
        backgroundColor: 'var(--bg)',
        borderColor: 'var(--border)',
        top: position?.top || 0,
        left: position?.left || 0,
      }}
    >
      <div className="p-4">
        <h3
          className="font-semibold text-sm mb-3"
          style={{ color: 'var(--text)' }}
        >
          프로젝트로 이동
        </h3>

        <button
          onClick={handleCreateNewFolder}
          className="w-full px-3 py-2.5 flex items-center gap-3 rounded-lg transition-all duration-150 text-left mb-2"
          style={{
            color: 'var(--text)',
            backgroundColor: 'var(--bg-secondary)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-secondary)'
          }}
        >
          <Plus
            className="w-4 h-4 flex-shrink-0"
            style={{ color: 'var(--accent)' }}
          />
          <span className="text-sm font-medium">새 프로젝트 생성</span>
        </button>

        <div
          className="my-2 border-t"
          style={{ borderColor: 'var(--border)' }}
        />

        <div className="space-y-1 max-h-[200px] overflow-y-auto thin-scrollbar">
          {folders.length === 0 ? (
            <div
              className="px-3 py-4 text-center text-sm"
              style={{ color: 'var(--text-secondary)' }}
            >
              생성된 프로젝트가 없습니다
            </div>
          ) : (
            folders.map((folder) => (
              <button
                key={folder.id}
                onClick={() => handleMoveToFolder(folder.id)}
                className="w-full px-3 py-2.5 flex items-center gap-3 rounded-lg transition-all duration-150 text-left"
                style={{
                  color: 'var(--text)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = 'var(--bg-secondary)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent'
                }}
              >
                <Folder
                  className="w-4 h-4 flex-shrink-0"
                  style={{ color: 'var(--text-secondary)' }}
                />
                <span className="text-sm font-medium">{folder.name}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

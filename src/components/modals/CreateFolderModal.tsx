import { useState, useEffect, useRef, KeyboardEvent } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useFolderStore } from '../../stores/folder-store'
import { X } from 'lucide-react'

export function CreateFolderModal() {
  const { activeModal, modalData, closeModal, setSelectedFolder, navigateToView } = useUIStore()
  const { createFolder, moveChatToFolder } = useFolderStore()
  const [folderName, setFolderName] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const isOpen = activeModal === 'create-folder'
  const chatId = modalData?.chatId as string | undefined

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isOpen])

  const handleCreateFolder = async () => {
    const trimmedName = folderName.trim()
    if (!trimmedName) {
      return
    }

    try {
      const folderId = await createFolder(trimmedName)
      if (!folderId) {
        alert('프로젝트 생성에 실패했습니다.')
        return
      }

      // If chatId is provided (from FolderMovePopover), move the chat to the new folder
      if (chatId) {
        moveChatToFolder(chatId, folderId)
      }

      // Navigate to the newly created folder
      setSelectedFolder(folderId)
      navigateToView('folder')

      setFolderName('')
      closeModal()
    } catch (err) {
      console.error('Failed to create project:', err)
      alert('프로젝트 생성에 실패했습니다.')
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      closeModal()
      setFolderName('')
    } else if (e.key === 'Enter') {
      e.preventDefault()
      handleCreateFolder()
    }
  }

  const handleCancel = () => {
    setFolderName('')
    closeModal()
  }

  if (!isOpen) return null

  return (
    <>
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={handleCancel}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="w-full max-w-sm rounded-2xl shadow-2xl border border-border overflow-hidden animate-scale-up"
          style={{ backgroundColor: 'var(--bg)' }}
        >
          <div className="flex items-center justify-between p-4 border-b border-border">
            <h2 className="text-lg font-serif font-medium text-text">새 프로젝트</h2>
            <button
              onClick={handleCancel}
              className="p-1 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
            >
              <X size={20} />
            </button>
          </div>

          <div className="p-5">
            <input
              ref={inputRef}
              type="text"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="프로젝트 이름을 입력하세요"
              className="w-full px-4 py-3 bg-bg-secondary rounded-xl text-text placeholder:text-text-tertiary border border-transparent focus:bg-bg focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all"
            />

            <div className="flex gap-3 mt-6">
              <button
                onClick={handleCancel}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-text-secondary bg-bg-tertiary hover:bg-bg-secondary transition-colors"
              >
                취소
              </button>
              <button
                onClick={handleCreateFolder}
                disabled={!folderName.trim()}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-white bg-accent hover:bg-accent-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
              >
                만들기
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

import { useState, useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useChatStore } from '../../stores/chat-store'
import {
  Pencil,
  Trash2,
  FolderInput,
  AlertTriangle,
} from 'lucide-react'

export function ChatOptionsPopover() {
  const { activePopover, popoverData, closePopover, openPopover } = useUIStore()
  const { updateChatName, deleteChat, chats } = useChatStore()
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  // 이전 로직 복원: folder-move가 열려있어도 chat-options는 유지
  const isOpen = activePopover === 'chat-options' || activePopover === 'folder-move'
  const chatId = popoverData?.chatId as string | undefined

  const chat = chats.find(c => c.id === chatId)

  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [isRenaming])

  useEffect(() => {
    if (isOpen && chat) {
      setRenameValue(chat.name)
    }
  }, [isOpen, chat])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      // folder-move popover가 열려있으면 닫지 않음 (서브 팝오버)
      if (activePopover === 'folder-move') {
        return
      }

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
          setRenameValue(chat?.name || '')
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
  }, [isOpen, activePopover, closePopover, isRenaming, showDeleteConfirm, chat?.name])

  const handleRename = () => {
    if (!chatId || !renameValue.trim()) {
      setIsRenaming(false)
      setRenameValue(chat?.name || '')
      return
    }

    updateChatName(chatId, renameValue.trim())
    setIsRenaming(false)
    closePopover()
  }

  const handleDelete = () => {
    if (!chatId) return

    // 채팅 삭제
    deleteChat(chatId)
    closePopover()
    setShowDeleteConfirm(false)
  }

  // 이전 로직 복원: openPopover로 folder-move 열기
  const handleMoveToFolder = (e: React.MouseEvent<HTMLButtonElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    openPopover('folder-move', {
      chatId,
      position: {
        top: rect.top,
        left: rect.right + 5,
      },
    })
  }

  if (!isOpen || !chatId || !chat) return null

  const position = popoverData?.position as { top: number; left: number } | undefined

  return (
    <div
      ref={popoverRef}
      className="bg-bg rounded-2xl shadow-2xl border border-border min-w-[200px] overflow-hidden animate-scale-up z-[60] fixed py-1.5"
      style={{
        top: position?.top || 0,
        left: position?.left || 0,
        backgroundColor: 'var(--bg)',
      }}
    >
      {showDeleteConfirm ? (
        <div className="p-4 w-64">
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-2 text-danger">
              <AlertTriangle size={18} strokeWidth={2.5} />
              <h3 className="font-bold text-sm text-text">채팅 삭제</h3>
            </div>
            <p className="text-xs text-text-secondary leading-relaxed">
              정말로 이 채팅을 삭제하시겠습니까? <br />이 작업은 되돌릴 수 없습니다.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowDeleteConfirm(false)}
              className="flex-1 px-3 py-2 rounded-lg text-sm text-text font-semibold bg-bg-secondary hover:bg-bg-tertiary transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleDelete}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-semibold text-white bg-danger hover:bg-danger-dark transition-colors shadow-sm"
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
              className="w-full pl-3 pr-8 py-2.5 bg-bg-secondary rounded-lg text-sm font-medium text-text border border-transparent focus:bg-bg focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all placeholder:text-text-tertiary"
              placeholder="채팅 이름"
              autoFocus
            />
            <div className="absolute right-2.5 top-2.5 text-text-tertiary">
              <Pencil size={15} />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={() => {
                setIsRenaming(false)
                setRenameValue(chat.name)
              }}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-text-tertiary hover:bg-bg-secondary hover:text-text transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleRename}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-accent hover:bg-accent-dark transition-colors shadow-sm"
            >
              저장
            </button>
          </div>
        </div>
      ) : (
        <div className="w-48 px-1">
          <button
            onClick={() => setIsRenaming(true)}
            className="w-full px-3 py-2.5 rounded-lg flex items-center gap-3 text-sm text-text-secondary hover:text-text hover:bg-bg-secondary transition-colors text-left font-medium"
          >
            <Pencil size={16} />
            <span>이름 바꾸기</span>
          </button>

          <button
            onClick={handleMoveToFolder}
            className="w-full px-3 py-2.5 rounded-lg flex items-center gap-3 text-sm text-text-secondary hover:text-text hover:bg-bg-secondary transition-colors text-left font-medium"
          >
            <FolderInput size={16} />
            <span>프로젝트 이동</span>
          </button>

          <div className="my-1.5 mx-2 border-t border-border/40" />

          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="w-full px-3 py-2.5 rounded-lg flex items-center gap-3 text-sm text-danger hover:bg-danger/5 transition-colors text-left font-medium"
          >
            <Trash2 size={16} />
            <span>삭제</span>
          </button>
        </div>
      )}
    </div>
  )
}

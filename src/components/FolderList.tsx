import { useEffect, useState, type DragEvent, type MouseEvent } from 'react'
import { Folder as FolderIcon, FolderOpen } from 'lucide-react'
import { useChatStore } from '../stores/chat-store'
import { useUIStore } from '../stores/ui-store'
import { Folder, useFolderStore } from '../stores/folder-store'
import { useFlipList } from '../hooks/useFlipList'
import { clearChatDragData, getChatDragData, hasChatDragData, setChatDragData } from '../utils/chat-dnd'

interface FolderListProps {
  folder: Folder
  level: number
  expandedFolders: string[]
  toggleFolder: (folderId: string) => void
  onContextMenu: (e: MouseEvent, type: 'folder' | 'file', id: string) => void
}

export function FolderList({
  folder,
  level,
  expandedFolders,
  toggleFolder,
  onContextMenu
}: FolderListProps) {
  const { chats, selectChat, currentChatId } = useChatStore()
  const { navigateToView, setSelectedFolder, theme } = useUIStore()
  const { moveChatToFolder, reorderChatInFolder } = useFolderStore()
  const [draggingChatId, setDraggingChatId] = useState<string | null>(null)
  const [dragOverChat, setDragOverChat] = useState<{ chatId: string; position: 'before' | 'after' } | null>(null)

  const chatById = new Map(chats.map((chat) => [chat.id, chat]))

  // 프로젝트 내부 채팅은 최근 사용순(updatedAt 내림차순)으로 표시
  const orderedFiles = [...folder.files].sort((a, b) => {
    const chatA = chatById.get(a.chatId)
    const chatB = chatById.get(b.chatId)
    const updatedA = chatA?.updatedAt ?? 0
    const updatedB = chatB?.updatedAt ?? 0
    if (updatedB !== updatedA) return updatedB - updatedA

    const createdA = chatA?.createdAt ?? 0
    const createdB = chatB?.createdAt ?? 0
    if (createdB !== createdA) return createdB - createdA

    return (b.addedAt ?? 0) - (a.addedAt ?? 0)
  })
  const setChatRef = useFlipList(orderedFiles.map(file => file.chatId))

  useEffect(() => {
    const handleDragEnd = () => {
      setDragOverChat(null)
      clearChatDragData()
    }
    window.addEventListener('dragend', handleDragEnd)
    return () => window.removeEventListener('dragend', handleDragEnd)
  }, [])

  const isExpanded = expandedFolders.includes(folder.id)
  const hasChats = folder.files.length > 0
  const baseIndent = 12 + level * 12
  const childIndent = 28 + level * 12

  const getChatName = (chatId: string): string => {
    const chat = chatById.get(chatId)
    return chat?.name ?? 'Unknown Chat'
  }

  const handleFolderClick = () => {
    setSelectedFolder(folder.id)
    navigateToView('folder')
  }

  const handleFolderToggle = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    toggleFolder(folder.id)
  }

  const handleFolderContextMenu = (e: MouseEvent) => {
    e.preventDefault()
    onContextMenu(e, 'folder', folder.id)
  }

  const handleFileContextMenu = (e: MouseEvent, fileId: string) => {
    e.preventDefault()
    onContextMenu(e, 'file', fileId)
  }

  const resolveDropPosition = (e: DragEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const midpoint = rect.top + rect.height / 2
    return e.clientY < midpoint ? 'before' : 'after'
  }

  const handleFolderDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!hasChatDragData(e)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  const handleFolderDrop = (e: DragEvent<HTMLDivElement>) => {
    const data = getChatDragData(e)
    if (!data?.chatId) return
    e.preventDefault()
    if (!chats.some((chat) => chat.id === data.chatId)) return
    if (folder.files.some(file => file.chatId === data.chatId)) return
    moveChatToFolder(data.chatId, folder.id)
    setDragOverChat(null)
    clearChatDragData()
  }

  const handleChatDragStart = (e: DragEvent<HTMLDivElement>, chatId: string) => {
    setChatDragData(e, { chatId, sourceFolderId: folder.id })
    setDraggingChatId(chatId)
  }

  const handleChatDragEnd = () => {
    setDraggingChatId(null)
    setDragOverChat(null)
    clearChatDragData()
  }

  const handleChatDragOver = (e: DragEvent<HTMLDivElement>, targetChatId: string) => {
    if (!hasChatDragData(e)) return
    e.preventDefault()
    e.stopPropagation()
    const data = getChatDragData(e)
    if (!data?.chatId) return
    if (!chats.some((chat) => chat.id === data.chatId)) return
    if (data.chatId === targetChatId) return

    const position = resolveDropPosition(e)
    setDragOverChat({ chatId: targetChatId, position })

    const isInFolder = folder.files.some(file => file.chatId === data.chatId)
    if (!isInFolder) return
    reorderChatInFolder(folder.id, data.chatId, targetChatId, position, false)
  }

  const handleChatDrop = (e: DragEvent<HTMLDivElement>, targetChatId: string) => {
    const data = getChatDragData(e)
    if (!data?.chatId) return
    e.preventDefault()
    e.stopPropagation()
    if (!chats.some((chat) => chat.id === data.chatId)) return
    if (data.chatId === targetChatId) return
    const position = resolveDropPosition(e)

    const isInFolder = folder.files.some(file => file.chatId === data.chatId)
    if (!isInFolder) {
      moveChatToFolder(data.chatId, folder.id)
    }
    reorderChatInFolder(folder.id, data.chatId, targetChatId, position, true)
    setDragOverChat(null)
    clearChatDragData()
  }

  return (
    <div onDragOver={handleFolderDragOver} onDrop={handleFolderDrop}>
      {/* Folder Item */}
      <div
        className="group flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all duration-200"
        style={{
          color: 'var(--text-secondary)',
          backgroundColor: 'transparent',
          paddingLeft: `${baseIndent}px`
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = 'var(--sidebar-hover)'
          e.currentTarget.style.color = 'var(--sidebar-text-hover)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = 'transparent'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
        onClick={handleFolderClick}
        onContextMenu={handleFolderContextMenu}
      >
        <div className="flex items-center gap-3 overflow-hidden flex-1">
          <button
            onClick={handleFolderToggle}
            className="flex-shrink-0 p-0 hover:bg-transparent"
            title={isExpanded ? '접기' : '펼치기'}
          >
            {isExpanded ? (
              <FolderOpen className="w-4 h-4" style={{ color: 'var(--icon-primary)' }} />
            ) : (
              <FolderIcon className="w-4 h-4" style={{ color: 'var(--icon-primary)' }} />
            )}
          </button>
          <span className="text-sm font-medium truncate">
            {folder.name}
          </span>
        </div>
        <button
          onClick={(e) => handleFolderContextMenu(e)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded transition-all flex-shrink-0"
          style={{ color: 'var(--icon-primary)' }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--sidebar-hover)'
            e.currentTarget.style.color = 'var(--icon-hover)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent'
            e.currentTarget.style.color = 'var(--icon-primary)'
          }}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z"
            />
          </svg>
        </button>
      </div>

      {/* Folder Chats (Expanded) */}
      {isExpanded && hasChats && (
        <div className="mt-1 space-y-1" style={{ marginLeft: `${childIndent}px` }}>
          {orderedFiles.map(file => {
            const isDropTarget = dragOverChat?.chatId === file.chatId
            const dropPosition = dragOverChat?.position
            const dropGradient = isDropTarget
              ? 'linear-gradient(var(--accent), var(--accent))'
              : undefined
            const dropPositionCss = dropPosition === 'before' ? '0 0' : '0 100%'
            return (
              <div
                key={file.id}
                onClick={() => {
                  selectChat(file.chatId)
                  navigateToView('chat')
                }}
                draggable
                onDragStart={(e) => handleChatDragStart(e, file.chatId)}
                onDragEnd={handleChatDragEnd}
                onDragOver={(e) => handleChatDragOver(e, file.chatId)}
                onDrop={(e) => handleChatDrop(e, file.chatId)}
                ref={setChatRef(file.chatId)}
                className={`group flex items-center justify-between p-2.5 pl-4 rounded-lg cursor-pointer transition-all duration-200
                  ${currentChatId === file.chatId
                    ? 'text-text font-medium shadow-sm'
                    : 'text-text-secondary hover:bg-bg-secondary hover:text-text'}
                  ${draggingChatId === file.chatId ? 'opacity-50' : ''}
                `}
                style={{
                  backgroundColor: currentChatId === file.chatId
                    ? (theme === 'dark' ? 'rgba(255,255,255,0.1)' : '#E4E4E7')
                    : 'transparent',
                  backgroundImage: dropGradient,
                  backgroundRepeat: dropGradient ? 'no-repeat' : undefined,
                  backgroundSize: dropGradient ? '100% 2px' : undefined,
                  backgroundPosition: dropGradient ? dropPositionCss : undefined
                }}
                onContextMenu={(e) => handleFileContextMenu(e, file.id)}
              >
                <span className="text-sm truncate">{getChatName(file.chatId)}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  handleFileContextMenu(e, file.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 rounded transition-all flex-shrink-0"
                style={{ color: 'var(--icon-primary)' }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = 'var(--sidebar-hover)'
                  e.currentTarget.style.color = 'var(--icon-hover)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent'
                  e.currentTarget.style.color = 'var(--icon-primary)'
                }}
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z"
                  />
                </svg>
              </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

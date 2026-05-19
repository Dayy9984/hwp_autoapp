import { useEffect, useState, type DragEvent } from 'react'
import logo from '@/components/icons/logo.png'
import { useChatStore } from '../stores/chat-store'
import { useUIStore } from '../stores/ui-store'
import { useFolderStore } from '../stores/folder-store'
import { useFlipList } from '../hooks/useFlipList'
import { clearChatDragData, getChatDragData, hasChatDragData, setChatDragData } from '../utils/chat-dnd'
import { FolderList } from './FolderList'
import { ToolsTab } from './ToolsTab'
import {
  PanelLeftClose,
  PanelLeftOpen,
  SquarePen,
  Search,
  MoreVertical, // Modified: Changed from MoreHorizontal to MoreVertical
  Settings,
  Plus
} from 'lucide-react'

type TabType = 'chat' | 'tools'

export function Sidebar() {
  const { chats, currentChatId, createChat, selectChat, reorderChat } = useChatStore()
  const { theme, openModal, openPopover, sidebarCollapsed, toggleSidebar, navigateToView, expandedFolders, toggleFolderExpanded, currentView, selectedFolderId } = useUIStore()
  const { folders, getChatFolder, removeFileFromFolder } = useFolderStore()
  const [activeTab, setActiveTab] = useState<TabType>('chat')
  const [draggingChatId, setDraggingChatId] = useState<string | null>(null)
  const [dragOverChat, setDragOverChat] = useState<{ chatId: string; position: 'before' | 'after' } | null>(null)
  // 정렬: updatedAt 우선 (최근 활동 채팅이 위로 자동 이동)
  // 같은 updatedAt이면 chats 배열 순서(=chat-store가 DnD 결과 storage로 유지하는 순서) 유지
  // → DnD 직후엔 그 순서, 새 메시지 오면 그 채팅이 자동으로 위로
  const unassignedChats = chats
    .filter(chat => !getChatFolder(chat.id))
    .slice()
    .sort((a, b) => b.updatedAt - a.updatedAt)
  const sortedFolders = [...folders].sort((a, b) => {
    if (b.createdAt !== a.createdAt) return b.createdAt - a.createdAt
    return b.updatedAt - a.updatedAt
  })
  const setChatRef = useFlipList(unassignedChats.map(chat => chat.id))

  useEffect(() => {
    const handleDragEnd = () => {
      setDragOverChat(null)
      clearChatDragData()
    }
    window.addEventListener('dragend', handleDragEnd)
    return () => window.removeEventListener('dragend', handleDragEnd)
  }, [])

  const handleNewChat = async () => {
    // 프로젝트 화면에서 새 채팅 생성 시, 현재 선택된 프로젝트에 바인딩
    const projectId = currentView === 'folder' ? selectedFolderId : undefined
    await createChat(projectId ?? undefined)

    // 채팅 뷰로 전환
    navigateToView('chat')
  }

  // Use isCollapsed as alias for sidebarCollapsed for consistency with existing code
  const isCollapsed = sidebarCollapsed

  const resolveDropPosition = (e: DragEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const midpoint = rect.top + rect.height / 2
    return e.clientY < midpoint ? 'before' : 'after'
  }

  const handleChatDragStart = (e: DragEvent<HTMLElement>, chatId: string) => {
    setChatDragData(e, { chatId, sourceFolderId: null })
    setDraggingChatId(chatId)
  }

  const handleChatDragEnd = () => {
    setDraggingChatId(null)
    setDragOverChat(null)
    clearChatDragData()
  }

  const handleUnassignedChatDragOver = (e: DragEvent<HTMLElement>, targetChatId: string) => {
    if (!hasChatDragData(e)) return
    e.preventDefault()
    e.stopPropagation()
    const data = getChatDragData(e)
    if (!data?.chatId) return
    if (!chats.some((chat) => chat.id === data.chatId)) return
    if (data.chatId === targetChatId) return

    const position = resolveDropPosition(e)
    setDragOverChat({ chatId: targetChatId, position })

    if (getChatFolder(data.chatId)) return
    reorderChat(data.chatId, targetChatId, position, false)
  }

  const handleUnassignedChatDrop = (e: DragEvent<HTMLElement>, targetChatId: string) => {
    const data = getChatDragData(e)
    if (!data?.chatId) return
    e.preventDefault()
    e.stopPropagation()
    if (!chats.some((chat) => chat.id === data.chatId)) return
    if (data.chatId === targetChatId) return
    const position = resolveDropPosition(e)

    const sourceFolder = data.sourceFolderId ?? getChatFolder(data.chatId)?.id
    if (sourceFolder) {
      removeFileFromFolder(sourceFolder, data.chatId)
    }
    reorderChat(data.chatId, targetChatId, position, true)
    setDragOverChat(null)
    clearChatDragData()
  }

  const handleGeneralDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!hasChatDragData(e)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  const handleGeneralDrop = (e: DragEvent<HTMLDivElement>) => {
    const data = getChatDragData(e)
    if (!data?.chatId) return
    e.preventDefault()
    if (!chats.some((chat) => chat.id === data.chatId)) return
    const folder = data.sourceFolderId ? { id: data.sourceFolderId } : getChatFolder(data.chatId)
    if (!folder) return
    removeFileFromFolder(folder.id, data.chatId)
    if (unassignedChats.length > 0) {
      const lastId = unassignedChats[unassignedChats.length - 1].id
      if (lastId && data.chatId !== lastId) {
        reorderChat(data.chatId, lastId, 'after', true)
      }
    }
    setDragOverChat(null)
    clearChatDragData()
  }

  return (
    <div
      className={`${isCollapsed ? 'w-16' : 'w-[280px]'} h-full bg-sidebar-bg text-sidebar-text flex flex-col transition-all duration-300 border-r`}
      style={{ borderColor: 'var(--border)' }}
    >
      {/* 1. Header (Logo & Collapse) */}
      <div className="flex-shrink-0 p-4 pl-5 flex items-center justify-between h-14">
        {!isCollapsed && (
          <div className="flex items-center gap-2">
            {/* Logo Icon */}
            <div className="w-8 h-8 flex items-center justify-center flex-shrink-0 overflow-hidden">
              <img src={logo} alt="Inserty" className="w-full h-full object-contain scale-150" />
            </div>
            <h1 className="text-lg font-serif text-text tracking-tight font-medium" style={{ fontFamily: 'Georgia, serif' }}>
              Inserty
            </h1>
          </div>
        )}
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded-md hover:bg-sidebar-hover text-icon-primary hover:text-text transition-colors"
          title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {isCollapsed ? <PanelLeftOpen size={20} strokeWidth={1.5} /> : <PanelLeftClose size={20} strokeWidth={1.5} />}
        </button>
      </div>

      {/* 2. Tab Toggle */}
      {!isCollapsed && (
        <div className="flex-shrink-0 px-3 pb-2">
          {/* dark mode background to transparent */}
          <div
            className="flex p-1 rounded-lg border border-transparent"
            style={{
              backgroundColor: theme === 'dark' ? 'transparent' : '#E4E4E7'
            }}
          >
            <button
              onClick={() => {
                setActiveTab('chat')
                navigateToView('chat')
              }}
              className={`flex-1 py-1 text-xs font-medium rounded-md transition-all ${activeTab === 'chat'
                ? 'text-text shadow-sm'
                : 'text-text-tertiary hover:text-text-secondary'
                }`}
              style={{ backgroundColor: activeTab === 'chat' ? 'var(--tab-active-bg)' : 'transparent' }}
            >
              채팅
            </button>
            <button
              onClick={() => setActiveTab('tools')}
              className={`flex-1 py-1 text-xs font-medium rounded-md transition-all ${activeTab === 'tools'
                ? 'text-text shadow-sm'
                : 'text-text-tertiary hover:text-text-secondary'
                }`}
              style={{ backgroundColor: activeTab === 'tools' ? 'var(--tab-active-bg)' : 'transparent' }}
            >
              도구
            </button>
          </div>
        </div>
      )}

      {/* 3. Content Area */}
      <div className="flex-1 overflow-y-auto thin-scrollbar">
        {activeTab === 'chat' ? (
          <div className="flex flex-col h-full">
            {/* New Chat & Search */}
            <div className="px-3 pb-4 pt-2 space-y-3">
              <button
                onClick={handleNewChat}
                className={`
                    w-full group py-2.5 px-3
                    rounded-lg text-sm font-semibold transition-all shadow-sm flex items-center justify-center gap-2
                    ${isCollapsed ? 'px-0 py-2 bg-transparent hover:bg-bg-tertiary' : 'bg-accent text-white hover:opacity-90'}
                  `}
                title={isCollapsed ? '새 채팅' : undefined}
                style={{
                  color: isCollapsed ? 'var(--text)' : 'white'
                }}
              >
                <SquarePen size={18} strokeWidth={2.5} className={isCollapsed ? "text-accent" : "text-white"} />
                {!isCollapsed && <span>새 채팅</span>}
              </button>

              {!isCollapsed && (
                <div className="relative group">
                  <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none transition-colors duration-200">
                    <Search size={16} className="text-text-tertiary group-hover:text-accent/70" />
                  </div>
                  <input
                    type="text"
                    placeholder="검색..."
                    readOnly
                    className="w-full pl-10 pr-4 py-2.5 rounded-xl text-sm transition-all duration-200 outline-none border"
                    style={{
                      backgroundColor: 'var(--bg)',
                      borderColor: 'var(--border)',
                      color: 'var(--text)',
                    }}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      openModal('chat-search')
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = 'var(--accent)'
                      e.currentTarget.style.boxShadow = '0 0 0 2px var(--accent-light)'
                      openModal('chat-search')
                      e.currentTarget.blur()
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = 'var(--border)'
                      e.currentTarget.style.boxShadow = 'none'
                    }}
                  />
                </div>
              )}
            </div>

            {/* Folder List */}
            {!isCollapsed && (
              <div className="flex-1 px-3 pb-4">
                <div className="flex items-center justify-between px-2 mb-2 group">
                  <span className="text-xs font-semibold text-text-tertiary">프로젝트</span>
                  <button
                    onClick={() => openModal('create-folder')}
                    className="p-1 rounded hover:bg-bg-tertiary text-text-tertiary hover:text-text transition-colors opacity-0 group-hover:opacity-100"
                    title="새 프로젝트"
                  >
                    <Plus size={14} />
                  </button>
                </div>

                <div className="space-y-0.5">
                  {sortedFolders.map(folder => (
                    <FolderList
                      key={folder.id}
                      folder={folder}
                      level={0}
                      expandedFolders={expandedFolders}
                      toggleFolder={toggleFolderExpanded}
                      onContextMenu={(e, type, id) => {
                        if (type === 'folder') {
                          openPopover('folder-options', {
                            folderId: id,
                            position: { top: e.clientY, left: e.clientX }
                          })
                          return
                        }

                        openPopover('chat-options', {
                          chatId: id,
                          position: { top: e.clientY, left: e.clientX }
                        })
                      }}
                    />
                  ))}
                </div>

                {/* 내 채팅 (Uncategorized Chats) */}
                <div className="mt-6" onDragOver={handleGeneralDragOver} onDrop={handleGeneralDrop}>
                  <div className="px-2 mb-2">
                    <span className="text-xs font-semibold text-text-tertiary">내 채팅</span>
                  </div>
                  <div
                    className="space-y-0.5"
                  >
                    {unassignedChats.map((chat) => {
                      const isDropTarget = dragOverChat?.chatId === chat.id
                      const dropPosition = dragOverChat?.position
                      const dropGradient = isDropTarget
                        ? 'linear-gradient(var(--accent), var(--accent))'
                        : undefined
                      const dropPositionCss = dropPosition === 'before' ? '0 0' : '0 100%'
                      return (
                        <button
                          key={chat.id}
                          ref={setChatRef(chat.id)}
                          data-chat-id={chat.id}
                          onClick={() => {
                            selectChat(chat.id)
                            navigateToView('chat')
                          }}
                          draggable
                          onDragStart={(e) => handleChatDragStart(e, chat.id)}
                          onDragEnd={handleChatDragEnd}
                          onDragOver={(e) => handleUnassignedChatDragOver(e, chat.id)}
                          onDrop={(e) => handleUnassignedChatDrop(e, chat.id)}
                          className={`
                            w-full text-left px-3 py-2 rounded-lg text-sm transition-all flex items-center gap-2 group
                            ${currentChatId === chat.id ? 'text-text font-medium shadow-sm' : 'text-text-secondary hover:bg-bg-secondary hover:text-text'}
                            ${draggingChatId === chat.id ? 'opacity-50' : ''}
                          `}
                          style={{
                            backgroundColor: currentChatId === chat.id
                              ? (theme === 'dark' ? 'rgba(255,255,255,0.1)' : '#E4E4E7')
                              : 'transparent',
                            backgroundImage: dropGradient,
                            backgroundRepeat: dropGradient ? 'no-repeat' : undefined,
                            backgroundSize: dropGradient ? '100% 2px' : undefined,
                            backgroundPosition: dropGradient ? dropPositionCss : undefined
                          }}
                          onContextMenu={(e) => {
                            e.preventDefault()
                            openPopover('chat-options', {
                              chatId: chat.id,
                              position: { top: e.clientY, left: e.clientX }
                            })
                          }}
                        >
                          <div className="flex items-center justify-between w-full overflow-hidden">
                            <span className="truncate pl-1 flex-1">{chat.name || '새로운 대화'}</span>
                            <div
                              role="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                openPopover('chat-options', {
                                  chatId: chat.id,
                                  position: { top: e.clientY, left: e.clientX }
                                })
                              }}
                              className="opacity-0 group-hover:opacity-100 p-1 hover:bg-bg-tertiary rounded transition-all flex-shrink-0"
                            >
                              {/* Modified: Changed icon to MoreVertical to match project list style */}
                              <MoreVertical size={14} className="text-text-tertiary" />
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <ToolsTab isCollapsed={isCollapsed} />
        )}
      </div>

      {/* 4. Settings */}
      <div className="flex-shrink-0 p-3 border-t bg-sidebar-bg" style={{ borderColor: 'var(--border)' }}>
        <button
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            openPopover('profile', {
              position: {
                bottom: window.innerHeight - rect.top + 10,
                left: rect.left + 10
              }
            })
          }}
          aria-label="설정"
          className={`
              w-full flex items-center gap-2 p-2 rounded-xl transition-all hover:bg-sidebar-hover group text-text-secondary hover:text-text
              ${isCollapsed ? 'justify-center' : ''}
            `}
        >
          <Settings size={18} />
          {!isCollapsed && <span className="text-sm">설정</span>}
        </button>
      </div>
    </div>
  )
}

import { useEffect } from 'react'
import { useFolderStore } from '../stores/folder-store'
import { useChatStore } from '../stores/chat-store'
import { useUIStore } from '../stores/ui-store'
import { ChatInput } from './ChatInput'

export function FolderView() {
  const { selectedFolderId, openModal, navigateBack, canNavigateBack, navigateToView } = useUIStore()
  const { getFolder, getChatsByFolder } = useFolderStore()
  const { chats, selectChat } = useChatStore()

  // FolderView 진입 시 currentChatId 초기화 (새 채팅 생성을 위해)
  useEffect(() => {
    if (selectedFolderId) {
      selectChat(null)
    }
  }, [selectedFolderId, selectChat])

  const folder = selectedFolderId ? getFolder(selectedFolderId) : null

  if (!selectedFolderId || !folder) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center animate-fade-in" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-tertiary)' }}>
        <div className="text-center">
          <h2 className="text-3xl font-bold mb-3" style={{ color: 'var(--text)' }}>프로젝트를 선택해주세요</h2>
          <p style={{ color: 'var(--text-secondary)' }}>사이드바에서 프로젝트를 선택하면 내용을 확인할 수 있습니다.</p>
        </div>
      </div>
    )
  }

  const chatIds = getChatsByFolder(selectedFolderId)
  const folderChats = chatIds
    .map(chatId => chats.find(c => c.id === chatId))
    .filter((chat): chat is NonNullable<typeof chat> => chat !== undefined)
    .sort((a, b) => {
      if (b.updatedAt !== a.updatedAt) return b.updatedAt - a.updatedAt
      return b.createdAt - a.createdAt
    })

  const handleChatClick = (chatId: string) => {
    selectChat(chatId)
    navigateToView('chat')
  }

  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp)
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    return `${year}/${month}/${day}`
  }

  return (
    <div className="flex-1 relative overflow-hidden flex flex-col" style={{ backgroundColor: 'var(--bg-secondary)' }}>
      {/* Folder Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b" style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-3">
          {canNavigateBack() && (
            <button
              onClick={navigateBack}
              className="p-2 rounded-lg transition-all duration-200"
              style={{ color: 'var(--text-tertiary)' }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--text-secondary)'
                e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = 'var(--text-tertiary)'
                e.currentTarget.style.backgroundColor = 'transparent'
              }}
              aria-label="뒤로 가기"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
          )}
          {/* Modified: Added serif font family to match user request (3rd image) */}
          <h1 className="text-xl font-normal font-serif" style={{ color: 'var(--text)', fontFamily: "'Georgia', 'NanumMyeongjo', 'Nanum Myeongjo', serif" }}>{folder.name}</h1>
        </div>
        <button
          onClick={() => openModal('add-folder-file', { folderId: selectedFolderId })}
          className="px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors duration-200 shadow-sm hover:shadow-md"
          style={{ backgroundColor: 'var(--accent)' }}
          onMouseEnter={(e) => e.currentTarget.style.opacity = '0.9'}
          onMouseLeave={(e) => e.currentTarget.style.opacity = '1'}
        >
          + 파일 추가
        </button>
      </div>

      {/* Chat Input - Fixed below header */}
      <div className="flex-shrink-0 px-4 py-3 border-b" style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}>
        <div className="[&>div]:static [&>div]:transform-none [&>div]:left-auto [&>div]:bottom-auto [&>div]:w-full [&>div]:max-w-full [&>div]:px-0">
          <ChatInput folderId={selectedFolderId} />
        </div>
      </div>

      {/* Chat History List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <h2 className="text-xs font-bold mb-3" style={{ color: 'var(--text-tertiary)' }}>
          프로젝트에 속한 채팅 히스토리
        </h2>

        {folderChats.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 animate-fade-in" style={{ color: 'var(--text-tertiary)' }}>
            <div className="w-20 h-20 rounded-2xl shadow-sm flex items-center justify-center mb-4" style={{ backgroundColor: 'var(--bg)' }}>
              <svg className="w-10 h-10 opacity-40" style={{ color: 'var(--text-tertiary)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
            </div>
            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>이 프로젝트에는 아직 채팅이 없습니다.</p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-tertiary)' }}>채팅을 추가하여 시작해보세요.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {folderChats.map(chat => (
              <div
                key={chat.id}
                onClick={() => handleChatClick(chat.id)}
                className="group flex items-start gap-3 p-4 border rounded-xl cursor-pointer hover:shadow-md transition-all duration-200 animate-slide-up"
                style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}
                onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--accent)'}
                onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--border)'}
              >
                <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center" style={{ color: 'var(--text-secondary)' }}>
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-medium truncate mb-1 group-hover:text-primary-600 transition-colors duration-200" style={{ color: 'var(--text)' }}>
                    {chat.name}
                  </h3>
                  <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {formatDate(chat.updatedAt)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

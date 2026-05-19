import { useState, useEffect, useRef, KeyboardEvent } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useChatStore } from '../../stores/chat-store'
import { Search, X, MessageSquare, ChevronRight, SearchX } from 'lucide-react'

export function ChatSearchModal() {
  const { activeModal, closeModal } = useUIStore()
  const { chats, selectChat } = useChatStore()
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<typeof chats>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)

  const isOpen = activeModal === 'chat-search'

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isOpen])

  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      setSelectedIndex(0)
      return
    }

    const query = searchQuery.toLowerCase()
    const results = chats.filter((chat) => {
      const nameMatch = chat.name.toLowerCase().includes(query)
      const messageMatch = chat.messages.some((msg) =>
        msg.content.toLowerCase().includes(query)
      )
      return nameMatch || messageMatch
    })

    setSearchResults(results)
    setSelectedIndex(0)
  }, [searchQuery, chats])

  const handleNavigateToChat = (chatId: string) => {
    selectChat(chatId)
    closeModal()
    setSearchQuery('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      closeModal()
      setSearchQuery('')
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((prev) => Math.min(prev + 1, searchResults.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((prev) => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter' && searchResults.length > 0) {
      e.preventDefault()
      handleNavigateToChat(searchResults[selectedIndex].id)
    }
  }

  useEffect(() => {
    if (resultsRef.current) {
      const selectedElement = resultsRef.current.children[selectedIndex] as HTMLElement
      if (selectedElement) {
        selectedElement.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }
    }
  }, [selectedIndex])

  const getPreviewText = (chatId: string): string => {
    const chat = chats.find((c) => c.id === chatId)
    if (!chat || chat.messages.length === 0) return '메시지 없음'

    const lastMessage = chat.messages[chat.messages.length - 1]
    const preview = lastMessage.content.substring(0, 80)
    return preview.length < lastMessage.content.length ? `${preview}...` : preview
  }

  if (!isOpen) return null

  return (
    <>
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={() => {
          closeModal()
          setSearchQuery('')
        }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] p-4 pointer-events-none">
        <div
          className="w-full max-w-2xl rounded-2xl shadow-2xl border border-border overflow-hidden pointer-events-auto animate-scale-up flex flex-col"
          style={{ backgroundColor: 'var(--bg)' }}
        >
          {/* Search Header */}
          <div className="p-4 border-b border-border">
            <div className="relative flex items-center">
              <div className="absolute left-4 z-10 text-text-tertiary pointer-events-none">
                <Search size={20} />
              </div>
              <input
                ref={inputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="채팅 검색..."
                className="w-full pl-12 pr-12 py-3.5 rounded-xl text-text placeholder:text-text-tertiary border border-transparent focus:ring-1 focus:ring-accent outline-none transition-all text-lg shadow-inner"
                style={{ backgroundColor: 'var(--bg-secondary)' }}
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-4 p-1 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
                >
                  <X size={18} />
                </button>
              )}
            </div>
          </div>

          {/* Results List */}
          <div
            ref={resultsRef}
            className="max-h-[400px] overflow-y-auto thin-scrollbar bg-bg"
            style={{ backgroundColor: 'var(--bg)' }}
          >
            {searchQuery.trim() === '' ? (
              <div className="py-20 text-center text-text-tertiary flex flex-col items-center">
                <div className="w-16 h-16 rounded-full bg-bg-secondary flex items-center justify-center mb-4">
                  <Search size={32} className="opacity-40" strokeWidth={1.5} />
                </div>
                <p className="font-medium mb-1">채팅 검색</p>
                <p className="text-sm opacity-70">채팅 이름이나 메시지 내용으로 검색해보세요</p>
              </div>
            ) : searchResults.length === 0 ? (
              <div className="py-20 text-center text-text-tertiary flex flex-col items-center">
                <div className="w-16 h-16 rounded-full bg-bg-secondary flex items-center justify-center mb-4">
                  <SearchX size={32} className="opacity-40" strokeWidth={1.5} />
                </div>
                <p className="font-medium mb-1">검색 결과 없음</p>
                <p className="text-sm opacity-70">"{searchQuery}"에 대한 결과를 찾을 수 없습니다</p>
              </div>
            ) : (
              <div className="py-2">
                {searchResults.map((chat, index) => (
                  <button
                    key={chat.id}
                    onClick={() => handleNavigateToChat(chat.id)}
                    className={`w-full px-4 py-3 text-left transition-all duration-150 border-l-4 group ${index === selectedIndex
                      ? 'bg-bg-secondary border-accent'
                      : 'border-transparent hover:bg-bg-secondary/50'
                      }`}
                    onMouseEnter={() => setSelectedIndex(index)}
                  >
                    <div className="flex items-start gap-4">
                      <div className={`p-2.5 rounded-xl bg-bg-tertiary/50 ${index === selectedIndex ? 'text-accent bg-accent/10' : 'text-text-tertiary'}`}>
                        <MessageSquare size={20} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className={`font-semibold text-sm mb-0.5 truncate ${index === selectedIndex ? 'text-text' : 'text-text-secondary'}`}>
                          {chat.name}
                        </div>
                        <div className="text-xs text-text-tertiary line-clamp-1 leading-relaxed">
                          {getPreviewText(chat.id)}
                        </div>
                      </div>
                      <div className={`self-center transition-all ${index === selectedIndex ? 'opacity-100 translate-x-0 text-accent' : 'opacity-0 -translate-x-2'}`}>
                        <ChevronRight size={18} />
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Footer Shortcuts */}
          <div className="px-4 py-3 border-t border-border bg-bg-secondary/30 flex items-center justify-between text-xs text-text-tertiary">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <kbd className="px-2 py-1 rounded bg-bg border border-border font-mono text-[10px]">↑↓</kbd>
                <span>이동</span>
              </div>
              <div className="flex items-center gap-1.5">
                <kbd className="px-2 py-1 rounded bg-bg border border-border font-mono text-[10px]">Enter</kbd>
                <span>선택</span>
              </div>
              <div className="flex items-center gap-1.5">
                <kbd className="px-2 py-1 rounded bg-bg border border-border font-mono text-[10px]">Esc</kbd>
                <span>닫기</span>
              </div>
            </div>
            <div>
              {searchResults.length > 0 && <span>{searchResults.length}개 결과</span>}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

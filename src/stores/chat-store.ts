import { create } from 'zustand'
import { useFolderStore } from './folder-store'
import { useUIStore } from './ui-store'
import { DEFAULT_CHAT_PROJECT_ID } from '../constants/rag'
import { IS_BETA } from '../config/beta'

const BETA_FORCE_DIFF_MODE = IS_BETA
import {
  appendToUnassignedChatOrder,
  getUnassignedChatOrder,
  loadChatOrderSettings,
  removeFromProjectChatOrder,
  removeFromUnassignedChatOrder,
  setUnassignedChatOrder
} from '../utils/chat-order-storage'

// v4.1.4: 메시지 메타데이터
export interface ThinkingSnapshotItem {
  content: string
  timestamp: number
  durationMs?: number
  durationLabel?: string
}

export interface ProgressSnapshot {
  thinkingItems?: ThinkingSnapshotItem[]
  stageMessage?: string
  isDone?: boolean
  thinkingDurationSeconds?: number
}

export interface ChatAttachment {
  id: string
  name: string
  type?: string
  size?: number
}

export interface MessageMetadata {
  executedDeltas?: any[]
  docKey?: string
  editCount?: number
  thinking?: ThinkingSnapshotItem[]
  progressState?: ProgressSnapshot
  animationCompleted?: boolean
  progressPlaceholder?: boolean
  attachments?: ChatAttachment[]
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  metadata?: MessageMetadata
}

// v4.1.4: RejectResult 타입
export interface RejectResult {
  fact: {
    success: boolean
    rejectedCount: number
    rejectionType: 'all' | 'partial'
    requiresFullRegen: boolean
    uncertain: boolean
    mismatch: boolean
  }
  explain: {
    rejectedOps: any[]
    reason: string
  }
}

export interface Chat {
  id: string
  name: string
  messages: Message[]
  createdAt: number
  updatedAt: number
  boundDocKey?: string
  diffModeEnabled: boolean
}

// v4.1.4: 큐 상한
const MAX_RESULTS_PER_KEY = 10
const MAX_OPS_TOTAL = 20
const LAST_ACTIVE_CHAT_KEY = 'chat_last_active_id'

interface ChatState {
  chats: Chat[]
  currentChatId: string | null
  progressMessage: string | null
  isLoading: boolean

  // v4.1.4: 거절 정보 큐
  pendingRejectionByKey: Record<string, RejectResult[]>
  lastActiveDocKey?: string

  // Actions
  createChat: (projectId?: string) => Promise<string>
  selectChat: (id: string | null) => void
  setIsLoading: (loading: boolean) => void
  addMessage: (chatId: string, role: 'user' | 'assistant', content: string, messageId?: string) => string
  updateLastMessage: (chatId: string, content: string) => void
  updateChatName: (id: string, name: string) => void
  deleteChat: (id: string) => void
  setProgressMessage: (message: string | null) => void

  // v4.1.4: 메시지 메타데이터 업데이트
  updateMessageMetadata: (chatId: string, messageId: string, metadata: Partial<MessageMetadata>) => void

  // v4.1.4: boundDocKey 관리
  updateChatBoundDocKey: (chatId: string, docKey: string) => void
  setLastActiveDocKey: (docKey?: string) => void

  // v4.1.4: 거절 정보 큐 관리 (peek/consume 분리 - 불변식 9)
  enqueuePendingRejection: (chatId: string, docKey: string, result: RejectResult) => void
  peekPendingRejection: (chatId: string, docKey: string) => RejectResult[] | null
  consumePendingRejection: (chatId: string, docKey: string) => void

  // Diff Actions (채팅별)
  toggleDiffMode: (chatId: string, enabled: boolean) => void
  getDiffModeEnabled: (chatId: string) => boolean

  // Ordering
  reorderChat: (
    chatId: string,
    targetChatId: string,
    position?: 'before' | 'after',
    persist?: boolean
  ) => void

  // Persistence
  hydrateFromDb: () => Promise<void>

  // Getters
  getCurrentChat: () => Chat | null
}

const generateId = () => Math.random().toString(36).substring(2, 15)

const callApi = (channel: string, ...args: any[]) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return Promise.resolve(null)
  return api.invoke(channel, ...args)
}

const persistLastActiveChatId = (chatId: string | null | undefined) => {
  if (!chatId) return
  void callApi('settings:set', {
    key: LAST_ACTIVE_CHAT_KEY,
    value: chatId,
    type: 'string',
  })
}

type ChatFileRow = {
  id: string
  chatId: string
  name: string
  ext?: string | null
  size?: number | null
  addedAt?: number | null
}

const attachFilesToMessages = (chat: Chat, files: ChatFileRow[]): Chat => {
  if (!files.length) return chat
  const messages = chat.messages.map((msg) => ({
    ...msg,
    metadata: msg.metadata ? { ...msg.metadata } : undefined,
  }))

  const userMessages = messages
    .filter((msg) => msg.role === 'user')
    .sort((a, b) => a.timestamp - b.timestamp)
  if (userMessages.length === 0) return { ...chat, messages }

  const existingAttachmentIds = new Set<string>()
  for (const msg of messages) {
    const attachments = msg.metadata?.attachments
    if (Array.isArray(attachments)) {
      attachments.forEach((att) => existingAttachmentIds.add(att.id))
    }
  }

  const sortedFiles = files
    .filter((file) => typeof file.addedAt === 'number')
    .sort((a, b) => (a.addedAt ?? 0) - (b.addedAt ?? 0))

  for (const file of sortedFiles) {
    if (existingAttachmentIds.has(file.id)) continue
    const target = userMessages.find((msg) => msg.timestamp >= (file.addedAt ?? 0))
    if (!target) continue
    if (!target.metadata) target.metadata = {}
    const attachments = Array.isArray(target.metadata.attachments)
      ? [...target.metadata.attachments]
      : []
    attachments.push({
      id: file.id,
      name: file.name,
      type: file.ext ?? undefined,
      size: typeof file.size === 'number' ? file.size : undefined,
    })
    target.metadata.attachments = attachments
    existingAttachmentIds.add(file.id)
  }

  return { ...chat, messages }
}

const getChatLastActivityAt = (chat: Chat): number => {
  const lastMessageTimestamp =
    chat.messages.length > 0 ? (chat.messages[chat.messages.length - 1]?.timestamp ?? 0) : 0
  return Math.max(chat.updatedAt ?? 0, lastMessageTimestamp, chat.createdAt ?? 0)
}

// v4.1.4: 큐 트리밍 함수 (불변성 유지)
function trimQueue(results: RejectResult[]): RejectResult[] {
  const queue = results.slice(-MAX_RESULTS_PER_KEY)
  let remaining = MAX_OPS_TOTAL
  const trimmed: RejectResult[] = []
  let omittedOpsCount = 0

  for (let i = queue.length - 1; i >= 0; i--) {
    const r = queue[i]
    const baseExplain = r.explain ?? { rejectedOps: [], reason: '' }
    const ops = baseExplain.rejectedOps ?? []

    const take = Math.max(0, Math.min(ops.length, remaining))
    const omitted = ops.length - take

    if (omitted > 0) omittedOpsCount += omitted

    trimmed.push({
      fact: { ...r.fact },
      explain: {
        ...baseExplain,
        rejectedOps: ops.slice(0, take),
      },
    })

    remaining -= take
    if (remaining <= 0) break
  }

  if (omittedOpsCount > 0 && trimmed.length > 0) {
    const newest = trimmed[0]
    const newestExplain = newest.explain ?? { rejectedOps: [], reason: '' }

    trimmed[0] = {
      fact: { ...newest.fact },
      explain: {
        ...newestExplain,
        reason: `${(newestExplain.reason ?? '').trim()} (추가 ${omittedOpsCount}개 생략)`.trim(),
      },
    }
  }

  return trimmed.reverse()
}

export const useChatStore = create<ChatState>((set, get) => ({
  chats: [],
  currentChatId: null,
  progressMessage: null,
  isLoading: false,

  pendingRejectionByKey: {},
  lastActiveDocKey: undefined,

  createChat: async (projectId?: string) => {
    const lastActiveDocKey = get().lastActiveDocKey
    const newChat: Chat = {
      id: generateId(),
      name: '새 채팅',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      diffModeEnabled: true,
      boundDocKey: lastActiveDocKey,
    }

    set((state) => ({
      chats: [newChat, ...state.chats],
      currentChatId: newChat.id,
    }))
    persistLastActiveChatId(newChat.id)

    await callApi('chat:create', newChat)

    // 프로젝트 ID가 있으면 프로젝트에 바인딩
    if (projectId && typeof window !== 'undefined') {
      const { useFolderStore } = await import('./folder-store')
      const { addFileToFolder } = useFolderStore.getState()
      addFileToFolder(projectId, newChat.id)
    }
    // ⚠️ 자동 appendToUnassignedChatOrder 호출 제거됨
    //   이유: 매 채팅 생성마다 호출되어 cached order가 항상 non-empty가 되고,
    //   hydrateFromDb에서 DB의 updated_at DESC 정렬을 무력화하여
    //   "새 메시지가 있는 채팅이 위로 안 올라옴" 증상 유발.
    //   stored order는 사용자가 명시적으로 DnD 재정렬했을 때만 reorderChat이 저장.

    return newChat.id
  },

  selectChat: (id) => {
    set({ currentChatId: id })
    persistLastActiveChatId(id)

    // Auto-expand project folder if this is a project chat
    if (!id) return
    const folder = useFolderStore.getState().getChatFolder(id)
    if (folder) {
      useUIStore.getState().expandFolder(folder.id)
    }
  },

  setIsLoading: (loading) => {
    set({ isLoading: loading })
  },

  addMessage: (chatId, role, content, messageId) => {
    const message: Message = {
      id: messageId ?? generateId(),
      role,
      content,
      timestamp: Date.now(),
    }

    // 첫 번째 사용자 메시지인지 확인 (이름 자동 생성 대상)
    const chatBefore = get().chats.find((c) => c.id === chatId)
    const isFirstUserMessage = chatBefore && chatBefore.messages.length === 0 && role === 'user'

    set((state) => ({
      chats: state.chats.map((chat) =>
        chat.id === chatId
          ? {
              ...chat,
              messages: [...chat.messages, message],
              updatedAt: Date.now(),
              name:
                chat.messages.length === 0 && role === 'user'
                  ? content.substring(0, 30) + (content.length > 30 ? '...' : '')
                  : chat.name,
            }
          : chat
      ),
    }))

    void callApi('message:insert', { chatId, message })

    // 자동 생성된 채팅 이름을 DB에 영구 저장
    if (isFirstUserMessage) {
      const updatedChat = get().chats.find((c) => c.id === chatId)
      if (updatedChat) {
        void callApi('chat:update', {
          id: updatedChat.id,
          name: updatedChat.name,
          updatedAt: updatedChat.updatedAt,
          boundDocKey: updatedChat.boundDocKey,
          diffModeEnabled: updatedChat.diffModeEnabled,
        })
      }
    }

    return message.id
  },

  updateLastMessage: (chatId, content) => {
    const chat = get().chats.find((c) => c.id === chatId)
    const lastMessage = chat?.messages[chat.messages.length - 1]
    if (!lastMessage) return

    set((state) => ({
      chats: state.chats.map((chat) => {
        if (chat.id !== chatId || chat.messages.length === 0) return chat

        const messages = [...chat.messages]
        const lastIndex = messages.length - 1
        messages[lastIndex] = {
          ...messages[lastIndex],
          content,
        }

        return {
          ...chat,
          messages,
          updatedAt: Date.now(),
        }
      }),
    }))

    void callApi('message:update', { messageId: lastMessage.id, content })
  },

  updateMessageMetadata: (chatId, messageId, metadata) => {
    const chat = get().chats.find((c) => c.id === chatId)
    const message = chat?.messages.find((m) => m.id === messageId)
    const nextMetadata = { ...(message?.metadata ?? {}), ...metadata }

    set((state) => ({
      chats: state.chats.map((chat) => {
        if (chat.id !== chatId) return chat

        return {
          ...chat,
          messages: chat.messages.map((msg) =>
            msg.id === messageId ? { ...msg, metadata: nextMetadata } : msg
          ),
          updatedAt: Date.now(),
        }
      }),
    }))

    void callApi('message:updateMetadata', { messageId, metadata: nextMetadata })
  },

  updateChatBoundDocKey: (chatId, docKey) => {
    set((state) => ({
      chats: state.chats.map((chat) =>
        chat.id === chatId ? { ...chat, boundDocKey: docKey } : chat
      ),
    }))

    const chat = get().chats.find((c) => c.id === chatId)
    if (chat) {
      void callApi('chat:update', { ...chat, boundDocKey: docKey, updatedAt: Date.now() })
    }
  },

  setLastActiveDocKey: (docKey) => {
    set({ lastActiveDocKey: docKey })
  },

  enqueuePendingRejection: (chatId, docKey, result) => {
    if (result.fact.mismatch) {
      console.log('[ChatStore] mismatch=true, enqueue 건너뜀')
      return
    }

    const key = `${chatId}::${docKey}`
    set((state) => {
      const existing = state.pendingRejectionByKey[key] ?? []
      const queued = [...existing, result]
      const trimmed = trimQueue(queued)

      return {
        pendingRejectionByKey: {
          ...state.pendingRejectionByKey,
          [key]: trimmed,
        },
      }
    })
  },

  peekPendingRejection: (chatId, docKey) => {
    const key = `${chatId}::${docKey}`
    const results = get().pendingRejectionByKey[key]
    return results && results.length > 0 ? results : null
  },

  consumePendingRejection: (chatId, docKey) => {
    const key = `${chatId}::${docKey}`
    set((state) => {
      const { [key]: _, ...rest } = state.pendingRejectionByKey
      return { pendingRejectionByKey: rest }
    })
  },

  updateChatName: (id, name) => {
    set((state) => ({
      chats: state.chats.map((chat) =>
        chat.id === id ? { ...chat, name, updatedAt: Date.now() } : chat
      ),
    }))

    const chat = get().chats.find((c) => c.id === id)
    if (chat) {
      void callApi('chat:update', { ...chat, name, updatedAt: Date.now() })
    }
  },

  deleteChat: (id) => {
    const folder = useFolderStore.getState().getChatFolder(id)
    const electronApi = typeof window !== 'undefined' ? window.electronAPI : undefined

    void removeFromUnassignedChatOrder(id)
    if (folder?.id) {
      void removeFromProjectChatOrder(folder.id, id)
      // v6.1: folder-store에서 채팅 참조 제거 (Unknown Chat 버그 수정)
      useFolderStore.getState().removeFileFromFolder(folder.id, id)
    }

    if (electronApi?.rag?.deleteChatScope) {
      const projectId = folder?.id ?? DEFAULT_CHAT_PROJECT_ID
      void electronApi.rag
        .deleteChatScope({ projectId, chatId: id })
        .then((result) => {
          if (!result?.success) {
            console.error('[ChatStore] RAG chat scope delete failed:', result?.error)
          }
        })
        .catch((err) => {
          console.error('[ChatStore] RAG chat scope delete error:', err)
        })
    }

    set((state) => {
      const newChats = state.chats.filter((chat) => chat.id !== id)
      return {
        chats: newChats,
        currentChatId:
          state.currentChatId === id ? newChats[0]?.id ?? null : state.currentChatId,
      }
    })

    void callApi('chat:delete', { id })
  },

  getCurrentChat: () => {
    const state = get()
    return state.chats.find((chat) => chat.id === state.currentChatId) ?? null
  },

  setProgressMessage: (message) => {
    set({ progressMessage: message })
  },

  toggleDiffMode: (chatId, enabled) => {
    set((state) => ({
      chats: state.chats.map((chat) =>
        chat.id === chatId ? { ...chat, diffModeEnabled: enabled } : chat
      ),
    }))

    const chat = get().chats.find((c) => c.id === chatId)
    if (chat) {
      void callApi('chat:update', { ...chat, diffModeEnabled: enabled, updatedAt: Date.now() })
    }
  },

  getDiffModeEnabled: (chatId) => {
    // 베타: 항상 검토 모드 강제 — delta UI 가 안 떠 즉시 적용 되는 사고 방지.
    if (BETA_FORCE_DIFF_MODE) return true
    const chat = get().chats.find((c) => c.id === chatId)
    return chat ? chat.diffModeEnabled : true
  },

  reorderChat: (chatId, targetChatId, position = 'before', persist = true) => {
    let nextChats: Chat[] = []
    let hasReordered = false
    set((state) => {
      if (chatId === targetChatId) return state
      const fromIndex = state.chats.findIndex((chat) => chat.id === chatId)
      const targetIndex = state.chats.findIndex((chat) => chat.id === targetChatId)
      if (fromIndex < 0 || targetIndex < 0) return state

      const updated = [...state.chats]
      const [moved] = updated.splice(fromIndex, 1)
      let insertIndex = position === 'after' ? targetIndex + 1 : targetIndex
      if (fromIndex < insertIndex) insertIndex -= 1
      if (insertIndex < 0) insertIndex = 0
      if (insertIndex > updated.length) insertIndex = updated.length
      updated.splice(insertIndex, 0, moved)

      nextChats = updated
      hasReordered = true
      return { chats: updated }
    })

    if (persist && hasReordered) {
      const getChatFolder = useFolderStore.getState().getChatFolder
      const unassignedOrder = nextChats
        .filter((chat) => !getChatFolder(chat.id))
        .map((chat) => chat.id)
      void setUnassignedChatOrder(unassignedOrder)
    }
  },

  hydrateFromDb: async () => {
    const result = await callApi('chat:list')
    if (!result?.success) return
    let chats: Chat[] = Array.isArray(result.data?.chats) ? (result.data.chats as Chat[]) : []
    const chatFilesResult = await callApi('chatFiles:listAll')
    if (chatFilesResult?.success) {
      const chatFilesMap = chatFilesResult.data?.chatFiles as Record<string, ChatFileRow[]> | undefined
      if (chatFilesMap && typeof chatFilesMap === 'object') {
        chats = chats.map((chat: Chat) => attachFilesToMessages(chat, chatFilesMap[chat.id] || []))
      }
    }
    await loadChatOrderSettings()
    const order = getUnassignedChatOrder()
    if (order.length > 0) {
      const chatMap = new Map<string, Chat>(chats.map((chat: Chat) => [chat.id, chat]))
      const ordered: Chat[] = []
      const orderSet = new Set(order)
      for (const id of order) {
        const entry = chatMap.get(id)
        if (entry) ordered.push(entry)
      }
      const remaining: Chat[] = chats.filter((chat: Chat) => !orderSet.has(chat.id))
      chats = [...ordered, ...remaining]
    }

    const settingsResult = await callApi('settings:getAll')
    const settings = settingsResult?.success ? settingsResult.data?.settings ?? {} : {}
    const lastActiveChatId =
      typeof settings[LAST_ACTIVE_CHAT_KEY] === 'string' ? settings[LAST_ACTIVE_CHAT_KEY] : null

    const hasLastActiveChat = lastActiveChatId
      ? chats.some((chat: Chat) => chat.id === lastActiveChatId)
      : false
    const mostRecentChatId =
      chats
        .slice()
        .sort((a: Chat, b: Chat) => {
          const byActivity = getChatLastActivityAt(b) - getChatLastActivityAt(a)
          if (byActivity !== 0) return byActivity
          return b.createdAt - a.createdAt
        })[0]?.id ?? null

    set({
      chats,
      currentChatId: hasLastActiveChat ? lastActiveChatId : mostRecentChatId,
    })
  },
}))

export type ChatDragPayload = {
  chatId: string
  sourceFolderId?: string | null
}

const CHAT_DRAG_TYPE = 'application/x-chat-dnd'
let lastDragPayload: ChatDragPayload | null = null

type DragLikeEvent = {
  dataTransfer: DataTransfer
}

export const setChatDragData = (event: DragLikeEvent, payload: ChatDragPayload) => {
  lastDragPayload = payload
  event.dataTransfer.setData(CHAT_DRAG_TYPE, JSON.stringify(payload))
  event.dataTransfer.setData('text/plain', payload.chatId)
  event.dataTransfer.effectAllowed = 'move'
}

export const getChatDragData = (event: DragLikeEvent): ChatDragPayload | null => {
  let raw = ''
  try {
    raw = event.dataTransfer.getData(CHAT_DRAG_TYPE)
  } catch {
    raw = ''
  }
  if (raw) {
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed.chatId === 'string') {
        lastDragPayload = parsed as ChatDragPayload
        return parsed as ChatDragPayload
      }
    } catch {
      // fall through to plain text
    }
  }

  let fallbackId = ''
  try {
    fallbackId = event.dataTransfer.getData('text/plain')
  } catch {
    fallbackId = ''
  }
  if (fallbackId) {
    const payload = {
      chatId: fallbackId,
      sourceFolderId: lastDragPayload?.chatId === fallbackId
        ? lastDragPayload.sourceFolderId
        : undefined
    }
    lastDragPayload = payload
    return payload
  }

  return lastDragPayload
}

export const hasChatDragData = (event: DragLikeEvent): boolean => {
  const types = Array.from(event.dataTransfer.types ?? [])
  if (types.length === 0 && lastDragPayload) return true
  return types.includes(CHAT_DRAG_TYPE) || types.includes('text/plain')
}

export const clearChatDragData = () => {
  lastDragPayload = null
}

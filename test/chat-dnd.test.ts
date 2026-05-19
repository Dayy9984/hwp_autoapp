import { describe, expect, it } from 'vitest'
import {
  clearChatDragData,
  getChatDragData,
  hasChatDragData,
  setChatDragData,
} from '../src/utils/chat-dnd'

class MockDataTransfer {
  private readonly data = new Map<string, string>()
  types: string[] = []
  effectAllowed = 'none'

  setData(type: string, value: string) {
    this.data.set(type, value)
    if (!this.types.includes(type)) this.types.push(type)
  }

  getData(type: string) {
    return this.data.get(type) ?? ''
  }
}

describe('chat-dnd fallback', () => {
  it('uses in-memory payload when dataTransfer payload is missing', () => {
    const transfer = new MockDataTransfer()
    setChatDragData(
      { dataTransfer: transfer as unknown as DataTransfer },
      { chatId: 'chat-1', sourceFolderId: 'folder-1' }
    )

    const emptyTransfer = {
      dataTransfer: {
        types: [],
        getData: () => '',
      } as unknown as DataTransfer,
    }

    expect(hasChatDragData(emptyTransfer)).toBe(true)
    expect(getChatDragData(emptyTransfer)).toEqual({
      chatId: 'chat-1',
      sourceFolderId: 'folder-1',
    })
  })

  it('clears payload cache on clearChatDragData', () => {
    const transfer = new MockDataTransfer()
    setChatDragData(
      { dataTransfer: transfer as unknown as DataTransfer },
      { chatId: 'chat-2' }
    )
    clearChatDragData()

    const emptyTransfer = {
      dataTransfer: {
        types: [],
        getData: () => '',
      } as unknown as DataTransfer,
    }

    expect(hasChatDragData(emptyTransfer)).toBe(false)
    expect(getChatDragData(emptyTransfer)).toBeNull()
  })
})

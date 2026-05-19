import { ipcMain } from 'electron'
import { dbManager } from '../services/db-manager'
import { appStorage } from '../services/app-storage'

type SettingRow = {
  key: string
  value: string | null
  type: string | null
}

type ChatRow = {
  id: string
  name: string
  created_at: number
  updated_at: number
  bound_doc_key: string | null
  diff_mode_enabled: number | null
}

type MessageRow = {
  id: string
  chat_id: string
  role: string
  content: string
  timestamp: number
  metadata: string | null
}

const inferSettingType = (value: unknown): string => {
  if (value === null || value === undefined) return 'string'
  if (Array.isArray(value) || typeof value === 'object') return 'json'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  return 'string'
}

const serializeSettingValue = (value: unknown, type: string): string | null => {
  if (value === null || value === undefined) return null
  if (type === 'json') return JSON.stringify(value)
  return String(value)
}

const parseSettingValue = (value: string | null, type: string | null): unknown => {
  if (value === null || value === undefined) return null
  if (type === 'number') {
    const num = Number(value)
    return Number.isFinite(num) ? num : null
  }
  if (type === 'boolean') {
    return value === 'true' || value === '1'
  }
  if (type === 'json') {
    try {
      return JSON.parse(value)
    } catch {
      return null
    }
  }
  return value
}

const parseMetadata = (raw: string | null): any => {
  if (!raw) return undefined
  try {
    return JSON.parse(raw)
  } catch {
    return undefined
  }
}

export function registerDbHandlers() {
  const db = dbManager.open()

  ipcMain.handle('settings:getAll', async () => {
    try {
      const rows = db.prepare('SELECT key, value, type FROM settings').all() as SettingRow[]
      const settings: Record<string, unknown> = {}
      for (const row of rows) {
        settings[row.key] = parseSettingValue(row.value, row.type)
      }
      return { success: true, data: { settings } }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('settings:set', async (_event, args: { key: string; value: unknown; type?: string }) => {
    try {
      const type = args.type ?? inferSettingType(args.value)
      const value = serializeSettingValue(args.value, type)
      const now = Date.now()
      db.prepare(
        `INSERT INTO settings (key, value, type, updated_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(key) DO UPDATE SET value=excluded.value, type=excluded.type, updated_at=excluded.updated_at`
      ).run(args.key, value, type, now)
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('settings:delete', async (_event, args: { key: string }) => {
    try {
      db.prepare('DELETE FROM settings WHERE key = ?').run(args.key)
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('chat:list', async () => {
    try {
      const chatRows = db
        .prepare(
          'SELECT id, name, created_at, updated_at, bound_doc_key, diff_mode_enabled FROM chats ORDER BY updated_at DESC'
        )
        .all() as ChatRow[]
      const messageStmt = db.prepare(
        'SELECT id, chat_id, role, content, timestamp, metadata FROM messages WHERE chat_id = ? ORDER BY timestamp ASC'
      )
      const chats = chatRows.map((chat) => {
        const messages = (messageStmt.all(chat.id) as MessageRow[]).map((msg) => ({
          id: msg.id,
          role: msg.role,
          content: msg.content,
          timestamp: msg.timestamp,
          metadata: parseMetadata(msg.metadata),
        }))
        return {
          id: chat.id,
          name: chat.name,
          createdAt: chat.created_at,
          updatedAt: chat.updated_at,
          boundDocKey: chat.bound_doc_key ?? undefined,
          diffModeEnabled: Boolean(chat.diff_mode_enabled),
          messages,
        }
      })
      return { success: true, data: { chats } }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('chat:create', async (_event, chat: any) => {
    try {
      db.prepare(
        `INSERT INTO chats (id, name, created_at, updated_at, bound_doc_key, diff_mode_enabled)
         VALUES (?, ?, ?, ?, ?, ?)`
      ).run(
        chat.id,
        chat.name,
        chat.createdAt,
        chat.updatedAt,
        chat.boundDocKey ?? null,
        chat.diffModeEnabled ? 1 : 0
      )
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('chat:update', async (_event, chat: any) => {
    try {
      db.prepare(
        `UPDATE chats
         SET name = ?, updated_at = ?, bound_doc_key = ?, diff_mode_enabled = ?
         WHERE id = ?`
      ).run(
        chat.name,
        chat.updatedAt,
        chat.boundDocKey ?? null,
        chat.diffModeEnabled ? 1 : 0,
        chat.id
      )
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('chat:delete', async (_event, args: { id: string }) => {
    try {
      db.prepare('DELETE FROM chats WHERE id = ?').run(args.id)
      try {
        appStorage.removeChatDirectory(args.id)
      } catch (error) {
        console.warn('[DB] Failed to remove chat file directory:', error)
      }
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('message:insert', async (_event, args: { chatId: string; message: any }) => {
    try {
      const metadata = args.message.metadata ? JSON.stringify(args.message.metadata) : null
      const insertMsg = db.prepare(
        `INSERT INTO messages (id, chat_id, role, content, timestamp, metadata)
         VALUES (?, ?, ?, ?, ?, ?)`
      )
      // chat.updated_at도 함께 갱신 — chat:list가 updated_at DESC로 정렬하므로
      // 메시지 추가 시 해당 채팅이 사이드바 최상단으로 올라가야 함
      const updateChat = db.prepare('UPDATE chats SET updated_at = ? WHERE id = ?')
      const tx = db.transaction(() => {
        insertMsg.run(
          args.message.id,
          args.chatId,
          args.message.role,
          args.message.content,
          args.message.timestamp,
          metadata
        )
        updateChat.run(args.message.timestamp, args.chatId)
      })
      tx()
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle('message:update', async (_event, args: { messageId: string; content: string }) => {
    try {
      // 메시지 content 갱신 + 해당 chat.updated_at도 현재 시각으로 갱신
      // (스트리밍 중 updateLastMessage가 반복 호출되므로 정렬에 반영되어야 함)
      const updateMsg = db.prepare('UPDATE messages SET content = ? WHERE id = ?')
      const updateChat = db.prepare(
        `UPDATE chats SET updated_at = ?
         WHERE id = (SELECT chat_id FROM messages WHERE id = ?)`
      )
      const now = Date.now()
      const tx = db.transaction(() => {
        updateMsg.run(args.content, args.messageId)
        updateChat.run(now, args.messageId)
      })
      tx()
      return { success: true }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })

  ipcMain.handle(
    'message:updateMetadata',
    async (_event, args: { messageId: string; metadata: any }) => {
      try {
        const metadata = args.metadata ? JSON.stringify(args.metadata) : null
        db.prepare('UPDATE messages SET metadata = ? WHERE id = ?').run(metadata, args.messageId)
        return { success: true }
      } catch (error: any) {
        return { success: false, error: { code: 'DB_ERROR', message: error.message } }
      }
    }
  )

  ipcMain.handle('chatFiles:listAll', async () => {
    try {
      const rows = db
        .prepare(
          "SELECT id, chat_id, name, extension, size, added_at FROM files WHERE scope = 'chat' ORDER BY added_at ASC"
        )
        .all() as Array<{
        id: string
        chat_id: string | null
        name: string
        extension: string | null
        size: number | null
        added_at: number | null
      }>

      const chatFiles: Record<string, Array<{
        id: string
        chatId: string
        name: string
        ext?: string | null
        size?: number | null
        addedAt?: number | null
      }>> = {}

      for (const row of rows) {
        if (!row.chat_id) continue
        if (!chatFiles[row.chat_id]) {
          chatFiles[row.chat_id] = []
        }
        chatFiles[row.chat_id].push({
          id: row.id,
          chatId: row.chat_id,
          name: row.name,
          ext: row.extension,
          size: row.size,
          addedAt: row.added_at,
        })
      }

      return { success: true, data: { chatFiles } }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_ERROR', message: error.message } }
    }
  })
}

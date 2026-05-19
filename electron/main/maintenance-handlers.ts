import { app, ipcMain } from 'electron'
import * as fs from 'fs'
import * as path from 'path'
import { dbManager } from '../services/db-manager'
import { appStorage } from '../services/app-storage'

type ResetOptions = {
  backup?: boolean
}

type ResetResult = {
  backupDir: string | null
  backedUp: string[]
  deleted: string[]
}

const getUserDataPath = () => app.getPath('userData')
const DB_FILENAME = 'inserty.db'
const LEGACY_DB_FILENAME = 'mallo.db'

const safeRemove = (targetPath: string, deleted: string[], errors: string[]) => {
  if (!fs.existsSync(targetPath)) return
  try {
    fs.rmSync(targetPath, { recursive: true, force: true })
    deleted.push(targetPath)
  } catch (error: any) {
    errors.push(`Delete failed: ${targetPath} (${error.message})`)
  }
}

const safeCopy = (label: string, srcPath: string, destPath: string, backedUp: string[], errors: string[]) => {
  if (!fs.existsSync(srcPath)) return
  try {
    fs.cpSync(srcPath, destPath, { recursive: true })
    backedUp.push(label)
  } catch (error: any) {
    errors.push(`Backup failed: ${label} (${error.message})`)
  }
}

const wipeDbData = () => {
  const db = dbManager.open()
  try {
    db.exec('BEGIN')
    db.exec('DELETE FROM messages')
    db.exec('DELETE FROM file_search_metadata')
    db.exec('DELETE FROM files')
    db.exec('DELETE FROM project_chat_bindings')
    db.exec('DELETE FROM template_pairs')
    db.exec('DELETE FROM chats')
    db.exec('DELETE FROM projects')
    db.exec('DELETE FROM settings')
    db.exec('COMMIT')
  } catch (error) {
    try {
      db.exec('ROLLBACK')
    } catch {
      // ignore rollback failure
    }
    throw error
  }
}

const collectCounts = (dbPath: string) => {
  if (!fs.existsSync(dbPath)) {
    return null
  }
  const db = dbManager.open()
  const tableCount = (table: string) => {
    const row = db.prepare(`SELECT COUNT(*) as count FROM ${table}`).get() as { count: number }
    return row?.count ?? 0
  }
  return {
    projects: tableCount('projects'),
    template_pairs: tableCount('template_pairs'),
    chats: tableCount('chats'),
    messages: tableCount('messages'),
    project_chat_bindings: tableCount('project_chat_bindings'),
    files: tableCount('files'),
    file_search_metadata: tableCount('file_search_metadata'),
    settings: tableCount('settings'),
  }
}

export function registerMaintenanceHandlers() {
  ipcMain.handle('maintenance:resetLocalData', async (_event, args?: ResetOptions) => {
    const options = args ?? {}
    const errors: string[] = []
    const result: ResetResult = {
      backupDir: null,
      backedUp: [],
      deleted: [],
    }

    if (options.backup !== false) {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
      const backupDir = path.join(getUserDataPath(), 'backups', `reset-${timestamp}`)
      try {
        fs.mkdirSync(backupDir, { recursive: true })
        result.backupDir = backupDir
      } catch (error: any) {
        errors.push(`Failed to create backup directory: ${backupDir} (${error.message})`)
      }

      if (result.backupDir) {
        const dbPath = path.join(getUserDataPath(), DB_FILENAME)
        safeCopy(DB_FILENAME, dbPath, path.join(result.backupDir, DB_FILENAME), result.backedUp, errors)
        const legacyDbPath = path.join(getUserDataPath(), LEGACY_DB_FILENAME)
        safeCopy(LEGACY_DB_FILENAME, legacyDbPath, path.join(result.backupDir, LEGACY_DB_FILENAME), result.backedUp, errors)
        safeCopy('projects', path.join(getUserDataPath(), 'projects'), path.join(result.backupDir, 'projects'), result.backedUp, errors)
        safeCopy('chat_files', path.join(getUserDataPath(), 'chat_files'), path.join(result.backupDir, 'chat_files'), result.backedUp, errors)
        safeCopy('vector_store', path.join(getUserDataPath(), 'vector_store'), path.join(result.backupDir, 'vector_store'), result.backedUp, errors)
        safeCopy('manifest.json', appStorage.getManifestPath(), path.join(result.backupDir, 'manifest.json'), result.backedUp, errors)
      }
    }

    try {
      wipeDbData()
    } catch (error: any) {
      errors.push(`DB reset failed: ${error.message}`)
    }

    safeRemove(path.join(getUserDataPath(), 'projects'), result.deleted, errors)
    safeRemove(path.join(getUserDataPath(), 'chat_files'), result.deleted, errors)
    safeRemove(path.join(getUserDataPath(), 'vector_store'), result.deleted, errors)
    safeRemove(appStorage.getManifestPath(), result.deleted, errors)

    if (errors.length > 0) {
      return { success: false, error: { code: 'RESET_FAILED', message: errors.join(' | ') }, data: result }
    }

    return { success: true, data: result }
  })

  ipcMain.handle('maintenance:validateDb', async () => {
    const dbPath = path.join(getUserDataPath(), DB_FILENAME)
    const legacyDbPath = path.join(getUserDataPath(), LEGACY_DB_FILENAME)
    const resolvedDbPath = fs.existsSync(dbPath) ? dbPath : legacyDbPath
    if (!fs.existsSync(resolvedDbPath)) {
      return { success: false, error: { code: 'DB_MISSING', message: 'DB file not found.' } }
    }

    try {
      const db = dbManager.open()
      const fkIssues = db.prepare('PRAGMA foreign_key_check').all() as Array<Record<string, unknown>>
      const counts = collectCounts(resolvedDbPath)
      const issues = fkIssues.length > 0 ? [{ type: 'foreign_key', count: fkIssues.length }] : []
      return {
        success: true,
        data: {
          ok: issues.length === 0,
          issues,
          counts,
          foreignKeyViolations: fkIssues,
        },
      }
    } catch (error: any) {
      return { success: false, error: { code: 'DB_VALIDATE_FAILED', message: error.message } }
    }
  })
}

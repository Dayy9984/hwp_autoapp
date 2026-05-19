/**
 * DB Manager
 * 로컬 SQLite 접속/초기화 관리
 */

import { app } from 'electron'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'node:url'
import Database from 'better-sqlite3'

const SCHEMA_VERSION = 1
const DB_FILENAME = 'inserty.db'
const LEGACY_DB_FILENAME = 'mallo.db'

const DEFAULT_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS schema_version (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  version INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS template_pairs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  template_name TEXT NOT NULL,
  template_rel_path TEXT NOT NULL,
  template_size INTEGER NOT NULL,
  filled_name TEXT NOT NULL,
  filled_rel_path TEXT NOT NULL,
  filled_size INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  extract_status TEXT DEFAULT 'pending',
  extract_status_message TEXT,
  extract_status_progress INTEGER,
  index_status TEXT DEFAULT 'pending',
  index_status_message TEXT,
  index_status_progress INTEGER,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_template_pairs_project_id ON template_pairs(project_id);

CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  bound_doc_key TEXT,
  diff_mode_enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  metadata TEXT,
  FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

CREATE TABLE IF NOT EXISTS project_chat_bindings (
  project_id TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (project_id, chat_id),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_project_chat_bindings_chat_id ON project_chat_bindings(chat_id);

CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  project_id TEXT,
  chat_id TEXT,
  name TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  extension TEXT,
  size INTEGER,
  text_length INTEGER,
  type TEXT DEFAULT 'reference',
  added_at INTEGER NOT NULL,
  index_status TEXT DEFAULT 'pending',
  index_status_message TEXT,
  index_status_progress INTEGER,
  original_path TEXT,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_files_scope ON files(scope);
CREATE INDEX IF NOT EXISTS idx_files_project_id ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_files_chat_id ON files(chat_id);

CREATE TABLE IF NOT EXISTS file_search_metadata (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  project_id TEXT NOT NULL,
  chat_id TEXT,
  kind TEXT NOT NULL,
  file_id TEXT,
  file_name TEXT,
  openai_file_id TEXT,
  vector_store_file_id TEXT,
  vector_store_id TEXT,
  usage_bytes INTEGER,
  uploaded_at INTEGER,
  updated_at INTEGER NOT NULL,
  raw_json TEXT NOT NULL,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_file_search_metadata_project_id ON file_search_metadata(project_id);
CREATE INDEX IF NOT EXISTS idx_file_search_metadata_chat_id ON file_search_metadata(chat_id);
CREATE INDEX IF NOT EXISTS idx_file_search_metadata_file_id ON file_search_metadata(file_id);
CREATE INDEX IF NOT EXISTS idx_file_search_metadata_vector_store_id ON file_search_metadata(vector_store_id);
CREATE INDEX IF NOT EXISTS idx_file_search_metadata_kind ON file_search_metadata(kind);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  type TEXT DEFAULT 'string',
  updated_at INTEGER NOT NULL
);
`

class DbManager {
  private db: Database.Database | null = null

  private getDbPath(): string {
    return path.join(app.getPath('userData'), DB_FILENAME)
  }

  private getLegacyDbPath(): string {
    return path.join(app.getPath('userData'), LEGACY_DB_FILENAME)
  }

  private ensureDbPath(): string {
    const dbPath = this.getDbPath()
    const legacyPath = this.getLegacyDbPath()
    if (!fs.existsSync(dbPath) && fs.existsSync(legacyPath)) {
      try {
        fs.copyFileSync(legacyPath, dbPath)
        console.log(`[DB] Migrated legacy DB: ${LEGACY_DB_FILENAME} → ${DB_FILENAME}`)
      } catch (error) {
        console.warn('[DB] Legacy DB migration failed:', error)
      }
    }
    return dbPath
  }

  private getSchemaPath(): string {
    const __dirname = path.dirname(fileURLToPath(import.meta.url))
    return path.join(__dirname, '../db/schema.sql')
  }

  private loadSchemaSql(): string {
    const schemaPath = this.getSchemaPath()
    try {
      return fs.readFileSync(schemaPath, 'utf-8')
    } catch {
      return DEFAULT_SCHEMA_SQL
    }
  }

  private applyPragmas(db: Database.Database): void {
    db.pragma('foreign_keys = ON')
    db.pragma('journal_mode = WAL')
    db.pragma('synchronous = NORMAL')
  }

  private ensureSchema(db: Database.Database): void {
    const table = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
      .get()
    if (!table) {
      db.exec(this.loadSchemaSql())
      return
    }

    const row = db.prepare('SELECT version FROM schema_version WHERE id = 1').get() as
      | { version?: number }
      | undefined
    if (!row || typeof row.version !== 'number') {
      db.prepare('INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)').run(
        SCHEMA_VERSION
      )
      return
    }

    if (row.version !== SCHEMA_VERSION) {
      console.warn(
        `[DB] schema_version mismatch: expected=${SCHEMA_VERSION}, actual=${row.version}`
      )
    }

    // files 테이블에 text_length 컬럼이 없으면 추가 (기존 DB 호환)
    try {
      const cols = db.pragma('table_info(files)') as Array<{ name: string }>
      if (!cols.some(c => c.name === 'text_length')) {
        db.exec('ALTER TABLE files ADD COLUMN text_length INTEGER')
        console.log('[DB] Added text_length column to files table')
      }
    } catch (e) {
      console.warn('[DB] Failed to add text_length column:', e)
    }
  }

  open(): Database.Database {
    if (this.db) return this.db
    const dbPath = this.ensureDbPath()
    fs.mkdirSync(path.dirname(dbPath), { recursive: true })
    this.db = new Database(dbPath)
    this.applyPragmas(this.db)
    this.ensureSchema(this.db)
    return this.db
  }

  get(): Database.Database {
    if (!this.db) {
      throw new Error('DB not initialized. Call dbManager.open() first.')
    }
    return this.db
  }

  close(): void {
    if (!this.db) return
    this.db.close()
    this.db = null
  }
}

export const dbManager = new DbManager()

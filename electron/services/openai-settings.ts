import { dbManager } from './db-manager'

// 베타: src/config/beta.ts 와 동기. true 일 때 connectionMode 'codex' 강제 (DB 무시).
const BETA_CODEX_ONLY = true

export type OpenAiSettings = {
  apiKey: string | null
  defaultModel: string
  fileSearchModel: string
  embeddingModel: string
  connectionMode: 'api' | 'codex'
}

const DEFAULT_MODEL = 'gpt-5.1'
const DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small'

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

export const getOpenAiSettings = (): OpenAiSettings => {
  const defaults: OpenAiSettings = {
    apiKey: null,
    defaultModel: DEFAULT_MODEL,
    fileSearchModel: DEFAULT_MODEL,
    embeddingModel: DEFAULT_EMBEDDING_MODEL,
    connectionMode: BETA_CODEX_ONLY ? 'codex' : 'api',
  }

  try {
    const db = dbManager.open()
    const rows = db
      .prepare(
        "SELECT key, value, type FROM settings WHERE key IN ('openai_api_key','openai_default_model','openai_embedding_model','connection_mode')"
      )
      .all() as { key: string; value: string | null; type: string | null }[]

    const settings: Record<string, unknown> = {}
    for (const row of rows) {
      settings[row.key] = parseSettingValue(row.value, row.type)
    }

    const defaultModel = typeof settings.openai_default_model === 'string'
      ? settings.openai_default_model
      : DEFAULT_MODEL
    const embeddingModel = typeof settings.openai_embedding_model === 'string'
      ? settings.openai_embedding_model
      : DEFAULT_EMBEDDING_MODEL

    return {
      apiKey: typeof settings.openai_api_key === 'string' ? settings.openai_api_key : null,
      defaultModel,
      fileSearchModel: defaultModel,
      embeddingModel,
      connectionMode: BETA_CODEX_ONLY ? 'codex' : (settings.connection_mode === 'codex' ? 'codex' : 'api'),
    }
  } catch {
    return defaults
  }
}

export const requireOpenAiKey = (): string => {
  const settings = getOpenAiSettings()
  if (settings.apiKey && settings.apiKey.trim()) {
    return settings.apiKey.trim()
  }
  throw new Error('OPENAI_API_KEY_REQUIRED')
}

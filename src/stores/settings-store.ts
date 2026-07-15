import { create } from 'zustand'
import openAiModels from '../config/openai-models.json'
import { CODEX_ONLY_MODE } from '../config/release'

export interface UsageRecord {
  id: string
  date: number
  tokensUsed: number
  apiCalls: number
  feature: string
  costUsd?: number
  eventType?: string  // 'chat', 'rag_indexing', etc.
  eventData?: Record<string, unknown>  // Additional event-specific data
}

interface SettingsState {
  // Text settings
  textSize: number

  // Usage tracking - Prepaid Balance
  usageHistory: UsageRecord[]
  prepaidInitialAmount: number  // 초기 충전 금액
  prepaidBalance: number  // 현재 잔액
  rechargeDate: number | null  // 충전 날짜 (타임스탬프)

  // OpenAI settings
  openaiApiKey: string | null
  openaiDefaultModel: string
  openaiEmbeddingModel: string
  connectionMode: 'api' | 'codex'

  // Account information
  email: string | null

  // Prompt customization
  promptCustomEnabled: boolean
  promptCustomRules: string
  promptFullOverride: string

  // Diagnostic data consent
  diagnosticConsent: boolean

  // Actions - Text Size
  setTextSize: (size: number) => void

  // Actions - Usage
  addUsageRecord: (record: Omit<UsageRecord, 'id'>) => void
  clearUsageHistory: () => void
  rechargeBalance: (amount: number) => void  // 새로 충전
  deductBalance: (cost: number) => void  // 사용량 차감
  setPrepaidInitialAmount: (amount: number) => void
  resetPrepaidBalance: () => void  // 충전 금액 초기화 (모두 리셋)
  resetUsageOnly: () => void  // 사용량만 리셋 (잔액 복구, 기록 유지)

  // Actions - OpenAI
  setOpenaiApiKey: (apiKey: string) => void
  clearOpenaiApiKey: () => void
  setOpenaiDefaultModel: (model: string) => void
  setOpenaiEmbeddingModel: (model: string) => void
  setConnectionMode: (mode: 'api' | 'codex') => void

  // Actions - Prompt
  setPromptCustomEnabled: (enabled: boolean) => void
  setPromptCustomRules: (rules: string) => void
  setPromptFullOverride: (prompt: string) => void

  // Actions - Diagnostic consent
  setDiagnosticConsent: (consented: boolean) => void

  // Actions - Account
  setEmail: (email: string | null) => void

  // Getters
  getTotalTokensUsed: () => number
  getTotalApiCalls: () => number
  getUsageByDateRange: (startDate: number, endDate: number) => UsageRecord[]

  // Persistence
  hasHydrated: boolean
  hydrateFromDb: () => Promise<void>
  dismissNotification: (notificationId: string) => void
}

const generateId = () => Math.random().toString(36).substring(2, 15)

const saveSetting = (key: string, value: unknown, type?: string) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  void api.invoke('settings:set', { key, value, type })
}

const deleteSetting = (key: string) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  void api.invoke('settings:delete', { key })
}

const applyTextSize = (size: number) => {
  if (typeof document === 'undefined') return
  document.documentElement.style.setProperty('--app-font-size', `${size}px`)
  document.documentElement.style.fontSize = `${size}px`
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  // Initial state
  textSize: 16,
  usageHistory: [],
  prepaidInitialAmount: 0,  // 초기 충전 금액
  prepaidBalance: 0,  // 현재 잔액
  rechargeDate: null,  // 초기값: null (충전 전)
  openaiApiKey: null,
  openaiDefaultModel: openAiModels.defaultModel || 'gpt-5.1',
  openaiEmbeddingModel: openAiModels.defaultEmbeddingModel || 'text-embedding-3-small',
  connectionMode: CODEX_ONLY_MODE ? 'codex' : 'api',
  email: null,
  promptCustomEnabled: false,
  promptCustomRules: '',
  promptFullOverride: '',
  diagnosticConsent: false,
  hasHydrated: false,

  // Text size actions
  setTextSize: (size) => {
    const validSize = Math.max(12, Math.min(24, size))
    set({ textSize: validSize })
    applyTextSize(validSize)
    saveSetting('text_size', validSize, 'number')
  },

  // Usage actions
  addUsageRecord: (record) => {
    const newRecord: UsageRecord = {
      ...record,
      id: generateId(),
    }

    set((state) => {
      const nextHistory = [...state.usageHistory, newRecord]
      saveSetting('usage_history', nextHistory, 'json')
      return { usageHistory: nextHistory }
    })
  },

  clearUsageHistory: () => {
    set({ usageHistory: [] })
    saveSetting('usage_history', [], 'json')
  },

  rechargeBalance: (amount) => {
    const normalized = Number.isFinite(amount) ? Math.max(0, amount) : 0
    const now = Date.now()
    set({
      prepaidInitialAmount: normalized,
      prepaidBalance: normalized,
      rechargeDate: now
    })
    saveSetting('prepaid_initial_amount', normalized, 'number')
    saveSetting('prepaid_balance', normalized, 'number')
    saveSetting('recharge_date', now, 'number')
  },

  deductBalance: (cost) => {
    const normalized = Number.isFinite(cost) ? Math.max(0, cost) : 0
    set((state) => {
      const newBalance = Math.max(0, state.prepaidBalance - normalized)
      saveSetting('prepaid_balance', newBalance, 'number')
      return { prepaidBalance: newBalance }
    })
  },

  setPrepaidInitialAmount: (amount) => {
    const normalized = Number.isFinite(amount) ? Math.max(0, amount) : 0
    set({ prepaidInitialAmount: normalized })
    saveSetting('prepaid_initial_amount', normalized, 'number')
  },

  resetPrepaidBalance: () => {
    set({
      prepaidInitialAmount: 0,
      prepaidBalance: 0,
      rechargeDate: null
    })
    saveSetting('prepaid_initial_amount', 0, 'number')
    saveSetting('prepaid_balance', 0, 'number')
    deleteSetting('recharge_date')
  },

  resetUsageOnly: () => {
    set((state) => {
      const resetBalance = state.prepaidInitialAmount
      saveSetting('prepaid_balance', resetBalance, 'number')
      return { prepaidBalance: resetBalance }
    })
  },

  // OpenAI actions
  setOpenaiApiKey: (apiKey) => {
    const trimmed = apiKey.trim()
    set({ openaiApiKey: trimmed })
    saveSetting('openai_api_key', trimmed, 'string')
  },

  clearOpenaiApiKey: () => {
    set({ openaiApiKey: null })
    deleteSetting('openai_api_key')
  },

  setOpenaiDefaultModel: (model) => {
    const trimmed = model.trim()
    if (!trimmed) return
    set({ openaiDefaultModel: trimmed })
    saveSetting('openai_default_model', trimmed, 'string')
  },

  setOpenaiEmbeddingModel: (model) => {
    const trimmed = model.trim()
    if (!trimmed) return
    set({ openaiEmbeddingModel: trimmed })
    saveSetting('openai_embedding_model', trimmed, 'string')
  },

  setConnectionMode: (mode) => {
    set({ connectionMode: mode })
    saveSetting('connection_mode', mode, 'string')
  },

  // Prompt actions
  setPromptCustomEnabled: (enabled) => {
    set({ promptCustomEnabled: enabled })
    saveSetting('prompt_custom_enabled', enabled, 'boolean')
  },

  setPromptCustomRules: (rules) => {
    set({ promptCustomRules: rules })
    saveSetting('prompt_custom_rules', rules, 'string')
  },

  setPromptFullOverride: (prompt) => {
    set({ promptFullOverride: prompt })
    saveSetting('prompt_full_override', prompt, 'string')
  },

  // Diagnostic consent actions
  setDiagnosticConsent: (consented) => {
    set({ diagnosticConsent: consented })
    saveSetting('diagnostic_consent', consented, 'boolean')
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    api?.consent?.set?.(consented)
  },

  // Account actions
  setEmail: (email) => {
    set({ email })
    saveSetting('email', email, 'string')
  },

  // Getters
  getTotalTokensUsed: () => {
    return get().usageHistory.reduce(
      (total, record) => total + record.tokensUsed,
      0
    )
  },

  getTotalApiCalls: () => {
    return get().usageHistory.reduce(
      (total, record) => total + record.apiCalls,
      0
    )
  },

  getUsageByDateRange: (startDate, endDate) => {
    return get().usageHistory.filter(
      (record) => record.date >= startDate && record.date <= endDate
    )
  },

  hydrateFromDb: async () => {
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    if (!api?.invoke) return
    const result = await api.invoke('settings:getAll')
    if (!result?.success) {
      console.warn('[SettingsStore] Failed to load settings from DB:', result)
      return
    }
    const settings = result.data?.settings ?? {}

    set({
      textSize: typeof settings.text_size === 'number' ? settings.text_size : 16,
      usageHistory: Array.isArray(settings.usage_history) ? settings.usage_history : [],
      email: typeof settings.email === 'string' ? settings.email : null,
      prepaidInitialAmount: typeof settings.prepaid_initial_amount === 'number' ? settings.prepaid_initial_amount : 0,
      prepaidBalance: typeof settings.prepaid_balance === 'number' ? settings.prepaid_balance : 0,
      rechargeDate: typeof settings.recharge_date === 'number' ? settings.recharge_date : null,
      openaiApiKey: typeof settings.openai_api_key === 'string'
        ? settings.openai_api_key
        : null,
      openaiDefaultModel: typeof settings.openai_default_model === 'string'
        ? settings.openai_default_model
        : (openAiModels.defaultModel || 'gpt-5.1'),
      openaiEmbeddingModel: typeof settings.openai_embedding_model === 'string'
        ? settings.openai_embedding_model
        : (openAiModels.defaultEmbeddingModel || 'text-embedding-3-small'),
      connectionMode: CODEX_ONLY_MODE
        ? 'codex' as const
        : (settings.connection_mode === 'codex' ? 'codex' as const : 'api' as const),
      promptCustomEnabled: typeof settings.prompt_custom_enabled === 'boolean'
        ? settings.prompt_custom_enabled
        : Boolean(settings.prompt_custom_enabled),
      promptCustomRules: typeof settings.prompt_custom_rules === 'string'
        ? settings.prompt_custom_rules
        : '',
      promptFullOverride: typeof settings.prompt_full_override === 'string'
        ? settings.prompt_full_override
        : '',
      diagnosticConsent: typeof settings.diagnostic_consent === 'boolean'
        ? settings.diagnostic_consent
        : Boolean(settings.diagnostic_consent),
      hasHydrated: true,
    })
    const appliedSize = typeof settings.text_size === 'number' ? settings.text_size : 16
    applyTextSize(appliedSize)
  },

  dismissNotification: (notificationId) => {
    saveSetting(`dismissed_notification_${notificationId}`, 1, 'boolean')
  },
}))

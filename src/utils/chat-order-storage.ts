const UNASSIGNED_KEY = 'chat_order_unassigned'
const PROJECT_KEY = 'chat_order_by_project'

type ChatOrderState = {
  unassigned: string[]
  byProject: Record<string, string[]>
}

let cached: ChatOrderState = { unassigned: [], byProject: {} }
let loaded = false

const normalizeList = (list: string[]): string[] => {
  const seen = new Set<string>()
  const result: string[] = []
  for (const id of list) {
    if (!id || seen.has(id)) continue
    seen.add(id)
    result.push(id)
  }
  return result
}

const saveSetting = async (key: string, value: unknown) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  await api.invoke('settings:set', { key, value, type: 'json' })
}

const loadSettings = async (): Promise<ChatOrderState> => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return { unassigned: [], byProject: {} }
  const result = await api.invoke('settings:getAll')
  if (!result?.success) return { unassigned: [], byProject: {} }
  const settings = result.data?.settings ?? {}

  const unassigned = Array.isArray(settings[UNASSIGNED_KEY])
    ? settings[UNASSIGNED_KEY]
    : []
  const byProject =
    settings[PROJECT_KEY] && typeof settings[PROJECT_KEY] === 'object'
      ? settings[PROJECT_KEY]
      : {}

  return {
    unassigned: normalizeList(unassigned),
    byProject: Object.fromEntries(
      Object.entries(byProject).map(([projectId, list]) => [
        projectId,
        Array.isArray(list) ? normalizeList(list) : []
      ])
    )
  }
}

export const loadChatOrderSettings = async () => {
  if (loaded) return cached
  cached = await loadSettings()
  loaded = true
  return cached
}

export const getUnassignedChatOrder = () => cached.unassigned

export const getProjectChatOrder = (projectId: string) => cached.byProject[projectId] ?? []

export const setUnassignedChatOrder = async (order: string[]) => {
  cached.unassigned = normalizeList(order)
  loaded = true
  await saveSetting(UNASSIGNED_KEY, cached.unassigned)
}

export const setProjectChatOrder = async (projectId: string, order: string[]) => {
  cached.byProject = {
    ...cached.byProject,
    [projectId]: normalizeList(order)
  }
  loaded = true
  await saveSetting(PROJECT_KEY, cached.byProject)
}

export const removeFromUnassignedChatOrder = async (chatId: string) => {
  if (!chatId) return
  if (!cached.unassigned.includes(chatId)) return
  cached.unassigned = cached.unassigned.filter((id) => id !== chatId)
  loaded = true
  await saveSetting(UNASSIGNED_KEY, cached.unassigned)
}

export const appendToUnassignedChatOrder = async (chatId: string) => {
  if (!chatId) return
  if (cached.unassigned.includes(chatId)) return
  cached.unassigned = [...cached.unassigned, chatId]
  loaded = true
  await saveSetting(UNASSIGNED_KEY, cached.unassigned)
}

export const removeFromProjectChatOrder = async (projectId: string, chatId: string) => {
  const current = cached.byProject[projectId]
  if (!current || current.length === 0) return
  if (!current.includes(chatId)) return
  cached.byProject = {
    ...cached.byProject,
    [projectId]: current.filter((id) => id !== chatId)
  }
  loaded = true
  await saveSetting(PROJECT_KEY, cached.byProject)
}

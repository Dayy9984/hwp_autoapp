import { create } from 'zustand'

// AI Tool types
export interface Tool {
  id: string
  name: string
  type: 'signature-stamp' | 'prompt-custom' | 'prompt-full'
  importedAt: number
}

export interface SignatureTool {
  id: string
  name: string
  imageData: string
  createdAt: number
  updatedAt: number
}

interface ToolState {
  tools: Tool[]
  signatures: SignatureTool[]

  // Tool Management Actions
  importTool: (name: string, type: Tool['type']) => string
  removeTool: (id: string) => void
  getTool: (id: string) => Tool | null
  hydrateToolsFromDb: () => Promise<void>

  // Signature/Stamp Actions
  addSignature: (name: string, imageData: string) => string
  deleteSignature: (id: string) => void
  updateSignature: (id: string, name: string, imageData?: string) => void
  getSignature: (id: string) => SignatureTool | null
}

const generateId = () => Math.random().toString(36).substring(2, 15)

const saveToolsToDb = (tools: Tool[]) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  void api.invoke('settings:set', { key: 'imported_tools', value: tools, type: 'json' })
}

export const useToolStore = create<ToolState>((set, get) => ({
  tools: [],
  signatures: [],

  importTool: (name, type) => {
    const newTool: Tool = {
      id: generateId(),
      name,
      type,
      importedAt: Date.now(),
    }

    set(state => {
      const nextTools = [...state.tools, newTool]
      saveToolsToDb(nextTools)
      return { tools: nextTools }
    })

    return newTool.id
  },

  removeTool: (id) => {
    set(state => {
      const nextTools = state.tools.filter(tool => tool.id !== id)
      saveToolsToDb(nextTools)
      return { tools: nextTools }
    })
  },

  getTool: (id) => {
    return get().tools.find(tool => tool.id === id) ?? null
  },

  hydrateToolsFromDb: async () => {
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    if (!api?.invoke) return

    try {
      const result = await api.invoke('settings:getAll')
      if (!result?.success) return

      const settings = result.data?.settings ?? {}
      const imported_tools = Array.isArray(settings.imported_tools) ? settings.imported_tools : []

      set({ tools: imported_tools })
      console.log('[ToolStore] Loaded tools from DB:', imported_tools)
    } catch (error) {
      console.error('[ToolStore] Failed to load tools from DB:', error)
    }
  },

  addSignature: (name, imageData) => {
    const newSignature: SignatureTool = {
      id: generateId(),
      name,
      imageData,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }

    set(state => ({
      signatures: [...state.signatures, newSignature],
    }))

    return newSignature.id
  },

  deleteSignature: (id) => {
    set(state => ({
      signatures: state.signatures.filter(signature => signature.id !== id),
    }))
  },

  updateSignature: (id, name, imageData) => {
    set(state => ({
      signatures: state.signatures.map(signature =>
        signature.id === id
          ? {
              ...signature,
              name,
              imageData: imageData ?? signature.imageData,
              updatedAt: Date.now(),
            }
          : signature
      ),
    }))
  },

  getSignature: (id) => {
    return get().signatures.find(signature => signature.id === id) ?? null
  },
}))

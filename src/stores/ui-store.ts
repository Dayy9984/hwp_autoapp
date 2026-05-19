import { create } from 'zustand'

export type Theme = 'light' | 'dark'

export type ModalId =
  | 'create-folder'
  | 'add-folder-file'
  | 'add-template-pair'
  | 'chat-search'
  | 'settings'
  | 'signature-tool'
  | 'notification'
  | 'tools-import'
  | 'tool-edit'
  | 'prompt-custom'
  | 'prompt-full'
  | null

export type PopoverId =
  | 'folder-options'
  | 'chat-options'
  | 'folder-move'
  | 'signature-options'
  | 'profile'
  | 'tool-options'
  | 'file-upload'
  | null

export type ViewType = 'chat' | 'folder' | 'tools-import'

const MAX_NAVIGATION_HISTORY = 20

interface UIState {
  // Theme state
  theme: Theme

  // Modal state
  activeModal: ModalId
  modalData: Record<string, unknown> | null

  // Popover state
  activePopover: PopoverId
  popoverData: Record<string, unknown> | null

  // Sidebar state
  sidebarCollapsed: boolean

  // Current view
  currentView: ViewType

  // Navigation history
  navigationHistory: ViewType[]

  // Selected folder
  selectedFolderId: string | null

  // Expanded folders (sidebar)
  expandedFolders: string[]

  // Actions - Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void

  // Actions - Modal
  openModal: (modalId: Exclude<ModalId, null>, data?: Record<string, unknown>) => void
  closeModal: () => void

  // Actions - Popover
  openPopover: (popoverId: Exclude<PopoverId, null>, data?: Record<string, unknown>) => void
  closePopover: () => void

  // Actions - Sidebar
  toggleSidebar: () => void
  setSidebarCollapsed: (collapsed: boolean) => void

  // Actions - View & Navigation
  setCurrentView: (view: ViewType) => void
  navigateToView: (view: ViewType) => void
  navigateBack: () => void
  canNavigateBack: () => boolean
  clearNavigationHistory: () => void

  // Actions - Folder
  setSelectedFolder: (folderId: string | null) => void
  toggleFolderExpanded: (folderId: string) => void
  expandFolder: (folderId: string) => void
  collapseFolder: (folderId: string) => void

  // Getters
  isModalOpen: (modalId: Exclude<ModalId, null>) => boolean
  isPopoverOpen: (popoverId: Exclude<PopoverId, null>) => boolean

  // Persistence
  hydrateFromDb: () => Promise<void>
}

const saveSetting = (key: string, value: unknown, type?: string) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  void api.invoke('settings:set', { key, value, type })
}

export const useUIStore = create<UIState>()((set, get) => ({
  // Initial state
  theme: 'light',
  activeModal: null,
  modalData: null,
  activePopover: null,
  popoverData: null,
  sidebarCollapsed: false,
  currentView: 'chat',
  navigationHistory: [],
  selectedFolderId: null,
  expandedFolders: [],

  // Theme actions
  setTheme: (theme) => {
    set({ theme })
    saveSetting('ui_theme', theme, 'string')
    if (typeof document !== 'undefined') {
      if (theme === 'dark') {
        document.body.classList.add('dark')
      } else {
        document.body.classList.remove('dark')
      }
    }
  },

  toggleTheme: () => {
    const currentTheme = get().theme
    const newTheme = currentTheme === 'light' ? 'dark' : 'light'
    get().setTheme(newTheme)
  },

  // Modal actions
  openModal: (modalId, data) => {
    set({
      activeModal: modalId,
      modalData: data ?? null,
    })
  },

  closeModal: () => {
    set({
      activeModal: null,
      modalData: null,
    })
  },

  // Popover actions
  openPopover: (popoverId, data) => {
    set({
      activePopover: popoverId,
      popoverData: data ?? null,
    })
  },

  closePopover: () => {
    set({
      activePopover: null,
      popoverData: null,
    })
  },

  // Sidebar actions
  toggleSidebar: () => {
    set((state) => {
      const next = !state.sidebarCollapsed
      saveSetting('sidebar_collapsed', next ? 1 : 0, 'boolean')
      return { sidebarCollapsed: next }
    })
  },

  setSidebarCollapsed: (collapsed) => {
    set({ sidebarCollapsed: collapsed })
    saveSetting('sidebar_collapsed', collapsed ? 1 : 0, 'boolean')
  },

  // View & Navigation actions
  setCurrentView: (view) => {
    set({ currentView: view })
  },

  navigateToView: (view) => {
    const state = get()
    const currentView = state.currentView

    if (currentView === view) {
      return
    }

    const newHistory = [...state.navigationHistory, currentView]
    const limitedHistory = newHistory.slice(-MAX_NAVIGATION_HISTORY)

    set({
      currentView: view,
      navigationHistory: limitedHistory,
    })
  },

  navigateBack: () => {
    const state = get()
    const history = state.navigationHistory

    if (history.length === 0) {
      return
    }

    const newHistory = [...history]
    const previousView = newHistory.pop()

    if (previousView) {
      set({
        currentView: previousView,
        navigationHistory: newHistory,
      })
    }
  },

  canNavigateBack: () => {
    return get().navigationHistory.length > 0
  },

  clearNavigationHistory: () => {
    set({ navigationHistory: [] })
  },

  // Actions - Folder
  setSelectedFolder: (folderId) => {
    set({ selectedFolderId: folderId })
  },

  toggleFolderExpanded: (folderId) => {
    set((state) => {
      const isExpanded = state.expandedFolders.includes(folderId)
      return {
        expandedFolders: isExpanded
          ? state.expandedFolders.filter((id) => id !== folderId)
          : [...state.expandedFolders, folderId],
      }
    })
  },

  expandFolder: (folderId) => {
    set((state) => ({
      expandedFolders: [...new Set([...state.expandedFolders, folderId])],
    }))
  },

  collapseFolder: (folderId) => {
    set((state) => ({
      expandedFolders: state.expandedFolders.filter((id) => id !== folderId),
    }))
  },

  // Getters
  isModalOpen: (modalId) => {
    return get().activeModal === modalId
  },

  isPopoverOpen: (popoverId) => {
    return get().activePopover === popoverId
  },

  hydrateFromDb: async () => {
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    if (!api?.invoke) return
    const result = await api.invoke('settings:getAll')
    if (!result?.success) return
    const settings = result.data?.settings ?? {}
    const theme = settings.ui_theme === 'dark' ? 'dark' : 'light'
    const sidebarCollapsed =
      typeof settings.sidebar_collapsed === 'boolean'
        ? settings.sidebar_collapsed
        : Boolean(settings.sidebar_collapsed)

    set({
      theme,
      sidebarCollapsed,
    })
    if (typeof document !== 'undefined') {
      if (theme === 'dark') {
        document.body.classList.add('dark')
      } else {
        document.body.classList.remove('dark')
      }
    }
  },
}))


import { create } from 'zustand'
import {
  appendToUnassignedChatOrder,
  removeFromProjectChatOrder,
  removeFromUnassignedChatOrder,
  setProjectChatOrder
} from '../utils/chat-order-storage'

// ===== 타입 정의 =====

export interface IndexStatus {
  status: 'pending' | 'indexing' | 'ready' | 'failed'
  message?: string
  progress?: number
}

export interface TemplatePair {
  id: string
  templatePath: string
  referencePath: string
  templateName: string
  referenceName: string
  diffPath?: string
  extractStatus: IndexStatus
  indexStatus: IndexStatus
  createdAt: number
}

export interface ProjectFile {
  id: string
  path: string
  name: string
  type: string
  size: number
  indexStatus: IndexStatus
  createdAt: number
}

export interface ChatFile {
  id: string
  path: string
  name: string
  type: string
  size: number
  indexStatus: IndexStatus
  addedAt: number
}

export interface FolderFile {
  id: string
  chatId: string
  addedAt: number
}

export interface Folder {
  id: string
  name: string
  files: FolderFile[]
  templatePairs: TemplatePair[]
  projectFiles: ProjectFile[]
  createdAt: number
  updatedAt: number
}

// ===== Store 인터페이스 =====

interface FolderState {
  folders: Folder[]
  chatFiles: Record<string, ChatFile[]>

  // CRUD Actions
  createFolder: (name: string) => Promise<string | null>
  updateFolder: (id: string, name: string) => void
  deleteFolder: (id: string) => void
  restoreFolder: (folder: Folder) => void

  // Chat-Folder Linking
  moveChatToFolder: (chatId: string, folderId: string) => void
  getChatsByFolder: (folderId: string) => string[]
  getChatFolder: (chatId: string) => Folder | null
  reorderChatInFolder: (
    folderId: string,
    chatId: string,
    targetChatId: string,
    position?: 'before' | 'after',
    persist?: boolean
  ) => void

  // Folder File Management
  addFileToFolder: (folderId: string, chatId: string) => void
  removeFileFromFolder: (folderId: string, chatId: string) => void

  // Template Pair 관리
  addTemplatePair: (folderId: string, pair: TemplatePair) => void
  removeTemplatePair: (folderId: string, pairId: string) => void
  updatePairExtractStatus: (folderId: string, pairId: string, status: IndexStatus) => void
  updatePairIndexStatus: (folderId: string, pairId: string, status: IndexStatus) => void
  updatePairDiffPath: (folderId: string, pairId: string, diffPath: string) => void

  // Project File 관리
  addProjectFile: (folderId: string, file: ProjectFile) => void
  removeProjectFile: (folderId: string, fileId: string) => void
  updateFileIndexStatus: (folderId: string, fileId: string, status: IndexStatus) => void

  // Chat File 관리
  addChatFile: (chatId: string, file: ChatFile) => void
  removeChatFile: (chatId: string, fileId: string) => void
  getChatFiles: (chatId: string) => ChatFile[]
  updateChatFileIndexStatus: (chatId: string, fileId: string, status: IndexStatus) => void
  restoreChatFiles: (chatId: string, files: ChatFile[]) => void

  // Getters
  getFolder: (id: string) => Folder | null
}

// ===== ID 생성 =====

const callApi = (channel: string, args: Record<string, unknown>) => {
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined
  if (!api?.invoke) return
  void api.invoke(channel, args).catch((error) => {
    console.error(`[FolderStore] ${channel} failed:`, error)
  })
}

const buildFolderFromProject = (project: any): Folder => {
  const projectName = typeof project?.name === 'string' ? project.name : 'Unnamed'
  const createdAt = typeof project?.createdAt === 'number' ? project.createdAt : Date.now()
  const updatedAt = typeof project?.updatedAt === 'number' ? project.updatedAt : Date.now()

  return {
    id: project.id,
    name: projectName,
    files: (project.chatBindings || []).map((chatId: string) => ({
      id: chatId,
      chatId,
      addedAt: createdAt,
    })),
    templatePairs: project.templatePairs || [],
    projectFiles: (project.files || []).map((file: any) => ({
      id: file.id,
      path: file.path ?? file.relPath ?? '',
      name: file.name,
      type: file.type ?? 'reference',
      size: file.size ?? 0,
      indexStatus: file.indexStatus,
      createdAt: file.addedAt ?? Date.now(),
    })),
    createdAt,
    updatedAt,
  }
}

// ===== Store 구현 =====

export const useFolderStore = create<FolderState>((set, get) => ({
  folders: [],
  chatFiles: {},

  // ===== CRUD Actions =====

  createFolder: async (name) => {
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    if (!api?.invoke) return null

    try {
      const result = await api.invoke('project:create', name)
      const project = result?.data?.project
      if (!result?.success || !project) {
        return null
      }
      const folderData = buildFolderFromProject(project)
      get().restoreFolder(folderData)
      return folderData.id
    } catch (error) {
      console.error('[FolderStore] project:create failed:', error)
      return null
    }
  },

  updateFolder: (id, name) => {
    set(state => ({
      folders: state.folders.map(folder =>
        folder.id === id
          ? { ...folder, name, updatedAt: Date.now() }
          : folder
      ),
    }))
  },

  deleteFolder: (id) => {
    set(state => ({
      folders: state.folders.filter(folder => folder.id !== id),
    }))
  },

  restoreFolder: (folder) => {
    set(state => {
      const exists = state.folders.find(f => f.id === folder.id)
      if (exists) {
        return {
          folders: state.folders.map(f =>
            f.id === folder.id ? folder : f
          )
        }
      }
      return {
        folders: [...state.folders, folder]
      }
    })
  },

  // ===== Chat-Folder Linking =====

  moveChatToFolder: (chatId, folderId) => {
    const state = get()

    // Remove chat from all folders first
    const foldersWithoutChat = state.folders.map(folder => ({
      ...folder,
      files: folder.files.filter(file => file.chatId !== chatId),
    }))

    // Add chat to target folder
    const nextFolders = foldersWithoutChat.map(folder =>
      folder.id === folderId
        ? {
            ...folder,
            files: [
              ...folder.files,
              {
                id: chatId,
                chatId,
                addedAt: Date.now(),
              },
            ],
            updatedAt: Date.now(),
          }
        : folder
    )
    set({ folders: nextFolders })

    callApi('chatFiles:bindProject', { chatId, projectId: folderId })

    void removeFromUnassignedChatOrder(chatId)
    const targetFolder = nextFolders.find((folder) => folder.id === folderId)
    if (targetFolder) {
      void setProjectChatOrder(folderId, targetFolder.files.map((file) => file.chatId))
    }
  },

  getChatsByFolder: (folderId) => {
    const folder = get().folders.find(f => f.id === folderId)
    return folder ? folder.files.map(file => file.chatId) : []
  },

  getChatFolder: (chatId) => {
    return get().folders.find(folder =>
      folder.files.some(file => file.chatId === chatId)
    ) ?? null
  },

  reorderChatInFolder: (folderId, chatId, targetChatId, position = 'before', persist = true) => {
    let nextOrder: string[] | null = null
    set((state) => {
      const folder = state.folders.find((f) => f.id === folderId)
      if (!folder) return state
      if (chatId === targetChatId) return state

      const fromIndex = folder.files.findIndex((file) => file.chatId === chatId)
      const targetIndex = folder.files.findIndex((file) => file.chatId === targetChatId)
      if (fromIndex < 0 || targetIndex < 0) return state

      const nextFiles = [...folder.files]
      const [moved] = nextFiles.splice(fromIndex, 1)
      let insertIndex = position === 'after' ? targetIndex + 1 : targetIndex
      if (fromIndex < insertIndex) insertIndex -= 1
      if (insertIndex < 0) insertIndex = 0
      if (insertIndex > nextFiles.length) insertIndex = nextFiles.length
      nextFiles.splice(insertIndex, 0, moved)
      nextOrder = nextFiles.map((file) => file.chatId)

      return {
        folders: state.folders.map((f) =>
          f.id === folderId
            ? { ...f, files: nextFiles, updatedAt: Date.now() }
            : f
        ),
      }
    })

    if (persist && nextOrder) {
      void setProjectChatOrder(folderId, nextOrder)
    }
  },

  // ===== Folder File Management =====

  addFileToFolder: (folderId, chatId) => {
    get().moveChatToFolder(chatId, folderId)
  },

  removeFileFromFolder: (folderId, chatId) => {
    let nextFolders: Folder[] = []
    set(state => {
      nextFolders = state.folders.map(folder =>
        folder.id === folderId
          ? {
              ...folder,
              files: folder.files.filter(file => file.chatId !== chatId),
              updatedAt: Date.now(),
            }
          : folder
      )
      return { folders: nextFolders }
    })

    callApi('chatFiles:unbindProject', { chatId })

    const targetFolder = nextFolders.find((folder) => folder.id === folderId)
    if (targetFolder) {
      void setProjectChatOrder(folderId, targetFolder.files.map((file) => file.chatId))
    } else {
      void removeFromProjectChatOrder(folderId, chatId)
    }
    void appendToUnassignedChatOrder(chatId)
  },

  // ===== Template Pair 관리 =====

  addTemplatePair: (folderId, pair) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? { ...f, templatePairs: [...f.templatePairs, pair], updatedAt: Date.now() }
          : f
      )
    }))
  },

  removeTemplatePair: (folderId, pairId) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? { ...f, templatePairs: f.templatePairs.filter(pair => pair.id !== pairId), updatedAt: Date.now() }
          : f
      )
    }))
  },

  updatePairExtractStatus: (folderId, pairId, status) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? {
              ...f,
              templatePairs: f.templatePairs.map(pair =>
                pair.id === pairId ? { ...pair, extractStatus: status } : pair
              ),
              updatedAt: Date.now()
            }
          : f
      )
    }))
  },

  updatePairIndexStatus: (folderId, pairId, status) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? {
              ...f,
              templatePairs: f.templatePairs.map(pair =>
                pair.id === pairId ? { ...pair, indexStatus: status } : pair
              ),
              updatedAt: Date.now()
            }
          : f
      )
    }))
  },

  updatePairDiffPath: (folderId, pairId, diffPath) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? {
              ...f,
              templatePairs: f.templatePairs.map(pair =>
                pair.id === pairId ? { ...pair, diffPath } : pair
              ),
              updatedAt: Date.now()
            }
          : f
      )
    }))
  },

  // ===== Project File 관리 =====

  addProjectFile: (folderId, file) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? { ...f, projectFiles: [...f.projectFiles, file], updatedAt: Date.now() }
          : f
      )
    }))
  },

  removeProjectFile: (folderId, fileId) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? { ...f, projectFiles: f.projectFiles.filter(file => file.id !== fileId), updatedAt: Date.now() }
          : f
      )
    }))
  },

  updateFileIndexStatus: (folderId, fileId, status) => {
    set(state => ({
      folders: state.folders.map(f =>
        f.id === folderId
          ? {
              ...f,
              projectFiles: f.projectFiles.map(file =>
                file.id === fileId ? { ...file, indexStatus: status } : file
              ),
              updatedAt: Date.now()
            }
          : f
      )
    }))
  },

  // ===== Chat File 관리 =====

  addChatFile: (chatId, file) => {
    set(state => ({
      chatFiles: {
        ...state.chatFiles,
        [chatId]: [...(state.chatFiles[chatId] || []), file]
      }
    }))
  },

  removeChatFile: (chatId, fileId) => {
    set(state => ({
      chatFiles: {
        ...state.chatFiles,
        [chatId]: (state.chatFiles[chatId] || []).filter(f => f.id !== fileId)
      }
    }))
  },

  getChatFiles: (chatId) => {
    return get().chatFiles[chatId] || []
  },

  updateChatFileIndexStatus: (chatId, fileId, status) => {
    set(state => ({
      chatFiles: {
        ...state.chatFiles,
        [chatId]: (state.chatFiles[chatId] || []).map(f =>
          f.id === fileId ? { ...f, indexStatus: status } : f
        )
      }
    }))
  },

  restoreChatFiles: (chatId, files) => {
    set(state => ({
      chatFiles: {
        ...state.chatFiles,
        [chatId]: files
      }
    }))
  },

  // ===== Getters =====

  getFolder: (id) => {
    return get().folders.find(folder => folder.id === id) ?? null
  },
}))

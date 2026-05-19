import { useFolderStore } from '../stores/folder-store'
import { useChatStore } from '../stores/chat-store'
import { useUIStore } from '../stores/ui-store'

/**
 * Development utility to populate test data
 * Call this from browser console: window.populateTestData()
 */
export async function populateTestData() {
  const folderStore = useFolderStore.getState()
  const chatStore = useChatStore.getState()

  // Create test chats first
  const chatId1 = await chatStore.createChat()
  chatStore.updateChatName(chatId1, '예비창업패키지 2페이지 표 작성')
  chatStore.addMessage(chatId1, 'user', '예비창업패키지 2페이지 표를 작성해주세요.')

  const chatId2 = await chatStore.createChat()
  chatStore.updateChatName(chatId2, '사업 개요 정리')
  chatStore.addMessage(chatId2, 'user', '사업 개요를 정리해주세요.')

  const chatId3 = await chatStore.createChat()
  chatStore.updateChatName(chatId3, '제안서 작성')
  chatStore.addMessage(chatId3, 'user', '제안서를 작성해주세요.')

  const chatId4 = await chatStore.createChat()
  chatStore.updateChatName(chatId4, '재무제표 작성')
  chatStore.addMessage(chatId4, 'user', '재무제표를 작성해주세요.')

  // Create test folders
  const folder1Id = await folderStore.createFolder('사업계획서')
  if (folder1Id) {
    folderStore.addFileToFolder(folder1Id, chatId1)
    folderStore.addFileToFolder(folder1Id, chatId2)
  }

  const folder2Id = await folderStore.createFolder('제안서')
  if (folder2Id) {
    folderStore.addFileToFolder(folder2Id, chatId3)
  }

  console.log('Test data populated successfully!')
  console.log(`Created ${chatStore.chats.length} chats and ${folderStore.folders.length} folders`)

  return {
    chats: chatStore.chats,
    folders: folderStore.folders,
  }
}

/**
 * Open signature tool modal for testing
 */
export function openSignatureModal() {
  const uiStore = useUIStore.getState()
  uiStore.openModal('signature-tool')
  console.log('Signature tool modal opened!')
}

/**
 * Clear all test data
 */
export async function clearTestData() {
  const folderStore = useFolderStore.getState()
  const chatStore = useChatStore.getState()
  const api = typeof window !== 'undefined' ? window.electronAPI : undefined

  // Delete all folders
  for (const folder of folderStore.folders) {
    if (api?.invoke) {
      try {
        await api.invoke('project:delete', folder.id)
      } catch (error) {
        console.error('Failed to delete project:', error)
      }
    }
    folderStore.deleteFolder(folder.id)
  }

  // Delete all chats
  chatStore.chats.forEach(chat => {
    chatStore.deleteChat(chat.id)
  })

  console.log('All test data cleared!')
}

// Expose to window for browser console access
if (typeof window !== 'undefined') {
  ;(window as any).populateTestData = populateTestData
  ;(window as any).clearTestData = clearTestData
  ;(window as any).openSignatureModal = openSignatureModal
}

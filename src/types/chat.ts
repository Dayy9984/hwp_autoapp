/**
 * Chat 관련 타입 정의
 * Backend manifest-manager.ts와 동기화
 */

import { IndexStatus } from './project'

export interface ChatFile {
  id: string
  chatId: string
  name: string
  path: string
  originalPath: string
  extension: string
  size: number
  addedAt: number
  indexStatus: IndexStatus
}

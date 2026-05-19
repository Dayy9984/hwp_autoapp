/**
 * Project 관련 타입 정의
 * Backend manifest-manager.ts와 동기화
 */

export interface IndexStatus {
  status: 'pending' | 'indexing' | 'ready' | 'failed'
  message?: string
  progress?: number
}

// ============================================================
// Frontend Types (folder-store.ts)
// ============================================================

export interface TemplatePair {
  id: string
  templateFile: {
    name: string
    relPath: string
    extension: string
    size: number
  }
  filledFile: {
    name: string
    relPath: string
    extension: string
    size: number
  }
  createdAt: number
  extractStatus: IndexStatus
  indexStatus: IndexStatus
  diffPath?: string
}

export interface ProjectFile {
  id: string
  name: string
  path: string
  originalPath: string
  type: 'reference'
  extension: string
  size: number
  addedAt: number
  indexStatus: IndexStatus
}

export interface Project {
  id: string
  name: string
  files: ProjectFile[]
  templatePairs: TemplatePair[]
  chatBindings: string[]
  createdAt: number
  updatedAt: number
}

// ============================================================
// Backend Manifest Types
// ============================================================

export interface ManifestFileInfo {
  id: string
  name: string
  relPath: string
  ext: string
  size: number
  addedAt: number
}

export interface ManifestTemplatePair {
  id: string
  templateFile: ManifestFileInfo
  filledFile: ManifestFileInfo
  createdAt: number
}

export interface ManifestFile {
  id: string
  name: string
  relPath: string
  ext: string
  size: number
  addedAt: number
  indexStatus?: IndexStatus  // v6.0: RAG 인덱싱 상태 저장
}

export interface ManifestProject {
  id: string
  name: string
  createdAt: number
  updatedAt: number
  templatePairs: ManifestTemplatePair[]
  files: ManifestFile[]
  chatBindings: string[]
}

export interface Manifest {
  version: string
  projects: { [projectId: string]: ManifestProject }
  chatFiles: { [chatId: string]: { files: ManifestFile[] } }
}

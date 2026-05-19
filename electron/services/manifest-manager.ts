/**
 * Manifest Manager (DB-backed)
 * 프로젝트/파일 메타데이터를 로컬 DB로 저장/복구한다.
 */

import * as fs from 'fs'
import * as path from 'path'
import { appStorage } from './app-storage'
import { projectStorage } from './project-storage'
import { dbManager } from './db-manager'
import type {
  Manifest,
  ManifestProject,
  ManifestFile,
  ManifestTemplatePair,
  Project,
  ProjectFile,
  TemplatePair,
} from '../../src/types/project'
import type { ChatFile } from '../../src/types/chat'

type TemplatePairRow = {
  id: string
  project_id: string
  template_name: string
  template_rel_path: string
  template_size: number
  filled_name: string
  filled_rel_path: string
  filled_size: number
  created_at: number
  extract_status: string | null
  extract_status_message: string | null
  extract_status_progress: number | null
  index_status: string | null
  index_status_message: string | null
  index_status_progress: number | null
}

type FileRow = {
  id: string
  scope: 'project' | 'chat'
  project_id: string | null
  chat_id: string | null
  name: string
  rel_path: string
  extension: string | null
  size: number | null
  type: string | null
  added_at: number
  index_status: string | null
  index_status_message: string | null
  index_status_progress: number | null
  original_path: string | null
}

type ProjectRow = {
  id: string
  name: string
  created_at: number
  updated_at: number
}

class ManifestManager {
  private writeQueue: Promise<void> = Promise.resolve()

  private getDb() {
    return dbManager.open()
  }

  private toManifestPair(row: TemplatePairRow): ManifestTemplatePair & {
    extractStatus?: any
    indexStatus?: any
  } {
    const templateExt = path.extname(row.template_name || row.template_rel_path).slice(1)
    const filledExt = path.extname(row.filled_name || row.filled_rel_path).slice(1)
    const pair: ManifestTemplatePair & { extractStatus?: any; indexStatus?: any } = {
      id: row.id,
      templateFile: {
        id: row.id,
        name: row.template_name,
        relPath: row.template_rel_path,
        ext: templateExt,
        size: row.template_size,
        addedAt: row.created_at,
      },
      filledFile: {
        id: row.id,
        name: row.filled_name,
        relPath: row.filled_rel_path,
        ext: filledExt,
        size: row.filled_size,
        addedAt: row.created_at,
      },
      createdAt: row.created_at,
    }

    pair.extractStatus = {
      status: row.extract_status ?? 'pending',
      message: row.extract_status_message ?? undefined,
      progress: row.extract_status_progress ?? undefined,
    }
    pair.indexStatus = {
      status: row.index_status ?? 'pending',
      message: row.index_status_message ?? undefined,
      progress: row.index_status_progress ?? undefined,
    }
    return pair
  }

  private toManifestFile(row: FileRow): ManifestFile {
    const ext = row.extension ?? path.extname(row.name || row.rel_path).slice(1)
    return {
      id: row.id,
      name: row.name,
      relPath: row.rel_path,
      ext,
      size: row.size ?? 0,
      addedAt: row.added_at,
      indexStatus: {
        status: (row.index_status ?? 'pending') as 'pending' | 'indexing' | 'ready' | 'failed',
        message: row.index_status_message ?? undefined,
        progress: row.index_status_progress ?? undefined,
      },
    }
  }

  loadManifest(): Manifest {
    const db = this.getDb()
    const manifest: Manifest = {
      version: 'db',
      projects: {},
      chatFiles: {},
    }

    const projects = db
      .prepare('SELECT id, name, created_at, updated_at FROM projects')
      .all() as ProjectRow[]

    const templateStmt = db.prepare('SELECT * FROM template_pairs WHERE project_id = ?')
    const filesStmt = db.prepare(
      "SELECT * FROM files WHERE scope = 'project' AND project_id = ?"
    )
    const bindingsStmt = db.prepare(
      'SELECT chat_id FROM project_chat_bindings WHERE project_id = ?'
    )

    for (const project of projects) {
      const templateRows = templateStmt.all(project.id) as TemplatePairRow[]
      const fileRows = filesStmt.all(project.id) as FileRow[]
      const bindingRows = bindingsStmt.all(project.id) as { chat_id: string }[]

      const manifestProject: ManifestProject = {
        id: project.id,
        name: project.name,
        createdAt: project.created_at,
        updatedAt: project.updated_at,
        templatePairs: templateRows.map((row) => this.toManifestPair(row)),
        files: fileRows.map((row) => this.toManifestFile(row)),
        chatBindings: bindingRows.map((row) => row.chat_id),
      }

      manifest.projects[project.id] = manifestProject
    }

    const chatFileRows = db
      .prepare("SELECT * FROM files WHERE scope = 'chat'")
      .all() as FileRow[]
    for (const row of chatFileRows) {
      if (!row.chat_id) continue
      if (!manifest.chatFiles[row.chat_id]) {
        manifest.chatFiles[row.chat_id] = { files: [] }
      }
      manifest.chatFiles[row.chat_id].files.push(this.toManifestFile(row))
    }

    return manifest
  }

  private persistManifest(manifest: Manifest): void {
    const db = this.getDb()
    const projectIds = Object.keys(manifest.projects)

    const tx = db.transaction(() => {
      if (projectIds.length === 0) {
        db.prepare('DELETE FROM projects').run()
      } else {
        const placeholders = projectIds.map(() => '?').join(',')
        db.prepare(`DELETE FROM projects WHERE id NOT IN (${placeholders})`).run(...projectIds)
      }

      const projectStmt = db.prepare(
        `INSERT INTO projects (id, name, created_at, updated_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET name=excluded.name, created_at=excluded.created_at, updated_at=excluded.updated_at`
      )
      const deletePairsStmt = db.prepare('DELETE FROM template_pairs WHERE project_id = ?')
      const deleteFilesStmt = db.prepare(
        "DELETE FROM files WHERE scope = 'project' AND project_id = ?"
      )
      const deleteBindingsStmt = db.prepare(
        'DELETE FROM project_chat_bindings WHERE project_id = ?'
      )
      const insertPairStmt = db.prepare(
        `INSERT INTO template_pairs (
          id, project_id, template_name, template_rel_path, template_size,
          filled_name, filled_rel_path, filled_size, created_at,
          extract_status, extract_status_message, extract_status_progress,
          index_status, index_status_message, index_status_progress
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      const insertFileStmt = db.prepare(
        `INSERT INTO files (
          id, scope, project_id, chat_id, name, rel_path, extension, size, type,
          added_at, index_status, index_status_message, index_status_progress, original_path
        )
        VALUES (?, 'project', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      const insertBindingStmt = db.prepare(
        `INSERT INTO project_chat_bindings (project_id, chat_id, created_at)
         VALUES (?, ?, ?)`
      )

      for (const project of Object.values(manifest.projects)) {
        projectStmt.run(project.id, project.name, project.createdAt, project.updatedAt)
        deletePairsStmt.run(project.id)
        deleteFilesStmt.run(project.id)
        deleteBindingsStmt.run(project.id)

        for (const pair of project.templatePairs as Array<
          ManifestTemplatePair & { extractStatus?: any; indexStatus?: any }
        >) {
          const extractStatus = pair.extractStatus ?? { status: 'pending' }
          const indexStatus = pair.indexStatus ?? { status: 'pending' }
          insertPairStmt.run(
            pair.id,
            project.id,
            pair.templateFile.name,
            pair.templateFile.relPath,
            pair.templateFile.size,
            pair.filledFile.name,
            pair.filledFile.relPath,
            pair.filledFile.size,
            pair.createdAt,
            extractStatus.status ?? 'pending',
            extractStatus.message ?? null,
            extractStatus.progress ?? null,
            indexStatus.status ?? 'pending',
            indexStatus.message ?? null,
            indexStatus.progress ?? null
          )
        }

        for (const file of project.files) {
          insertFileStmt.run(
            file.id,
            project.id,
            file.name,
            file.relPath,
            file.ext,
            file.size,
            'reference',
            file.addedAt,
            file.indexStatus?.status ?? 'pending',
            file.indexStatus?.message ?? null,
            file.indexStatus?.progress ?? null,
            ''
          )
        }

        for (const chatId of project.chatBindings) {
          insertBindingStmt.run(project.id, chatId, Date.now())
        }
      }

      db.prepare("DELETE FROM files WHERE scope = 'chat'").run()
      const insertChatFileStmt = db.prepare(
        `INSERT INTO files (
          id, scope, project_id, chat_id, name, rel_path, extension, size, type,
          added_at, index_status, index_status_message, index_status_progress, original_path
        )
        VALUES (?, 'chat', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      for (const [chatId, entry] of Object.entries(manifest.chatFiles)) {
        for (const file of entry.files) {
          insertChatFileStmt.run(
            file.id,
            chatId,
            file.name,
            file.relPath,
            file.ext,
            file.size,
            'reference',
            file.addedAt,
            file.indexStatus?.status ?? 'pending',
            file.indexStatus?.message ?? null,
            file.indexStatus?.progress ?? null,
            ''
          )
        }
      }
    })

    tx()
  }

  async update(fn: (manifest: Manifest) => Manifest): Promise<void> {
    this.writeQueue = this.writeQueue.then(async () => {
      const manifest = this.loadManifest()
      const updated = fn(manifest)
      this.persistManifest(updated)
    })
    return this.writeQueue
  }

  async saveProject(project: Project): Promise<void> {
    const db = this.getDb()
    const tx = db.transaction(() => {
      db.prepare(
        `INSERT INTO projects (id, name, created_at, updated_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET name=excluded.name, created_at=excluded.created_at, updated_at=excluded.updated_at`
      ).run(project.id, project.name, project.createdAt, project.updatedAt)

      db.prepare('DELETE FROM template_pairs WHERE project_id = ?').run(project.id)
      db.prepare("DELETE FROM files WHERE scope = 'project' AND project_id = ?").run(project.id)
      db.prepare('DELETE FROM project_chat_bindings WHERE project_id = ?').run(project.id)

      const insertPairStmt = db.prepare(
        `INSERT INTO template_pairs (
          id, project_id, template_name, template_rel_path, template_size,
          filled_name, filled_rel_path, filled_size, created_at,
          extract_status, extract_status_message, extract_status_progress,
          index_status, index_status_message, index_status_progress
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      for (const pair of project.templatePairs) {
        insertPairStmt.run(
          pair.id,
          project.id,
          pair.templateFile.name,
          pair.templateFile.relPath,
          pair.templateFile.size,
          pair.filledFile.name,
          pair.filledFile.relPath,
          pair.filledFile.size,
          pair.createdAt,
          pair.extractStatus?.status ?? 'pending',
          pair.extractStatus?.message ?? null,
          pair.extractStatus?.progress ?? null,
          pair.indexStatus?.status ?? 'pending',
          pair.indexStatus?.message ?? null,
          pair.indexStatus?.progress ?? null
        )
      }

      const insertFileStmt = db.prepare(
        `INSERT INTO files (
          id, scope, project_id, chat_id, name, rel_path, extension, size, type,
          added_at, index_status, index_status_message, index_status_progress, original_path
        )
        VALUES (?, 'project', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      for (const file of project.files) {
        const relPath = path.relative(projectStorage.getProjectBasePath(project.id), file.path)
        insertFileStmt.run(
          file.id,
          project.id,
          file.name,
          relPath,
          file.extension,
          file.size,
          file.type,
          file.addedAt,
          file.indexStatus?.status ?? 'pending',
          file.indexStatus?.message ?? null,
          file.indexStatus?.progress ?? null,
          file.originalPath ?? ''
        )
      }

      const insertBindingStmt = db.prepare(
        `INSERT INTO project_chat_bindings (project_id, chat_id, created_at)
         VALUES (?, ?, ?)`
      )
      for (const chatId of project.chatBindings) {
        insertBindingStmt.run(project.id, chatId, Date.now())
      }
    })
    tx()
  }

  async deleteProject(projectId: string): Promise<void> {
    const db = this.getDb()
    db.prepare('DELETE FROM projects WHERE id = ?').run(projectId)
  }

  async saveChatFiles(chatId: string, files: ChatFile[]): Promise<void> {
    const db = this.getDb()
    const tx = db.transaction(() => {
      db.prepare("DELETE FROM files WHERE scope = 'chat' AND chat_id = ?").run(chatId)
      const insertChatFileStmt = db.prepare(
        `INSERT INTO files (
          id, scope, project_id, chat_id, name, rel_path, extension, size, type,
          added_at, index_status, index_status_message, index_status_progress, original_path
        )
        VALUES (?, 'chat', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      for (const file of files) {
        const relPath = path.relative(appStorage.getChatFilesBasePath(chatId), file.path)
        insertChatFileStmt.run(
          file.id,
          chatId,
          file.name,
          relPath,
          file.extension,
          file.size,
          'reference',
          file.addedAt,
          file.indexStatus?.status ?? 'pending',
          file.indexStatus?.message ?? null,
          file.indexStatus?.progress ?? null,
          file.originalPath ?? ''
        )
      }
    })
    tx()
  }

  async deleteChatFiles(chatId: string): Promise<void> {
    const db = this.getDb()
    db.prepare("DELETE FROM files WHERE scope = 'chat' AND chat_id = ?").run(chatId)
  }

  restoreProjects(): Project[] {
    const db = this.getDb()
    const projects = db
      .prepare('SELECT id, name, created_at, updated_at FROM projects')
      .all() as ProjectRow[]

    const templateStmt = db.prepare('SELECT * FROM template_pairs WHERE project_id = ?')
    const filesStmt = db.prepare(
      "SELECT * FROM files WHERE scope = 'project' AND project_id = ?"
    )
    const bindingsStmt = db.prepare(
      'SELECT chat_id FROM project_chat_bindings WHERE project_id = ?'
    )

    return projects.map((project) => {
      const basePath = projectStorage.getProjectBasePath(project.id)
      const templateRows = templateStmt.all(project.id) as TemplatePairRow[]
      const fileRows = filesStmt.all(project.id) as FileRow[]
      const bindingRows = bindingsStmt.all(project.id) as { chat_id: string }[]

      const templatePairs: TemplatePair[] = templateRows.map((row) => {
        const diffPath = projectStorage.diffExists(project.id, row.id)
          ? path.join(basePath, 'template_pairs', row.id, 'diff.json')
          : undefined
        return {
          id: row.id,
          templateFile: {
            name: row.template_name,
            relPath: row.template_rel_path,
            extension: path.extname(row.template_name || row.template_rel_path).slice(1),
            size: row.template_size,
          },
          filledFile: {
            name: row.filled_name,
            relPath: row.filled_rel_path,
            extension: path.extname(row.filled_name || row.filled_rel_path).slice(1),
            size: row.filled_size,
          },
          createdAt: row.created_at,
          extractStatus: {
            status: (row.extract_status ?? 'pending') as any,
            message: row.extract_status_message ?? undefined,
            progress: row.extract_status_progress ?? undefined,
          },
          indexStatus: {
            status: (row.index_status ?? 'pending') as any,
            message: row.index_status_message ?? undefined,
            progress: row.index_status_progress ?? undefined,
          },
          diffPath,
        }
      })

      const files: ProjectFile[] = fileRows.map((row) => ({
        id: row.id,
        name: row.name,
        path: path.join(basePath, row.rel_path),
        originalPath: row.original_path ?? '',
        type: 'reference' as const,
        extension: row.extension ?? path.extname(row.name || row.rel_path).slice(1),
        size: row.size ?? 0,
        addedAt: row.added_at,
        indexStatus: {
          status: (row.index_status ?? 'pending') as any,
          message: row.index_status_message ?? undefined,
          progress: row.index_status_progress ?? undefined,
        },
      }))

      return {
        id: project.id,
        name: project.name,
        createdAt: project.created_at,
        updatedAt: project.updated_at,
        templatePairs,
        files,
        chatBindings: bindingRows.map((row) => row.chat_id),
      }
    })
  }

  restoreChatFiles(chatId: string): ChatFile[] {
    const db = this.getDb()
    const basePath = appStorage.getChatFilesBasePath(chatId)
    const rows = db
      .prepare("SELECT * FROM files WHERE scope = 'chat' AND chat_id = ?")
      .all(chatId) as FileRow[]

    return rows.map((row) => ({
      id: row.id,
      chatId,
      name: row.name,
      path: path.join(basePath, row.rel_path),
      originalPath: row.original_path ?? '',
      extension: row.extension ?? path.extname(row.name || row.rel_path).slice(1),
      size: row.size ?? 0,
      addedAt: row.added_at,
      indexStatus: {
        status: (row.index_status ?? 'pending') as any,
        message: row.index_status_message ?? undefined,
        progress: row.index_status_progress ?? undefined,
      },
    }))
  }
}

export const manifestManager = new ManifestManager()

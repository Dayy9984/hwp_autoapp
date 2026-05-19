/**
 * Project IPC Handlers
 * 프로젝트 관리 관련 IPC 핸들러
 * 
 * 이 파일을 electron/main/index.ts의 끝부분에 import 후 호출하세요:
 * import { registerProjectHandlers } from './project-handlers'
 * registerProjectHandlers()
 */

import { ipcMain, dialog, BrowserWindow, app } from 'electron'
import * as path from 'path'
import * as fs from 'fs'
import { projectStorage } from '../services/project-storage'
import { appStorage } from '../services/app-storage'
import { keystore } from '../services/keystore'
import { dbManager } from '../services/db-manager'
import { getOpenAiSettings } from '../services/openai-settings'
import type { PythonBridge } from '../services/python-bridge'
import type { Project, ProjectFile, TemplatePair, IndexStatus } from '../../src/types/project'
import {
    ALLOWED_EXTENSIONS,
    MAX_TOTAL_PROJECT_FILES,
    MAX_TEMPLATE_PAIRS,
    isAllowedExtension,
    getMaxFileSize
} from './constants'

// ID 생성 헬퍼
const generateId = () => Math.random().toString(36).substring(2, 15)

type ProjectRow = {
    id: string
    name: string
    created_at: number
    updated_at: number
}

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

const getDb = () => dbManager.open()

const normalizeExtension = (name: string, relPath: string, extension?: string | null) => {
    if (extension && extension.length > 0) return extension
    return path.extname(name || relPath).slice(1)
}

const buildIndexStatus = (
    status: string | null,
    message: string | null,
    progress: number | null
): IndexStatus => ({
    status: (status ?? 'pending') as 'pending' | 'indexing' | 'ready' | 'failed',
    message: message ?? undefined,
    progress: progress ?? undefined,
})

const mapTemplatePairRow = (projectId: string, row: TemplatePairRow): TemplatePair => {
    const basePath = projectStorage.getProjectBasePath(projectId)
    const templateExt = normalizeExtension(row.template_name, row.template_rel_path)
    const filledExt = normalizeExtension(row.filled_name, row.filled_rel_path)
    const diffPath = projectStorage.diffExists(projectId, row.id)
        ? path.join(basePath, 'template_pairs', row.id, 'diff.json')
        : undefined

    return {
        id: row.id,
        templateFile: {
            name: row.template_name,
            relPath: row.template_rel_path,
            extension: templateExt,
            size: row.template_size,
        },
        filledFile: {
            name: row.filled_name,
            relPath: row.filled_rel_path,
            extension: filledExt,
            size: row.filled_size,
        },
        createdAt: row.created_at,
        extractStatus: buildIndexStatus(
            row.extract_status,
            row.extract_status_message,
            row.extract_status_progress
        ),
        indexStatus: buildIndexStatus(
            row.index_status,
            row.index_status_message,
            row.index_status_progress
        ),
        diffPath,
    }
}

const mapProjectFileRow = (projectId: string, row: FileRow): ProjectFile => {
    const basePath = projectStorage.getProjectBasePath(projectId)
    const extension = normalizeExtension(row.name, row.rel_path, row.extension)
    return {
        id: row.id,
        name: row.name,
        path: path.join(basePath, row.rel_path),
        originalPath: row.original_path ?? '',
        type: 'reference',
        extension,
        size: row.size ?? 0,
        addedAt: row.added_at,
        indexStatus: buildIndexStatus(
            row.index_status,
            row.index_status_message,
            row.index_status_progress
        ),
    }
}

const listProjectsFromDb = (): Project[] => {
    const db = getDb()
    const projectRows = db
        .prepare('SELECT id, name, created_at, updated_at FROM projects')
        .all() as ProjectRow[]

    const templateStmt = db.prepare('SELECT * FROM template_pairs WHERE project_id = ?')
    const filesStmt = db.prepare("SELECT * FROM files WHERE scope = 'project' AND project_id = ?")
    const bindingsStmt = db.prepare(
        'SELECT chat_id FROM project_chat_bindings WHERE project_id = ?'
    )

    return projectRows.map((project) => {
        const templateRows = templateStmt.all(project.id) as TemplatePairRow[]
        const fileRows = filesStmt.all(project.id) as FileRow[]
        const bindingRows = bindingsStmt.all(project.id) as { chat_id: string }[]

        return {
            id: project.id,
            name: project.name,
            createdAt: project.created_at,
            updatedAt: project.updated_at,
            templatePairs: templateRows.map((row) => mapTemplatePairRow(project.id, row)),
            files: fileRows.map((row) => mapProjectFileRow(project.id, row)),
            chatBindings: bindingRows.map((row) => row.chat_id),
        }
    })
}

const getProjectRow = (projectId: string): ProjectRow | undefined => {
    const db = getDb()
    return db
        .prepare('SELECT id, name, created_at, updated_at FROM projects WHERE id = ?')
        .get(projectId) as ProjectRow | undefined
}

const getProjectFileCount = (projectId: string): number => {
    const db = getDb()
    const row = db
        .prepare("SELECT COUNT(*) as count FROM files WHERE scope = 'project' AND project_id = ?")
        .get(projectId) as { count?: number } | undefined
    return row?.count ?? 0
}

const getTemplatePairCount = (projectId: string): number => {
    const db = getDb()
    const row = db
        .prepare('SELECT COUNT(*) as count FROM template_pairs WHERE project_id = ?')
        .get(projectId) as { count?: number } | undefined
    return row?.count ?? 0
}

const getProjectSlotUsage = (projectId: string): number => {
    const fileCount = getProjectFileCount(projectId)
    const pairCount = getTemplatePairCount(projectId)
    return fileCount + pairCount * 2
}

const getProjectFileRow = (projectId: string, fileId: string): FileRow | undefined => {
    const db = getDb()
    return db
        .prepare("SELECT * FROM files WHERE scope = 'project' AND project_id = ? AND id = ?")
        .get(projectId, fileId) as FileRow | undefined
}

const deleteProjectFileRow = (projectId: string, fileId: string): void => {
    const db = getDb()
    db.prepare("DELETE FROM files WHERE scope = 'project' AND project_id = ? AND id = ?")
        .run(projectId, fileId)
}

const setProjectFileIndexStatus = (
    projectId: string,
    fileId: string,
    status: 'pending' | 'indexing' | 'ready' | 'failed'
): void => {
    const db = getDb()
    db.prepare(
        `UPDATE files
         SET index_status = ?, index_status_message = NULL, index_status_progress = NULL
         WHERE scope = 'project' AND project_id = ? AND id = ?`
    ).run(status, projectId, fileId)
}

const setTemplatePairIndexStatus = (
    projectId: string,
    pairId: string,
    status: 'pending' | 'indexing' | 'ready' | 'failed'
): void => {
    const db = getDb()
    db.prepare(
        `UPDATE template_pairs
         SET index_status = ?, index_status_message = NULL, index_status_progress = NULL
         WHERE project_id = ? AND id = ?`
    ).run(status, projectId, pairId)
}

const deleteTemplatePairRow = (projectId: string, pairId: string): void => {
    const db = getDb()
    db.prepare('DELETE FROM template_pairs WHERE project_id = ? AND id = ?')
        .run(projectId, pairId)
}

export function registerProjectHandlers(
    getWin: () => BrowserWindow | null,
    getPythonBridge: () => PythonBridge | null,
    getFileReaderBridge: () => PythonBridge | null = () => null
) {

    // ⭐ 규칙 B3: dialog type은 reference | template | chat 만
    ipcMain.handle('dialog:selectFiles', async (_, args: { type: 'reference' | 'template' | 'chat' }) => {
        const win = getWin()
        const { type } = args
        const extensions = ALLOWED_EXTENSIONS[type] || ALLOWED_EXTENSIONS.reference

        const result = await dialog.showOpenDialog(win!, {
            properties: ['openFile', 'multiSelections'],
            filters: [{ name: 'Documents', extensions: [...extensions] }]
        })

        if (result.canceled) {
            return { success: false, filePaths: [] }
        }

        return { success: true, filePaths: result.filePaths }
    })

    // ============================================================
    // Project CRUD
    // ============================================================

    // 프로젝트 목록 조회
    // ⭐ 규칙 F3: 완전한 Project[] 반환
    ipcMain.handle('project:list', async () => {
        try {
            const projects = listProjectsFromDb()
            return { success: true, data: { projects } }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // 프로젝트 생성
    ipcMain.handle('project:create', async (_, name: string) => {
        try {
            const projectId = generateId()

            // 디렉토리 생성
            projectStorage.createProjectDirectories(projectId)

            const project = {
                id: projectId,
                name,
                createdAt: Date.now(),
                updatedAt: Date.now(),
                templatePairs: [],
                files: [],
                chatBindings: []
            }

            const db = getDb()
            db.prepare(
                `INSERT INTO projects (id, name, created_at, updated_at)
                 VALUES (?, ?, ?, ?)`
            ).run(project.id, project.name, project.createdAt, project.updatedAt)

            return { success: true, data: { project } }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // 프로젝트 삭제
    ipcMain.handle('project:delete', async (_, projectId: string) => {
        try {
            // 로컬 파일 방식 — OpenAI RAG 삭제 불필요
            projectStorage.removeProjectDirectories(projectId)

            const db = getDb()
            db.prepare('DELETE FROM projects WHERE id = ?').run(projectId)

            console.log(`[ProjectHandlers] Project deleted: ${projectId}`)
            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // 프로젝트 이름 변경
    ipcMain.handle('project:rename', async (_, args: { projectId: string; name: string }) => {
        try {
            const project = getProjectRow(args.projectId)

            if (!project) {
                return { success: false, error: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }
            }

            const updatedAt = Date.now()
            const db = getDb()
            db.prepare(
                `UPDATE projects
                 SET name = ?, updated_at = ?
                 WHERE id = ?`
            ).run(args.name, updatedAt, args.projectId)

            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // ============================================================
    // File Upload
    // ============================================================

    // 파일 업로드
    // ⭐ 규칙 F1: destRelPath 방식
    ipcMain.handle('project:uploadFiles', async (_, args: { projectId: string; filePaths: string[] }) => {
        try {
            const { projectId, filePaths } = args

            // 프로젝트 조회
            const project = getProjectRow(projectId)

            if (!project) {
                return { success: false, error: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }
            }

            // 파일 수 제한 확인 (일반 파일 + 양식쌍*2 총합 기준)
            const currentSlotUsage = getProjectSlotUsage(projectId)
            if (currentSlotUsage + filePaths.length > MAX_TOTAL_PROJECT_FILES) {
                return {
                    success: false,
                    error: {
                        code: 'MAX_FILES_EXCEEDED',
                        message: `Maximum ${MAX_TOTAL_PROJECT_FILES} slots allowed (template pair counts as 2 files)`
                    }
                }
            }

            const uploadedFiles: ProjectFile[] = []
            const failedFiles: Array<{ name: string; errorCode: string; errorMessage: string }> = []

            for (const filePath of filePaths) {
                const fileName = path.basename(filePath)
                const ext = path.extname(filePath).slice(1).toLowerCase()

                // 확장자 검증 — 실패해도 다른 파일은 계속 처리 (early-return 금지)
                if (!isAllowedExtension(ext, 'reference')) {
                    failedFiles.push({
                        name: fileName,
                        errorCode: 'UNSUPPORTED_EXTENSION',
                        errorMessage: `지원하지 않는 형식: .${ext}`
                    })
                    continue
                }

                // 파일 크기 검증
                const stat = fs.statSync(filePath)
                const limit = getMaxFileSize(ext)
                if (stat.size > limit) {
                    failedFiles.push({
                        name: fileName,
                        errorCode: 'FILE_TOO_LARGE',
                        errorMessage: `최대 ${limit / 1024 / 1024}MB 초과`
                    })
                    continue
                }

                // 파일 복사
                const fileId = generateId()
                const relPath = `files/${fileId}.${ext}`
                const savedPath = projectStorage.copyFileToProject(projectId, filePath, relPath)

                // HWP/HWPX: 텍스트 추출 → .extracted.txt 캐시 저장
                // RAG 검색 시 HWP COM 충돌 방지를 위해 업로드 시점에 미리 추출
                if (['hwp', 'hwpx'].includes(ext)) {
                    try {
                        let reader = getFileReaderBridge()
                        if (reader && !reader.isRunning()) {
                            try { await reader.start() } catch {}
                        }
                        if (reader && reader.isRunning()) {
                            const extractResult = await reader.call('readHwpFile', { filePath: savedPath })
                            if (extractResult?.success && extractResult?.text) {
                                fs.writeFileSync(savedPath + '.extracted.txt', extractResult.text, 'utf-8')
                            }
                        }
                    } catch (extractErr: any) {
                        // 추출 실패해도 업로드는 계속 진행 (fallback: 런타임 시 직접 읽기)
                        console.error(`[project:uploadFiles] HWP text extraction failed: ${extractErr.message}`)
                    }
                }

                const projectFile = {
                    id: fileId,
                    name: path.basename(filePath),
                    path: savedPath,
                    originalPath: filePath,
                    type: 'reference' as const,
                    extension: ext,
                    size: stat.size,
                    addedAt: Date.now(),
                    indexStatus: { status: 'ready' as const }
                }

                uploadedFiles.push(projectFile)
            }

            const db = getDb()
            const basePath = projectStorage.getProjectBasePath(projectId)
            const insertFileStmt = db.prepare(
                `INSERT INTO files (
                    id, scope, project_id, chat_id, name, rel_path, extension, size, type,
                    added_at, index_status, index_status_message, index_status_progress, original_path
                )
                VALUES (?, 'project', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
            )
            const tx = db.transaction(() => {
                for (const file of uploadedFiles) {
                    const relPath = path.relative(basePath, file.path)
                    insertFileStmt.run(
                        file.id,
                        projectId,
                        file.name,
                        relPath,
                        file.extension,
                        file.size,
                        file.type,
                        file.addedAt,
                        'ready',
                        null,
                        null,
                        file.originalPath ?? ''
                    )
                }
            })
            tx()

            return { success: true, data: { uploadedFiles, failedFiles } }
        } catch (error: any) {
            if (error.message.includes('locked')) {
                return { success: false, error: { code: 'FILE_LOCKED', message: 'File is locked by another process' } }
            }
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // 프로젝트 파일 삭제
    ipcMain.handle('project:removeFile', async (_, args: { projectId: string; fileId: string }) => {
        try {
            const { projectId, fileId } = args

            const project = getProjectRow(projectId)

            if (!project) {
                return { success: false, error: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }
            }

            const fileRow = getProjectFileRow(projectId, fileId)
            if (!fileRow) {
                return { success: false, error: { code: 'NOT_FOUND', message: 'File not found' } }
            }

            // 파일 삭제 (로컬 파일 방식 — OpenAI RAG 호출 불필요)
            projectStorage.removeFile(projectId, fileRow.rel_path)
            // .extracted.txt 캐시도 삭제
            const absPath = path.join(projectStorage.getProjectBasePath(projectId), fileRow.rel_path)
            const cachePath = absPath + '.extracted.txt'
            if (fs.existsSync(cachePath)) {
                fs.unlinkSync(cachePath)
            }
            deleteProjectFileRow(projectId, fileId)
            const db = getDb()
            db.prepare('UPDATE projects SET updated_at = ? WHERE id = ?')
                .run(Date.now(), projectId)

            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // ============================================================
    // Template Pair
    // ============================================================

    // Template Pair 추가
    // ⭐ 규칙 F1: destRelPath 방식
    // ⭐ 규칙 S1: pairId만 사용
    ipcMain.handle('project:addTemplatePair', async (_, args: {
        projectId: string;
        templatePath: string;
        filledPath: string
    }) => {
        try {
            const { projectId, templatePath, filledPath } = args

            const project = getProjectRow(projectId)

            if (!project) {
                return { success: false, error: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }
            }

            // Pair 수 제한 확인
            const pairCount = getTemplatePairCount(projectId)
            if (pairCount >= MAX_TEMPLATE_PAIRS) {
                return {
                    success: false,
                    error: { code: 'MAX_PAIRS_EXCEEDED', message: `Maximum ${MAX_TEMPLATE_PAIRS} pairs allowed` }
                }
            }

            // 총 슬롯 제한 확인 (일반 파일 + 양식쌍*2)
            const currentSlotUsage = getProjectSlotUsage(projectId)
            if (currentSlotUsage + 2 > MAX_TOTAL_PROJECT_FILES) {
                return {
                    success: false,
                    error: {
                        code: 'MAX_FILES_EXCEEDED',
                        message: `Maximum ${MAX_TOTAL_PROJECT_FILES} slots allowed (template pair counts as 2 files)`
                    }
                }
            }

            const pairId = generateId()

            // 확장자 추출
            const templateExt = path.extname(templatePath).slice(1).toLowerCase()
            const filledExt = path.extname(filledPath).slice(1).toLowerCase()

            // 확장자 검증
            if (!isAllowedExtension(templateExt, 'template') || !isAllowedExtension(filledExt, 'template')) {
                return {
                    success: false,
                    error: { code: 'UNSUPPORTED_EXTENSION', message: 'Only HWP/HWPX files are allowed for template pairs' }
                }
            }

            // 파일 복사
            const templateRelPath = `template_pairs/${pairId}/template.${templateExt}`
            const filledRelPath = `template_pairs/${pairId}/filled.${filledExt}`

            projectStorage.copyFileToProject(projectId, templatePath, templateRelPath)
            projectStorage.copyFileToProject(projectId, filledPath, filledRelPath)

            const templateStat = fs.statSync(templatePath)
            const filledStat = fs.statSync(filledPath)

            const pair = {
                id: pairId,
                templateFile: {
                    name: path.basename(templatePath),
                    relPath: templateRelPath,
                    extension: templateExt,
                    size: templateStat.size
                },
                filledFile: {
                    name: path.basename(filledPath),
                    relPath: filledRelPath,
                    extension: filledExt,
                    size: filledStat.size
                },
                createdAt: Date.now(),
                extractStatus: buildIndexStatus('pending', null, null),
                indexStatus: buildIndexStatus('pending', null, null)
            }

            const db = getDb()
            db.prepare(
                `INSERT INTO template_pairs (
                    id, project_id, template_name, template_rel_path, template_size,
                    filled_name, filled_rel_path, filled_size, created_at,
                    extract_status, extract_status_message, extract_status_progress,
                    index_status, index_status_message, index_status_progress
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
            ).run(
                pair.id,
                projectId,
                pair.templateFile.name,
                pair.templateFile.relPath,
                pair.templateFile.size,
                pair.filledFile.name,
                pair.filledFile.relPath,
                pair.filledFile.size,
                pair.createdAt,
                pair.extractStatus.status,
                pair.extractStatus.message ?? null,
                pair.extractStatus.progress ?? null,
                pair.indexStatus.status,
                pair.indexStatus.message ?? null,
                pair.indexStatus.progress ?? null
            )
            db.prepare('UPDATE projects SET updated_at = ? WHERE id = ?')
                .run(Date.now(), projectId)

            // CVD 추출 및 Diff 생성 (백그라운드, 별도 프로세스에서 실행)
            const pythonBridge = getPythonBridge()
            const fileReaderBridge = getFileReaderBridge()
            if (pythonBridge && pythonBridge.isRunning()) {
                const basePath = projectStorage.getProjectBasePath(projectId)
                const templateFullPath = path.join(basePath, templateRelPath)
                const filledFullPath = path.join(basePath, filledRelPath)
                const templateExtractPath = fs.existsSync(templatePath) ? templatePath : templateFullPath
                const filledExtractPath = fs.existsSync(filledPath) ? filledPath : filledFullPath
                const userDataPath = app.getPath('userData')

                // fileReaderBridge 시작 후 CVD 추출 → Diff 생성 순차 실행
                const startReader = async () => {
                    if (!fileReaderBridge) {
                        throw new Error('FileReader bridge unavailable')
                    }
                    if (!fileReaderBridge.isRunning()) {
                        try { await fileReaderBridge.start() } catch {}
                    }
                    if (!fileReaderBridge.isRunning()) {
                        throw new Error('FileReader bridge not running')
                    }
                    return fileReaderBridge
                }

                startReader().then(reader =>
                    reader.call('cvd:extractPair', {
                        projectId: projectId,
                        pairId: pairId,
                        templatePath: templateExtractPath,
                        filledPath: filledExtractPath,
                        userDataPath: userDataPath
                    })
                ).then(extractResult => {
                    if (extractResult.success) {
                        console.log(`[ProjectHandlers] CVD extraction completed for pair: ${pairId}`)

                        // Diff 생성 (별도 프로세스에서 실행)
                        return startReader().then((reader) => reader.call('cvd:generateDiff', {
                            projectId: projectId,
                            pairId: pairId,
                            userDataPath: userDataPath
                        }))
                    } else {
                        console.error(`[ProjectHandlers] CVD extraction failed for pair: ${pairId}`, extractResult.error)
                        throw new Error(extractResult.error)
                    }
                }).then(async diffResult => {
                    if (diffResult && diffResult.success) {
                        console.log(`[ProjectHandlers] Diff generation completed for pair: ${pairId}`)

                        // NOTE: 양식쌍은 RAG에 인덱싱하지 않음 (파일/채팅 파일만 인덱싱)
                        // Diff 파일은 시스템 프롬프트에 직접 포함되므로 RAG 불필요

                        // ✅ manifest에 indexStatus: ready 저장 (영구 저장)
                        setTemplatePairIndexStatus(projectId, pairId, 'ready')

                        // 처리 완료 이벤트 발송
                        const win = getWin()
                        if (win) {
                            win.webContents.send('chat:progress', 'pair:indexComplete', {
                                pairId: pairId
                            })
                        }
                    } else {
                        console.error(`[ProjectHandlers] Diff generation failed for pair: ${pairId}`, diffResult?.error)
                        throw new Error(diffResult?.error || 'Diff generation failed')
                    }
                }).catch(async (err) => {
                    console.error(`[ProjectHandlers] Template pair processing error for pair: ${pairId}`, err)
                    // 처리 실패 시 manifest에서 pair 제거
                    deleteTemplatePairRow(projectId, pairId)
                    // UI에 에러 이벤트 전송
                    const win = getWin()
                    if (win) {
                        win.webContents.send('chat:progress', 'pair:uploadFailed', {
                            pairId: pairId,
                            error: err instanceof Error ? err.message : String(err)
                        })
                    }
                })
            } else {
                console.warn('[ProjectHandlers] Python bridge not available, skipping CVD extraction and RAG indexing')
            }

            return { success: true, data: { pair } }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // Template Pair 삭제
    // ⭐ 규칙 C6: RAG 삭제 포함
    ipcMain.handle('project:removeTemplatePair', async (_, args: { projectId: string; pairId: string }) => {
        try {
            const { projectId, pairId } = args

            const project = getProjectRow(projectId)

            if (!project) {
                return { success: false, error: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }
            }

            // Pair 디렉토리 삭제
            projectStorage.removeTemplatePairDirectory(projectId, pairId)

            deleteTemplatePairRow(projectId, pairId)
            const db = getDb()
            db.prepare('UPDATE projects SET updated_at = ? WHERE id = ?')
                .run(Date.now(), projectId)

            // NOTE: 양식쌍은 RAG에 인덱싱하지 않으므로 삭제도 불필요
            // (파일/채팅 파일만 RAG에 인덱싱됨)

            console.log(`[ProjectHandlers] Template pair deleted: ${pairId} from project ${projectId}`)

            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // ============================================================
    // Keystore
    // ============================================================

    // ⭐ 규칙 B2: saveKey/loadKey 메서드명 고정
    ipcMain.handle('keystore:save', async (_, args: { key: string; value: string }) => {
        try {
            if (!keystore.isEncryptionAvailable()) {
                return { success: false, error: { code: 'ENCRYPTION_NOT_AVAILABLE', message: 'Encryption not available' } }
            }

            keystore.saveKey(args.key, args.value)
            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    ipcMain.handle('keystore:load', async (_, args: { key: string }) => {
        try {
            const value = keystore.loadKey(args.key)
            return { success: true, data: { value } }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // ============================================================
    // Chat-Project Binding
    // ============================================================

    ipcMain.handle('chatFiles:bindProject', async (_, args: { chatId: string; projectId: string }) => {
        try {
            const project = getProjectRow(args.projectId)
            const db = getDb()
            db.prepare('DELETE FROM project_chat_bindings WHERE chat_id = ?')
                .run(args.chatId)
            if (!project) {
                console.warn('[ProjectHandlers] chatFiles:bindProject project not found:', args.projectId)
                return { success: true }
            }
            db.prepare(
                `INSERT INTO project_chat_bindings (project_id, chat_id, created_at)
                 VALUES (?, ?, ?)`
            ).run(args.projectId, args.chatId, Date.now())

            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    ipcMain.handle('chatFiles:unbindProject', async (_, args: { chatId: string }) => {
        try {
            const db = getDb()
            db.prepare('DELETE FROM project_chat_bindings WHERE chat_id = ?')
                .run(args.chatId)

            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    // ============================================================
    // Chat File Storage (DB)
    // ============================================================

    ipcMain.handle('chatFiles:add', async (_, args: {
        chatId: string
        fileId: string
        fileName: string
        filePath: string
        extension?: string
        size?: number
        addedAt?: number
    }) => {
        try {
            const { chatId, fileId, fileName, filePath } = args

            if (!chatId || !fileId || !fileName || !filePath) {
                return { success: false, error: { code: 'INVALID_ARGS', message: 'chatId/fileId/fileName/filePath required' } }
            }

            const ext = (args.extension || path.extname(fileName || filePath).slice(1)).toLowerCase()
            const relPath = ext ? `files/${fileId}.${ext}` : `files/${fileId}`
            const savedPath = appStorage.copyChatFile(chatId, filePath, relPath)

            // HWP/HWPX: 텍스트 추출 → .extracted.txt 캐시 저장
            if (['hwp', 'hwpx'].includes(ext)) {
                try {
                    let reader = getFileReaderBridge()
                    if (reader && !reader.isRunning()) {
                        try { await reader.start() } catch {}
                    }
                    if (reader && reader.isRunning()) {
                        const extractResult = await reader.call('readHwpFile', { filePath: savedPath })
                        if (extractResult?.success && extractResult?.text) {
                            fs.writeFileSync(savedPath + '.extracted.txt', extractResult.text, 'utf-8')
                        }
                    }
                } catch (extractErr: any) {
                    console.error(`[chatFiles:add] HWP text extraction failed: ${extractErr.message}`)
                }
            }

            const size = typeof args.size === 'number' ? args.size : fs.statSync(savedPath).size
            const addedAt = typeof args.addedAt === 'number' ? args.addedAt : Date.now()

            const db = dbManager.open()
            db.prepare(
                `INSERT INTO files (id, scope, project_id, chat_id, name, rel_path, extension, size, type, added_at, original_path)
                 VALUES (?, 'chat', NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(id) DO UPDATE SET
                   scope='chat',
                   chat_id=excluded.chat_id,
                   name=excluded.name,
                   rel_path=excluded.rel_path,
                   extension=excluded.extension,
                   size=excluded.size,
                   type=excluded.type,
                   added_at=excluded.added_at,
                   original_path=excluded.original_path`
            ).run(
                fileId,
                chatId,
                fileName,
                relPath,
                ext || null,
                size,
                'reference',
                addedAt,
                filePath
            )

            console.log(`[ProjectHandlers] chat file saved: ${fileName} (chat=${chatId}, file=${fileId})`)
            return { success: true, data: { relPath } }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    ipcMain.handle('chatFiles:remove', async (_, args: { chatId: string; fileId: string }) => {
        try {
            const db = dbManager.open()
            const row = db.prepare(
                "SELECT rel_path, name FROM files WHERE scope = 'chat' AND chat_id = ? AND id = ?"
            ).get(args.chatId, args.fileId) as { rel_path?: string; name?: string } | undefined

            // 로컬 파일 방식 — OpenAI RAG 호출 없이 파일/DB만 삭제
            if (row?.rel_path) {
                appStorage.removeChatFile(args.chatId, row.rel_path)
                // .extracted.txt 캐시도 삭제
                const absPath = path.join(appStorage.getChatFilesBasePath(args.chatId), row.rel_path)
                const cachePath = absPath + '.extracted.txt'
                if (fs.existsSync(cachePath)) {
                    fs.unlinkSync(cachePath)
                }
            }

            db.prepare("DELETE FROM files WHERE scope = 'chat' AND chat_id = ? AND id = ?")
                .run(args.chatId, args.fileId)

            console.log(`[ProjectHandlers] chat file removed: ${args.fileId} (chat=${args.chatId})`)
            return { success: true }
        } catch (error: any) {
            return { success: false, error: { code: 'UNKNOWN_ERROR', message: error.message } }
        }
    })

    console.log('[Main] Project IPC handlers registered')
}

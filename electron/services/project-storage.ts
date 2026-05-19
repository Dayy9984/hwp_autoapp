/**
 * Project Storage
 * 프로젝트 파일 저장소 클래스
 */

import { app } from 'electron'
import * as path from 'path'
import * as fs from 'fs'

class ProjectStorage {
    // ✅ userData 기준 경로 (규칙 2)
    private getUserDataPath(): string {
        return app.getPath('userData')
    }

    /**
     * 프로젝트 기본 경로 조회
     */
    getProjectBasePath(projectId: string): string {
        return path.join(this.getUserDataPath(), 'projects', projectId)
    }

    /**
     * 프로젝트 디렉토리 생성
     */
    createProjectDirectories(projectId: string): void {
        const basePath = this.getProjectBasePath(projectId)
        fs.mkdirSync(path.join(basePath, 'template_pairs'), { recursive: true })
        fs.mkdirSync(path.join(basePath, 'files'), { recursive: true })
        fs.mkdirSync(path.join(basePath, 'vector_store', 'chroma'), { recursive: true })
    }

    /**
     * 파일을 프로젝트에 복사
     * ⭐ 규칙 F1: destRelPath 방식만 사용
     */
    copyFileToProject(
        projectId: string,
        srcPath: string,
        destRelPath: string
    ): string {
        // ✅ v3.3 고정: 경로 탈출 방지 (보안)
        const normalized = path.normalize(destRelPath)
        if (normalized.startsWith('..') || path.isAbsolute(normalized)) {
            throw new Error('Invalid path: path traversal detected')
        }

        const basePath = this.getProjectBasePath(projectId)
        const destPath = path.join(basePath, destRelPath)

        // ✅ 디렉토리 생성
        fs.mkdirSync(path.dirname(destPath), { recursive: true })

        // ✅ 파일 복사 (규칙 S5: Windows 파일 잠김 대응)
        this.safeCopyFile(srcPath, destPath)

        return destPath
    }

    /**
     * Windows 파일 잠김 대응 복사
     * ⭐ 규칙 S5
     */
    private safeCopyFile(src: string, dest: string): void {
        try {
            fs.copyFileSync(src, dest)
            // ✅ 복사 후 read-only 제거
            if (process.platform === 'win32') {
                try {
                    fs.chmodSync(dest, 0o666)
                } catch {
                    // chmod 실패 시 무시
                }
            }
        } catch (error: any) {
            if (error.code === 'EBUSY' || error.code === 'EPERM') {
                throw new Error('File is locked by another process')
            }
            throw error
        }
    }

    /**
     * 프로젝트 파일 삭제
     */
    removeFile(projectId: string, relPath: string): void {
        const basePath = this.getProjectBasePath(projectId)
        const filePath = path.join(basePath, relPath)
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath)
        }
    }

    /**
     * Template Pair 디렉토리 삭제
     */
    removeTemplatePairDirectory(projectId: string, pairId: string): void {
        const pairPath = path.join(this.getProjectBasePath(projectId), 'template_pairs', pairId)
        if (fs.existsSync(pairPath)) {
            fs.rmSync(pairPath, { recursive: true })
        }
    }

    /**
     * 프로젝트 디렉토리 전체 삭제
     * ⭐ 규칙 C6: Chroma 삭제 포함
     */
    removeProjectDirectories(projectId: string): void {
        const basePath = this.getProjectBasePath(projectId)
        if (fs.existsSync(basePath)) {
            fs.rmSync(basePath, { recursive: true })
        }
    }

    /**
     * 파일 존재 확인
     */
    fileExists(projectId: string, relPath: string): boolean {
        const basePath = this.getProjectBasePath(projectId)
        return fs.existsSync(path.join(basePath, relPath))
    }

    /**
     * CVD 파일 존재 확인
     */
    cvdFilesExist(projectId: string, pairId: string): { template: boolean; filled: boolean } {
        const pairPath = path.join(this.getProjectBasePath(projectId), 'template_pairs', pairId)
        return {
            template: fs.existsSync(path.join(pairPath, 'template.cvd.md')),
            filled: fs.existsSync(path.join(pairPath, 'filled.cvd.md'))
        }
    }

    /**
     * diff.json 존재 확인
     */
    diffExists(projectId: string, pairId: string): boolean {
        const diffPath = path.join(
            this.getProjectBasePath(projectId),
            'template_pairs',
            pairId,
            'diff.json'
        )
        return fs.existsSync(diffPath)
    }
}

export const projectStorage = new ProjectStorage()

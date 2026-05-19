/**
 * App Storage
 * 앱 전역 파일 저장소 (Chat 파일, Manifest 등)
 * ⭐ 규칙 C1: chat_files는 projects 바깥에 저장
 */

import { app } from 'electron'
import * as path from 'path'
import * as fs from 'fs'

class AppStorage {
    private getUserDataPath(): string {
        return app.getPath('userData')
    }

    /**
     * Chat 파일 기본 경로
     * ⭐ 규칙 C1: {userData}/chat_files/{chatId}/
     */
    getChatFilesBasePath(chatId: string): string {
        return path.join(this.getUserDataPath(), 'chat_files', chatId)
    }

    /**
     * Chat 파일 복사
     * ⭐ 규칙 F1: relPath 방식
     */
    copyChatFile(chatId: string, srcPath: string, relPath: string): string {
        // ✅ v3.3 고정: 경로 탈출 방지 (보안)
        const normalized = path.normalize(relPath)
        if (normalized.startsWith('..') || path.isAbsolute(normalized)) {
            throw new Error('Invalid path: path traversal detected')
        }

        const basePath = this.getChatFilesBasePath(chatId)
        const destPath = path.join(basePath, relPath)

        fs.mkdirSync(path.dirname(destPath), { recursive: true })
        this.safeCopyFile(srcPath, destPath)

        return destPath
    }

    /**
     * Windows 파일 잠김 대응 복사
     */
    private safeCopyFile(src: string, dest: string): void {
        try {
            fs.copyFileSync(src, dest)
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
     * Chat 파일 삭제
     */
    removeChatFile(chatId: string, relPath: string): void {
        const filePath = path.join(this.getChatFilesBasePath(chatId), relPath)
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath)
        }
    }

    /**
     * Chat 디렉토리 전체 삭제
     */
    removeChatDirectory(chatId: string): void {
        const dirPath = this.getChatFilesBasePath(chatId)
        if (fs.existsSync(dirPath)) {
            fs.rmSync(dirPath, { recursive: true })
        }
    }

    /**
     * Manifest 경로
     */
    getManifestPath(): string {
        return path.join(this.getUserDataPath(), 'manifest.json')
    }

    /**
     * 전역 Vector Store 경로
     */
    getGlobalChromaPath(): string {
        return path.join(this.getUserDataPath(), 'vector_store', 'global_chroma')
    }

    /**
     * 전역 Vector Store 디렉토리 생성
     */
    ensureGlobalChromaDirectory(): void {
        const chromaPath = this.getGlobalChromaPath()
        fs.mkdirSync(chromaPath, { recursive: true })
    }
}

export const appStorage = new AppStorage()

/**
 * Keystore
 * API 키 암호화 저장소
 * ⭐ 규칙 F6: Main 프로세스 전용, safeStorage 사용
 */

import { safeStorage, app } from 'electron'
import * as fs from 'fs'
import * as path from 'path'

export class KeyStore {
    /**
     * Secrets 파일 경로
     */
    private getSecretsPath(): string {
        return path.join(app.getPath('userData'), 'secrets.json')
    }

    /**
     * 키 저장 (async API for compatibility)
     */
    async set(key: string, value: string): Promise<void> {
        this.saveKey(key, value)
    }

    /**
     * 키 로드 (async API for compatibility)
     */
    async get(key: string): Promise<string | null> {
        return this.loadKey(key)
    }

    /**
     * 키 삭제 (async API for compatibility)
     */
    async delete(key: string): Promise<void> {
        this.deleteKey(key)
    }

    /**
     * 키 저장
     * ⭐ 규칙 B2: saveKey 메서드명 고정
     */
    saveKey(key: string, value: string): void {
        if (!safeStorage.isEncryptionAvailable()) {
            throw new Error('Encryption not available')
        }

        const encrypted = safeStorage.encryptString(value)
        const secrets = this.loadSecrets()
        secrets[key] = encrypted.toString('base64')

        // ✅ Main 파일로 저장 (규칙 F6)
        fs.writeFileSync(
            this.getSecretsPath(),
            JSON.stringify(secrets, null, 2),
            'utf-8'
        )
    }

    /**
     * 키 로드
     * ⭐ 규칙 B2: loadKey 메서드명 고정
     */
    loadKey(key: string): string | null {
        if (!safeStorage.isEncryptionAvailable()) {
            return null
        }

        const secrets = this.loadSecrets()
        const encryptedBase64 = secrets[key]
        if (!encryptedBase64) return null

        try {
            const encrypted = Buffer.from(encryptedBase64, 'base64')
            return safeStorage.decryptString(encrypted)
        } catch {
            return null
        }
    }

    /**
     * 키 삭제
     */
    deleteKey(key: string): void {
        const secrets = this.loadSecrets()
        delete secrets[key]

        fs.writeFileSync(
            this.getSecretsPath(),
            JSON.stringify(secrets, null, 2),
            'utf-8'
        )
    }

    /**
     * 모든 키 이름 조회
     */
    getAllKeys(): string[] {
        const secrets = this.loadSecrets()
        return Object.keys(secrets)
    }

    /**
     * 암호화 사용 가능 여부
     */
    isEncryptionAvailable(): boolean {
        return safeStorage.isEncryptionAvailable()
    }

    /**
     * Secrets 파일 로드
     */
    private loadSecrets(): Record<string, string> {
        const secretsPath = this.getSecretsPath()
        if (!fs.existsSync(secretsPath)) {
            return {}
        }
        try {
            return JSON.parse(fs.readFileSync(secretsPath, 'utf-8'))
        } catch {
            return {}
        }
    }
}

// Backward compatibility - export singleton instance
export const keystore = new KeyStore()

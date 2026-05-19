/**
 * Document Key 생성 모듈
 * 불변식 7, 8, 9, 13 관련
 */

import path from 'node:path'
import crypto from 'node:crypto'

const DOC_KEY_VERSION = 'v1'

/**
 * 파일 경로 정규화 (Windows 고정)
 */
function normalizePath(filePath: string): string {
    return path.win32.normalize(filePath)
        .toLowerCase()
        .replace(/\\/g, '/')
}

/**
 * 문서 키 생성
 * - 1순위: documentId (HWP COM 제공)
 * - 2순위: path 정규화 + 해시
 * - 둘 다 없으면 undefined (기능 비활성)
 */
export function generateDocumentKey(doc: {
    documentId?: number
    path?: string
}): string | undefined {
    // 1순위: documentId
    if (doc.documentId && doc.documentId > 0) {
        return `${DOC_KEY_VERSION}:doc:${doc.documentId}`
    }

    // 2순위: path 정규화 + 해시
    if (doc.path && doc.path.trim() !== '') {
        const normalized = normalizePath(doc.path)
        const hash = crypto.createHash('sha1').update(normalized).digest('hex').slice(0, 12)
        return `${DOC_KEY_VERSION}:path:${hash}`
    }

    // 기능 비활성
    return undefined
}

/**
 * 요청 docKey의 스킴에 맞춰 activeKey 생성 (불변식 13)
 * - v1:doc: → activeDocumentId 기준
 * - v1:path: → activePath 기준
 */
export function buildActiveKeyForComparison(
    requestDocKey: string,
    active: { activeDocumentId?: number; activePath?: string }
): string | undefined {
    // requestDocKey가 v1:doc:이면 docId 기준
    if (requestDocKey.startsWith(`${DOC_KEY_VERSION}:doc:`)) {
        const docId = active.activeDocumentId
        return docId && docId > 0 ? `${DOC_KEY_VERSION}:doc:${docId}` : undefined
    }

    // requestDocKey가 v1:path:이면 path-hash 기준 (docId 무시)
    if (requestDocKey.startsWith(`${DOC_KEY_VERSION}:path:`)) {
        const p = active.activePath
        if (!p) return undefined
        const normalized = normalizePath(p)
        const hash = crypto.createHash('sha1').update(normalized).digest('hex').slice(0, 12)
        return `${DOC_KEY_VERSION}:path:${hash}`
    }

    return undefined
}

/**
 * docKey에서 스킴 추출
 */
export function getDocKeyScheme(docKey: string): 'doc' | 'path' | undefined {
    if (docKey.startsWith(`${DOC_KEY_VERSION}:doc:`)) return 'doc'
    if (docKey.startsWith(`${DOC_KEY_VERSION}:path:`)) return 'path'
    return undefined
}

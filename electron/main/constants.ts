/**
 * Constants
 * 프로젝트 관련 상수 정의
 */

// ⭐ 규칙 C3, S4: 점 없음, 소문자
export const ALLOWED_EXTENSIONS = {
    reference: ['hwp', 'hwpx', 'pdf', 'docx', 'pptx', 'xls', 'xlsx', 'xlsm', 'txt', 'md'],
    template: ['hwp', 'hwpx'],
    chat: ['hwp', 'hwpx', 'pdf', 'docx', 'pptx', 'xls', 'xlsx', 'xlsm', 'txt', 'md']
} as const

// 파일 크기 제한 (bytes)
export const MAX_FILE_SIZE: Record<string, number> = {
    'hwp': 50 * 1024 * 1024,   // 50MB
    'hwpx': 50 * 1024 * 1024,  // 50MB
    'pdf': 50 * 1024 * 1024,   // 50MB
    'pptx': 50 * 1024 * 1024,  // 50MB
    'xlsx': 50 * 1024 * 1024,  // 50MB
    'xlsm': 50 * 1024 * 1024,  // 50MB
    'xls': 50 * 1024 * 1024,   // 50MB
    'docx': 50 * 1024 * 1024,  // 50MB
    'txt': 50 * 1024 * 1024,   // 50MB
    'md': 50 * 1024 * 1024     // 50MB
}

// 기본 파일 크기 제한
export const DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  // 50MB

// 개수 제한
export const MAX_TOTAL_PROJECT_FILES = 10
export const MAX_TEMPLATE_PAIRS = 5
export const MAX_CHAT_FILES_PER_CHAT = 5

// 확장자 검증
export function isAllowedExtension(ext: string, type: keyof typeof ALLOWED_EXTENSIONS): boolean {
    const normalized = ext.toLowerCase().replace(/^\./, '')
    return ALLOWED_EXTENSIONS[type].includes(normalized as any)
}

// 파일 크기 제한 조회
export function getMaxFileSize(ext: string): number {
    const normalized = ext.toLowerCase().replace(/^\./, '')
    return MAX_FILE_SIZE[normalized] ?? DEFAULT_MAX_FILE_SIZE
}

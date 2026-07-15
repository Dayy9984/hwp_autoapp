/**
 * Common Types
 * IPC 응답 및 에러 처리 공통 타입
 */

// 에러 코드 정의
export type ErrorCode =
    // 일반 에러
    | 'UNKNOWN_ERROR'
    | 'INVALID_ARGUMENT'
    | 'NOT_FOUND'

    // 파일 관련
    | 'FILE_NOT_FOUND'
    | 'FILE_TOO_LARGE'
    | 'UNSUPPORTED_EXTENSION'
    | 'FILE_LOCKED'
    | 'COPY_FAILED'

    // 프로젝트 관련
    | 'PROJECT_NOT_FOUND'
    | 'MAX_FILES_EXCEEDED'
    | 'MAX_PAIRS_EXCEEDED'
    | 'PAIR_NOT_FOUND'

    // Python 관련
    | 'PYTHON_NOT_RUNNING'
    | 'HDML_EXTRACTION_FAILED'
    | 'OCR_FAILED'
    | 'OCR_NOT_AVAILABLE'

    // RAG 관련
    | 'RAG_INDEX_FAILED'
    | 'RAG_QUERY_FAILED'
    | 'EMBEDDING_FAILED'

    // 키 저장
    | 'ENCRYPTION_NOT_AVAILABLE'
    | 'KEY_NOT_FOUND'

// IPC 응답 공통 형식
export interface IPCResponse<T = void> {
    success: boolean
    data?: T
    error?: {
        code: ErrorCode
        message: string
        details?: any
    }
}

// 에러 생성 헬퍼 (Electron Main에서 사용)
export function createError(code: ErrorCode, message: string, details?: any): IPCResponse<never> {
    return {
        success: false,
        error: { code, message, details }
    }
}

// 성공 응답 생성 헬퍼
export function createSuccess<T>(data?: T): IPCResponse<T> {
    return {
        success: true,
        data
    }
}

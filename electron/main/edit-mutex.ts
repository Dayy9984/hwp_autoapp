/**
 * 뮤텍스 모듈 (동시성 제어)
 * 불변식 14, 15 관련
 */

import { Mutex } from 'async-mutex'

// docKey 단위 뮤텍스 맵lit
const docMutexMap = new Map<string, Mutex>()

// Python Bridge 전역 뮤텍스 (불변식 15)
export const bridgeMutex = new Mutex()

/**
 * docKey 단위로 함수를 직렬화 실행 (불변식 14)
 * chat:send와 reject는 같은 docKey면 순차 실행됨
 */
export async function withDocLock<T>(
    docKey: string,
    fn: () => Promise<T>
): Promise<T> {
    let mutex = docMutexMap.get(docKey)
    if (!mutex) {
        mutex = new Mutex()
        docMutexMap.set(docKey, mutex)
    }
    return mutex.runExclusive(fn)
}

/**
 * 뮤텍스 정리 (메모리 누수 방지)
 * 오래된 docKey의 뮤텍스 제거
 */
export function cleanupMutexes(): void {
    // 현재는 간단히 비움 (필요시 LRU 구현)
    for (const [key, mutex] of docMutexMap.entries()) {
        if (!mutex.isLocked()) {
            docMutexMap.delete(key)
        }
    }
}

// 주기적 정리 (10분마다)
setInterval(cleanupMutexes, 10 * 60 * 1000)

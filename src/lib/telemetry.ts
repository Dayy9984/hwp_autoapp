// 베타 텔레메트리 — main 측 큐로 fire-and-forget.
// Worker /track 에 누적 전송 (실제 fetch 는 main process 에서 실행).
//
// 사용:
//   import { track } from '@/lib/telemetry'
//   track('chat_sent', { mode: 'codex', length: msg.length })
//
// 절대 보내지 말 것: HWP 본문, 채팅 내용, 파일 내용, API 키, 비밀번호

import { IS_BETA } from '../config/beta'

export function track(eventType: string, payload?: Record<string, unknown>): void {
  if (!IS_BETA) return
  if (typeof window === 'undefined') return
  const api = (window as unknown as { electronAPI?: any }).electronAPI
  api?.telemetry?.track?.(eventType, payload)?.catch?.(() => {})
}

// 베타 텔레메트리 전송 — Worker /track 으로 POST.
//
// 흐름:
//   1. license-bridge 에서 verify token + device_id 가져옴 (없으면 skip)
//   2. fire-and-forget POST — UI 차단 없음, 실패 시 silent
//   3. 일괄 큐잉 + 5초 / 50건 마다 flush — UI 응답성 보장
//
// 수집하는 이벤트:
//   - license_activated      (라이센스 키 입력 성공)
//   - app_launched           (앱 시작)
//   - session_started        (HWP 문서 연결)
//   - chat_sent              (사용자 메시지 전송)
//   - delta_accepted/rejected (변경 수락/거절)
//   - tally_form_opened      (모달 열림)
//   - error_occurred         (에러)
//
// **수집하지 않는 것**: HWP 본문 텍스트, 파일 내용, 채팅 내용, API key

import { app } from 'electron'
import { licenseBridge } from './license-bridge'
import { getDeviceInfo } from './license-device-id'

const TRACK_URL = 'https://inserty-beta-worker.snsoffice.workers.dev/track'
const FLUSH_INTERVAL_MS = 5000
const MAX_BATCH = 50

interface TrackEvent {
  event_type: string
  ts?: number
  app_version?: string
  device_id?: string
  payload?: Record<string, unknown>
}

class TelemetryQueue {
  private queue: TrackEvent[] = []
  private timer: NodeJS.Timeout | null = null
  private flushing = false
  private appVersion = ''
  private deviceId = ''

  init() {
    try {
      this.appVersion = app.getVersion()
      const di = getDeviceInfo()
      this.deviceId = di.device_id
    } catch {}
    if (!this.timer) {
      this.timer = setInterval(() => this.flush().catch(() => {}), FLUSH_INTERVAL_MS)
      if (this.timer.unref) this.timer.unref()
    }
  }

  push(eventType: string, payload?: Record<string, unknown>) {
    if (!eventType) return
    this.queue.push({
      event_type: eventType,
      ts: Date.now(),
      app_version: this.appVersion,
      device_id: this.deviceId,
      payload,
    })
    if (this.queue.length >= MAX_BATCH) this.flush().catch(() => {})
  }

  async flush() {
    if (this.flushing || this.queue.length === 0) return
    const token = (licenseBridge as any).pickAuthToken?.() as string | null
    if (!token) {
      // 라이센스 활성화 전 — 큐를 비우지 않고 보류 (활성화 후 한번에 발송).
      // 단 큐가 너무 커지면 가장 오래된 것부터 drop.
      if (this.queue.length > 500) this.queue.splice(0, this.queue.length - 500)
      return
    }
    this.flushing = true
    const batch = this.queue.splice(0, MAX_BATCH)
    try {
      await fetch(TRACK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          'X-Device-Id': this.deviceId,
        },
        body: JSON.stringify(batch),
      })
    } catch {
      // 실패한 배치는 큐 앞에 다시 — 다음 flush 에서 재시도.
      this.queue.unshift(...batch)
      if (this.queue.length > 500) this.queue.splice(0, this.queue.length - 500)
    } finally {
      this.flushing = false
    }
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
  }
}

export const telemetry = new TelemetryQueue()

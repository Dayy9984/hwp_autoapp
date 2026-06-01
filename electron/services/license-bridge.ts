// 라이센스 활성화 / 검증 / heartbeat
// Supabase Edge Functions와 통신

import * as fs from 'fs'
import * as path from 'path'
import * as crypto from 'crypto'
import { app } from 'electron'
import { getDeviceInfo } from './license-device-id'

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SUPABASE_ANON_KEY = process.env.INSERTYAI_SUPABASE_ANON_KEY || ''  // 빌드 시 inject

const GRACE_DAYS = 7
const HEARTBEAT_INTERVAL_MS = 24 * 3600 * 1000  // 24h

export type LicenseStatus =
  | { state: 'ok'; expires_at: string | null }
  | { state: 'expired' }
  | { state: 'leaked'; device_count?: number }  // 레거시 — 신규 코드는 사용 안 함
  | {
      state: 'device_limit_reached'
      device_count?: number
      max_devices?: number
      devices?: RegisteredDevice[]
    }
  | { state: 'revoked' }
  | { state: 'invalid'; reason?: string }
  | { state: 'no_license' }
  | { state: 'offline_grace'; expires_at: string | null }
  | { state: 'offline_blocked' }

export interface RegisteredDevice {
  device_id: string
  device_name: string | null
  device_os: string | null
  last_seen_at: string
  first_seen_at: string
}

export interface DeviceListResult {
  ok: boolean
  reason?: string
  current_device_id?: string
  max_devices?: number
  devices?: RegisteredDevice[]
}

export interface DeviceRemoveResult {
  ok: boolean
  reason?: string
  removed_device_id?: string
  device_count?: number
  max_devices?: number
}

interface LocalCache {
  token: string
  license_key: string
  expires_at: string | null
  last_verified_at: number  // unix ms
  server_clock_offset_ms: number  // 서버 - 클라이언트
}

const ENC_KEY = crypto
  .createHash('sha256')
  .update('insertyai-license-store-v1')
  .digest()

// setTimeout 32-bit signed int 한계 = 2^31-1 ms ≈ 24.85일. 이를 초과하면
// 즉시 발화되거나 의도와 다르게 동작. 그래서 24일로 클램프 후 재예약.
const MAX_TIMEOUT_MS = 24 * 24 * 3600 * 1000

class LicenseBridge {
  // app.getPath('userData')는 app.whenReady() 이후에만 안전 → 지연 평가
  private _cachePath: string | null = null
  private _pendingKeyPath: string | null = null
  // device_limit_reached 응답으로 받은 임시 management 토큰 (15분 수명).
  // 캐시 토큰이 없을 때 listDevices/removeDevice 가 이걸 fallback 으로 사용.
  // 디스크에 저장하지 않음 — 프로세스 종료 시 사라짐.
  private _managementToken: string | null = null
  private _managementLicenseKey: string | null = null

  // 라이센스 만료 시점 자동 verify 타이머 (패치 B).
  // 만료 시각이 도래하면 verify() 자동 호출 → 서버가 expired 응답 →
  // updateLastStatus 가 broadcast → renderer 가 BlockedScreen 으로 전환.
  // 사용자 액션 없이도 만료 즉시 차단 가능.
  private _expiryTimer: NodeJS.Timeout | null = null
  // verify 콜백 — license-gate 의 updateLastStatus 통합용. main/index.ts 가
  // 초기화 시점에 등록. 등록 안 됐을 땐 일반 verify() 호출 → IPC handler 가
  // updateLastStatus 호출하지 않으므로 callback 으로 우회.
  private _onAutoVerifyCallback: ((status: LicenseStatus) => void) | null = null

  /** main/index.ts 초기화 시 등록 — 자동 verify 결과를 license-gate 에 전달 */
  setAutoVerifyCallback(cb: (status: LicenseStatus) => void): void {
    this._onAutoVerifyCallback = cb
  }

  /**
   * 캐시의 expires_at 기준으로 만료 시점에 verify() 가 자동 호출되도록 타이머 등록.
   * 기존 타이머가 있으면 교체. 무기한 라이센스(expires_at == null)는 등록 안 함.
   * setTimeout 의 24일 한계를 넘으면 24일 후 재예약 (재귀 갱신).
   */
  private scheduleExpiryCheck(cache: LocalCache): void {
    if (this._expiryTimer) {
      clearTimeout(this._expiryTimer)
      this._expiryTimer = null
    }
    if (!cache.expires_at) return  // 무기한
    const expiresAtMs = new Date(cache.expires_at).getTime()
    if (!Number.isFinite(expiresAtMs)) return

    // 서버 기준 현재 시각 = 로컬 + offset. 도래까지 남은 ms.
    const serverNowMs = Date.now() + (cache.server_clock_offset_ms || 0)
    const delayMs = expiresAtMs - serverNowMs
    if (delayMs <= 0) {
      // 이미 만료 — 즉시 verify 트리거. setImmediate 로 호출 스택 분리.
      setImmediate(() => this._fireAutoVerify())
      return
    }
    // 24일 초과 시 클램프 — 만료 시점에 가까워질 때까지 재예약 루프.
    const safeDelay = Math.min(delayMs, MAX_TIMEOUT_MS)
    this._expiryTimer = setTimeout(() => {
      this._expiryTimer = null
      // 24일이 지났지만 아직 만료 전이면 → 캐시 재로드 후 재예약.
      const fresh = this.loadCache()
      if (!fresh) return
      const freshExpMs = fresh.expires_at ? new Date(fresh.expires_at).getTime() : NaN
      if (Number.isFinite(freshExpMs) && freshExpMs > Date.now() + (fresh.server_clock_offset_ms || 0)) {
        this.scheduleExpiryCheck(fresh)
      } else {
        this._fireAutoVerify()
      }
    }, safeDelay)
    // unref — Node 의 이벤트 루프가 이 타이머만 남았을 때 프로세스 종료를 막지 않음.
    if (typeof this._expiryTimer.unref === 'function') this._expiryTimer.unref()
  }

  /** 만료 도래 시 자동 verify 실행 — 결과를 license-gate 콜백에 전달 */
  private async _fireAutoVerify(): Promise<void> {
    try {
      const status = await this.verify()
      // verify 가 expired/revoked 면 clearCache 이미 호출됨 → 타이머도 정리됨.
      // callback 으로 license-gate 의 updateLastStatus 호출 → renderer broadcast.
      if (this._onAutoVerifyCallback) {
        try {
          this._onAutoVerifyCallback(status)
        } catch {}
      }
    } catch {
      // 무시 — verify 자체는 try/catch 로 안전.
    }
  }

  /** 외부에서 현재 캐시 기준 타이머 재예약 (앱 시작 시 호출) */
  rescheduleExpiryCheck(): void {
    const cache = this.loadCache()
    if (cache) this.scheduleExpiryCheck(cache)
  }

  private get cachePath(): string {
    if (!this._cachePath) {
      this._cachePath = path.join(app.getPath('userData'), '.license_cache')
    }
    return this._cachePath
  }

  private get pendingKeyPath(): string {
    if (!this._pendingKeyPath) {
      this._pendingKeyPath = path.join(app.getPath('userData'), '.pending_license')
    }
    return this._pendingKeyPath
  }

  /** 인스톨러가 저장한 키가 있으면 읽음 */
  getPendingKey(): string | null {
    try {
      if (fs.existsSync(this.pendingKeyPath)) {
        const key = fs.readFileSync(this.pendingKeyPath, 'utf8').trim()
        return key || null
      }
    } catch {}
    return null
  }

  consumePendingKey(): void {
    try {
      if (fs.existsSync(this.pendingKeyPath)) fs.unlinkSync(this.pendingKeyPath)
    } catch {}
  }

  /** 활성화 */
  async activate(license_key: string): Promise<LicenseStatus> {
    const info = getDeviceInfo()
    try {
      const res = await fetch(`${SUPABASE_URL}/functions/v1/activate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
        body: JSON.stringify({
          license_key: license_key.trim().toUpperCase(),
          device_id: info.device_id,
          device_name: info.device_name,
          device_os: info.device_os,
        }),
      })
      const data = await res.json()
      if (!data.ok) {
        // device_limit_reached 응답이면 management_token + devices 를 메모리에 저장
        if (data.reason === 'device_limit_reached' && data.management_token) {
          this._managementToken = data.management_token
          this._managementLicenseKey = license_key.trim().toUpperCase()
        }
        return this.mapReason(data.reason, data.device_count, data.max_devices, data.devices)
      }
      const serverNow = data.server_now ? new Date(data.server_now).getTime() : Date.now()
      const offset = serverNow - Date.now()
      this.saveCache({
        token: data.token,
        license_key,
        expires_at: data.expires_at,
        last_verified_at: Date.now(),
        server_clock_offset_ms: offset,
      })
      // 정식 활성화 성공 → management 토큰 폐기
      this._managementToken = null
      this._managementLicenseKey = null
      return { state: 'ok', expires_at: data.expires_at }
    } catch (e) {
      return { state: 'invalid', reason: 'network_error' }
    }
  }

  /**
   * 캐시된 JWT 의 exp(초 단위 unix) 추출. 파싱 실패 시 null.
   * 서명 검증은 안 함 — 단순 만료 시각 확인용 (서명은 Supabase 가 발급 시점에 검증).
   */
  private getCachedJwtExpMs(token: string): number | null {
    try {
      const parts = token.split('.')
      if (parts.length < 2) return null
      const padded = parts[1] + '='.repeat((4 - (parts[1].length % 4)) % 4)
      const payload = JSON.parse(
        Buffer.from(padded.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'),
      )
      const exp = typeof payload?.exp === 'number' ? payload.exp : Number(payload?.exp)
      if (!Number.isFinite(exp) || exp <= 0) return null
      return exp * 1000
    } catch {
      return null
    }
  }

  /** 검증 (앱 시작 시 + 24h heartbeat) */
  async verify(): Promise<LicenseStatus> {
    const cache = this.loadCache()
    if (!cache) return { state: 'no_license' }

    // B1: 네트워크 verify 이전에 클라이언트측 JWT exp 1차 검사 — 시간 조작이나
    // heartbeat 누락으로 cache 토큰이 이미 만료된 경우 즉시 expired 반환.
    // 서버 시계 오프셋을 보정한 "서버 기준 현재 시각" 으로 비교.
    const jwtExpMs = this.getCachedJwtExpMs(cache.token)
    const serverNowMs = Date.now() + (cache.server_clock_offset_ms || 0)
    if (jwtExpMs !== null && jwtExpMs < serverNowMs) {
      this.clearCache()
      return { state: 'expired' }
    }

    const info = getDeviceInfo()
    try {
      const res = await fetch(`${SUPABASE_URL}/functions/v1/verify`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
        body: JSON.stringify({
          token: cache.token,
          device_id: info.device_id,
        }),
      })
      const data = await res.json()
      if (!data.ok) {
        if (['leaked', 'expired', 'revoked'].includes(data.reason)) {
          this.clearCache()
        }
        return this.mapReason(data.reason, data.device_count, data.max_devices)
      }
      const serverNow = data.server_now ? new Date(data.server_now).getTime() : Date.now()
      cache.last_verified_at = Date.now()
      cache.server_clock_offset_ms = serverNow - Date.now()
      cache.expires_at = data.expires_at
      this.saveCache(cache)
      return { state: 'ok', expires_at: data.expires_at }
    } catch (e) {
      // 네트워크 실패 — grace 적용
      const daysSinceVerify = (Date.now() - cache.last_verified_at) / (24 * 3600 * 1000)
      if (daysSinceVerify < GRACE_DAYS) {
        return { state: 'offline_grace', expires_at: cache.expires_at }
      }
      return { state: 'offline_blocked' }
    }
  }

  /** 캐시된 라이센스 키 (UI 표시용) */
  getCachedLicenseKey(): string | null {
    const cache = this.loadCache()
    return cache?.license_key || null
  }

  clearCache(): void {
    try {
      if (fs.existsSync(this.cachePath)) fs.unlinkSync(this.cachePath)
    } catch {}
    // 캐시 사라지면 만료 타이머도 의미 없음
    if (this._expiryTimer) {
      clearTimeout(this._expiryTimer)
      this._expiryTimer = null
    }
  }

  private mapReason(
    reason: string | undefined,
    device_count?: number,
    max_devices?: number,
    devices?: RegisteredDevice[],
  ): LicenseStatus {
    switch (reason) {
      case 'expired':
        return { state: 'expired' }
      case 'leaked':
        return { state: 'leaked', device_count }
      case 'device_limit_reached':
        return { state: 'device_limit_reached', device_count, max_devices, devices }
      case 'revoked':
        return { state: 'revoked' }
      case 'invalid':
      case 'invalid_token':
      case 'invalid_key_format':
      case 'device_mismatch':
      case 'device_not_registered':
        return { state: 'invalid', reason }
      default:
        return { state: 'invalid', reason: reason || 'unknown' }
    }
  }

  /** 정식 verify 토큰 우선, 없으면 management 토큰 fallback */
  private pickAuthToken(): string | null {
    const cache = this.loadCache()
    if (cache?.token) return cache.token
    return this._managementToken
  }

  /** 등록된 디바이스 목록 조회 */
  async listDevices(): Promise<DeviceListResult> {
    const token = this.pickAuthToken()
    if (!token) return { ok: false, reason: 'no_license' }
    const info = getDeviceInfo()
    try {
      const res = await fetch(`${SUPABASE_URL}/functions/v1/devices-list`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
        body: JSON.stringify({
          token,
          device_id: info.device_id,
        }),
      })
      const data = await res.json()
      return data as DeviceListResult
    } catch {
      return { ok: false, reason: 'network_error' }
    }
  }

  /** 다른 디바이스 삭제 (자기 자신은 서버가 거부) */
  async removeDevice(targetDeviceId: string): Promise<DeviceRemoveResult> {
    const token = this.pickAuthToken()
    if (!token) return { ok: false, reason: 'no_license' }
    const info = getDeviceInfo()
    if (targetDeviceId === info.device_id) {
      // 클라이언트 측 빠른 가드 (서버도 거부함)
      return { ok: false, reason: 'cannot_remove_self' }
    }
    try {
      const res = await fetch(`${SUPABASE_URL}/functions/v1/devices-remove`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
        body: JSON.stringify({
          token,
          device_id: info.device_id,
          target_device_id: targetDeviceId,
        }),
      })
      const data = await res.json()
      return data as DeviceRemoveResult
    } catch {
      return { ok: false, reason: 'network_error' }
    }
  }

  /** 슬롯 정리 후 management 흐름에서 자동 활성화 재시도 */
  async retryActivateWithManagement(): Promise<LicenseStatus> {
    const key = this._managementLicenseKey
    if (!key) return { state: 'no_license' }
    const result = await this.activate(key)
    return result
  }

  private saveCache(c: LocalCache): void {
    const plain = Buffer.from(JSON.stringify(c), 'utf8')
    const iv = crypto.randomBytes(12)
    const cipher = crypto.createCipheriv('aes-256-gcm', ENC_KEY, iv)
    const enc = Buffer.concat([cipher.update(plain), cipher.final()])
    const tag = cipher.getAuthTag()
    const out = Buffer.concat([iv, tag, enc])
    fs.mkdirSync(path.dirname(this.cachePath), { recursive: true })
    fs.writeFileSync(this.cachePath, out)
    // 캐시 갱신될 때마다 만료 자동 verify 타이머 재예약 (활성화/heartbeat 모두 커버)
    this.scheduleExpiryCheck(c)
  }

  private loadCache(): LocalCache | null {
    try {
      if (!fs.existsSync(this.cachePath)) return null
      const data = fs.readFileSync(this.cachePath)
      const iv = data.slice(0, 12)
      const tag = data.slice(12, 28)
      const enc = data.slice(28)
      const decipher = crypto.createDecipheriv('aes-256-gcm', ENC_KEY, iv)
      decipher.setAuthTag(tag)
      const plain = Buffer.concat([decipher.update(enc), decipher.final()])
      return JSON.parse(plain.toString('utf8'))
    } catch (e) {
      return null
    }
  }
}

export const licenseBridge = new LicenseBridge()

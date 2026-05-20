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
  | { state: 'leaked'; device_count?: number }
  | { state: 'revoked' }
  | { state: 'invalid'; reason?: string }
  | { state: 'no_license' }
  | { state: 'offline_grace'; expires_at: string | null }
  | { state: 'offline_blocked' }

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

class LicenseBridge {
  private cachePath: string
  private pendingKeyPath: string

  constructor() {
    const userData = app.getPath('userData')
    this.cachePath = path.join(userData, '.license_cache')
    this.pendingKeyPath = path.join(userData, '.pending_license')
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
        return this.mapReason(data.reason, data.device_count)
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
      return { state: 'ok', expires_at: data.expires_at }
    } catch (e) {
      return { state: 'invalid', reason: 'network_error' }
    }
  }

  /** 검증 (앱 시작 시 + 24h heartbeat) */
  async verify(): Promise<LicenseStatus> {
    const cache = this.loadCache()
    if (!cache) return { state: 'no_license' }

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
        return this.mapReason(data.reason, data.device_count)
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
  }

  private mapReason(reason: string | undefined, device_count?: number): LicenseStatus {
    switch (reason) {
      case 'expired':
        return { state: 'expired' }
      case 'leaked':
        return { state: 'leaked', device_count }
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

  private saveCache(c: LocalCache): void {
    const plain = Buffer.from(JSON.stringify(c), 'utf8')
    const iv = crypto.randomBytes(12)
    const cipher = crypto.createCipheriv('aes-256-gcm', ENC_KEY, iv)
    const enc = Buffer.concat([cipher.update(plain), cipher.final()])
    const tag = cipher.getAuthTag()
    const out = Buffer.concat([iv, tag, enc])
    fs.mkdirSync(path.dirname(this.cachePath), { recursive: true })
    fs.writeFileSync(this.cachePath, out)
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

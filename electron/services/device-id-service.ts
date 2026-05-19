// ============================================================
// device-id-service.ts
// 디바이스 ID 생성 및 관리 서비스
// ============================================================

import { KeyStore } from './keystore'
import { createHash } from 'crypto'
import os from 'os'
import { execFileSync } from 'child_process'

export class DeviceIdService {
  private keystore: KeyStore

  constructor() {
    this.keystore = new KeyStore()
  }

  private getWindowsMachineGuid(): string | null {
    try {
      const output = execFileSync(
        'reg',
        ['query', 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'],
        { encoding: 'utf8' }
      )
      const match = output.match(/MachineGuid\s+REG_SZ\s+([^\s]+)/i)
      return match?.[1]?.trim() || null
    } catch {
      return null
    }
  }

  private getHardwareId(): string {
    if (process.platform !== 'win32') {
      throw new Error('Windows only: Hardware ID not available')
    }

    const guid = this.getWindowsMachineGuid()
    if (!guid) {
      throw new Error('Hardware ID not available')
    }

    return guid
  }

  private hashDeviceId(rawId: string): string {
    const namespace = 'inserty-ai-device-v1'
    return createHash('sha256').update(`${namespace}:${rawId}`).digest('hex')
  }

  /**
   * 디바이스 ID 가져오기 (하드웨어 기반)
   */
  async getDeviceId(): Promise<string> {
    const cachedId = await this.keystore.get('device:id')
    const source = await this.keystore.get('device:source')
    const createdAt = await this.keystore.get('device:created_at')

    if (cachedId && source === 'hardware') {
      return cachedId
    }

    const hardwareId = this.getHardwareId()
    const hashedId = this.hashDeviceId(hardwareId)

    await this.keystore.set('device:id', hashedId)
    await this.keystore.set('device:source', 'hardware')
    if (!createdAt) {
      await this.keystore.set('device:created_at', new Date().toISOString())
    }

    return hashedId
  }

  /**
   * 디바이스 정보 가져오기
   */
  async getDeviceInfo(): Promise<{
    deviceId: string
    platform: string
    hostname: string
    arch: string
    osVersion: string
    createdAt: string | null
    source: string | null
  }> {
    const deviceId = await this.getDeviceId()
    const createdAt = await this.keystore.get('device:created_at')
    const source = await this.keystore.get('device:source')

    return {
      deviceId,
      platform: process.platform,
      hostname: os.hostname(),
      arch: process.arch,
      osVersion: os.release(),
      createdAt,
      source
    }
  }

  /**
   * 디바이스 ID 재생성 (테스트/디버그용)
   */
  async regenerateDeviceId(): Promise<string> {
    await this.keystore.delete('device:id')
    await this.keystore.delete('device:created_at')
    await this.keystore.delete('device:source')
    return await this.getDeviceId()
  }
}

// Singleton instance
let deviceIdService: DeviceIdService | null = null

export function getDeviceIdService(): DeviceIdService {
  if (!deviceIdService) {
    deviceIdService = new DeviceIdService()
  }
  return deviceIdService
}

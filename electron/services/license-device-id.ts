// Device ID 생성 — node-machine-id + OS UUID 해시
// 라이센스에 디바이스를 식별하기 위해 사용

import { machineIdSync } from 'node-machine-id'
import * as crypto from 'crypto'
import * as os from 'os'

let cachedId: string | null = null

/**
 * 안정적인 디바이스 ID 생성 (해시).
 * raw machine-id를 직접 노출하지 않고 SHA-256 해시의 앞 32자를 사용.
 */
export function getDeviceId(): string {
  if (cachedId) return cachedId
  try {
    const machineId = machineIdSync(true)  // OS 수준 UUID
    cachedId = crypto.createHash('sha256').update(machineId).digest('hex').slice(0, 32)
  } catch (e) {
    // 폴백: hostname + arch
    const fallback = `${os.hostname()}-${os.arch()}-${os.platform()}`
    cachedId = crypto.createHash('sha256').update(fallback).digest('hex').slice(0, 32)
  }
  return cachedId
}

export interface DeviceInfo {
  device_id: string
  device_name: string
  device_os: string
}

export function getDeviceInfo(): DeviceInfo {
  return {
    device_id: getDeviceId(),
    device_name: os.hostname(),
    device_os: `${os.platform()} ${os.release()}`,
  }
}

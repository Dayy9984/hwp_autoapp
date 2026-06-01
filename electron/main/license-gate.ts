// Electron main 프로세스 — 라이센스 게이트 IPC 핸들러
// App 시작 시 main/index.ts에서 호출

import { EventEmitter } from 'node:events'
import { BrowserWindow, ipcMain, shell } from 'electron'
import { licenseBridge, LicenseStatus } from '../services/license-bridge'
import { pushBetaCredsNow } from '../services/beta-creds-push'
import { announcementFetcher } from '../services/announcement-fetcher'

let lastStatus: LicenseStatus = { state: 'no_license' }

/**
 * 라이센스 상태 변화 이벤트 — main/index.ts 가 구독해서 stream cancel + renderer 통지.
 *  - 'change'(prev, next): lastStatus 가 갱신될 때마다 발생.
 *  - 'revoked'(prev, next): ok|offline_grace → 그 외 상태로 전이될 때만 발생.
 */
export const licenseEvents = new EventEmitter()

/** 라이센스가 작업 가능한 상태인지 (ok 또는 offline_grace) */
export function isLicenseOk(): boolean {
  return lastStatus.state === 'ok' || lastStatus.state === 'offline_grace'
}

/**
 * IPC 핸들러 진입 가드. 라이센스가 ok 가 아니면 즉시 throw.
 * Frontend 가 catch 한 message 에서 LICENSE_ 접두사를 보고 verify 강제 호출.
 */
export function assertLicenseOk(_channel?: string): void {
  if (isLicenseOk()) return
  const state = (lastStatus.state || 'blocked').toString().toUpperCase()
  // expired/revoked/leaked/device_limit_reached/invalid/no_license/offline_blocked
  // 모두 LICENSE_<STATE> 로 통일. Python 의 LICENSE_REQUIRED / LICENSE_EXPIRED 와
  // 일관된 접두사 → renderer 의 단일 catch 로직이 모두 처리.
  throw new Error(`LICENSE_${state}`)
}

/** 상태 갱신 + 전이 이벤트 발행 (license-gate 내부 헬퍼) */
function updateLastStatus(next: LicenseStatus): void {
  const prev = lastStatus
  lastStatus = next
  const wasOk = prev.state === 'ok' || prev.state === 'offline_grace'
  const isOk = next.state === 'ok' || next.state === 'offline_grace'
  licenseEvents.emit('change', prev, next)
  if (wasOk && !isOk) {
    licenseEvents.emit('revoked', prev, next)
  }
  // renderer 에 새 상태 push — App.tsx 의 onStatusChanged 리스너가 LicenseGate 갱신.
  try {
    for (const w of BrowserWindow.getAllWindows()) {
      if (!w.isDestroyed()) w.webContents.send('license:statusChanged', next)
    }
  } catch {}
}

export function registerLicenseHandlers() {
  // 패치 B: license-bridge 의 만료 자동 verify 콜백 등록.
  // expires_at 도래 시 license-bridge 가 verify() 자동 호출 → 결과를 이 콜백으로
  // 전달 → updateLastStatus 가 lastStatus 갱신 + renderer 에 statusChanged push +
  // revoked 이벤트 발행 → main/index.ts 가 active stream cancel + LicenseGate 노출.
  // 사용자가 아무 동작 없이 앱을 열어둔 상태에서도 만료 즉시 차단됨.
  licenseBridge.setAutoVerifyCallback((status) => {
    updateLastStatus(status)
  })

  // 활성화
  ipcMain.handle('license:activate', async (_evt, license_key: string) => {
    const result = await licenseBridge.activate(license_key)
    updateLastStatus(result)
    if (result.state === 'ok') {
      // Python 에 verify 토큰 + device_id 푸시 (베타 trace)
      pushBetaCredsNow().catch(() => {})
      // 알림 즉시 fetch (사용자가 활성화 후 알림 종 클릭 전 미리 채워둠)
      announcementFetcher.fetchNow().catch(() => {})
    }
    return result
  })

  // 검증 (앱 시작 시 + 24h heartbeat + Frontend 강제 재verify)
  // forceReverify 별칭으로도 노출 (preload 가 license.forceReverify 로 호출 가능).
  ipcMain.handle('license:verify', async () => {
    const result = await licenseBridge.verify()
    updateLastStatus(result)
    return result
  })

  // 인스톨러가 저장한 pending key 자동 활성화
  ipcMain.handle('license:tryPendingKey', async () => {
    const key = licenseBridge.getPendingKey()
    if (!key) return null
    const result = await licenseBridge.activate(key)
    if (result.state === 'ok') {
      licenseBridge.consumePendingKey()
    }
    updateLastStatus(result)
    return result
  })

  // 현재 캐시된 라이센스 키 (UI 표시용)
  ipcMain.handle('license:getCachedKey', () => {
    return licenseBridge.getCachedLicenseKey()
  })

  // 외부 링크 열기 (카카오톡 등)
  ipcMain.handle('license:openExternal', async (_evt, url: string) => {
    if (typeof url === 'string' && url.startsWith('https://')) {
      await shell.openExternal(url)
    }
  })

  // 등록된 디바이스 목록 조회 (DeviceManagerScreen 용)
  ipcMain.handle('license:listDevices', async () => {
    return await licenseBridge.listDevices()
  })

  // 다른 디바이스 삭제 (자기 자신은 서버 측에서 거부됨)
  ipcMain.handle('license:removeDevice', async (_evt, targetDeviceId: string) => {
    return await licenseBridge.removeDevice(targetDeviceId)
  })

  // 슬롯 정리 후 management 흐름에서 자동 활성화 재시도 → 성공 시 ok 상태
  ipcMain.handle('license:retryActivate', async () => {
    const result = await licenseBridge.retryActivateWithManagement()
    updateLastStatus(result)
    return result
  })
}

/**
 * 앱 시작 시 호출 — BrowserWindow 생성 전에 라이센스 검증.
 * 결과가 ok 또는 offline_grace면 메인 UI 진입. 아니면 차단 화면 표시.
 */
export async function initialLicenseCheck(): Promise<LicenseStatus> {
  // 1. 인스톨러에서 저장한 pending key 가 있으면 자동 활성화
  const pendingKey = licenseBridge.getPendingKey()
  if (pendingKey) {
    const result = await licenseBridge.activate(pendingKey)
    if (result.state === 'ok') {
      licenseBridge.consumePendingKey()
      updateLastStatus(result)
      return result
    }
    // 활성화 결과가 "의미 있는 거절" 이면 그 상태를 그대로 노출.
    // (verify 로 덮어쓰면 캐시가 없을 때 no_license 로 떨어져 사용자가
    //  실제 차단 사유 — device_limit_reached/expired/revoked — 를 못 봄.)
    const blockingStates: LicenseStatus['state'][] = [
      'device_limit_reached',
      'expired',
      'revoked',
      'leaked',
    ]
    if (blockingStates.includes(result.state)) {
      updateLastStatus(result)
      return result
    }
    // 그 외 (invalid_*, network_error 등) → 아래 verify 로 fallback
  }

  // 2. 캐시된 토큰으로 검증
  const verify = await licenseBridge.verify()
  updateLastStatus(verify)
  // 패치 B: 앱 시작 직후 캐시 기반 만료 타이머 재예약. saveCache 가 호출되지
  // 않은 경우 (verify 가 네트워크 실패로 offline_grace 반환) 에도 디스크 캐시
  // 기준으로 타이머 등록 → 시스템 시계 도달 시 자동 verify → 만료 차단 또는
  // offline_blocked 전환.
  try {
    licenseBridge.rescheduleExpiryCheck()
  } catch (e) {
    console.warn('[License] rescheduleExpiryCheck error:', e)
  }
  return verify
}

export function getLastStatus(): LicenseStatus {
  return lastStatus
}

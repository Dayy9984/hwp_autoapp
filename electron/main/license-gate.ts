// Electron main 프로세스 — 라이센스 게이트 IPC 핸들러
// App 시작 시 main/index.ts에서 호출

import { ipcMain, shell } from 'electron'
import { licenseBridge, LicenseStatus } from '../services/license-bridge'
import { pushBetaCredsNow } from '../services/beta-creds-push'
import { announcementFetcher } from '../services/announcement-fetcher'

let lastStatus: LicenseStatus = { state: 'no_license' }

export function registerLicenseHandlers() {
  // 활성화
  ipcMain.handle('license:activate', async (_evt, license_key: string) => {
    const result = await licenseBridge.activate(license_key)
    lastStatus = result
    if (result.state === 'ok') {
      // Python 에 verify 토큰 + device_id 푸시 (베타 trace)
      pushBetaCredsNow().catch(() => {})
      // 알림 즉시 fetch (사용자가 활성화 후 알림 종 클릭 전 미리 채워둠)
      announcementFetcher.fetchNow().catch(() => {})
    }
    return result
  })

  // 검증 (앱 시작 시 + 24h heartbeat)
  ipcMain.handle('license:verify', async () => {
    const result = await licenseBridge.verify()
    lastStatus = result
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
    lastStatus = result
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
    lastStatus = result
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
      lastStatus = result
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
      lastStatus = result
      return result
    }
    // 그 외 (invalid_*, network_error 등) → 아래 verify 로 fallback
  }

  // 2. 캐시된 토큰으로 검증
  const verify = await licenseBridge.verify()
  lastStatus = verify
  return verify
}

export function getLastStatus(): LicenseStatus {
  return lastStatus
}

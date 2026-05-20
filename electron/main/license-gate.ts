// Electron main 프로세스 — 라이센스 게이트 IPC 핸들러
// App 시작 시 main/index.ts에서 호출

import { ipcMain, shell } from 'electron'
import { licenseBridge, LicenseStatus } from '../services/license-bridge'

let lastStatus: LicenseStatus = { state: 'no_license' }

export function registerLicenseHandlers() {
  // 활성화
  ipcMain.handle('license:activate', async (_evt, license_key: string) => {
    const result = await licenseBridge.activate(license_key)
    lastStatus = result
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
}

/**
 * 앱 시작 시 호출 — BrowserWindow 생성 전에 라이센스 검증.
 * 결과가 ok 또는 offline_grace면 메인 UI 진입. 아니면 차단 화면 표시.
 */
export async function initialLicenseCheck(): Promise<LicenseStatus> {
  // 1. 인스톨러에서 저장한 pending key가 있으면 자동 활성화
  const pendingKey = licenseBridge.getPendingKey()
  if (pendingKey) {
    const result = await licenseBridge.activate(pendingKey)
    if (result.state === 'ok') {
      licenseBridge.consumePendingKey()
      lastStatus = result
      return result
    }
    // activate 실패 (만료된 시험판 등) → consumePendingKey 없이 다음 단계로
  }

  // 2. 캐시된 토큰으로 검증
  const verify = await licenseBridge.verify()
  lastStatus = verify
  return verify
}

export function getLastStatus(): LicenseStatus {
  return lastStatus
}

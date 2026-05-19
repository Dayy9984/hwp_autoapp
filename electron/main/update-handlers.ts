// ============================================================
// update-handlers.ts
// 앱 업데이트 IPC 핸들러 (electron-updater 기반)
// ============================================================

import { ipcMain, BrowserWindow } from 'electron'
import { getAutoUpdateService } from '../services/auto-update-service'

export function registerUpdateHandlers(mainWindow?: BrowserWindow) {
  const autoUpdateService = getAutoUpdateService()

  // 메인 윈도우 설정
  if (mainWindow) {
    autoUpdateService.setMainWindow(mainWindow)
  }

  // update:check - 업데이트 확인
  ipcMain.handle('update:check', async () => {
    return await autoUpdateService.checkForUpdates()
  })

  // update:download - 업데이트 다운로드 시작
  ipcMain.handle('update:download', async () => {
    return await autoUpdateService.downloadUpdate()
  })

  // update:install - 업데이트 설치 및 재시작
  ipcMain.handle('update:install', async () => {
    autoUpdateService.quitAndInstall()
    return { success: true }
  })

  // update:getCurrentVersion - 현재 버전 정보
  ipcMain.handle('update:getCurrentVersion', async () => {
    return {
      success: true,
      version: autoUpdateService.getCurrentVersion(),
      downloadedVersion: autoUpdateService.getDownloadedVersion()
    }
  })

  // update:startPeriodicCheck - 주기적 체크 시작
  ipcMain.handle('update:startPeriodicCheck', async (_event, intervalMs?: number) => {
    autoUpdateService.startPeriodicCheck(intervalMs)
    return { success: true }
  })

  // update:stopPeriodicCheck - 주기적 체크 중지
  ipcMain.handle('update:stopPeriodicCheck', async () => {
    autoUpdateService.stopPeriodicCheck()
    return { success: true }
  })

  // update:getLastStatus - 마지막 의미 있는 상태 조회 (renderer 늦은 연결 복구용)
  ipcMain.handle('update:getLastStatus', async () => {
    const lastStatus = autoUpdateService.getLastStatus()
    return { success: true, status: lastStatus }
  })
}

/**
 * 메인 윈도우 설정 (앱 시작 후 호출)
 */
export function setUpdateMainWindow(mainWindow: BrowserWindow) {
  const autoUpdateService = getAutoUpdateService()
  autoUpdateService.setMainWindow(mainWindow)
}

/**
 * 주기적 업데이트 체크 시작 (앱 시작 시 호출)
 */
export function startAutoUpdateCheck(intervalMs = 60 * 60 * 1000) {
  const autoUpdateService = getAutoUpdateService()
  autoUpdateService.startPeriodicCheck(intervalMs)
}

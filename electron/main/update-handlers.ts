// ============================================================
// update-handlers.ts
// 앱 업데이트 IPC 핸들러 (electron-updater 기반)
// ============================================================

import path from 'node:path'
import { ipcMain, BrowserWindow, app } from 'electron'
import { getAutoUpdateService } from '../services/auto-update-service'

// 업데이트 진행 중에는 메인 창을 닫더라도 앱이 종료되면 안 됨.
// quitAndInstall 호출 시 명시적으로 종료되도록 플래그로 제어.
let progressWindow: BrowserWindow | null = null
let isUpdatingFlow = false

export function isUpdatingNow(): boolean { return isUpdatingFlow }

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

  // update:startInstallFlow — 다운로드 시작 + 별도 진행률 창 표시 + 메인 창 닫기.
  // 사용자가 "다운로드" 버튼 클릭 시 이 핸들러를 호출하면 단일 흐름으로 install 까지 자동 진행.
  ipcMain.handle('update:startInstallFlow', async (_event) => {
    if (isUpdatingFlow) return { success: false, error: 'already-in-progress' }
    isUpdatingFlow = true

    // 1. 진행률 전용 BrowserWindow 생성
    const VITE_DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL
    const preload = path.join(app.getAppPath(), 'dist-electron', 'preload', 'index.js')
    const indexHtml = path.join(app.getAppPath(), 'dist', 'index.html')

    progressWindow = new BrowserWindow({
      title: 'Inserty AI 업데이트',
      width: 480,
      height: 320,
      resizable: false,
      maximizable: false,
      minimizable: true,
      fullscreenable: false,
      autoHideMenuBar: true,
      backgroundColor: '#FFFFFF',
      icon: path.join(process.env.VITE_PUBLIC || '', 'favicon.ico'),
      webPreferences: { preload },
    })

    const routeHash = '#update-progress'
    if (VITE_DEV_SERVER_URL) {
      void progressWindow.loadURL(VITE_DEV_SERVER_URL + routeHash)
    } else {
      void progressWindow.loadFile(indexHtml, { hash: 'update-progress' })
    }
    progressWindow.center()

    // 2. autoUpdateService 가 progress window 로도 이벤트 보내도록 등록
    autoUpdateService.addBroadcastWindow(progressWindow)

    // 3. 메인 창 닫기
    const wins = BrowserWindow.getAllWindows()
    for (const w of wins) {
      if (w !== progressWindow && !w.isDestroyed()) {
        w.close()
      }
    }

    // 4. progress window 가 ready 된 후 download 시작 (race condition 방어)
    const waitReady = new Promise<void>((resolve) => {
      if (!progressWindow) return resolve()
      if (!progressWindow.webContents.isLoading()) return resolve()
      progressWindow.webContents.once('did-finish-load', () => {
        // React mount + onStatus listener 등록 시간 확보
        setTimeout(resolve, 800)
      })
    })
    await waitReady

    const lastStatus = autoUpdateService.getLastStatus()
    if (lastStatus?.status === 'downloaded') {
      return { success: true, alreadyDownloaded: true }
    }

    const result = await autoUpdateService.downloadUpdate()
    return result
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

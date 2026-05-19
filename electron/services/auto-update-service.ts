// ============================================================
// auto-update-service.ts
// electron-updater 기반 자동 업데이트 서비스
// ============================================================

import { app, BrowserWindow } from 'electron'
import { EventEmitter } from 'events'

// Lazy-loaded autoUpdater (ESM/CJS 호환을 위해 동적 로딩)
let autoUpdater: any = null

async function getAutoUpdater() {
  if (!autoUpdater) {
    const electronUpdater = await import('electron-updater')
    autoUpdater = electronUpdater.autoUpdater || electronUpdater.default?.autoUpdater
  }
  return autoUpdater
}

// Type definitions for electron-updater
interface UpdateInfo {
  version: string
  releaseDate: string
  releaseNotes?: string | null
}

interface ProgressInfo {
  total: number
  delta: number
  transferred: number
  percent: number
  bytesPerSecond: number
}

export interface UpdateStatus {
  status: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  info?: UpdateInfo
  progress?: ProgressInfo
  error?: string
  isCritical?: boolean  // 필수 업데이트 여부
  releaseNotes?: string // 릴리스 노트
}

export class AutoUpdateService extends EventEmitter {
  private checkIntervalId: NodeJS.Timeout | null = null
  private mainWindow: BrowserWindow | null = null
  private isDownloading = false
  private downloadedVersion: string | null = null
  private pendingCriticalUpdate: { version: string; releaseNotes?: string } | null = null
  private isInitialized = false
  // 마지막 의미 있는 상태 캐시 (renderer가 늦게 연결되어도 놓치지 않도록)
  private lastSignificantStatus: UpdateStatus | null = null

  constructor() {
    super()
  }

  /**
   * 초기화 (lazy)
   */
  private async ensureInitialized() {
    if (this.isInitialized) return

    try {
      const updater = await getAutoUpdater()
      if (updater) {
        this.setupAutoUpdater(updater)
        this.isInitialized = true
      }
    } catch (error) {
      console.error('[AutoUpdate] Failed to initialize:', error)
    }
  }

  /**
   * 메인 윈도우 설정 (IPC 통신용)
   */
  setMainWindow(window: BrowserWindow) {
    this.mainWindow = window
  }

  /**
   * autoUpdater 초기 설정
   */
  private setupAutoUpdater(updater: any) {
    const updateFeedUrl = process.env.UPDATE_FEED_URL
    if (!updateFeedUrl) {
      console.log('[AutoUpdate] UPDATE_FEED_URL not set, auto-update disabled')
      return
    }
    updater.setFeedURL({ provider: 'generic', url: updateFeedUrl })

    // 자동 다운로드 비활성화 (사용자 확인 후 다운로드)
    updater.autoDownload = false
    updater.autoInstallOnAppQuit = true

    // 이벤트 핸들러 등록
    updater.on('checking-for-update', () => {
      console.log('[AutoUpdate] Checking for updates...')
      this.sendStatusToRenderer({ status: 'checking' })
    })

    updater.on('update-available', (info: UpdateInfo) => {
      console.log('[AutoUpdate] Update available:', info.version)
      const isCritical = false
      const releaseNotes = undefined

      this.sendStatusToRenderer({ status: 'available', info, isCritical, releaseNotes })
      this.emit('update-available', { ...info, isCritical, releaseNotes })
    })

    updater.on('update-not-available', (info: UpdateInfo) => {
      console.log('[AutoUpdate] No updates available:', info.version)
      this.sendStatusToRenderer({ status: 'not-available', info })
    })

    updater.on('download-progress', (progress: ProgressInfo) => {
      console.log(`[AutoUpdate] Download progress: ${progress.percent.toFixed(1)}%`)
      this.sendStatusToRenderer({ status: 'downloading', progress })
    })

    updater.on('update-downloaded', (info: UpdateInfo) => {
      console.log('[AutoUpdate] Update downloaded:', info.version)
      this.isDownloading = false
      this.downloadedVersion = info.version
      this.sendStatusToRenderer({ status: 'downloaded', info })
      this.emit('update-downloaded', info)
    })

    updater.on('error', (error: Error) => {
      console.error('[AutoUpdate] Error:', error.message)
      this.isDownloading = false
      this.sendStatusToRenderer({ status: 'error', error: error.message })
      this.emit('error', error)
    })
  }

  /**
   * Renderer 프로세스에 상태 전송
   */
  private sendStatusToRenderer(status: UpdateStatus) {
    // 의미 있는 상태는 캐시 (available, downloaded - renderer가 놓쳤을 때 복구용)
    if (status.status === 'available' || status.status === 'downloaded') {
      this.lastSignificantStatus = status
    }
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('auto-update:status', status)
    }
  }

  /**
   * 마지막 의미 있는 상태 조회 (renderer가 늦게 연결될 때 사용)
   */
  getLastStatus(): UpdateStatus | null {
    return this.lastSignificantStatus
  }

  /**
   * 업데이트 확인
   */
  async checkForUpdates(): Promise<{ success: boolean; updateAvailable?: boolean; version?: string; error?: string }> {
    try {
      await this.ensureInitialized()
      const updater = await getAutoUpdater()
      if (!updater) {
        return { success: false, error: 'AutoUpdater not available' }
      }

      const result = await updater.checkForUpdates()
      if (result?.updateInfo) {
        const currentVersion = app.getVersion()
        const latestVersion = result.updateInfo.version
        const updateAvailable = this.compareVersions(latestVersion, currentVersion) > 0

        return {
          success: true,
          updateAvailable,
          version: latestVersion
        }
      }
      return { success: true, updateAvailable: false }
    } catch (error) {
      console.error('[AutoUpdate] Check error:', error)
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error'
      }
    }
  }

  /**
   * 업데이트 다운로드 시작
   */
  async downloadUpdate(): Promise<{ success: boolean; error?: string }> {
    if (this.isDownloading) {
      return { success: false, error: 'Download already in progress' }
    }

    try {
      await this.ensureInitialized()
      const updater = await getAutoUpdater()
      if (!updater) {
        return { success: false, error: 'AutoUpdater not available' }
      }

      this.isDownloading = true
      // await 하지 않음 — downloadUpdate()는 완료까지 수 분 소요
      // 진행률/완료는 download-progress / update-downloaded 이벤트로 처리
      updater.downloadUpdate().catch((error: Error) => {
        this.isDownloading = false
        console.error('[AutoUpdate] Download error:', error)
        this.sendStatusToRenderer({ status: 'error', error: error.message })
      })
      return { success: true }
    } catch (error) {
      this.isDownloading = false
      console.error('[AutoUpdate] Download init error:', error)
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error'
      }
    }
  }

  /**
   * 업데이트 설치 및 앱 재시작
   */
  async quitAndInstall() {
    console.log('[AutoUpdate] Installing update and restarting...')
    const updater = await getAutoUpdater()
    if (updater) {
      try {
        // isSilent=true: NSIS /S 플래그 → 설치 마법사 UI 없이 조용히 설치
        // installer.nsh의 ${Silent} 분기로 업그레이드 모드(데이터 보존) 처리됨
        updater.quitAndInstall(true, true)
      } catch (error) {
        console.error('[AutoUpdate] quitAndInstall error:', error)
      }
    }
  }

  /**
   * 주기적 업데이트 체크 시작
   */
  startPeriodicCheck(intervalMs = 60 * 60 * 1000) {
    // 앱 시작 10초 후 첫 체크
    setTimeout(() => {
      void this.checkForUpdates()
    }, 10_000)

    // 주기적 체크
    this.checkIntervalId = setInterval(() => {
      void this.checkForUpdates()
    }, intervalMs)
  }

  /**
   * 주기적 체크 중지
   */
  stopPeriodicCheck() {
    if (this.checkIntervalId) {
      clearInterval(this.checkIntervalId)
      this.checkIntervalId = null
    }
  }

  /**
   * 현재 버전 정보
   */
  getCurrentVersion(): string {
    return app.getVersion()
  }

  /**
   * 다운로드 완료된 버전 정보
   */
  getDownloadedVersion(): string | null {
    return this.downloadedVersion
  }

  /**
   * 필수 업데이트 대기 중인지 확인
   */
  hasPendingCriticalUpdate(): boolean {
    return this.pendingCriticalUpdate !== null
  }

  /**
   * 대기 중인 필수 업데이트 정보
   */
  getPendingCriticalUpdate(): { version: string; releaseNotes?: string } | null {
    return this.pendingCriticalUpdate
  }

  /**
   * 버전 비교 (semver)
   * @returns 1 if v1 > v2, -1 if v1 < v2, 0 if equal
   */
  private compareVersions(v1: string, v2: string): number {
    const parts1 = v1.split('.').map(Number)
    const parts2 = v2.split('.').map(Number)

    for (let i = 0; i < Math.max(parts1.length, parts2.length); i++) {
      const p1 = parts1[i] || 0
      const p2 = parts2[i] || 0

      if (p1 > p2) return 1
      if (p1 < p2) return -1
    }

    return 0
  }
}

// Singleton instance
let autoUpdateService: AutoUpdateService | null = null

export function getAutoUpdateService(): AutoUpdateService {
  if (!autoUpdateService) {
    autoUpdateService = new AutoUpdateService()
  }
  return autoUpdateService
}

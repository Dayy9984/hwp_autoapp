// ============================================================
// auto-update-service.ts
// electron-updater 기반 자동 업데이트 서비스.
//
// 아키텍처 (beta.3+):
//   클라이언트는 PAT 를 가지지 않음. Cloudflare Worker 가 PAT 보유하고
//   GitHub private release 와 사용자 사이에서 proxy 역할.
//
//   electron-updater → Worker /auto-update/latest.yml → GitHub releases/latest
//                    → Worker /auto-update/<file>     → 302 signed URL → GitHub CDN
//
//   이전: PAT inject (보안 hole — bundle 노출 시 private repo 접근 가능)
//   현재: provider=generic, url=Worker endpoint
//
// 기타:
//   - lastSignificantStatus 에 error / not-available 도 캐싱 → renderer 가
//     늦게 마운트되어도 모든 상태를 복구.
//   - checkForUpdates 재진입 가드: 동시 다중 호출 방지.
//   - UpdateInfo.releaseNotes 안전 직렬화 (string | string[]).
// ============================================================

import { app, BrowserWindow } from 'electron'
import { EventEmitter } from 'events'

// Lazy-loaded autoUpdater (ESM/CJS 호환)
let autoUpdater: any = null

async function getAutoUpdater() {
  if (!autoUpdater) {
    const electronUpdater = await import('electron-updater')
    autoUpdater = electronUpdater.autoUpdater || electronUpdater.default?.autoUpdater
  }
  return autoUpdater
}

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
  status: 'idle' | 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  info?: UpdateInfo
  progress?: ProgressInfo
  error?: string
  isCritical?: boolean
  releaseNotes?: string
}

// Worker proxy endpoint. PAT 는 Worker 측에서만 보유.
// 변경 시 Worker (inserty-beta-admin/worker/src/auto-update.ts) 와
// electron-builder.json 의 publish.url 도 함께 갱신.
const UPDATE_FEED_URL = 'https://inserty-beta-worker.snsoffice.workers.dev/auto-update/'
const LATEST_YML_URL = UPDATE_FEED_URL + 'latest.yml'

/**
 * latest.yml 을 raw fetch 해서 isCritical custom field 를 파싱.
 * electron-updater 는 알 수 없는 field 를 무시하므로 별도 fetch 가 필요.
 *
 * Worker 가 X-Is-Critical 헤더도 같이 내려주므로 헤더 우선, fallback 으로 body 파싱.
 */
async function fetchIsCriticalForVersion(version: string): Promise<boolean> {
  try {
    const res = await fetch(LATEST_YML_URL + `?v=${encodeURIComponent(version)}&_=${Date.now()}`, {
      method: 'GET',
      headers: { 'Cache-Control': 'no-cache' },
    })
    if (!res.ok) return false

    // 1차: X-Is-Critical 헤더 (Worker 가 inject).
    const hdr = res.headers.get('X-Is-Critical')
    if (hdr) return hdr.toLowerCase() === 'true'

    // 2차: body 파싱 — `isCritical: true` 라인 (Worker 가 inject).
    const body = await res.text()
    return /^isCritical:\s*true\s*$/im.test(body)
  } catch (e) {
    console.warn('[AutoUpdate] fetchIsCriticalForVersion failed:', (e as Error).message)
    return false
  }
}

export class AutoUpdateService extends EventEmitter {
  private checkIntervalId: NodeJS.Timeout | null = null
  private mainWindow: BrowserWindow | null = null
  private isDownloading = false
  private downloadedVersion: string | null = null
  private isInitialized = false
  private isChecking = false
  private lastSignificantStatus: UpdateStatus | null = null

  constructor() {
    super()
  }

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

  setMainWindow(window: BrowserWindow) {
    this.mainWindow = window
    // 창이 destroy 될 때 자동으로 참조 해제 → 이후 push 시도 race condition 방어
    window.once('closed', () => {
      if (this.mainWindow === window) {
        this.mainWindow = null
      }
    })
    // 마지막 상태가 있으면 push (창이 늦게 떴을 때 복구)
    if (this.lastSignificantStatus) {
      this.pushToWindow(this.lastSignificantStatus)
    }
  }

  private setupAutoUpdater(updater: any) {
    // Worker proxy — PAT 클라이언트 미보유.
    // Worker 가 /auto-update/latest.yml + /auto-update/<file> 로 GitHub 와 통신.
    updater.setFeedURL({
      provider: 'generic',
      url: UPDATE_FEED_URL,
      channel: 'latest',
    } as any)

    // autoDownload=false: 사용자가 [업데이트] 클릭한 후에 다운로드 시작.
    // 진행률 창이 download → install 까지 단일 흐름으로 표시.
    updater.autoDownload = false
    updater.autoInstallOnAppQuit = false

    updater.on('checking-for-update', () => {
      console.log('[AutoUpdate] Checking for updates...')
      this.sendStatusToRenderer({ status: 'checking' })
    })

    updater.on('update-available', (info: UpdateInfo) => {
      console.log('[AutoUpdate] Update available:', info.version)
      const releaseNotes = this.normalizeReleaseNotes((info as any).releaseNotes)

      // 우선 즉시 available 상태 push (isCritical 미정).
      this.sendStatusToRenderer({ status: 'available', info, releaseNotes })
      this.emit('update-available', { ...info, releaseNotes })

      // 백그라운드에서 isCritical 조회 후 보강된 상태 재push.
      // electron-updater 는 unknown yaml field 를 무시하므로 별도 raw fetch 가 필요.
      void fetchIsCriticalForVersion(info.version).then((isCritical) => {
        if (isCritical) {
          console.log('[AutoUpdate] Marked as CRITICAL:', info.version)
          this.sendStatusToRenderer({ status: 'available', info, releaseNotes, isCritical: true })
          this.emit('update-available', { ...info, releaseNotes, isCritical: true })
        }
      })
    })

    updater.on('update-not-available', (info: UpdateInfo) => {
      console.log('[AutoUpdate] No updates available:', info.version)
      this.sendStatusToRenderer({ status: 'not-available', info })
    })

    updater.on('download-progress', (progress: ProgressInfo) => {
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

  private normalizeReleaseNotes(notes: string | string[] | null | undefined): string | undefined {
    if (!notes) return undefined
    if (Array.isArray(notes)) return notes.filter((s) => typeof s === 'string').join('\n\n')
    return typeof notes === 'string' ? notes : undefined
  }

  private extraBroadcastWindows: Set<BrowserWindow> = new Set()
  private lastDownloadingStatus: UpdateStatus | null = null

  /** 추가 broadcast 대상 (메인 외 update progress 창 등). */
  addBroadcastWindow(w: BrowserWindow) {
    this.extraBroadcastWindows.add(w)
    w.once('closed', () => this.extraBroadcastWindows.delete(w))

    // 윈도우 첫 로드 완료 시점에 최신 progress / lastStatus push.
    // (events fire 시점에 renderer listener 미등록 race 방어)
    const sendCurrent = () => {
      try {
        if (w.isDestroyed()) return
        if (this.lastDownloadingStatus) {
          w.webContents.send('auto-update:status', this.lastDownloadingStatus)
        } else if (this.lastSignificantStatus) {
          w.webContents.send('auto-update:status', this.lastSignificantStatus)
        }
      } catch {}
    }

    if (w.webContents.isLoading()) {
      w.webContents.once('did-finish-load', () => setTimeout(sendCurrent, 500))
    } else {
      sendCurrent()
    }
  }

  private pushToWindow(status: UpdateStatus) {
    const targets = [this.mainWindow, ...this.extraBroadcastWindows].filter(Boolean) as BrowserWindow[]
    for (const w of targets) {
      if (!w || w.isDestroyed()) continue
      try {
        const wc = w.webContents
        if (!wc || wc.isDestroyed()) continue
        wc.send('auto-update:status', status)
      } catch (err) {
        console.warn('[AutoUpdate] pushToWindow skipped:', (err as Error).message)
      }
    }
  }

  private sendStatusToRenderer(status: UpdateStatus) {
    if (status.status !== 'checking' && status.status !== 'downloading') {
      this.lastSignificantStatus = status
    }
    // downloading 도 별도 캐싱 — 새로 띄운 window 가 즉시 현재 % 받을 수 있게.
    if (status.status === 'downloading') {
      this.lastDownloadingStatus = status
    }
    this.pushToWindow(status)
  }

  getLastStatus(): UpdateStatus | null {
    return this.lastSignificantStatus
  }

  async checkForUpdates(): Promise<{
    success: boolean
    updateAvailable?: boolean
    version?: string
    error?: string
  }> {
    if (this.isChecking) {
      return { success: false, error: 'check already in progress' }
    }
    this.isChecking = true
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
        return { success: true, updateAvailable, version: latestVersion }
      }
      return { success: true, updateAvailable: false }
    } catch (error) {
      console.error('[AutoUpdate] Check error:', error)
      const msg = error instanceof Error ? error.message : 'Unknown error'
      this.sendStatusToRenderer({ status: 'error', error: msg })
      return { success: false, error: msg }
    } finally {
      this.isChecking = false
    }
  }

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
      // 진행률 / 완료는 이벤트로 처리. await 하지 않음 (수 분 소요).
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
        error: error instanceof Error ? error.message : 'Unknown error',
      }
    }
  }

  async quitAndInstall() {
    console.log('[AutoUpdate] Installing update and restarting...')
    const updater = await getAutoUpdater()
    if (updater) {
      try {
        // (isSilent=false, isForceRunAfter=true): NSIS UI 표시 + 설치 후 앱 자동 재실행.
        // isSilent=true 였을 때 사용자가 "재시작" 클릭 후 70-90초간 화면이 깜깜해서
        // 진행 중인지 알 수 없는 UX 문제가 있었음. 직접 Setup.exe 실행 시와 동일한 progress 표시.
        updater.quitAndInstall(false, true)
      } catch (error) {
        console.error('[AutoUpdate] quitAndInstall error:', error)
      }
    }
  }

  startPeriodicCheck(intervalMs = 60 * 60 * 1000) {
    this.stopPeriodicCheck()
    setTimeout(() => {
      void this.checkForUpdates()
    }, 10_000)
    this.checkIntervalId = setInterval(() => {
      void this.checkForUpdates()
    }, intervalMs)
  }

  stopPeriodicCheck() {
    if (this.checkIntervalId) {
      clearInterval(this.checkIntervalId)
      this.checkIntervalId = null
    }
  }

  getCurrentVersion(): string {
    return app.getVersion()
  }

  getDownloadedVersion(): string | null {
    return this.downloadedVersion
  }

  private compareVersions(v1: string, v2: string): number {
    const parts1 = v1.split('.').map((p) => parseInt(p, 10) || 0)
    const parts2 = v2.split('.').map((p) => parseInt(p, 10) || 0)
    const len = Math.max(parts1.length, parts2.length)
    for (let i = 0; i < len; i++) {
      const p1 = parts1[i] || 0
      const p2 = parts2[i] || 0
      if (p1 > p2) return 1
      if (p1 < p2) return -1
    }
    return 0
  }
}

let autoUpdateService: AutoUpdateService | null = null

export function getAutoUpdateService(): AutoUpdateService {
  if (!autoUpdateService) {
    autoUpdateService = new AutoUpdateService()
  }
  return autoUpdateService
}

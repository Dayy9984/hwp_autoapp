// ─── EPIPE / stdio broken pipe 안전 가드 ──────────────────────────────────────
// Windows GUI subsystem + NSIS oneClick(runAfterFinish:true) 로 설치 직후 앱이
// detached 로 spawn 되는데, console.log 가 받는 stdout 이 inherited pipe 일 때
// 인스톨러 종료와 동시에 broken pipe 가 되어 EPIPE 가 다이얼로그로 노출됨.
// stdio error listener + EPIPE uncaughtException 둘 다 가드.
for (const stream of [process.stdout, process.stderr]) {
  try { (stream as any)?.on?.('error', () => {}) } catch {}
}
process.on('uncaughtException', (err: any) => {
  if (err?.code === 'EPIPE') return
  // EPIPE 가 아니면 Electron 기본 동작 (다이얼로그) 유지 — 다른 버그는 가리지 않음.
  throw err
})

// 개발 모드에서 .env 로드 (다른 import보다 먼저 실행)
// .env에는 공개 키 + 시크릿 키가 모두 포함됨
// 프로덕션 빌드에서는 inject-env.cjs가 빌드 시점에 값을 인라인하므로
// dotenv 로딩은 무시됨 (파일 없으면 무시)
import { config as dotenvConfig } from 'dotenv'
import { fileURLToPath as fileURLToPathForDotenv } from 'node:url'
import { dirname as dirnameForDotenv, resolve as resolveForDotenv } from 'node:path'
const __dirnameForDotenv = dirnameForDotenv(fileURLToPathForDotenv(import.meta.url))
dotenvConfig({ path: resolveForDotenv(__dirnameForDotenv, '../../.env') })

import { app, BrowserWindow, shell, ipcMain, dialog, screen, powerMonitor } from 'electron'



import { fileURLToPath } from 'node:url'



import path from 'node:path'



import os from 'node:os'



import fs from 'node:fs'



import { getPythonBridge, getFileReaderBridge, PythonBridge, getWindowMonitor, WindowMonitorBridge } from '../services/python-bridge'



import { getCvdBridge, CvdBridge } from '../services/cvd-bridge'



import { getAgentBridge, AgentBridge } from '../services/agent-bridge'
import { checkHwpCompatibility, ensureTypeLibRegistered, getCompatibilityStatus, runCompatibilityCheck, getLastCheckResult } from '../services/hwp-compatibility'
import { getOpenAiSettings } from '../services/openai-settings'
import { detectCodex, getCodexAuth } from '../services/codex-detector'



// v4.1.4: 뮤텍스 및 docKey 모듈



import { bridgeMutex, withDocLock } from './edit-mutex'



import { generateDocumentKey, buildActiveKeyForComparison } from './document-key'



// v5.0: 프로젝트 관리 IPC 핸들러



import { registerProjectHandlers } from './project-handlers'
import { registerDbHandlers } from './db-handlers'
import { registerMaintenanceHandlers } from './maintenance-handlers'
import { registerUpdateHandlers, setUpdateMainWindow, startAutoUpdateCheck } from './update-handlers'
import { registerLogHandlers } from './log-handlers'
import { registerLicenseHandlers, initialLicenseCheck, getLastStatus } from './license-gate'
import { getLogService } from '../services/log-service'
import { dbManager } from '../services/db-manager'
import { telemetry } from '../services/telemetry'
import { announcementFetcher, registerAnnouncementHandlers } from '../services/announcement-fetcher'







const SEARCHABLE_EXTENSIONS = new Set([
  'hwp', 'hwpx', 'pdf', 'docx', 'pptx',
  'xls', 'xlsx', 'xlsm', 'txt', 'md'
])

type TemplatePairContextRow = {
  id: string
  template_name: string
  filled_name: string
}

type FileContextRow = {
  name: string
  rel_path: string
  extension: string | null
  index_status?: string | null
}

type SettingRow = {
  key: string
  value: string | null
  type: string | null
}

const parseSettingValue = (value: string | null, type: string | null): unknown => {
  if (value === null || value === undefined) return null
  if (type === 'number') {
    const num = Number(value)
    return Number.isFinite(num) ? num : null
  }
  if (type === 'boolean') {
    return value === 'true' || value === '1'
  }
  if (type === 'json') {
    try {
      return JSON.parse(value)
    } catch {
      return null
    }
  }
  return value
}

// ✅ Role-based permission + prompt settings
const getPromptSettings = (): { scope: 'none' | 'partial' | 'full'; promptCustomEnabled: boolean; promptCustomRules: string; promptFullOverride: string } => {
  const defaults = { scope: 'partial' as const, promptCustomEnabled: false, promptCustomRules: '', promptFullOverride: '' }
  try {
    const db = dbManager.open()
    const rows = db.prepare("SELECT key, value, type FROM settings WHERE key IN ('prompt_custom_enabled','prompt_custom_rules','prompt_full_override')").all() as SettingRow[]
    const settings: Record<string, unknown> = {}
    for (const row of rows) {
      settings[row.key] = parseSettingValue(row.value, row.type)
    }

    return {
      scope: 'partial',
      promptCustomEnabled: Boolean(settings.prompt_custom_enabled),
      promptCustomRules: typeof settings.prompt_custom_rules === 'string' ? settings.prompt_custom_rules : '',
      promptFullOverride: typeof settings.prompt_full_override === 'string' ? settings.prompt_full_override : ''
    }
  } catch {
    return defaults
  }
}

const resolveContextExtension = (name: string, relPath: string, extension?: string | null) => {
  if (extension && extension.length > 0) return extension
  return path.extname(name || relPath).slice(1)
}

const getTemplatePairsForProject = (projectId: string) => {
  const db = dbManager.open()
  const rows = db
    .prepare('SELECT id, template_name, filled_name FROM template_pairs WHERE project_id = ?')
    .all(projectId) as TemplatePairContextRow[]
  return rows.map((row) => ({
    id: row.id,
    templateFile: { name: row.template_name },
    filledFile: { name: row.filled_name },
  }))
}

const getProjectFilesForContext = (projectId: string) => {
  const db = dbManager.open()
  const rows = db
    .prepare(
      "SELECT name, rel_path, extension, index_status FROM files WHERE scope = 'project' AND project_id = ?"
    )
    .all(projectId) as FileContextRow[]
  return rows.map((row) => ({
    name: row.name,
    extension: resolveContextExtension(row.name, row.rel_path, row.extension),
    indexStatus: {
      status: (row.index_status ?? 'pending') as 'pending' | 'indexing' | 'ready' | 'failed'
    },
  }))
}

const getChatFilesForContext = (chatId: string) => {
  const db = dbManager.open()
  const rows = db
    .prepare(
      "SELECT name, rel_path, extension FROM files WHERE scope = 'chat' AND chat_id = ?"
    )
    .all(chatId) as FileContextRow[]
  return rows.map((row) => ({
    name: row.name,
    extension: resolveContextExtension(row.name, row.rel_path, row.extension),
  }))
}

const isChatFileStillAttached = (chatId: string, fileId: string) => {
  const db = dbManager.open()
  const row = db
    .prepare("SELECT 1 AS exists_flag FROM files WHERE scope = 'chat' AND chat_id = ? AND id = ? LIMIT 1")
    .get(chatId, fileId) as { exists_flag?: number } | undefined
  return Boolean(row?.exists_flag)
}







const __dirname = path.dirname(fileURLToPath(import.meta.url))

// ============================================================
// Main Process File Logging
// ============================================================
let mainLogFilePath: string | null = null

function getMainLogFilePath(): string | null {
  if (mainLogFilePath) return mainLogFilePath
  try {
    if (!app.isReady()) return null
    const logDir = path.join(app.getPath('userData'), 'logs')
    fs.mkdirSync(logDir, { recursive: true })
    mainLogFilePath = path.join(logDir, 'main-process.log')
    return mainLogFilePath
  } catch {
    return null
  }
}

function mainLog(message: string): void {
  const logFile = getMainLogFilePath()
  const line = `[${new Date().toISOString()}] ${message}`
  console.log(line)
  if (logFile) {
    try {
      fs.appendFileSync(logFile, line + '\n', 'utf-8')
    } catch {}
  }
}




process.env.APP_ROOT = path.join(__dirname, '../..')







const MAIN_DIST = path.join(process.env.APP_ROOT, 'dist-electron')



const RENDERER_DIST = path.join(process.env.APP_ROOT, 'dist')



const VITE_DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL







process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL



  ? path.join(process.env.APP_ROOT, 'public')



  : RENDERER_DIST







// Disable GPU Acceleration for Windows 7



if (os.release().startsWith('6.1')) app.disableHardwareAcceleration()







// Set application name for Windows 10+ notifications



if (process.platform === 'win32') app.setAppUserModelId('Inserty')







if (!app.requestSingleInstanceLock()) {



  app.quit()



  process.exit(0)



}







let win: BrowserWindow | null = null



let splashWindow: BrowserWindow | null = null



let pythonBridge: PythonBridge | null = null
let fileReaderBridge: PythonBridge | null = null



let cvdBridge: CvdBridge | null = null



let agentBridge: AgentBridge | null = null
let agentBridgeInitialized = false



let windowMonitor: WindowMonitorBridge | null = null



let shutdownPromise: Promise<void> | null = null

// 중복 호출 방지용 Promise
let startPythonProcessPromise: Promise<{ success: boolean; error?: string }> | null = null

let quitInProgress = false

let lastAppStatus: { message: string; progress?: number } | null = null

const RENDERER_RECOVERY_THROTTLE_MS = 1500
const WAKE_RECOVERY_DELAY_MS = 900

let rendererRecoveryInFlight = false
let lastRendererRecoveryAt = 0
let wakeRecoveryTimer: ReturnType<typeof setTimeout> | null = null
let lastSuspendAt = 0
let lifecycleGuardsRegistered = false







// v4.1.4: 활성 문서 polling 상태



let pollingInFlight = false



let lastActiveDocKey: string | undefined



let chatCancelTokenSeq = 0



let activeChatCancelToken: { id: number; cancelled: boolean; contextId?: string | null } | null = null







// v4.1.4: RejectResult 타입 정의



interface RejectResult {



  fact: {



    success: boolean



    rejectedCount: number



    rejectionType: 'all' | 'partial'



    requiresFullRegen: boolean



    uncertain: boolean



    mismatch: boolean



  }



  explain: {



    rejectedOps: any[]



    reason: string



  }



}







const preload = path.join(__dirname, '../preload/index.js')



const indexHtml = path.join(RENDERER_DIST, 'index.html')







// 네이티브 HTML 스플래시 윈도우 생성 (즉시 표시)
function createSplashWindow() {
  const splashHtml = path.join(process.env.VITE_PUBLIC!, 'splash.html')

  console.log('[Main] Creating splash window...')
  console.log('[Main] VITE_PUBLIC:', process.env.VITE_PUBLIC)
  console.log('[Main] Splash HTML path:', splashHtml)

  splashWindow = new BrowserWindow({
    width: 400,
    height: 300,
    frame: false,
    backgroundColor: '#27272A',
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload,
    },
  })

  splashWindow.once('ready-to-show', () => {
    console.log('[Main] Splash window ready-to-show')
    splashWindow?.show()
    console.log('[Main] Splash window shown')
  })

  splashWindow.on('closed', () => {
    console.log('[Main] Splash window closed')
    splashWindow = null
  })

  splashWindow.loadFile(splashHtml).then(() => {
    console.log('[Main] Splash HTML loaded successfully')
  }).catch(err => {
    console.error('[Main] Failed to load splash HTML:', err)
  })

  splashWindow.center()
}



async function createWindow() {



  win = new BrowserWindow({
    title: 'Inserty',
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    show: false, // 준비될 때까지 숨김
    backgroundColor: '#27272A', // dark mode bg-secondary와 동일 (흰 화면 방지)
    icon: path.join(process.env.VITE_PUBLIC, 'favicon.ico'),
    webPreferences: {
      preload,
      backgroundThrottling: false, // 백그라운드에서도 렌더링 유지
    },
  })

  // 메인 윈도우 표시 및 스플래시 닫기
  let windowShown = false
  const showMainWindow = () => {
    if (!windowShown && win && !win.isDestroyed()) {
      windowShown = true
      console.log('[Main] Showing main window - closing splash')

      // 스플래시 닫기
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.close()
      }

      // 메인 윈도우 표시
      win.show()
      console.log('[Main] Main window shown')
    }
  }

  // did-finish-load: HTML/JS 로드 완료 시
  win.webContents.on('did-finish-load', () => {
    console.log('[Main] Main window did-finish-load, pythonReadyFlag:', pythonReadyFlag)
    if (lastAppStatus) {
      win?.webContents.send('app:status', lastAppStatus.message, lastAppStatus.progress)
    }

    // Python 준비 완료 시에만 메인 윈도우 표시
    // (splash.html이 모든 준비 완료까지 대기하도록)
    if (pythonReadyFlag) {
      updateSplashStatus('실행 중...', 100)
      sendAppStatus('실행 중...', 100)
      // 짧은 지연 후 표시 (React 초기 렌더링 대기)
      setTimeout(() => {
        showMainWindow()
        // React에 Python 준비 완료 알림 (SplashScreen 바이패스용)
        win?.webContents.send('python:ready')
      }, 300)
    } else {
      // Python이 아직 준비 안됨 - 준비 완료 시 표시될 예정
      console.log('[Main] Waiting for Python to be ready before showing main window')
    }
  })

  win.webContents.on('render-process-gone', (_event, details) => {
    mainLog(`[Main] webContents render-process-gone: reason=${details.reason}, exitCode=${details.exitCode}`)
    void recoverRenderer(`webContents:render-process-gone:${details.reason}`, { reload: true })
  })

  win.webContents.on('unresponsive', () => {
    mainLog('[Main] webContents unresponsive detected')
    void recoverRenderer('webContents:unresponsive', { reload: true })
  })

  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === -3) return
    mainLog(`[Main] webContents did-fail-load: code=${errorCode}, desc=${errorDescription}, url=${validatedURL}`)
    void recoverRenderer('webContents:did-fail-load', { reload: true })
  })

  win.webContents.on('did-fail-provisional-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === -3) return
    mainLog(`[Main] webContents did-fail-provisional-load: code=${errorCode}, desc=${errorDescription}, url=${validatedURL}`)
    void recoverRenderer('webContents:did-fail-provisional-load', { reload: true })
  })

  // Fallback: 60초 후에도 안 보이면 강제 표시 (dev 서버 느릴 때 대비)
  setTimeout(() => {
    if (!windowShown) {
      console.log('[Main] Fallback: force showing main window after 60s timeout')
      showMainWindow()
    }
  }, 60000)







  if (VITE_DEV_SERVER_URL) {



    win.loadURL(VITE_DEV_SERVER_URL)



    win.webContents.openDevTools()



  } else {



    win.loadFile(indexHtml)



  }







  // 창 복원/활성화 시 렌더링 보장



  win.on('restore', () => {
    void recoverRenderer('window:restore', { verifyAfterInvalidate: true })
  })







  // 포커스 이벤트 - 화이트 스크린 체크 로직 제거







  // Make all links open with the browser, not with the application



  win.webContents.setWindowOpenHandler(({ url }) => {



    if (url.startsWith('https:')) shell.openExternal(url)



    return { action: 'deny' }



  })



}







async function arrangeWindows(monitorInfo?: any) {



  if (!win || win.isDestroyed() || !pythonBridge) return







  let display: Electron.Display



  let area: Electron.Rectangle







  if (monitorInfo && monitorInfo.monitorWorkArea) {



    // HWP 창의 모니터 정보를 기반으로 올바른 display 선택



    const hwpWorkArea = monitorInfo.monitorWorkArea



    const allDisplays = screen.getAllDisplays()







    // HWP 창이 속한 모니터와 일치하는 display 찾기



    const matchingDisplay = allDisplays.find(d => {



      const dw = d.workArea



      // 작업 영역이 거의 일치하는 display 찾기 (± 50px 허용)



      return (



        Math.abs(dw.x - hwpWorkArea.x) < 50 &&



        Math.abs(dw.y - hwpWorkArea.y) < 50 &&



        Math.abs(dw.width - hwpWorkArea.width) < 50 &&



        Math.abs(dw.height - hwpWorkArea.height) < 50



      )



    })







    if (matchingDisplay) {



      display = matchingDisplay



      area = display.workArea



      console.log('[Main] Using HWP monitor:', {



        hwpWorkArea,



        displayWorkArea: area,



        isPrimary: monitorInfo.isPrimary,



        dpiScale: monitorInfo.dpiScale



      })



    } else {



      // 일치하는 display가 없으면 Inserty 창의 display 사용 (fallback)



      display = screen.getDisplayMatching(win.getBounds())



      area = display.workArea



      console.log('[Main] No matching display, using Inserty monitor (fallback)')



    }



  } else {



    // monitorInfo가 없으면 Inserty 창의 display 사용 (레거시 동작)



    display = screen.getDisplayMatching(win.getBounds())



    area = display.workArea



    console.log('[Main] No monitor info, using Inserty monitor (fallback)')



  }







  const minWidth = win.getMinimumSize()[0] || 800



  const leftWidth = Math.max(Math.round(area.width * 0.45), minWidth)







  console.log('[Main] Inserty window setBounds:', {



    x: area.x,



    y: area.y,



    width: leftWidth,



    height: area.height,



    workAreaWidth: area.width,



    percentage: ((leftWidth / area.width) * 100).toFixed(1) + '%'



  })







  win.setBounds({



    x: area.x,



    y: area.y,



    width: leftWidth,



    height: area.height,



  })







  try {



    // Python에 Inserty 창의 실제 너비를 전달 (minWidth로 인해 비율과 다를 수 있음)



    await pythonBridge.call('window:arrange', {



      mode: 'right_side',  // HWP 창을 자신의 모니터 오른쪽에 배치



      insertyWidth: leftWidth  // Inserty 창의 실제 너비



    })



  } catch (err) {



    console.error('[Main] HWP arrange failed:', err)



  }



}







// Python 준비 완료 플래그 (윈도우 로드 완료 전에 Python이 준비될 수 있음)
let pythonReadyFlag = false

// 스플래시 윈도우 상태 업데이트 헬퍼
function updateSplashStatus(message: string, progress?: number) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send('splash:status', message, progress)
  }
}

// Renderer splash status helper
function sendAppStatus(message: string, progress?: number) {
  lastAppStatus = { message, progress }
  if (win && !win.isDestroyed()) {
    win.webContents.send('app:status', message, progress)
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

async function ensureMainWindowForRecovery(reason: string): Promise<BrowserWindow | null> {
  if (!app.isReady()) return null

  if (!win || win.isDestroyed()) {
    mainLog(`[Main] Main window missing - recreating (${reason})`)
    await createWindow()
    if (win) {
      setUpdateMainWindow(win)
    }
  }

  if (!win || win.isDestroyed()) {
    return null
  }

  if (win.webContents.isDestroyed()) {
    mainLog(`[Main] Main webContents destroyed - recreating (${reason})`)
    try {
      win.destroy()
    } catch {}
    win = null
    await createWindow()
    if (win) {
      setUpdateMainWindow(win)
    }
  }

  if (!win || win.isDestroyed() || win.webContents.isDestroyed()) {
    return null
  }

  if (win.isMinimized()) {
    win.restore()
  }
  if (!win.isVisible()) {
    win.show()
  }

  return win
}

async function checkRendererHealth(targetWindow: BrowserWindow, timeoutMs = 1800): Promise<boolean> {
  if (targetWindow.isDestroyed()) return false

  const webContents = targetWindow.webContents
  if (webContents.isDestroyed()) return false

  const probePromise = webContents.executeJavaScript(`(() => {
    const root = document.getElementById('root')
    return {
      readyState: document.readyState,
      hasBody: Boolean(document.body),
      hasRoot: Boolean(root),
      rootChildren: root ? root.childElementCount : -1
    }
  })()`, true).catch(() => null)

  const timeoutPromise = new Promise<null>((resolve) => {
    setTimeout(() => resolve(null), timeoutMs)
  })

  const result = await Promise.race([probePromise, timeoutPromise]) as {
    readyState?: string
    hasBody?: boolean
    hasRoot?: boolean
    rootChildren?: number
  } | null

  if (!result) return false
  if (!result.hasBody || !result.hasRoot) return false
  return typeof result.rootChildren === 'number' && result.rootChildren > 0
}

async function recoverRenderer(
  reason: string,
  options: { reload?: boolean; verifyAfterInvalidate?: boolean } = {}
) {
  if (quitInProgress) return

  const now = Date.now()
  if (rendererRecoveryInFlight) {
    mainLog(`[Main] Renderer recovery skipped (in-flight): ${reason}`)
    return
  }
  if (now - lastRendererRecoveryAt < RENDERER_RECOVERY_THROTTLE_MS) {
    mainLog(`[Main] Renderer recovery throttled: ${reason}`)
    return
  }

  rendererRecoveryInFlight = true
  lastRendererRecoveryAt = now

  try {
    const targetWindow = await ensureMainWindowForRecovery(reason)
    if (!targetWindow || targetWindow.isDestroyed()) return

    const webContents = targetWindow.webContents
    if (webContents.isDestroyed()) return

    if (webContents.isLoadingMainFrame()) {
      mainLog(`[Main] Renderer recovery deferred while loading: ${reason}`)
      return
    }

    if (options.reload) {
      mainLog(`[Main] Renderer reload requested: ${reason}`)
      webContents.reload()
      return
    }

    webContents.invalidate()

    if (options.verifyAfterInvalidate && pythonReadyFlag) {
      await sleep(700)
      const healthy = await checkRendererHealth(targetWindow)
      if (!healthy && !webContents.isDestroyed() && !webContents.isLoadingMainFrame()) {
        mainLog(`[Main] Renderer health check failed - reloading: ${reason}`)
        webContents.reload()
      }
    }
  } catch (err) {
    mainLog(`[Main] Renderer recovery failed (${reason}): ${String(err)}`)
  } finally {
    rendererRecoveryInFlight = false
  }
}

function scheduleWakeRecovery(reason: string, delayMs = WAKE_RECOVERY_DELAY_MS) {
  if (wakeRecoveryTimer) {
    clearTimeout(wakeRecoveryTimer)
  }

  wakeRecoveryTimer = setTimeout(() => {
    wakeRecoveryTimer = null
    void recoverRenderer(`wake:${reason}`, { verifyAfterInvalidate: true })
  }, delayMs)
}

function registerLifecycleGuards() {
  if (lifecycleGuardsRegistered) return
  lifecycleGuardsRegistered = true

  powerMonitor.on('suspend', () => {
    lastSuspendAt = Date.now()
    mainLog('[Main] powerMonitor suspend detected')
  })

  powerMonitor.on('resume', () => {
    mainLog('[Main] powerMonitor resume detected')
    lastSuspendAt = 0
    scheduleWakeRecovery('resume')
  })

  powerMonitor.on('unlock-screen', () => {
    mainLog('[Main] powerMonitor unlock-screen detected')
    lastSuspendAt = 0
    scheduleWakeRecovery('unlock-screen', 500)
  })

  powerMonitor.on('lock-screen', () => {
    mainLog('[Main] powerMonitor lock-screen detected')
  })

  app.on('browser-window-focus', (_event, focusedWindow) => {
    if (!win || focusedWindow !== win) return
    if (!lastSuspendAt) return

    const elapsed = Date.now() - lastSuspendAt
    if (elapsed >= 0 && elapsed < 10 * 60 * 1000) {
      lastSuspendAt = 0
      scheduleWakeRecovery('focus-after-suspend', 350)
    }
  })

  app.on('child-process-gone', (_event, details) => {
    mainLog(`[Main] child-process-gone: type=${details.type}, reason=${details.reason}`)
    const processType = String(details.type || '').toLowerCase()
    if (processType === 'gpu') {
      void recoverRenderer('child-process-gone:gpu', { reload: true })
    }
  })

  app.on('render-process-gone', (_event, webContents, details) => {
    if (!win || win.isDestroyed()) return
    if (webContents.id !== win.webContents.id) return

    mainLog(`[Main] app render-process-gone: reason=${details.reason}, exitCode=${details.exitCode}`)
    void recoverRenderer(`app:render-process-gone:${details.reason}`, { reload: true })
  })
}

// Python 프로세스만 시작 (앱 시작 시 즉시 호출)
async function startPythonProcess(): Promise<{ success: boolean; error?: string }> {
  // 중복 호출 방지
  if (startPythonProcessPromise) {
    mainLog('[Main] startPythonProcess() already in progress, waiting...')
    return startPythonProcessPromise
  }

  // 이미 실행 중인 경우
  if (pythonBridge && pythonBridge.isRunning()) {
    mainLog('[Main] startPythonProcess() skipped - already running')
    return { success: true }
  }

  startPythonProcessPromise = _doStartPythonProcess()
  try {
    return await startPythonProcessPromise
  } finally {
    startPythonProcessPromise = null
  }
}

async function _doStartPythonProcess(): Promise<{ success: boolean; error?: string }> {
  try {
    mainLog('[Main] _doStartPythonProcess() called')

    // HWP 호환성 검사 (64-bit ↔ 32-bit COM 마샬링 지원)
    updateSplashStatus('HWP 호환성 검사 중...', 5)
    sendAppStatus('HWP 호환성 검사 중...', 5)
    const compatResult = await runCompatibilityCheck()
    mainLog(`[Main] HWP compatibility check: ${JSON.stringify(compatResult)}`)

    updateSplashStatus('HWP 호환성 검사 완료', 15)
    sendAppStatus('HWP 호환성 검사 완료', 15)

    if (compatResult.status === 'REGISTRATION_NEEDED' && compatResult.needsAdmin) {
      mainLog('[Main] TypeLib registration needed - attempting registration')
      updateSplashStatus('HWP TypeLib 등록 중...', 25)
      sendAppStatus('HWP TypeLib 등록 중...', 25)
      const registered = await ensureTypeLibRegistered()
      if (!registered) {
        mainLog('[Main] TypeLib registration failed - 32-bit HWP may not work')
      }
    }

    updateSplashStatus('한글 엔진 구동 중...', 35)
    sendAppStatus('한글 엔진 구동 중...', 35)

    // COM Process 시작
    mainLog('[Main] Creating PythonBridge...')
    pythonBridge = getPythonBridge(process.env.APP_ROOT!)

    // Progress 이벤트를 React로 전달
    pythonBridge.on('progress', (event: string, data: any) => {
      if (win && !win.isDestroyed()) {
        win.webContents.send('chat:progress', event, data)
      }
    })

    mainLog('[Main] Starting PythonBridge...')
    await pythonBridge.start()

    // FileReaderBridge 시작 (파일 읽기 전용, 메인 프로세스 blocking 방지)
    mainLog('[Main] Creating FileReaderBridge...')
    fileReaderBridge = getFileReaderBridge(process.env.APP_ROOT!)
    fileReaderBridge.on('progress', (event: string, data: any) => {
      if (win && !win.isDestroyed()) {
        win.webContents.send('chat:progress', event, data)
      }
    })
    // 백그라운드에서 시작 (실패해도 앱 시작에 영향 없음, 필요 시 on-demand 시작)
    fileReaderBridge.start().catch(err => {
      mainLog(`[Main] FileReaderBridge start failed (will retry on demand): ${err}`)
    })

    mainLog('[Main] Python Bridge initialized (without HWP binding)')

    updateSplashStatus('한글 엔진 준비 완료', 70)
    sendAppStatus('한글 엔진 준비 완료', 70)

    updateSplashStatus('리소스 로딩 중...', 85)
    sendAppStatus('리소스 로딩 중...', 85)

    // 보조 프로세스 자동 시작 (병렬 실행으로 최적화)
    mainLog('[Main] Starting auxiliary processes in parallel...')
    const auxiliaryResults = await Promise.allSettled([
      (async () => {
        mainLog('[Main] Calling enableHwpBinding()...')
        await enableHwpBinding()
        mainLog('[Main] enableHwpBinding() completed')
      })(),
      (async () => {
        mainLog('[Main] Calling initAgentBridge()...')
        await initAgentBridge()
        mainLog('[Main] initAgentBridge() completed')
      })()
    ])

    // 결과 로깅
    if (auxiliaryResults[0].status === 'rejected') {
      mainLog(`[Main] Auto-enable HWP binding failed: ${auxiliaryResults[0].reason}`)
    }
    if (auxiliaryResults[1].status === 'rejected') {
      mainLog(`[Main] Auto-start Agent Bridge failed: ${auxiliaryResults[1].reason}`)
    }

    // Python 준비 완료 플래그 설정
    pythonReadyFlag = true

    // 프론트엔드에 Python 준비 완료 알림 및 메인 윈도우 표시
    if (win && !win.isDestroyed()) {
      // did-finish-load 이후에 이벤트 전송 및 윈도우 표시
      if (win.webContents.isLoading()) {
        // 윈도우가 아직 로드 중이면 did-finish-load에서 처리됨
        mainLog('[Main] Window still loading - will show after load completes')
      } else {
        // 윈도우가 이미 로드됨 - 즉시 표시
        mainLog('[Main] Window already loaded - showing main window now')
        updateSplashStatus('실행 중...', 100)
        sendAppStatus('실행 중...', 100)

        // 스플래시 닫고 메인 윈도우 표시
        if (splashWindow && !splashWindow.isDestroyed()) {
          splashWindow.close()
        }
        win.show()

        // React에 Python 준비 완료 알림 (SplashScreen 바이패스용)
        win.webContents.send('python:ready')
      }
    }

    mainLog('[Main] startPythonProcess() completed successfully')
    return { success: true }
  } catch (err) {
    mainLog(`[Main] Failed to start Python process: ${err}`)
    updateSplashStatus('초기화 실패', 100)
    sendAppStatus('초기화 실패', 100)
    return { success: false, error: String(err) }
  }
}

// 보조 프로세스(Agent/Window Monitor) 초기 실행
async function startAuxiliaryProcesses() {
  console.log('[Main] startAuxiliaryProcesses() called')
  console.log('[Main] APP_ROOT:', process.env.APP_ROOT)
  console.log('[Main] resourcesPath:', process.resourcesPath)

  try {
    console.log('[Main] Creating WindowMonitor instance...')
    windowMonitor = getWindowMonitor(process.env.APP_ROOT!)
    console.log('[Main] Starting WindowMonitor...')
    await windowMonitor.start()
    console.log('[Main] Window Monitor started (early)')
  } catch (err) {
    console.error('[Main] Failed to start Window Monitor early:', err)
  }

  try {
    console.log('[Main] Starting AgentBridge...')
    await initAgentBridge()
  } catch (err) {
    console.error('[Main] Failed to start Agent Bridge early:', err)
  }

  console.log('[Main] startAuxiliaryProcesses() completed')
}

// HWP binding activation (login-only)
let hwpBindingEnabled = false
async function enableHwpBinding() {
  mainLog('[Main] enableHwpBinding() called')

  if (hwpBindingEnabled) {
    mainLog('[Main] HWP binding already enabled')
    return { success: true }
  }

  if (!pythonBridge) {
    mainLog('[Main] Cannot enable HWP binding - Python bridge not started')
    return { success: false, error: 'Python bridge not started' }
  }

  try {
    // Window Monitor 시작
    mainLog('[Main] Creating WindowMonitor instance...')
    windowMonitor = getWindowMonitor(process.env.APP_ROOT!)

    let lastBound: { pid: number; hwnd: number } | null = null

    const bindWindow = async (data: { pid: number; hwnd: number; title: string; reason?: string }) => {
      if (data.reason === 'new_window_added') {
        mainLog(`[Main] Ignoring auto-added HWP window for binding: PID=${data.pid}, HWND=${data.hwnd}`)
        return
      }
      mainLog(`[Main] HWP WINDOW BIND: PID=${data.pid}, HWND=${data.hwnd}, TITLE=${data.title}`)
      try {
        if (lastBound && lastBound.pid === data.pid && lastBound.hwnd === data.hwnd) {
          return
        }

        const result = await pythonBridge!.call('window:bind', {
          pid: data.pid,
          hwnd: data.hwnd,
        })
        mainLog(`[Main] Window bound successfully: ${JSON.stringify(result)}`)

        // HWP 창의 모니터 정보를 arrangeWindows에 전달
        await arrangeWindows(result.monitorInfo)
        lastBound = { pid: data.pid, hwnd: data.hwnd }

        // 프론트엔드로 바인딩 성공 이벤트 전송 (문서 정보 갱신용)
        if (win && win.webContents && !win.webContents.isDestroyed()) {
          win.webContents.send('hwp:windowBound', {
            pid: data.pid,
            hwnd: data.hwnd,
            title: data.title
          })
        }
      } catch (err) {
        mainLog(`[Main] FAILED TO BIND WINDOW: ${err}`)
      }
    }

    // HWP 창 발견/전환 시 COM Process에 PID/HWND 전달
    windowMonitor.on('window_found', bindWindow)
    windowMonitor.on('window_switched', bindWindow)

    windowMonitor.on('window_lost', () => {
      mainLog('[Main] HWP window lost')
      lastBound = null
      // UI에 문서 연결 해제 알림 (unbind 대기 없이 즉시 갱신)
      if (win && !win.isDestroyed()) {
        win.webContents.send('hwp:windowLost')
      }
      void pythonBridge!.call('window:unbind', {}).catch((err) => {
        mainLog(`[Main] Failed to unbind window: ${err}`)
      })
    })

    // 선택 영역 변경 이벤트
    windowMonitor.on('selection_changed', (data: any) => {
      if (win && win.webContents && !win.webContents.isDestroyed()) {
        win.webContents.send('hwp:selectionChanged', data)
      }
    })

    mainLog('[Main] Starting WindowMonitor...')
    await windowMonitor.start()
    hwpBindingEnabled = true
    mainLog('[Main] HWP binding enabled - Window Monitor started')
    return { success: true }
  } catch (err) {
    mainLog(`[Main] Failed to enable HWP binding: ${err}`)
    return { success: false, error: String(err) }
  }
}

// 레거시 호환: initPythonBridge는 startPythonProcess만 호출
// (startPythonProcess 내부에서 enableHwpBinding과 initAgentBridge를 이미 호출함)
async function initPythonBridge() {
  await startPythonProcess()
}







async function initCvdBridge() {



  try {



    if (cvdBridge && cvdBridge.isRunning()) {



      return



    }



    cvdBridge = getCvdBridge(process.env.APP_ROOT!)







    if (cvdBridge.listenerCount('progress') === 0) {



      cvdBridge.on('progress', (event: string, data: any) => {



        if (win && !win.isDestroyed()) {



          win.webContents.send('chat:progress', event, data)



        }



      })



    }







    await cvdBridge.start()



    console.log('[Main] CVD Bridge initialized')



  } catch (err) {



    console.error('[Main] Failed to initialize CVD Bridge:', err)



  }



}







async function initAgentBridge() {
  mainLog('[Main] initAgentBridge() called')

  try {
    // Agent process start
    if (agentBridge && agentBridge.isRunning()) {
      mainLog('[Main] Agent Bridge already running')
      return
    }

    mainLog('[Main] Creating AgentBridge instance...')
    agentBridge = getAgentBridge(process.env.APP_ROOT!)

    // Forward delta events to renderer
    if (!agentBridgeInitialized) {
      agentBridge.on('delta', (deltaEvent: any) => {
        if (win && !win.isDestroyed()) {
          win.webContents.send('chat:delta', deltaEvent)
        }
      })
      agentBridgeInitialized = true
    }

    mainLog('[Main] Starting AgentBridge...')
    await agentBridge.start()

    mainLog('[Main] Agent Bridge initialized successfully')
  } catch (err) {
    mainLog(`[Main] Failed to initialize Agent Bridge: ${err}`)
  }
}








async function shutdownBridges(reason?: string) {



  if (shutdownPromise) return shutdownPromise



  shutdownPromise = (async () => {



    if (reason) {



      console.log(`[Main] Shutting down bridges (${reason})`)



    }







    const tasks: Promise<void>[] = []







    if (pythonBridge) {



      tasks.push(



        pythonBridge.stop().catch((err) => {



          console.error('[Main] Python Bridge stop failed:', err)



        })



      )



    }

    if (fileReaderBridge) {
      tasks.push(
        fileReaderBridge.stop().catch((err) => {
          console.error('[Main] FileReader Bridge stop failed:', err)
        })
      )
    }







    if (cvdBridge) {



      tasks.push(



        cvdBridge.stop().catch((err) => {



          console.error('[Main] CVD Bridge stop failed:', err)



        })



      )



    }







    if (agentBridge) {



      tasks.push(



        agentBridge.stop().catch((err) => {



          console.error('[Main] Agent Bridge stop failed:', err)



        })



      )



    }







    if (windowMonitor) {



      tasks.push(



        windowMonitor.stop().catch((err) => {



          console.error('[Main] Window Monitor stop failed:', err)



        })



      )



    }







    if (tasks.length) {



      await Promise.all(tasks)



    }







    pythonBridge = null



    cvdBridge = null



    agentBridge = null



    windowMonitor = null



  })()







  return shutdownPromise



}







app.whenReady().then(async () => {
  console.log('[Main] ========================================')
  console.log('[Main] App ready - starting initialization')
  console.log('[Main] VITE_PUBLIC path:', process.env.VITE_PUBLIC)
  console.log('[Main] ========================================')

  registerDbHandlers()
  registerMaintenanceHandlers()
  registerUpdateHandlers()
  registerLogHandlers()
  registerLifecycleGuards()
  registerLicenseHandlers()

  // 베타 텔레메트리 큐 시작 + app_launched 기록
  telemetry.init()
  telemetry.push('app_launched', { ts: Date.now() })
  ipcMain.handle('telemetry:track', (_, eventType: string, payload?: Record<string, unknown>) => {
    telemetry.push(eventType, payload)
    return { ok: true }
  })

  // 베타 announcement 시스템 — 5분마다 fetch + dismissed 캐시.
  announcementFetcher.init()
  registerAnnouncementHandlers()

  // 라이센스 초기 검증 — pending key 자동 활성화 + 캐시 토큰 검증
  // BrowserWindow 생성 전에 결과가 결정되어야 렌더러가 getInitialStatus 호출 시
  // 올바른 상태를 받는다. 네트워크 실패 시에도 verify()가 빠르게 offline_grace/blocked를
  // 반환하므로 무한 대기는 없음.
  ipcMain.handle('license:getInitialStatus', () => getLastStatus())
  try {
    await initialLicenseCheck()
  } catch (e) {
    console.error('[License] initial check error:', e)
  }

  // 1. 스플래시 윈도우 먼저 생성 (즉시 표시)
  try {
    createSplashWindow()
  } catch (err) {
    console.error('[Main] Failed to create splash window:', err)
  }

  // 2. 메인 윈도우 생성 (백그라운드에서 로드)
  await createWindow()

  // 자동 업데이트 서비스 초기화
  if (win) {
    setUpdateMainWindow(win)
    startAutoUpdateCheck(60 * 60 * 1000) // 1시간 주기
  }

  // v5.0: 프로젝트 관리 IPC 핸들러 등록
  registerProjectHandlers(
    () => win,
    () => pythonBridge,
    () => fileReaderBridge
  )

  // Python 프로세스 즉시 시작 (로그인 전에 미리 준비)
  // Note: startPythonProcess() 내부에서 enableHwpBinding()과 initAgentBridge()를 호출하여
  // WindowMonitor와 AgentBridge도 시작합니다.
  console.log('[Main] Starting Python process...')
  startPythonProcess().catch(err => {
    console.error('[Main] Failed to start Python process on startup:', err)
  })
})











app.on('window-all-closed', () => {
  // 업데이트 진행 중에는 모든 창이 닫혀도 app.quit 안 함.
  // (progress window 만 띄우려고 메인 창을 닫는 순간 quit 되면 안 되므로)
  // quitAndInstall 호출 시점에 명시적으로 종료됨.
  try {
    const { isUpdatingNow } = require('./update-handlers')
    if (isUpdatingNow && isUpdatingNow()) {
      win = null
      return
    }
  } catch {}

  win = null
  app.quit()
})







app.on('second-instance', () => {



  if (win) {



    if (win.isMinimized()) win.restore()



    win.focus()



  }



})







app.on('activate', () => {



  const allWindows = BrowserWindow.getAllWindows()



  if (allWindows.length) {



    allWindows[0].focus()



  } else {



    createWindow()



  }



})







app.on('before-quit', (event) => {



  if (quitInProgress) return



  quitInProgress = true

  if (wakeRecoveryTimer) {
    clearTimeout(wakeRecoveryTimer)
    wakeRecoveryTimer = null
  }

  // Log app shutdown
  const logService = getLogService()
  logService.enqueueUsage({
    eventType: 'app_stop',
    eventData: {
      timestamp: new Date().toISOString()
    }
  })



  event.preventDefault()







  const flushPromise = logService.flushUsage().catch(err => {
    console.error('[Main] Failed to flush usage logs:', err)
  })

  const shutdownPromise = shutdownBridges('before-quit')
    .catch((err) => {
      console.error('[Main] Bridge shutdown failed:', err)
    })

  Promise.all([flushPromise, shutdownPromise])
    .finally(() => {
      app.exit(0)
    })



})







// 앱이 완전히 종료될 때 정리 (보조 처리)



app.on('will-quit', () => {



  console.log('[Main] App will quit')



})







// ============================================================



// IPC Handlers



// ============================================================







// 앱 버전 조회 (동기)
ipcMain.on('app:getVersion', (event) => {
  event.returnValue = app.getVersion()
})

// Python 준비 상태 확인 (SplashScreen 바이패스용)
ipcMain.handle('python:isReady', () => {
  return { ready: pythonReadyFlag }
})

// Python Bridge 시작



ipcMain.handle('python:start', async () => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    await initPythonBridge()



  }



  return { success: pythonBridge?.isRunning() ?? false }



})

// Agent Bridge start (LLM process)
ipcMain.handle('agent:start', async () => {
  if (!agentBridge || !agentBridge.isRunning()) {
    await initAgentBridge()
  }
  return { success: agentBridge?.isRunning() ?? false }
})

// Codex CLI 상태 확인
ipcMain.handle('codex:status', async () => {
  try {
    const status = await detectCodex()
    return status
  } catch (e) {
    return { installed: false, reason: String(e) }
  }
})

// Codex CLI 로그인 (브라우저 열기)
ipcMain.handle('codex:login', async () => {
  const { exec } = require('child_process')
  return new Promise((resolve) => {
    exec('codex login', { shell: true, timeout: 60000 }, (err: any, stdout: string, stderr: string) => {
      if (err) {
        resolve({ success: false, error: stderr || err.message })
      } else {
        resolve({ success: true })
      }
    })
  })
})







// HWP 바인딩 활성화 (로그인 후 호출)
ipcMain.handle('hwp:enableBinding', async () => {
  return await enableHwpBinding()
})

// HWP 호환성 검사
ipcMain.handle('hwp:checkCompatibility', async () => {
  try {
    const result = await runCompatibilityCheck()
    return { success: true, ...result }
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

// HWP TypeLib 등록 확인 및 등록
ipcMain.handle('hwp:ensureCompatibility', async () => {
  try {
    const registered = await ensureTypeLibRegistered()
    return { success: registered }
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

// HWP 호환성 상태 조회
ipcMain.handle('hwp:getCompatibilityStatus', () => {
  return {
    status: getCompatibilityStatus(),
    lastResult: getLastCheckResult()
  }
})

// Python 명령 실행



ipcMain.handle('python:call', async (_, method: string, params: any) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python Bridge not running' }



  }



  try {



    const result = await pythonBridge.call(method, params)



    return result



  } catch (err: any) {



    return { success: false, error: err.message, trace: err.trace }



  }



})







// 파일 선택 다이얼로그 (단일)



ipcMain.handle('dialog:openFile', async (_, options: any) => {



  const result = await dialog.showOpenDialog(win!, {



    properties: ['openFile'],



    filters: options?.filters || [



      { name: 'All Supported Files', extensions: ['hwp', 'hwpx', 'pdf', 'docx', 'pptx', 'xls', 'xlsx', 'xlsm', 'md', 'txt'] },



      { name: 'HWP Files', extensions: ['hwp', 'hwpx'] },



      { name: 'PDF Files', extensions: ['pdf'] },



      { name: 'Office Files', extensions: ['docx', 'xls', 'xlsx', 'xlsm', 'pptx'] },



      { name: 'Text Files', extensions: ['md', 'txt'] },






      { name: 'All Files', extensions: ['*'] },



    ],



  })







  if (result.canceled || result.filePaths.length === 0) {



    return null



  }







  return result.filePaths[0]



})







// 파일 선택 다이얼로그 (다중)



ipcMain.handle('dialog:openFiles', async (_, options: any) => {



  const result = await dialog.showOpenDialog(win!, {



    properties: ['openFile', 'multiSelections'],



    filters: options?.filters || [



      { name: 'All Supported Files', extensions: ['hwp', 'hwpx', 'pdf', 'docx', 'pptx', 'xls', 'xlsx', 'xlsm', 'md', 'txt'] },



      { name: 'HWP Files', extensions: ['hwp', 'hwpx'] },



      { name: 'PDF Files', extensions: ['pdf'] },



      { name: 'Office Files', extensions: ['docx', 'xls', 'xlsx', 'xlsm', 'pptx'] },



      { name: 'Text Files', extensions: ['md', 'txt'] },






      { name: 'All Files', extensions: ['*'] },



    ],



  })







  if (result.canceled || result.filePaths.length === 0) {



    return []



  }







  return result.filePaths



})







// v6.1: 임시 파일 저장 (드래그앤드롭 File API fallback용)
ipcMain.handle('file:saveTempFile', async (_, args: { fileName: string; buffer: number[] }) => {
  try {
    const tempDir = app.getPath('temp')
    const tempPath = path.join(tempDir, `inserty_upload_${Date.now()}_${args.fileName}`)
    await fs.promises.writeFile(tempPath, Buffer.from(args.buffer))
    console.log('[Main] Temp file saved:', tempPath)
    return { success: true, path: tempPath }
  } catch (error) {
    console.error('[Main] Temp file save failed:', error)
    return { success: false, error: String(error) }
  }
})

// TXT 파일 읽기 (별도 프로세스에서 처리)



ipcMain.handle('file:readTxt', async (_, filePath: string, maxChars?: number) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('readTxtFile', { filePath, maxChars })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// Excel 파일 읽기 (서버 분리 대비)



ipcMain.handle('file:readExcel', async (_, filePath: string, sheetName?: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('readExcelFile', { filePath, sheetName })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// Excel 시트 목록 조회



ipcMain.handle('file:getExcelSheets', async (_, filePath: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('getExcelSheets', { filePath })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// PDF 파일 읽기



ipcMain.handle('file:readPdf', async (_, filePath: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('readPdfFile', { filePath })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// HWP/HWPX 파일 읽기



ipcMain.handle('file:readHwp', async (_, filePath: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    if (fileReaderBridge && !fileReaderBridge.isRunning()) {
      try { await fileReaderBridge.start() } catch {}
    }
    if (!fileReaderBridge || !fileReaderBridge.isRunning()) {
      return { success: false, error: 'FileReader bridge not running' }
    }
    const result = await fileReaderBridge.call('readHwpFile', { filePath })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// DOCX 파일 읽기



ipcMain.handle('file:readDoc', async (_, filePath: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('readDocFile', { filePath })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// PPTX 파일 읽기



ipcMain.handle('file:readPpt', async (_, filePath: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const reader = fileReaderBridge?.isRunning() ? fileReaderBridge : pythonBridge
    const result = await reader!.call('readPptFile', { filePath })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// 열린 문서 목록 조회 (Python Bridge 사용)



ipcMain.handle('documents:getOpen', async () => {



  try {



    // Python Bridge 시작 (아직 안 됐으면)



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running', documents: [] }



    }







    const result = await pythonBridge.call('getOpenDocuments', {})



    return result



  } catch (err: any) {



    console.error('[Main] documents:getOpen error:', err)



    return { success: false, error: err.message, documents: [] }



  }



})







// 문서 선택 (Python Bridge 사용)



ipcMain.handle('documents:select', async (_, docType: string, index: number, docId?: string) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('selectDocument', { type: docType, index, docId })



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// 현재 페이지 정보 조회 (Python Bridge 사용)



ipcMain.handle('documents:getCurrentPageInfo', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('getCurrentPageInfo', {})



    return result



  } catch (err: any) {



    return { success: false, error: err.message }



  }



})







// 선택 영역 정보 조회
ipcMain.handle('documents:getSelectionInfo', async () => {
  try {
    if (!pythonBridge || !pythonBridge.isRunning()) {
      await initPythonBridge()
    }
    if (!pythonBridge || !pythonBridge.isRunning()) {
      return { success: false, error: 'Python Bridge not running' }
    }
    const result = await pythonBridge.call('getSelectionInfo', {})
    return result
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

// 선택 영역 감지 중단
ipcMain.handle('selection:pauseDetection', async () => {
  try {
    if (windowMonitor) {
      windowMonitor.sendCommand('pause_selection')
    }
    return { success: true }
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

// 선택 영역 감지 재개
ipcMain.handle('selection:resumeDetection', async () => {
  try {
    if (windowMonitor) {
      windowMonitor.sendCommand('resume_selection')
    }
    return { success: true }
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

// v4.1.9: 현재 활성 문서 docKey 조회
ipcMain.handle('doc:getActiveKey', async () => {
  try {
    if (lastActiveDocKey) {
      return { docKey: lastActiveDocKey }
    }

    if (!pythonBridge || !pythonBridge.isRunning()) {
      await initPythonBridge()
    }

    if (!pythonBridge || !pythonBridge.isRunning()) {
      return { docKey: lastActiveDocKey }
    }

    const info = await bridgeMutex.runExclusive(() =>
      pythonBridge!.call('getActiveDocInfo', {})
    )
    const currentDocKey = generateDocumentKey({
      documentId: info.activeDocumentId,
      path: info.activePath
    })

    if (currentDocKey) {
      lastActiveDocKey = currentDocKey
      if (win && !win.isDestroyed()) {
        win.webContents.send('doc:activeChanged', { docKey: currentDocKey })
      }
    }

    return { docKey: currentDocKey }
  } catch (err: any) {
    console.error('[Main] doc:getActiveKey error:', err)
    return { docKey: lastActiveDocKey }
  }
})

// 채팅 중지



ipcMain.handle('chat:cancel', async () => {
  try {
    if (activeChatCancelToken) {
      activeChatCancelToken.cancelled = true
    }

    const contextId = activeChatCancelToken?.contextId
    if (contextId && pythonBridge && pythonBridge.isRunning()) {
      pythonBridge.call('cancel_context', { contextId }).catch((err) => {
        console.error('[Main] chat:cancel - cancel_context failed:', err)
      })
    }

    // 프론트엔드에 취소 완료 알림 (즉시 UI 종료)
    if (win && !win.isDestroyed()) {
      win.webContents.send('chat:progress', 'cancelled', {
        message: '생성이 중지되었습니다.'
      })
    }

    if (agentBridge && agentBridge.isRunning()) {
      console.log('[Main] chat:cancel - Cancelling LLM stream')
      agentBridge.cancelStream().catch((err) => {
        console.error('[Main] chat:cancel - cancelStream failed:', err)
      })
    } else {
      console.log('[Main] chat:cancel - Agent Bridge not running')
    }

    return { success: true }
  } catch (err: any) {
    console.error('[Main] chat:cancel error:', err)
    return { success: false, error: err.message }
  }
})
// 채팅 (LLM 기반 문서 편집) - Agent Child Process 패턴



// v4.1.4: rejectionInfo, conversationHistory 파라미터 추가



// v5.0: projectId 파라미터 추가 (RAG 컨텍스트용)



// v5.1: chatId 파라미터 추가 (RAG 채팅 스코프 분리)


ipcMain.handle('chat:send', async (



  _,



  prompt: string,



  docType?: string,



  docIndex?: number,



  referenceContent?: string,



  referenceFileName?: string,



  startPage?: number,



  endPage?: number,



  diffModeEnabled?: boolean,



  // v4.1.4 추가 파라미터



  assistantMessageId?: string,



  rejectionInfo?: any[],



  conversationHistory?: string,



  // v5.0 추가 파라미터



  projectId?: string,



  // v5.1 추가 파라미터



  chatId?: string,



  // v6.2 추가 파라미터



  model?: string



) => {



  const cancelToken = { id: ++chatCancelTokenSeq, cancelled: false, contextId: null as string | null }



  activeChatCancelToken = cancelToken







  const isCancelled = () => activeChatCancelToken !== cancelToken || cancelToken.cancelled







  try {
    console.log('[Main] chat:send handler started, prompt:', prompt?.substring(0, 50))

    // Python Bridge (COM Process) 시작



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await initPythonBridge()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    // Agent Bridge (LLM Process) 시작



    if (!agentBridge || !agentBridge.isRunning()) {



      await initAgentBridge()



    }







    if (!agentBridge || !agentBridge.isRunning()) {



      return { success: false, error: 'Agent Bridge not running' }



    }



    const openAiSettings = getOpenAiSettings()
    let openAiKey: string
    let codexMode = false
    let codexAccountId = ''

    if (openAiSettings.connectionMode === 'codex') {
      const codexAuth = getCodexAuth()
      if (!codexAuth) {
        return { success: false, error: 'CODEX_AUTH_REQUIRED', message: 'Codex 인증이 필요합니다. 터미널에서 codex login을 실행하세요.' }
      }
      openAiKey = codexAuth.accessToken
      codexMode = true
      codexAccountId = codexAuth.accountId
    } else {
      openAiKey = openAiSettings.apiKey?.trim() ?? ''
      if (!openAiKey) {
        return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
      }
    }



    if (win && !win.isDestroyed()) {



      win.webContents.send('chat:progress', 'start', { stage: 'initializing' })



    }







    console.log('[Main] chat:send - prompt:', prompt.substring(0, 50) + '...')



    if (referenceContent) {



      console.log('[Main] chat:send - reference file:', referenceFileName)



    }



    if (startPage !== undefined) {



      console.log('[Main] chat:send - page range:', startPage, '-', endPage)



    }







    // CRITICAL: Track Changes must be enabled BEFORE LLM streaming starts



    // 첫 채팅에서도 Track Changes가 활성화되어야 승인/거절 기능이 작동함



    const shouldEnableDiffMode = diffModeEnabled !== false  // undefined or true → enable



    console.log('[Main] chat:send - diff mode:', shouldEnableDiffMode ? 'ON' : 'OFF', '(explicit:', diffModeEnabled, ')')



    try {



      const diffResult = await pythonBridge.call('setDiffMode', {
        enabled: shouldEnableDiffMode,
        skipTrackChanges: !shouldEnableDiffMode,
      })



      if (!diffResult.success) {



        console.error('[Main] setDiffMode returned failure:', diffResult.error)



      }



    } catch (err) {



      console.error('[Main] setDiffMode failed:', err)



      // Track Changes 활성화 실패 시 사용자에게 경고



      if (win && !win.isDestroyed()) {



        win.webContents.send('chat:progress', 'error', {



          message: 'Track Changes 활성화 실패 - 승인/거절 기능을 사용할 수 없습니다.'



        })



      }



    }







    // v4.1.4: 거절 정보 및 채팅 이력 로깅



    console.log('='.repeat(60))



    console.log('[v4.1.4] assistantMessageId:', assistantMessageId)



    console.log('[v4.1.4] rejectionInfo:', rejectionInfo ? `${rejectionInfo.length}개 거절 항목` : 'null')



    console.log('[v4.1.4] conversationHistory:', conversationHistory ? `${conversationHistory.length}자` : 'null')



    if (rejectionInfo && rejectionInfo.length > 0) {



      console.log('[v4.1.4] 거절 정보 상세:')



      rejectionInfo.forEach((r, i) => {



        console.log(`  [${i}] ${r.explain?.reason || 'reason 없음'}`)



      })



    }



    if (conversationHistory) {



      console.log('[v4.1.4] 대화 이력 미리보기:', conversationHistory.substring(0, 200) + '...')



    }



    console.log('='.repeat(60))







    // v4.1.4: 거절 정보를 프롬프트에 결합



    let enhancedPrompt = prompt

    if (conversationHistory) {
      const trimmed = conversationHistory.trim()
      if (trimmed) {
        enhancedPrompt = `<PREVIOUS_CONVERSATION>\n${trimmed}\n</PREVIOUS_CONVERSATION>\n\n${enhancedPrompt}`
      }
    }








    // 선택 영역 정보 추가 (대화 이력 다음, 거절 정보 이전)
    try {
      const selectionResult = await pythonBridge.call('getSelectionInfo', {})
      if (selectionResult.success && selectionResult.hasSelection) {
        const selType = selectionResult.isTableSelection ? '표' : '텍스트'
        const selText = selectionResult.selectedTextFull || selectionResult.selectedText || ''

        if (selText) {
          enhancedPrompt = `[선택영역: ${selType}]\n${selText}\n\n---\n\n${enhancedPrompt}`
          console.log('[Main] 선택 영역 정보 추가:', selType, `(${selText.length}자)`)
        }
      }
    } catch (err) {
      console.error('[Main] 선택 영역 정보 가져오기 실패:', err)
    }

    if (rejectionInfo && rejectionInfo.length > 0) {
      const normalizedRejections = rejectionInfo.filter((r: any) => !!r && !!r.fact)
      const latestAll = [...normalizedRejections]
        .reverse()
        .find((r: any) => r.fact?.rejectionType === 'all')
      const latest = latestAll || normalizedRejections[normalizedRejections.length - 1]
      if (latest) {
        const rejectionType = latest.fact?.rejectionType === 'all' ? 'all' : 'partial'
        const requiresFullRegen = Boolean(
          latest.fact?.requiresFullRegen ?? (rejectionType === 'all')
        )
        const rejectedCount = Number(latest.fact?.rejectedCount ?? 0)
        const reason = String(latest.explain?.reason || '').trim()
        const ops = Array.isArray(latest.explain?.rejectedOps) ? latest.explain.rejectedOps : []
        const rejectedSamples = ops
          .slice(0, 3)
          .map((op: any) => `${op.kind || '변경'}${op.summary ? `: ${op.summary}` : ''}`)
          .join(' | ')

        enhancedPrompt = `<REJECTION_CONTEXT>\nrejection_type: ${rejectionType}\nrequires_full_regen: ${requiresFullRegen ? 'true' : 'false'}\nrejected_count: ${rejectedCount}\n${reason ? `reason: ${reason}\n` : ''}${rejectedSamples ? `rejected_samples: ${rejectedSamples}\n` : ''}</REJECTION_CONTEXT>\n\n${enhancedPrompt}`
      }
    }







    // v5.3: File Search Tool Use로 변경 - LLM이 필요할 때 직접 검색



    // 기존 자동 RAG 쿼리 제거됨 (LLM Tool Use로 대체)



    console.log('[v5.3] File Search Tool Use 모드 - projectId:', projectId, ', chatId:', chatId || 'none')







    // v4.1.4: docKey 생성 (한 번만 호출하여 재사용)



    const activeDocInfo = await bridgeMutex.runExclusive(() =>



      pythonBridge!.call('getActiveDocInfo', {})



    )







    // v6.0: 양식쌍 자동 매칭 (TypeScript에서 직접 처리 - Python 호출 제거)



    let matchedPairContext = ''



    if (projectId) {



      try {



        const templatePairs = getTemplatePairsForProject(projectId)










        if (templatePairs.length > 0) {



          const boundDocName = activeDocInfo.activeName || ''







          if (boundDocName) {



            console.log('[v6.0] 양식쌍 매칭 시도 - boundDocName:', boundDocName)







            // TypeScript에서 직접 매칭 (Python 호출 제거)



            const boundNameNoExt = boundDocName.replace(/\.[^/.]+$/, '').toLowerCase()



            let bestMatch: { pair: typeof templatePairs[0]; score: number } | null = null







            for (const pair of templatePairs) {



              const templateNoExt = pair.templateFile.name.replace(/\.[^/.]+$/, '').toLowerCase()



              const referenceNoExt = pair.filledFile.name.replace(/\.[^/.]+$/, '').toLowerCase()







              let score = 0



              // 정확히 일치



              if (boundNameNoExt === templateNoExt || boundNameNoExt === referenceNoExt) {



                score = 100



              }



              // 템플릿명이 바인딩문서명에 포함



              else if (boundNameNoExt.includes(templateNoExt) || boundNameNoExt.includes(referenceNoExt)) {



                score = 80



              }



              // 바인딩문서명이 템플릿명에 포함



              else if (templateNoExt.includes(boundNameNoExt) || referenceNoExt.includes(boundNameNoExt)) {



                score = 70



              }







              if (score > 0 && (!bestMatch || score > bestMatch.score)) {



                bestMatch = { pair, score }



              }



            }







            if (bestMatch && bestMatch.score >= 50) {



              const { pair, score } = bestMatch



              // v6.0: 양식쌍 이름은 템플릿 파일명에서 확장자 제거



              const pairName = pair.templateFile.name.replace(/\.[^/.]+$/, '')



              console.log('[v6.0] 양식쌍 매칭 성공:', pairName, '(score:', score, ')')







              // v6.2: diff.json을 직접 읽어서 프롬프트에 포함 (RAG 검색 대신)



              let diffContent = ''



              try {



                const diffPath = path.join(



                  app.getPath('userData'),



                  'projects',



                  projectId,



                  'template_pairs',



                  pair.id,



                  'diff.json'



                )



                console.log('[v6.2] diff.json 경로:', diffPath)







                if (fs.existsSync(diffPath)) {



                  const diffData = JSON.parse(fs.readFileSync(diffPath, 'utf-8'))



                  const changes = diffData.changes || []







                  // 의미 있는 변경사항만 필터링 (빈 값→실제 값으로 채워진 필드)



                  const meaningfulChanges = changes.filter((c: any) => {



                    const templateEmpty = !c.templateValue || c.templateValue.trim() === ''



                    const filledHasValue = c.filledValue && c.filledValue.trim() !== ''



                    return templateEmpty && filledHasValue



                  })







                  if (meaningfulChanges.length > 0) {



                    const diffLines = meaningfulChanges.slice(0, 50).map((c: any) => {



                      return `  - ID ${c.fieldId} (${c.fieldType}): "${c.filledValue}"`



                    })



                    diffContent = `



**작성 패턴 (참조 문서에서 추출된 실제 데이터)**:



아래는 사례 문서에서 빈칸에 채워진 값들입니다. 이 패턴을 참고하세요:



${diffLines.join('\n')}







이 값들은 참조 예시입니다. 사용자가 다른 내용(예: "사업계획서")으로 작성하라고 하면,



프로젝트 참조 파일에서 관련 내용을 검색하여 실제 내용을 채우세요.



`



                    console.log('[v6.2] diff 변경사항 포함:', meaningfulChanges.length, '개')



                  }



                }



              } catch (err) {



                console.error('[v6.2] diff.json 읽기 오류:', err)



              }







              matchedPairContext = `



<MATCHED_TEMPLATE_PAIR>



현재 편집 중인 문서는 **'${pairName}'** 양식쌍의 템플릿입니다.



${diffContent}



**v6.2 중요 안내**:

- 위 작성 패턴은 **양식 스타일/톤 참고용**입니다.

- 작성할 실제 내용은 현재 사용자 요청과 문서 컨텍스트를 우선합니다.

- 파일 검색이 필요한 경우에는 작성 대상 필드명/라벨 키워드로 검색하세요.

</MATCHED_TEMPLATE_PAIR>







`



            } else {



              console.log('[v6.0] 양식쌍 매칭 실패 - 일치하는 양식쌍 없음')



            }



          }



        }



      } catch (err) {



        console.error('[v6.0] 양식쌍 매칭 오류:', err)



      }



    }







    // v6.1: 사용 가능한 파일 목록 컨텍스트 생성 (DB 기반)



    let availableFilesContext = ''



    if (projectId) {



      try {



        const projectFiles = getProjectFilesForContext(projectId)










        if (projectFiles.length >= 0) {



          const filesList: string[] = []



          const readyProjectFiles = projectFiles.filter(



            file => file.indexStatus?.status === 'ready' && SEARCHABLE_EXTENSIONS.has(file.extension)



          )



          if (readyProjectFiles.length > 0) {



            filesList.push('**프로젝트 일반 파일**:')



            for (const file of readyProjectFiles) {



              filesList.push(`- ${file.name}`)



            }



          }







          // 2. 채팅 업로드 파일 (chatId가 있는 경우)



          if (chatId) {



            const chatFiles = getChatFilesForContext(chatId)



              .filter(file => SEARCHABLE_EXTENSIONS.has(file.extension))



            if (chatFiles.length > 0) {



              filesList.push('')



              filesList.push('**이 채팅에 업로드된 파일**:')



              for (const file of chatFiles) {



                filesList.push(`- ${file.name}`)



              }



            }



          }







          if (filesList.length > 0) {



            // 3. 양식쌍 목록 (참고용)



            const templatePairs = getTemplatePairsForProject(projectId)
            if (templatePairs.length > 0) {



              filesList.push('')



              filesList.push('**양식쌍 (템플릿-작성사례)**:')



              for (const pair of templatePairs) {



                const pairName = pair.templateFile.name.replace(/\.[^/.]+$/, '')



                filesList.push(`- ${pairName}: 템플릿(${pair.templateFile.name}) ↔ 사례(${pair.filledFile.name})`)



              }



            }







            availableFilesContext = `



<AVAILABLE_FILES>



아래는 이 프로젝트에서 검색 가능한 파일 목록입니다.



검색 기능으로 작성 대상 필드명/라벨 키워드를 검색하면 해당 내용을 참조할 수 있습니다.







${filesList.join('\n')}



</AVAILABLE_FILES>







`



            console.log('[v6.1] AVAILABLE_FILES 컨텍스트 추가:', filesList.length, '개 항목')



          } else if (projectFiles.length > 0) {



            console.warn('[v6.1] manifest에 인덱싱 완료 파일이 없어 AVAILABLE_FILES 생략')



          }



        }



      } catch (err) {



        console.error('[v6.1] 파일 목록 컨텍스트 생성 오류:', err)



      }



    }







    // 매칭된 양식쌍 컨텍스트와 파일 목록을 프롬프트에 추가



    if (availableFilesContext) {



      enhancedPrompt = availableFilesContext + enhancedPrompt



    }



    if (matchedPairContext) {



      enhancedPrompt = matchedPairContext + enhancedPrompt



    }







    console.log('[v4.1.4] 최종 프롬프트 미리보기:', enhancedPrompt.substring(0, 500) + '...')



    const currentDocKey = generateDocumentKey({



      documentId: activeDocInfo.activeDocumentId,



      path: activeDocInfo.activePath



    })



    console.log('[v4.1.4] 현재 문서 docKey:', currentDocKey)







    // Phase 1: COM Process에서 DocumentView 준비
    console.log('[Main] Calling prepare_context...')

    const contextResult = await pythonBridge.call('prepare_context', {



      prompt: enhancedPrompt,  // v4.1.4: 향상된 프롬프트 사용



      startPage,



      endPage,



      referenceFile: referenceContent,



      referenceFileName,



      docIndex,



      docType,



    })







    console.log('[Main] prepare_context returned:', contextResult.success, contextResult.error || '')

    if (!contextResult.success) {
      console.error('[Main] prepare_context failed:', contextResult.error)



      return contextResult



    }







    if (isCancelled()) {



      console.log('[Main] chat:send cancelled before LLM stream start')



      return { success: false, error: 'cancelled', cancelled: true }



    }







    const { document_graph_json, prompt: finalPrompt, context_id } = contextResult
    cancelToken.contextId = context_id



    const docContent = typeof document_graph_json === 'string' ? document_graph_json.trim() : ''
    if (!docContent) {
      return { success: false, error: 'document_graph_json_missing' }
    }
    console.log('[Main] Context prepared - context_id:', context_id)

    // v4.1.5: context_ready stage 이벤트 전송 - scan → thinking 단계 전환
    if (win && !win.isDestroyed()) {
      console.log('[Main] Sending context_ready stage event')
      win.webContents.send('chat:progress', 'stage', {
        stage: 'context_ready',
        message: 'AI가 분석 중...',
      })
    } else {
      console.log('[Main] Window not available for context_ready event')
    }

    console.log('[v4.1.4] 최종 LLM 프롬프트 길이:', finalPrompt?.length || 0)











    // Phase 2: Delta 이벤트 즉시 실행 (순서 보장을 위해 큐로 직렬화)



    const messages: string[] = []



    const executedDeltas: any[] = []



    const executedDeltaKeys = new Set<string>()



    let editsCount = 0



    let editStarted = false



    let editQueue = Promise.resolve()







    const recordDeltaSummary = (deltaEvent: any, operation: string) => {



      if (executedDeltas.length >= 50) return







      const summary: Record<string, any> = {



        action: deltaEvent.action,



        id: deltaEvent.id,



        metadata: { operation },



      }







      if (typeof deltaEvent.content === 'string') {



        summary.content = deltaEvent.content.slice(0, 200)



      }







      const oldText = deltaEvent.metadata?.old_text



      if (typeof oldText === 'string') {



        summary.metadata.old_text = oldText.slice(0, 200)



      }







      const rowTexts = Array.isArray(deltaEvent.metadata?.row_texts)



        ? deltaEvent.metadata.row_texts



        : Array.isArray(deltaEvent.rows)



          ? deltaEvent.rows



          : null



      if (rowTexts) {



        summary.rows = rowTexts.slice(0, 5).map((text: any) =>



          typeof text === 'string' ? text.slice(0, 80) : text



        )



      }







      executedDeltas.push(summary)



    }







    const enqueueDelta = (deltaEvent: any) => {



      if (isCancelled()) {



        return



      }



      const operation = deltaEvent.metadata?.operation || deltaEvent.action



      const contentKey = typeof deltaEvent.content === 'string' ? deltaEvent.content : ''



      const oldTextKey = deltaEvent.metadata?.old_text || deltaEvent.metadata?.find || ''



      const styleMeta = deltaEvent.metadata || {}
      const styleKey = (operation === 'apply_para_style' || operation === 'apply_charshape' || operation === 'apply_format')
        ? JSON.stringify({
          font_size: styleMeta.font_size,
          font_family: styleMeta.font_family,
          align: styleMeta.align,
          spacing: styleMeta.spacing,
          indentation: styleMeta.indentation,
          bold: styleMeta.bold,
          italic: styleMeta.italic,
        })
        : ''

      const signatureKey = JSON.stringify({
        scope_table_id: styleMeta.scope_table_id,
        td_sig_v1: styleMeta.td_sig_v1,
        p_sig_v1: styleMeta.p_sig_v1,
        table_path: styleMeta.table_path,
      })



      const rowTextsKey = Array.isArray(deltaEvent.metadata?.row_texts)



        ? JSON.stringify(deltaEvent.metadata.row_texts)



        : Array.isArray(deltaEvent.rows)



          ? JSON.stringify(deltaEvent.rows)



          : ''



      const dedupeKey = `${operation}|${deltaEvent.id ?? ''}|${oldTextKey}|${contentKey}|${rowTextsKey}|${styleKey}|${signatureKey}`







      if (executedDeltaKeys.has(dedupeKey)) {



        console.log('[Main] Skipping duplicate delta:', operation, deltaEvent.id)



        return



      }



      executedDeltaKeys.add(dedupeKey)







      if (!editStarted && win && !win.isDestroyed()) {



        editStarted = true



        win.webContents.send('chat:progress', 'editDocument', { status: 'start' })



      }







      editQueue = editQueue



        .then(async () => {



          try {



            if (isCancelled()) {



              return



            }



            console.log('[Main] Executing delta:', deltaEvent.id, operation)



            const editResult = await pythonBridge!.call('execute_delta', {
              ...deltaEvent,
              context_id: context_id
            })



            const replacedCount = typeof editResult.replaced_count === 'number'



              ? editResult.replaced_count



              : 0



            const wasEdited = editResult.edited === true || replacedCount > 0



            if (wasEdited) {



              editsCount++



              recordDeltaSummary(deltaEvent, operation)



              if (win && !win.isDestroyed()) {



                const content =



                  typeof deltaEvent.content === 'string'



                    ? deltaEvent.content.slice(0, 50)



                    : undefined



                win.webContents.send('chat:progress', 'edit', {



                  type: operation,



                  content,



                })



              }



            }



            console.log('[Main] Delta executed:', deltaEvent.id, 'edited:', editResult.edited, 'replaced_count:', replacedCount)



          } catch (err) {



            console.error('[Main] execute_delta failed:', deltaEvent.id, err)



          }



        })



        .catch((err) => {



          console.error('[Main] execute_delta queue error:', err)



        })



    }







    const allowedDeltaActions = new Set([
      'edit_document',
      'format_text',
      'insert_note',
      'insert_source_ref',
      'write_text',
      'line_break',
    ])

    const deltaHandler = (deltaEvent: any) => {



      if (isCancelled()) {



        return



      }



      console.log('[Main] Delta received:', deltaEvent.action, deltaEvent.id)







      if (deltaEvent.action === 'thinking') {



        if (win && !win.isDestroyed()) {



          win.webContents.send('chat:progress', 'thinking', {



            content: deltaEvent.message || deltaEvent.content,



            metadata: deltaEvent.metadata,



          })



        }



        return



      }







      if (deltaEvent.action === 'message') {



        if (deltaEvent.message) {



          messages.push(deltaEvent.message)



          if (win && !win.isDestroyed()) {



            win.webContents.send('chat:progress', 'message', { text: deltaEvent.message })



          }



        }



        return



      }







      if (allowedDeltaActions.has(deltaEvent.action) || deltaEvent.action === 'append_table_row') {
        enqueueDelta(deltaEvent)
        return
      }

      console.log('[Main] Delta ignored (unsupported action):', deltaEvent.action)



    }







    // Delta 핸들러 등록



    agentBridge.on('delta', deltaHandler)







    // v5.3: RAG 검색 Progress 핸들러 등록 (Tool Use에서 발생)



    const progressHandler = (event: string, data: any) => {
      if (isCancelled()) {
        return
      }

      if (event === 'tool:validation_error') {
        console.warn('[Main] Tool validation error:', data?.tool_name, data?.reason)
        return
      }



      if ((event === 'rag:search' || event === 'fileSearch:search') && win && !win.isDestroyed()) {



        console.log('[v5.3] RAG Tool Use:', data.stage, data.message)



        // rag:* 이벤트로 전달 (프론트엔드와 동기화)



        win.webContents.send('chat:progress', 'rag:search', {



          stage: data.stage,



          message: data.message || '자료 검색 중...'



        })



      }



    }



    agentBridge.on('progress', progressHandler)







    try {



      // Phase 3: Agent Process에서 LLM 스트리밍 실행



      console.log('[Main] Starting LLM stream...')



      if (win && !win.isDestroyed()) {



        win.webContents.send('chat:progress', 'stage', {



          stage: 'thinking',



          message: 'AI가 분석 중...',



        })



      }







      // v5.3: File Search Tool Use 컨텍스트 설정



      const userDataPath = app.getPath('userData')



      const ragContext = (projectId || chatId) ? {



        projectId,



        chatId,



        userDataPath,



        openaiApiKey: openAiKey,

        fileSearchModel: openAiSettings.fileSearchModel,

        embeddingModel: openAiSettings.embeddingModel
      } : undefined

      const promptSettings = getPromptSettings()
      const allowPartialPrompt = promptSettings.scope === 'partial' || promptSettings.scope === 'full'
      const allowFullPrompt = promptSettings.scope === 'full'
      const promptCustomEnabled = allowPartialPrompt && promptSettings.promptCustomEnabled
      const promptCustomRules = allowPartialPrompt ? promptSettings.promptCustomRules : ''
      const promptFullOverride = allowFullPrompt ? promptSettings.promptFullOverride : ''







      console.log('[v5.3] File Search 컨텍스트:', ragContext ? 'enabled' : 'disabled')







      const streamOptions = {
        useDelta: true,
        useHtml: false,  // Delta 모드 (textbox/p 구조 유지)
        compactMode: true,
        openaiApiKey: openAiKey,
        ragContext,  // v5.3: RAG Tool Use
        // Codex(ChatGPT) 엔드포인트는 gpt-5.5만 지원, 일반 API는 사용자 설정 모델
        model: codexMode ? 'gpt-5.5' : openAiSettings.defaultModel,
        promptCustomRules,
        promptCustomEnabled,
        promptFullOverride,
        codexMode,
        codexAccountId,
      }

      const appendLatestMessage = (result: any, replaceExisting = false) => {
        if (replaceExisting) {
          messages.length = 0
        } else if (messages.length > 0) {
          return
        }
        if (!Array.isArray(result?.messages) || result.messages.length === 0) return
        const trimmed = result.messages.filter((msg: any) => typeof msg === 'string' && msg.trim())
        if (trimmed.length > 0) {
          messages.push(trimmed[trimmed.length - 1])
        }
      }

      const streamResult = await agentBridge.streamLLM(docContent, finalPrompt, streamOptions)
      const cancelledAfterStream = isCancelled()
      const finalPassCommands = Number((streamResult as any)?.commands ?? 0)
      appendLatestMessage(streamResult)

      console.log('[Main] LLM command counts:', `final=${finalPassCommands}, edits=${editsCount}`)
      console.log('[Main] LLM stream completed:', streamResult)



      // Delta 및 Progress 핸들러 제거 (스트리밍 완료 후)



      agentBridge.off('delta', deltaHandler)



      agentBridge.off('progress', progressHandler)







      // Phase 3.5: 남은 큐가 끝날 때까지 대기



      await editQueue



      if (!cancelledAfterStream && win && !win.isDestroyed()) {



        if (editStarted) {



          win.webContents.send('chat:progress', 'editDocument', { status: 'end' })



        } else {



          win.webContents.send('chat:progress', 'editDocument', { status: 'end', edits: 0 })



        }



      }







      console.log('[Main] All deltas executed. Total edits:', editsCount)







      if (cancelledAfterStream) {



        console.log('[Main] chat:send cancelled during LLM stream')



        return {
          success: false,
          error: 'cancelled',
          cancelled: true,
          edits: editsCount,
          executedDeltas,
          messages,
          token_usage: streamResult?.token_usage
        }



      }

      // Phase 4: 편집 완료 처리



      const finalizeResult = await pythonBridge.call('finalize_edits', {



        contextId: context_id,



        editsCount,



        messages,



      })







      if (win && !win.isDestroyed()) {



        win.webContents.send('chat:progress', 'complete', {



          edits: editsCount,



          messages,



          editHistory: finalizeResult?.edit_history ?? finalizeResult?.editHistory,
          executedDeltas,



        })



      }







      const resolvedMessage =
        (Array.isArray(messages)
          ? [...messages].reverse().find((msg) => typeof msg === 'string' && msg.trim())
          : undefined) ||
        (typeof (finalizeResult as any)?.message === 'string' && (finalizeResult as any).message.trim()
          ? (finalizeResult as any).message.trim()
          : undefined)

      return {



        success: true,



        edits: editsCount,



        messages,
        message: resolvedMessage,



        executedDeltas,



        token_usage: streamResult?.token_usage,



        ...finalizeResult,



      }



    } catch (err: any) {



      // 에러 발생 시에도 핸들러 제거



      agentBridge.off('delta', deltaHandler)



      agentBridge.off('progress', progressHandler)  // v5.3: Progress 핸들러도 제거



      throw err



    }



  } catch (err: any) {



    console.error('[Main] chat:send error:', err)
    const rawError = err?.message || '요청 처리 중 오류가 발생했습니다.'
    const safeError =
      typeof rawError === 'string' && (
        /Error code:\s*\d+/i.test(rawError) ||
        /invalid_api_key|timeout|connection/i.test(rawError)
      )
        ? 'AI 요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
        : rawError

    if (win && !win.isDestroyed()) {
      win.webContents.send('chat:progress', 'error', {
        message: safeError
      })
    }

    return { success: false, error: safeError, trace: err.trace }



  } finally {



    if (activeChatCancelToken === cancelToken) {



      activeChatCancelToken = null



    }



  }



})







// ============================================================



// Undo/Redo 및 Diff 모드 API (TASK-006)



// ============================================================







// Undo 실행



ipcMain.handle('edit:undo', async (_, count?: number) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('undo', { count: count ?? 1 })



    console.log('[Main] edit:undo - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] edit:undo error:', err)



    return { success: false, error: err.message }



  }



})







// Redo 실행



ipcMain.handle('edit:redo', async (_, count?: number) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('redo', { count: count ?? 1 })



    console.log('[Main] edit:redo - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] edit:redo error:', err)



    return { success: false, error: err.message }



  }



})







// Diff 모드 설정



ipcMain.handle('edit:setDiffMode', async (_, enabled: boolean) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('setDiffMode', { enabled })



    console.log('[Main] edit:setDiffMode - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] edit:setDiffMode error:', err)



    return { success: false, error: err.message }



  }



})







// Diff 모드 상태 조회



ipcMain.handle('edit:getDiffMode', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('getDiffMode', {})



    return result



  } catch (err: any) {



    console.error('[Main] edit:getDiffMode error:', err)



    return { success: false, error: err.message }



  }



})







// 편집 이력 조회



ipcMain.handle('edit:getHistory', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running', history: [], totalEdits: 0 }



    }







    const result = await pythonBridge.call('getEditHistory', {})



    return result



  } catch (err: any) {



    console.error('[Main] edit:getHistory error:', err)



    return { success: false, error: err.message, history: [], totalEdits: 0 }



  }



})







// 편집 이력 초기화



ipcMain.handle('edit:clearHistory', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('clearEditHistory', {})



    return result



  } catch (err: any) {



    console.error('[Main] edit:clearHistory error:', err)



    return { success: false, error: err.message }



  }



})







// Diff 변경사항 승인



ipcMain.handle('edit:accept', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('applyChanges', {})



    console.log('[Main] edit:accept - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] edit:accept error:', err)



    return { success: false, error: err.message }



  }



})







// Diff 변경사항 거절



ipcMain.handle('edit:reject', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('rejectChanges', {})



    console.log('[Main] edit:reject - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] edit:reject error:', err)



    return { success: false, error: err.message }



  }



})







// ============================================================



// TrackChange 부분 승인/거절 (HWP 2022 이전)



// ============================================================







// TrackChange UI 상태 조회



ipcMain.handle('trackChanges:getContext', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { pending: false, selectionCount: 0, contextVisible: false }



    }







    const result = await pythonBridge.call('trackChanges:getContext', {})



    return result



  } catch (err: any) {



    console.error('[Main] trackChanges:getContext error:', err)



    return { pending: false, selectionCount: 0, contextVisible: false }



  }



})







// TrackChange 선택 범위 캐시 (드래그 유지용)



ipcMain.handle('trackChanges:cacheSelection', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, selectionCount: 0, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('trackChanges:cacheSelection', {})



    return result



  } catch (err: any) {



    console.error('[Main] trackChanges:cacheSelection error:', err)



    return { success: false, selectionCount: 0, error: err.message }



  }



})







// 전체 TrackChange 승인



ipcMain.handle('trackChanges:applyAll', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, hasRemaining: true, autoComplete: false, error: 'Python Bridge not running' }



    }







    const result = await pythonBridge.call('trackChanges:applyAll', {})



    console.log('[Main] trackChanges:applyAll - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] trackChanges:applyAll error:', err)



    return { success: false, hasRemaining: true, autoComplete: false, error: err.message }



  }



})







// 전체 TrackChange 거절 (v4.1.4: mismatch 방지 + RejectResult 구조)



ipcMain.handle('trackChanges:rejectAll', async (_, params?: { docKey?: string; chatId?: string }) => {



  const docKey = params?.docKey







  // 헬퍼 함수: mismatch 결과 생성



  function mismatchResult(reason: string): RejectResult {



    return {



      fact: {



        success: false,



        rejectedCount: 0,



        rejectionType: 'all',
        requiresFullRegen: true,



        uncertain: true,



        mismatch: true



      },



      explain: {



        rejectedOps: [],



        reason: `거절 중단: ${reason}`



      }



    }



  }



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return mismatchResult('Python Bridge not running')



    }







    // v4.1.4: bridgeMutex로 직렬화



    return bridgeMutex.runExclusive(async () => {



      // Step 1: reject 실행 전에 mismatch 체크 (불변식 12)



      if (docKey) {



        const activeInfo = await pythonBridge!.call('getActiveDocInfo', {})



        const activeDocKey = buildActiveKeyForComparison(docKey, {



          activeDocumentId: activeInfo.activeDocumentId,



          activePath: activeInfo.activePath



        })







        if (!activeDocKey) {



          console.log('[Main] trackChanges:rejectAll activeDocKey unavailable - skipping mismatch check')



        } else if (activeDocKey !== docKey) {



          console.log(`[Main] trackChanges:rejectAll mismatch - docKey: ${docKey}, activeDocKey: ${activeDocKey}`)



          return mismatchResult('문서 불일치')



        }



      }







      // Step 2: 일치할 때만 reject 실행



      const pythonResult = await pythonBridge!.call('trackChanges:rejectAll', {})



      console.log('[Main] trackChanges:rejectAll - pythonResult:', pythonResult)







      // RejectResult 구조로 변환



      const extractionQuality = pythonResult.extractionQuality || 'none'



      const uncertain = extractionQuality !== 'full'



      const result: RejectResult = {



        fact: {



          success: pythonResult.success,



          rejectedCount: pythonResult.count ?? (pythonResult.success ? 1 : 0),



          rejectionType: 'all',
          requiresFullRegen: true,



          uncertain,



          mismatch: false



        },



        explain: {



          rejectedOps: extractionQuality === 'none' ? [] : (pythonResult.rejectedOps ?? []),



          reason: generateRejectReason(pythonResult, 'all')



        }



      }







      // 기존 응답 필드도 포함 (하위 호환성)



      return {



        ...result,



        success: pythonResult.success,



        rejectionType: result.fact.rejectionType,



        requiresFullRegen: result.fact.requiresFullRegen,



        hasRemaining: pythonResult.hasRemaining,



        autoComplete: pythonResult.autoComplete



      }



    })



  } catch (err: any) {



    console.error('[Main] trackChanges:rejectAll error:', err)



    return mismatchResult(err.message)



  }



})







// 선택된 TrackChange 승인



ipcMain.handle('trackChanges:applySelected', async () => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, processed: 0, hasRemaining: true, autoComplete: false, showToast: false, error: 'Python Bridge not running' }



    }







    const result = await bridgeMutex.runExclusive(() =>



      pythonBridge!.call('trackChanges:applySelected', {})



    )



    console.log('[Main] trackChanges:applySelected - result:', result)



    return result



  } catch (err: any) {



    console.error('[Main] trackChanges:applySelected error:', err)



    return { success: false, processed: 0, hasRemaining: true, autoComplete: false, showToast: false, error: err.message }



  }



})







// 선택된 TrackChange 거절 (v4.1.4: mismatch 방지 + RejectResult 구조)



ipcMain.handle('trackChanges:rejectSelected', async (_, params?: { docKey?: string; chatId?: string }) => {



  const docKey = params?.docKey







  function mismatchResult(reason: string): RejectResult {



    return {



      fact: {



        success: false,



        rejectedCount: 0,



        rejectionType: 'partial',
        requiresFullRegen: false,



        uncertain: true,



        mismatch: true



      },



      explain: {



        rejectedOps: [],



        reason: `거절 중단: ${reason}`



      }



    }



  }



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      return mismatchResult('Python Bridge not running')



    }







    return bridgeMutex.runExclusive(async () => {



      // Step 1: reject 실행 전에 mismatch 체크



      if (docKey) {



        const activeInfo = await pythonBridge!.call('getActiveDocInfo', {})



        const activeDocKey = buildActiveKeyForComparison(docKey, {



          activeDocumentId: activeInfo.activeDocumentId,



          activePath: activeInfo.activePath



        })







        if (!activeDocKey) {



          console.log('[Main] trackChanges:rejectSelected activeDocKey unavailable - skipping mismatch check')



        } else if (activeDocKey !== docKey) {



          console.log(`[Main] trackChanges:rejectSelected mismatch - docKey: ${docKey}, activeDocKey: ${activeDocKey}`)



          return mismatchResult('문서 불일치')



        }



      }







      // Step 2: 일치할 때만 reject 실행



      const pythonResult = await pythonBridge!.call('trackChanges:rejectSelected', {})



      console.log('[Main] trackChanges:rejectSelected - pythonResult:', pythonResult)







      const extractionQuality = pythonResult.extractionQuality || 'none'



      const uncertain = extractionQuality !== 'full'



      const result: RejectResult = {



        fact: {



          success: pythonResult.success,



          rejectedCount: pythonResult.count ?? pythonResult.processed ?? 0,



          rejectionType: 'partial',
          requiresFullRegen: Boolean(pythonResult.requiresFullRegen ?? false),



          uncertain,



          mismatch: false



        },



        explain: {



          rejectedOps: extractionQuality === 'none' ? [] : (pythonResult.rejectedOps ?? []),



          reason: generateRejectReason(pythonResult, 'partial')



        }



      }







      return {



        ...result,



        success: pythonResult.success,



        rejectionType: result.fact.rejectionType,



        requiresFullRegen: result.fact.requiresFullRegen,



        processed: pythonResult.processed,



        hasRemaining: pythonResult.hasRemaining,



        autoComplete: pythonResult.autoComplete,



        showToast: pythonResult.showToast



      }



    })



  } catch (err: any) {



    console.error('[Main] trackChanges:rejectSelected error:', err)



    return mismatchResult(err.message)



  }



})







// v4.1.4: reason 템플릿 생성



function generateRejectReason(result: any, type: 'all' | 'partial'): string {



  if (!result.success) return '거절 수행 실패'







  if (result.extractionQuality === 'none') {



    return '거절 상세 추출 실패'



  }







  const count = result.count ?? result.processed ?? 0







  if (type === 'all') {



    return `변경사항 전체 거절 (${count}개)`



  }







  if (result.inTable === true) {



    return `표 영역 변경 거절 (${count}개)`



  } else if (result.inTable === false) {



    return `본문 텍스트 변경 거절 (${count}개)`



  }







  return `변경사항 부분 거절 (${count}개)`



}







// ============================================================



// CVD 추출 및 Diff (Phase 4)



// ============================================================







// CVD 추출 (Template Pair)



ipcMain.handle('cvd:extractPair', async (_, args: {



  projectId: string



  pairId: string



  templatePath: string  // 상대 경로 또는 절대 경로



  filledPath: string    // 상대 경로 또는 절대 경로



}) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await startPythonProcess()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python bridge not running' }



    }







    const userDataPath = app.getPath('userData')



    const projectBasePath = path.join(userDataPath, 'projects', args.projectId)







    // 상대 경로인지 확인 후 변환



    const templateAbsPath = path.isAbsolute(args.templatePath)



      ? args.templatePath



      : path.join(projectBasePath, args.templatePath)



    const filledAbsPath = path.isAbsolute(args.filledPath)



      ? args.filledPath



      : path.join(projectBasePath, args.filledPath)







    console.log('[Main] cvd:extractPair - projectId:', args.projectId, ', pairId:', args.pairId)







    // Python(FileReaderBridge)에 CVD 추출 요청
    if (fileReaderBridge && !fileReaderBridge.isRunning()) {
      try { await fileReaderBridge.start() } catch {}
    }
    if (!fileReaderBridge || !fileReaderBridge.isRunning()) {
      return { success: false, error: 'FileReader bridge not running' }
    }

    const result = await fileReaderBridge.call('cvd:extractPair', {



      projectId: args.projectId,



      pairId: args.pairId,



      templatePath: templateAbsPath,



      filledPath: filledAbsPath,



      userDataPath: userDataPath



    })







    // 진행 이벤트는 pythonBridge의 progress 이벤트로 전달됨



    return result



  } catch (err: any) {



    console.error('[Main] cvd:extractPair error:', err)



    return { success: false, error: err.message }



  }



})







// Diff 생성 (CVD 추출 후 호출)



ipcMain.handle('cvd:generateDiff', async (_, args: {



  projectId: string



  pairId: string



}) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {

      await startPythonProcess()

    }

    if (!pythonBridge || !pythonBridge.isRunning()) {

      return { success: false, error: 'Python bridge not running' }

    }










    console.log('[Main] cvd:generateDiff - projectId:', args.projectId, ', pairId:', args.pairId)







    const result = await pythonBridge.call('cvd:generateDiff', {



      projectId: args.projectId,



      pairId: args.pairId,



      userDataPath: app.getPath('userData')



    })







    return result



  } catch (err: any) {



    console.error('[Main] cvd:generateDiff error:', err)



    return { success: false, error: err.message }



  }



})







// CVD 추출 + Diff 생성 통합 (편의용)



ipcMain.handle('cvd:processTemplatePair', async (_, args: {



  projectId: string



  pairId: string



  templatePath: string  // 상대 경로 (template_pairs/{pairId}/template.hwp)



  filledPath: string    // 상대 경로 (template_pairs/{pairId}/filled.hwp)



}) => {



  try {



    if (!pythonBridge || !pythonBridge.isRunning()) {



      await startPythonProcess()



    }







    if (!pythonBridge || !pythonBridge.isRunning()) {



      return { success: false, error: 'Python bridge not running' }



    }







    const userDataPath = app.getPath('userData')



    const projectBasePath = path.join(userDataPath, 'projects', args.projectId)







    // 상대 경로 → 절대 경로 변환



    const templateAbsPath = path.join(projectBasePath, args.templatePath)



    const filledAbsPath = path.join(projectBasePath, args.filledPath)







    console.log('[Main] cvd:processTemplatePair - starting full process for pairId:', args.pairId)



    console.log('[Main] templatePath:', templateAbsPath)



    console.log('[Main] filledPath:', filledAbsPath)







    // 1. CVD 추출
    if (fileReaderBridge && !fileReaderBridge.isRunning()) {
      try { await fileReaderBridge.start() } catch {}
    }
    if (!fileReaderBridge || !fileReaderBridge.isRunning()) {
      return { success: false, error: 'FileReader bridge not running' }
    }

    const extractResult = await fileReaderBridge.call('cvd:extractPair', {



      projectId: args.projectId,



      pairId: args.pairId,



      templatePath: templateAbsPath,



      filledPath: filledAbsPath,



      userDataPath: userDataPath



    })







    if (!extractResult.success) {



      return extractResult



    }







    // 2. Diff 생성
    const diffResult = await fileReaderBridge.call('cvd:generateDiff', {



      projectId: args.projectId,



      pairId: args.pairId,



      userDataPath: userDataPath



    })







    return diffResult



  } catch (err: any) {



    console.error('[Main] cvd:processTemplatePair error:', err)



    return { success: false, error: err.message }



  }



})







// ============================================================



// Phase 5: File Search 인덱싱/쿼리 IPC 핸들러



// ============================================================







// File Search: Template Pair 인덱싱



ipcMain.handle('fileSearch:indexPair', async (_, args: {



  projectId: string



  pairId: string



  diffPath: string



  openaiApiKey?: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }



  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }



  try {



    const userDataPath = app.getPath('userData')







    // diffPath가 상대 경로인 경우 절대 경로로 변환



    const projectBasePath = path.join(userDataPath, 'projects', args.projectId)



    const diffAbsPath = path.isAbsolute(args.diffPath)



      ? args.diffPath



      : path.join(projectBasePath, args.diffPath)







    console.log('[Main] fileSearch:indexPair - projectId:', args.projectId, ', pairId:', args.pairId)







    const result = await pythonBridge.call('fileSearch:indexPair', {



      projectId: args.projectId,



      pairId: args.pairId,



      diffPath: diffAbsPath,



      userDataPath: userDataPath,



      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:indexPair error:', err)



    return { success: false, error: err.message }



  }



})







// File Search: 파일 인덱싱



ipcMain.handle('fileSearch:indexFile', async (_, args: {



  projectId: string



  fileId: string



  textContent: string



  fileName: string



  filePath?: string



  openaiApiKey?: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }

  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }







  try {



    const userDataPath = app.getPath('userData')







    console.log('[Main] fileSearch:indexFile - projectId:', args.projectId, ', fileId:', args.fileId)







    const result = await pythonBridge.call('fileSearch:indexFile', {



      projectId: args.projectId,



      fileId: args.fileId,



      textContent: args.textContent,



      fileName: args.fileName,



      filePath: args.filePath,



      userDataPath: userDataPath,



      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:indexFile error:', err)



    return { success: false, error: err.message }



  }



})







// File Search: Pair 삭제



ipcMain.handle('fileSearch:deletePair', async (_, args: {



  projectId: string



  pairId: string



  openaiApiKey?: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }

  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }







  try {



    const userDataPath = app.getPath('userData')







    console.log('[Main] fileSearch:deletePair - projectId:', args.projectId, ', pairId:', args.pairId)







    const result = await pythonBridge.call('fileSearch:deletePair', {



      projectId: args.projectId,



      pairId: args.pairId,



      userDataPath: userDataPath,



      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:deletePair error:', err)



    return { success: false, error: err.message }



  }



})







// File Search: File 삭제



ipcMain.handle('fileSearch:deleteFile', async (_, args: {



  projectId: string



  fileId: string



  chatId?: string



  openaiApiKey?: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }

  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }

  if (args.chatId) {
    try {
      // 안전장치: 채팅 파일이 아직 연결된 상태라면 RAG 삭제를 막는다.
      // (구버전 프런트/레이스 컨디션으로 인한 오삭제 방지)
      if (isChatFileStillAttached(args.chatId, args.fileId)) {
        console.warn('[Main] fileSearch:deleteFile skipped (chat file still attached):', args.chatId, args.fileId)
        return { success: true, skipped: true, reason: 'CHAT_FILE_STILL_ATTACHED' }
      }
    } catch (guardErr: any) {
      console.error('[Main] fileSearch:deleteFile guard check failed:', guardErr)
      return { success: false, error: `CHAT_FILE_GUARD_CHECK_FAILED: ${guardErr?.message || String(guardErr)}` }
    }
  }







  try {



    const userDataPath = app.getPath('userData')







    console.log('[Main] fileSearch:deleteFile - projectId:', args.projectId, ', fileId:', args.fileId, ', chatId:', args.chatId)







    const result = await pythonBridge.call('fileSearch:deleteFile', {



      projectId: args.projectId,



      fileId: args.fileId,



      chatId: args.chatId,



      userDataPath: userDataPath,



      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:deleteFile error:', err)



    return { success: false, error: err.message }



  }



})







// File Search: 채팅 파일 인덱싱



ipcMain.handle('fileSearch:indexChatFile', async (_, args: {



  projectId: string



  chatId: string



  fileId: string



  textContent: string



  fileName: string



  filePath?: string



  openaiApiKey?: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }

  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }







  try {



    const userDataPath = app.getPath('userData')







    console.log('[Main] fileSearch:indexChatFile - projectId:', args.projectId, ', chatId:', args.chatId, ', fileName:', args.fileName)







    const result = await pythonBridge.call('fileSearch:indexChatFile', {



      projectId: args.projectId,



      chatId: args.chatId,



      fileId: args.fileId,



      textContent: args.textContent,



      fileName: args.fileName,



      filePath: args.filePath,



      userDataPath: userDataPath,



      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:indexChatFile error:', err)



    return { success: false, error: err.message }



  }



})







// File Search: 채팅 스코프 삭제



ipcMain.handle('fileSearch:deleteChatScope', async (_, args: {



  projectId: string



  chatId: string



}) => {



  if (!pythonBridge || !pythonBridge.isRunning()) {



    return { success: false, error: 'Python bridge not running' }



  }

  const openAiSettings = getOpenAiSettings()
  const openAiKey = openAiSettings.apiKey?.trim()
  if (!openAiKey) {
    return { success: false, error: 'OPENAI_API_KEY_REQUIRED' }
  }







  try {



    const userDataPath = app.getPath('userData')







    console.log('[Main] fileSearch:deleteChatScope - projectId:', args.projectId, ', chatId:', args.chatId)







    const result = await pythonBridge.call('fileSearch:deleteChatScope', {



      projectId: args.projectId,



      chatId: args.chatId,



      userDataPath: userDataPath,
      openaiApiKey: openAiKey,
      fileSearchModel: openAiSettings.fileSearchModel,
      embeddingModel: openAiSettings.embeddingModel
    })







    return result



  } catch (err: any) {



    console.error('[Main] fileSearch:deleteChatScope error:', err)



    return { success: false, error: err.message }



  }



})







// v4.1.4: 활성 문서 변경 polling (불변식 15)



function startActiveDocPolling() {



  setInterval(async () => {



    if (pollingInFlight) return  // 재진입 가드



    if (!pythonBridge || !pythonBridge.isRunning()) return







    pollingInFlight = true



    try {



      const info = await bridgeMutex.runExclusive(() =>



        pythonBridge!.call('getActiveDocInfo', {})



      )







      const currentDocKey = generateDocumentKey({



        documentId: info.activeDocumentId,



        path: info.activePath



      })







      if (currentDocKey !== lastActiveDocKey) {



        lastActiveDocKey = currentDocKey



        // FE에 알림



        if (win && !win.isDestroyed()) {



          win.webContents.send('doc:activeChanged', {
            docKey: currentDocKey,
            activeDocumentId: info.activeDocumentId,
            activePath: info.activePath,
            activeName: info.activeName,
          })



        }



      }



    } catch (err) {



      // 무시 (창이 없거나 브릿지 끊김)



    } finally {



      pollingInFlight = false



    }



  }, 1000)



}







// 앱 시작 후 polling 시작



app.whenReady().then(() => {



  startActiveDocPolling()



})

/**
 * Python Bridge - Electron ↔ Python subprocess communication
 *
 * Spawns Python process and communicates via stdin/stdout using JSON-RPC.
 * Uses uv to manage Python environment.
 */

import { spawn, ChildProcess } from 'node:child_process'
import path from 'node:path'
import fs from 'node:fs'
import { EventEmitter } from 'node:events'
import { app } from 'electron'

interface PythonRequest {
  id: number
  method: string
  params?: Record<string, any>
}

interface PythonResponse {
  id: number
  result?: any
  error?: string
  trace?: string
}

interface ProgressEvent {
  type: 'progress'
  event: string
  data: Record<string, any>
}

type ResponseCallback = (response: PythonResponse) => void

export class PythonBridge extends EventEmitter {
  private process: ChildProcess | null = null
  private requestId = 0
  private pendingRequests = new Map<number, ResponseCallback>()
  private buffer = ''
  private pythonDir: string
  protected scriptName: string
  protected prodExeName: string
  private startPromise: Promise<void> | null = null
  private recoverPromise: Promise<void> | null = null
  private isStopping = false
  private logFilePath: string | null = null

  private getLogFilePath(): string | null {
    if (this.logFilePath) return this.logFilePath
    try {
      if (!app.isReady()) return null
      const logDir = path.join(app.getPath('userData'), 'logs')
      fs.mkdirSync(logDir, { recursive: true })
      this.logFilePath = path.join(logDir, 'python-bridge.log')
      return this.logFilePath
    } catch {
      return null
    }
  }

  private log(message: string): void {
    const logFile = this.getLogFilePath()
    const line = `[${new Date().toISOString()}] ${message}`
    if (logFile) {
      try {
        fs.appendFileSync(logFile, line + '\n', 'utf-8')
      } catch {}
    }
  }

  private failPendingRequests(errorMessage: string): void {
    if (this.pendingRequests.size === 0) return
    const pending = Array.from(this.pendingRequests.entries())
    this.pendingRequests.clear()
    for (const [id, callback] of pending) {
      try {
        callback({ id, error: errorMessage })
      } catch {}
    }
  }

  private shouldRecoverFromError(method: string, err: unknown): boolean {
    if (method === 'ping') return false
    const message = err instanceof Error ? err.message : String(err)
    const normalized = message.toLowerCase()
    return (
      normalized.includes('request timeout') ||
      normalized.includes('python process not started') ||
      normalized.includes('closed') ||
      normalized.includes('epipe') ||
      normalized.includes('broken pipe') ||
      normalized.includes('econnreset') ||
      normalized.includes('stdin is not writable')
    )
  }

  private async recoverProcess(reason: string): Promise<void> {
    if (this.recoverPromise) return this.recoverPromise
    this.recoverPromise = (async () => {
      this.log(`[PythonBridge] Recovering process: ${reason}`)
      const proc = this.process

      // 1. 프로세스가 살아있으면 ping으로 재활용 가능 여부 확인
      if (proc && !proc.killed && proc.exitCode === null) {
        try {
          const result = await this.callOnce('ping', {}, { timeoutMs: 3000 })
          if (result?.pong) {
            // 프로세스 정상 응답 → buffer/pending만 초기화하고 재활용 (kill 없음)
            this.log(`[PythonBridge] Process healthy, reusing (reason: ${reason})`)
            this.buffer = ''
            this.failPendingRequests(`Python bridge recovering: ${reason}`)
            return
          }
        } catch (pingErr) {
          this.log(`[PythonBridge] Ping check failed, will terminate: ${pingErr}`)
        }
      }

      // 2. 프로세스 불량 → 안정적 종료 후 재시작
      this.log(`[PythonBridge] Terminating process (reason: ${reason})`)
      this.process = null
      this.buffer = ''
      this.failPendingRequests(`Python bridge recovering: ${reason}`)

      if (proc && proc.exitCode === null) {
        // kill 후 완전 종료 대기 — COM 자원 해제 보장 (새 프로세스 ROT 스캔 충돌 방지)
        await new Promise<void>((resolve) => {
          const timer = setTimeout(() => {
            this.log(`[PythonBridge] Process exit wait timeout (3s), proceeding`)
            resolve()
          }, 3000)
          proc.once('close', () => {
            clearTimeout(timer)
            this.log(`[PythonBridge] Old process exited cleanly`)
            resolve()
          })
          if (!proc.killed) {
            try {
              proc.kill()
            } catch (killErr) {
              this.log(`[PythonBridge] Kill failed: ${killErr}`)
              clearTimeout(timer)
              resolve()
            }
          }
          // proc.killed === true: 이미 kill 신호 전달됨, close 이벤트 대기
        })
      }

      if (!this.isStopping) {
        await this.start()
      }
    })().finally(() => {
      this.recoverPromise = null
    })
    return this.recoverPromise
  }

  constructor(appRoot: string, scriptName = 'hwp_com_process.py', prodExeName = 'inserty_python') {
    super()
    this.pythonDir = path.join(appRoot, 'python')
    this.scriptName = scriptName
    this.prodExeName = prodExeName
  }

  async start(): Promise<void> {
    if (this.process) {
      console.log('[PythonBridge] Already started')
      return
    }
    if (this.startPromise) {
      return this.startPromise
    }

    const isDev = !app.isPackaged

    this.startPromise = new Promise((resolve, reject) => {
      if (isDev) {
        // 개발 모드: 64-bit Python 사용 (COM 마샬링으로 32/64-bit HWP 모두 호환)
        console.log(`[PythonBridge] Starting (dev): uv run python ${this.scriptName}`)
        this.log(`[PythonBridge] Starting development mode: ${this.scriptName}`)
        this.process = spawn('uv', ['run', 'python', this.scriptName], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: this.pythonDir,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
          shell: true,
          windowsHide: true,
        })
      } else {
        // 프로덕션 모드
        const exePath = path.join(process.resourcesPath, 'python', this.prodExeName, `${this.prodExeName}.exe`)
        const exeDir = path.dirname(exePath)

        console.log(`[PythonBridge] Starting (prod): ${exePath}`)
        this.log(`[PythonBridge] Starting (prod): ${exePath}`)
        if (!fs.existsSync(exePath)) {
          this.log(`[PythonBridge] Executable not found: ${exePath}`)
        }
        this.process = spawn(exePath, [], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: exeDir,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
          windowsHide: true,  // CMD 창 숨김 (프로덕션)
        })
      }

      let settled = false
      const finish = (err?: Error) => {
        if (settled) return
        settled = true
        const promise = this.startPromise
        this.startPromise = null
        if (err) {
          this.log(`[PythonBridge] Start failed: ${err.message}`)
          reject(err)
        } else {
          this.log('[PythonBridge] Started successfully')
          resolve()
        }
        return promise
      }

      const myProc = this.process!

      myProc.stdout?.on('data', (data: Buffer) => {
        this.handleStdout(data.toString('utf-8'))
      })

      myProc.stderr?.on('data', (data: Buffer) => {
        const msg = data.toString('utf-8').trim()
        console.log(`[Python] ${msg}`)
        if (msg) {
          this.log(`[Python] ${msg}`)
        }
      })

      myProc.on('close', (code) => {
        console.log(`[PythonBridge] Process closed with code ${code}`)
        this.log(`[PythonBridge] Process closed with code ${code}`)
        // 현재 활성 프로세스인 경우에만 상태 변경 (이전 프로세스 close 이벤트가 새 프로세스 상태를 덮어쓰지 않도록)
        const isCurrent = this.process === myProc
        if (isCurrent) {
          this.failPendingRequests(`Python process closed (code ${code})`)
          this.process = null
          this.emit('close', code)
        }
        finish(new Error(`Python process closed (code ${code})`))
        if (!this.isStopping && settled && isCurrent) {
          void this.recoverProcess(`unexpected close code=${code}`).catch((err) => {
            this.log(`[PythonBridge] Auto-recover failed: ${err}`)
          })
        }
      })

      myProc.on('error', (err) => {
        console.error('[PythonBridge] Process error:', err)
        this.log(`[PythonBridge] Process error: ${err.message}`)
        const isCurrent = this.process === myProc
        if (isCurrent) {
          this.failPendingRequests(`Python process error: ${err.message}`)
        }
        finish(err)
        if (!this.isStopping && settled && isCurrent) {
          void this.recoverProcess(`process error: ${err.message}`).catch((recoverErr) => {
            this.log(`[PythonBridge] Auto-recover failed: ${recoverErr}`)
          })
        }
      })

      const maxAttempts = 20
      const delayMs = 500  // 1000ms → 500ms: 더 빠른 재시도
      const attemptPing = async (attempt: number) => {
        if (settled) return
        if (!this.process) {
          finish(new Error('Python process not started'))
          return
        }
        try {
          this.log(`[PythonBridge] Ping attempt ${attempt}/${maxAttempts}`)
          const result = await this.call('ping', {}, { timeoutMs: 3000 })
          if (result?.pong) {
            console.log('[PythonBridge] Started successfully')
            finish()
            return
          }
          throw new Error('Ping failed')
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err)
          this.log(`[PythonBridge] Ping attempt ${attempt} failed: ${message}`)
          if (attempt >= maxAttempts) {
            finish(err instanceof Error ? err : new Error(String(err)))
            return
          }
          setTimeout(() => attemptPing(attempt + 1), delayMs)
        }
      }

      // 1000ms → 300ms: 프로세스 시작 후 더 빨리 첫 ping 시도
      setTimeout(() => attemptPing(1), 300)
    })

    return this.startPromise
  }

  async stop(): Promise<void> {
    const proc = this.process
    if (!proc) return
    this.isStopping = true
    try {
      this.send('quit', {})
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 300))
    try {
      if (!proc.killed) {
        proc.kill()
      }
    } catch (err) {
      console.error('[PythonBridge] Kill failed:', err)
    }
    this.failPendingRequests('Python process stopped')
    this.buffer = ''
    this.process = null
    this.isStopping = false
  }

  async call(method: string, params: Record<string, any> = {}, options?: { timeoutMs?: number }): Promise<any> {
    const maxAttempts = method === 'ping' ? 1 : 2
    let lastError: Error | null = null

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        if (!this.process) {
          await this.start()
        }
        return await this.callOnce(method, params, options)
      } catch (err) {
        const asError = err instanceof Error ? err : new Error(String(err))
        lastError = asError
        if (attempt >= maxAttempts || !this.shouldRecoverFromError(method, asError)) {
          break
        }
        try {
          await this.recoverProcess(`${method} failed: ${asError.message}`)
        } catch (recoverErr) {
          const recoverError = recoverErr instanceof Error ? recoverErr : new Error(String(recoverErr))
          this.log(`[PythonBridge] Recover attempt failed for ${method}: ${recoverError.message}`)
          lastError = recoverError
          break
        }
      }
    }

    throw lastError ?? new Error(`Python RPC failed: ${method}`)
  }

  private callOnce(method: string, params: Record<string, any> = {}, options?: { timeoutMs?: number }): Promise<any> {
    if (!this.process) {
      return Promise.reject(new Error('Python process not started'))
    }
    const processRef = this.process
    const stdin = processRef.stdin
    if (!stdin || stdin.destroyed || !stdin.writable) {
      return Promise.reject(new Error('Python process stdin is not writable'))
    }

    return new Promise((resolve, reject) => {
      const id = ++this.requestId
      const request: PythonRequest = { id, method, params }

      // 메서드별 타임아웃 설정 (0 = 무제한)
      let timeoutMs = 30000
      if (method === 'chat') {
        timeoutMs = 60000
      } else if (method === 'prepare_context') {
        timeoutMs = 120000
      } else if (method.startsWith('cvd:') || method.includes('cvd')) {
        timeoutMs = 0  // 사용자 권한 승인 대기 시 브리지 강제 복구 방지 (무제한)
      } else if (method === 'readHwpFile') {
        timeoutMs = 0  // HWPX 보안 팝업 대기 시 브리지 강제 복구 방지 (무제한)
      } else if (method === 'getActiveDocInfo') {
        timeoutMs = 0  // prepare_context 중 폴링 타임아웃으로 인한 오탐 복구 방지 (on('close')가 실제 종료 감지)
      } else if (method.startsWith('fileSearch:index')) {
        timeoutMs = 300000  // File Search 인덱싱: 5분 (OpenAI Vector Store 업로드 포함)
      } else if (method.startsWith('fileSearch:')) {
        timeoutMs = 60000  // File Search 쿼리/삭제: 1분
      } else if (method === 'getOpenDocuments') {
        timeoutMs = 60000  // HWP COM 조회
      }
      if (options?.timeoutMs !== undefined) {
        timeoutMs = options.timeoutMs
      }

      // 타임아웃 설정 (0이면 무제한)
      let timeout: NodeJS.Timeout | null = null
      if (timeoutMs > 0) {
        timeout = setTimeout(() => {
          this.pendingRequests.delete(id)
          reject(new Error(`Request timeout: ${method}`))
        }, timeoutMs)
      }

      this.pendingRequests.set(id, (response) => {
        if (timeout) clearTimeout(timeout)
        this.pendingRequests.delete(id)
        if (response.error) {
          const error = new Error(response.error)
          ;(error as any).trace = response.trace
          reject(error)
        } else {
          resolve(response.result)
        }
      })

      const json = JSON.stringify(request) + '\n'
      try {
        stdin.write(json, 'utf-8')
      } catch (writeErr) {
        if (timeout) clearTimeout(timeout)
        this.pendingRequests.delete(id)
        reject(writeErr instanceof Error ? writeErr : new Error(String(writeErr)))
      }
    })
  }

  private send(method: string, params: Record<string, any> = {}): void {
    if (!this.process) return
    const request: PythonRequest = { id: ++this.requestId, method, params }
    try {
      const json = JSON.stringify(request) + '\n'
      this.process.stdin?.write(json, 'utf-8')
    } catch (err) {
      console.error('[PythonBridge] Failed to send request:', err)
    }
  }

  private handleStdout(data: string): void {
    this.buffer += data
    const lines = this.buffer.split('\n')
    this.buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const parsed = JSON.parse(line)

        // Progress 이벤트 처리
        if (parsed.type === 'progress') {
          const progressEvent = parsed as ProgressEvent
          this.emit('progress', progressEvent.event, progressEvent.data)
          continue
        }

        // 일반 응답 처리
        const response = parsed as PythonResponse
        if (response.id !== undefined) {
          const callback = this.pendingRequests.get(response.id)
          if (callback) {
            callback(response)
          }
        }
      } catch (e) {
        console.error('[PythonBridge] Parse error:', e, 'Line:', line)
      }
    }
  }

  isRunning(): boolean {
    return this.process !== null
  }
}

let bridgeInstance: PythonBridge | null = null

export function getPythonBridge(appRoot: string): PythonBridge {
  if (!bridgeInstance) {
    bridgeInstance = new PythonBridge(appRoot)
  }
  return bridgeInstance
}

// FileReaderBridge - 파일 읽기/CVD 추출 전용 (메인 프로세스 blocking 방지)
let fileReaderInstance: PythonBridge | null = null

export function getFileReaderBridge(appRoot: string): PythonBridge {
  if (!fileReaderInstance) {
    fileReaderInstance = new PythonBridge(appRoot, 'file_reader_process.py', 'inserty_file_reader')
  }
  return fileReaderInstance
}

// ============================================================================
// Window Monitor Bridge - HWP 창 감지
// ============================================================================

interface WindowFoundEvent {
  type: 'window_found'
  pid: number
  hwnd: number
  title: string
  totalWindows?: number
}

interface WindowSwitchedEvent {
  type: 'window_switched'
  pid: number
  hwnd: number
  title: string
  totalWindows?: number
  reason?: string
}

interface WindowLostEvent {
  type: 'window_lost'
}

interface SelectionChangedEvent {
  type: 'selection_changed'
  data: any
}

type MonitorMessage = WindowFoundEvent | WindowSwitchedEvent | WindowLostEvent | SelectionChangedEvent

export class WindowMonitorBridge extends EventEmitter {
  private process: ChildProcess | null = null
  private buffer = ''
  private pythonDir: string
  private startPromise: Promise<void> | null = null
  private logFilePath: string | null = null

  private getLogFilePath(): string | null {
    if (this.logFilePath) return this.logFilePath
    try {
      if (!app.isReady()) return null
      const logDir = path.join(app.getPath('userData'), 'logs')
      fs.mkdirSync(logDir, { recursive: true })
      this.logFilePath = path.join(logDir, 'window-monitor.log')
      return this.logFilePath
    } catch {
      return null
    }
  }

  private log(message: string): void {
    const logFile = this.getLogFilePath()
    const line = `[${new Date().toISOString()}] ${message}`
    console.log(line)
    if (logFile) {
      try {
        fs.appendFileSync(logFile, line + '\n', 'utf-8')
      } catch {}
    }
  }

  constructor(appRoot: string) {
    super()
    this.pythonDir = path.join(appRoot, 'python')
  }

  async start(): Promise<void> {
    if (this.process) {
      this.log('[WindowMonitor] Already started (process exists)')
      return
    }
    if (this.startPromise) {
      this.log('[WindowMonitor] Already starting, waiting for existing promise')
      return this.startPromise
    }

    this.startPromise = this._doStart()
    try {
      await this.startPromise
    } finally {
      this.startPromise = null
    }
  }

  private async _doStart(): Promise<void> {
    const isDev = !app.isPackaged

    if (isDev) {
      // 개발 모드: uv로 Window Monitor 실행
      this.log(`[WindowMonitor] Starting (dev): uv run python hwp_window_monitor.py`)
      this.process = spawn('uv', ['run', 'python', 'hwp_window_monitor.py'], {
        stdio: ['pipe', 'pipe', 'pipe'],
        cwd: this.pythonDir,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        shell: true,
        windowsHide: true,  // CMD 창 숨김
      })
    } else {
      // 프로덕션 모드: 빌드된 exe 실행
      const exePath = path.join(process.resourcesPath, 'python', 'hwp_window_monitor', 'hwp_window_monitor.exe')
      const exists = fs.existsSync(exePath)
      this.log(`[WindowMonitor] Exe path: ${exePath}`)
      this.log(`[WindowMonitor] File exists: ${exists}`)
      if (!exists) {
        this.log(`[WindowMonitor] ERROR: hwp_window_monitor.exe not found at ${exePath}`)
        throw new Error(`hwp_window_monitor.exe not found at ${exePath}`)
      }
      this.log(`[WindowMonitor] Starting (prod): ${exePath}`)
      this.process = spawn(exePath, [], {
        stdio: ['pipe', 'pipe', 'pipe'],
        cwd: path.dirname(exePath),
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,  // CMD 창 숨김 (프로덕션)
      })
    }

    this.process.stdout?.on('data', (data: Buffer) => {
      this.handleStdout(data.toString('utf-8'))
    })

    this.process.stderr?.on('data', (data: Buffer) => {
      this.log(`[WindowMonitor] stderr: ${data.toString('utf-8').trim()}`)
    })

    this.process.on('close', (code) => {
      this.log(`[WindowMonitor] Process closed with code ${code}`)
      this.process = null
    })

    this.process.on('error', (err) => {
      this.log(`[WindowMonitor] Process error: ${err.message}`)
    })

    this.log('[WindowMonitor] Started successfully')
  }

  async stop(): Promise<void> {
    const proc = this.process
    if (!proc) return
    try {
      if (!proc.killed) {
        proc.kill()
      }
    } catch (err) {
      console.error('[WindowMonitor] Kill failed:', err)
    }
    this.buffer = ''
    this.process = null
  }

  sendCommand(command: string): void {
    if (!this.process || !this.process.stdin) {
      this.log('[WindowMonitor] Cannot send command: process not running')
      return
    }
    try {
      const message = JSON.stringify({ type: command }) + '\n'
      this.process.stdin.write(message, 'utf-8')
      this.log(`[WindowMonitor] Sent command: ${command}`)
    } catch (err) {
      this.log(`[WindowMonitor] Failed to send command: ${err}`)
    }
  }

  private handleStdout(data: string): void {
    this.buffer += data
    const lines = this.buffer.split('\n')
    this.buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.trim()) continue

      try {
        const message: MonitorMessage = JSON.parse(line)
        this.handleMessage(message)
      } catch (err) {
        console.error('[WindowMonitor] JSON parse error:', err, 'Line:', line)
      }
    }
  }

  private handleMessage(message: MonitorMessage): void {
    if (message.type === 'window_found' || message.type === 'window_switched') {
      const eventLabel = message.type === 'window_found' ? 'found' : 'switched'
      console.log(`[WindowMonitor] Window ${eventLabel}: PID=${message.pid}, HWND=${message.hwnd}`)
      this.emit(message.type, {
        pid: message.pid,
        hwnd: message.hwnd,
        title: message.title,
        totalWindows: message.totalWindows,
        reason: (message as WindowSwitchedEvent).reason,
      })
    } else if (message.type === 'window_lost') {
      console.log('[WindowMonitor] Window lost')
      this.emit('window_lost')
    } else if (message.type === 'selection_changed') {
      this.emit('selection_changed', (message as SelectionChangedEvent).data)
    }
  }
}

let monitorInstance: WindowMonitorBridge | null = null

export function getWindowMonitor(appRoot: string): WindowMonitorBridge {
  if (!monitorInstance) {
    monitorInstance = new WindowMonitorBridge(appRoot)
  }
  return monitorInstance
}

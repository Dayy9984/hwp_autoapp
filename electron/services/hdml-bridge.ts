/**
 * HDML Bridge - Electron ↔ HDML Child Process 통신
 *
 * HDML 추출/디프 생성을 별도 Python 프로세스로 분리합니다.
 */

import { spawn, ChildProcess } from 'node:child_process'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import { app } from 'electron'

interface HdmlRequest {
  id: number
  method: string
  params?: Record<string, any>
}

interface HdmlResponse {
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

type ResponseCallback = (response: HdmlResponse) => void

export class HdmlBridge extends EventEmitter {
  private process: ChildProcess | null = null
  private requestId = 0
  private pendingRequests = new Map<number, ResponseCallback>()
  private buffer = ''
  private pythonDir: string

  constructor(appRoot: string) {
    super()
    this.pythonDir = path.join(appRoot, 'python')
  }

  async start(): Promise<void> {
    if (this.process) {
      console.log('[HdmlBridge] Already started')
      return
    }

    const isDev = !app.isPackaged

    return new Promise((resolve, reject) => {
      if (isDev) {
        console.log(`[HdmlBridge] Starting (dev): uv run python hdml_process.py in ${this.pythonDir}`)
        this.process = spawn('uv', ['run', 'python', 'hdml_process.py'], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: this.pythonDir,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
          shell: true,
        })
      } else {
        // 프로덕션 모드: 64-bit Python 실행 (COM 마샬링으로 32-bit HWP 호환)
        const exePath = path.join(process.resourcesPath, 'python_module', 'inserty_module.exe')
        const exeDir = path.dirname(exePath)
        console.log(`[HdmlBridge] Starting (prod): ${exePath}`)
        this.process = spawn(exePath, [], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: exeDir,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        })
      }

      this.process.stdout?.on('data', (data: Buffer) => {
        this.handleStdout(data.toString('utf-8'))
      })

      this.process.stderr?.on('data', (data: Buffer) => {
        console.log(`[HDML] ${data.toString('utf-8').trim()}`)
      })

      this.process.on('close', (code) => {
        console.log(`[HdmlBridge] Process closed with code ${code}`)
        this.process = null
        this.emit('close', code)
      })

      this.process.on('error', (err) => {
        console.error('[HdmlBridge] Process error:', err)
        reject(err)
      })

      setTimeout(async () => {
        try {
          const result = await this.call('ping', {})
          if (result.pong) {
            console.log('[HdmlBridge] Started successfully')
            resolve()
          } else {
            reject(new Error('Ping failed'))
          }
        } catch (err) {
          reject(err)
        }
      }, 3000)
    })
  }

  async stop(): Promise<void> {
    const proc = this.process
    if (!proc) return
    try {
      this.send('quit', {})
    } catch { }
    await new Promise((resolve) => setTimeout(resolve, 300))
    try {
      if (!proc.killed) {
        proc.kill()
      }
    } catch (err) {
      console.error('[HdmlBridge] Kill failed:', err)
    }
    this.pendingRequests.clear()
    this.buffer = ''
    this.process = null
  }

  async call(method: string, params: Record<string, any> = {}): Promise<any> {
    if (!this.process) {
      throw new Error('HDML process not started')
    }

    return new Promise((resolve, reject) => {
      const id = ++this.requestId
      const request: HdmlRequest = { id, method, params }

      let timeoutMs = 30000
      if (method.startsWith('hdml:') || method.includes('hdml')) {
        timeoutMs = 120000
      }

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
            ; (error as any).trace = response.trace
          reject(error)
        } else {
          resolve(response.result)
        }
      })

      const json = JSON.stringify(request) + '\n'
      this.process!.stdin?.write(json, 'utf-8')
    })
  }

  private send(method: string, params: Record<string, any> = {}): void {
    if (!this.process) return
    const request: HdmlRequest = { id: ++this.requestId, method, params }
    try {
      const json = JSON.stringify(request) + '\n'
      this.process.stdin?.write(json, 'utf-8')
    } catch (err) {
      console.error('[HdmlBridge] Failed to send request:', err)
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

        if (parsed.type === 'progress') {
          const progressEvent = parsed as ProgressEvent
          this.emit('progress', progressEvent.event, progressEvent.data)
          continue
        }

        const response = parsed as HdmlResponse
        if (response.id !== undefined) {
          const callback = this.pendingRequests.get(response.id)
          if (callback) {
            callback(response)
          }
        }
      } catch (e) {
        console.error('[HdmlBridge] Parse error:', e, 'Line:', line)
      }
    }
  }

  isRunning(): boolean {
    return this.process !== null
  }
}

let bridgeInstance: HdmlBridge | null = null

export function getHdmlBridge(appRoot: string): HdmlBridge {
  if (!bridgeInstance) {
    bridgeInstance = new HdmlBridge(appRoot)
  }
  return bridgeInstance
}

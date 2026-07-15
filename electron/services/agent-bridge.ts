/**
 * Agent Bridge - Electron ↔ Agent Child Process 통신
 *
 * 레거시 Agent Child Process 패턴을 따릅니다.
 * Python Agent Process를 spawn하여 LLM API 호출을 격리합니다.
 *
 * **격리 효과:**
 * - LLM 크래시 시 Main Process 및 HWP COM Process 영향 없음
 * - 빠른 복구 (< 1초 Agent 재시작)
 * - 메모리 격리 (LLM 메모리 누수가 HWP에 영향 없음)
 */

import { spawn, ChildProcess } from 'node:child_process'
import path from 'node:path'
import fs from 'node:fs'
import { EventEmitter } from 'node:events'
import { app } from 'electron'

interface AgentRequest {
  id: number
  method: string
  params?: Record<string, any>
}

interface AgentResponse {
  id: number
  result?: any
  error?: string
  trace?: string
}

interface DeltaEvent {
  type: 'delta'
  request_id: number
  action: string  // "thinking" | "message" | "edit_document" | "append_table_row"
  id?: number | string
  content?: string
  message?: string
  metadata?: Record<string, any>
  rows?: any[]
}

// v5.3: Progress 이벤트 (RAG 검색 등)
interface ProgressEvent {
  type: 'progress'
  event: string
  data: Record<string, any>
}

// v5.3: RAG 컨텍스트 인터페이스
interface RagContext {
  projectId?: string
  chatId?: string
  userDataPath?: string
  openaiApiKey?: string
  fileSearchModel?: string
  embeddingModel?: string
}

type ResponseCallback = (response: AgentResponse) => void

export class AgentBridge extends EventEmitter {
  private process: ChildProcess | null = null
  private requestId = 0
  private pendingRequests = new Map<number, ResponseCallback>()
  private buffer = ''
  private pythonDir: string
  private startPromise: Promise<void> | null = null
  private logFilePath: string | null = null
  // v6.5: Inactivity timeout (마지막 활동 기준)
  private activeStreamTimeouts = new Map<number, NodeJS.Timeout>()
  private activeStreamRejects = new Map<number, (reason: Error) => void>()
  private readonly INACTIVITY_TIMEOUT_MS = 180000  // 180초 동안 아무 활동 없으면 타임아웃 (RAG 검색 고려)

  private getLogFilePath(): string | null {
    if (this.logFilePath) return this.logFilePath
    try {
      if (!app.isReady()) return null
      const logDir = path.join(app.getPath('userData'), 'logs')
      fs.mkdirSync(logDir, { recursive: true })
      this.logFilePath = path.join(logDir, 'agent-bridge.log')
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
      this.log('[AgentBridge] Already started (process exists)')
      return
    }
    if (this.startPromise) {
      this.log('[AgentBridge] Already starting, waiting for existing promise')
      return this.startPromise
    }

    this.startPromise = this._doStart()
    try {
      await this.startPromise
    } finally {
      this.startPromise = null
    }
  }

  private _doStart(): Promise<void> {
    return new Promise((resolve, reject) => {
      const isDev = !app.isPackaged

      if (isDev) {
        // 개발 모드: uv로 Agent Process 실행
        this.log(`[AgentBridge] Starting (dev): uv run python agent_process.py in ${this.pythonDir}`)
        this.process = spawn('uv', ['run', 'python', 'agent_process.py'], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: this.pythonDir,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
          shell: true,
        })
      } else {
        // 프로덕션 모드: 빌드된 exe 실행
        const exePath = path.join(process.resourcesPath, 'python', 'inserty_agent', 'inserty_agent.exe')
        const exeDir = path.dirname(exePath)
        const exists = fs.existsSync(exePath)
        this.log(`[AgentBridge] Exe path: ${exePath}`)
        this.log(`[AgentBridge] File exists: ${exists}`)
        if (!exists) {
          this.log(`[AgentBridge] ERROR: inserty_agent.exe not found at ${exePath}`)
          reject(new Error(`inserty_agent.exe not found at ${exePath}`))
          return
        }
        this.log(`[AgentBridge] Starting (prod): ${exePath}`)
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
        this.log(`[Agent] stderr: ${data.toString('utf-8').trim()}`)
      })

      this.process.on('close', (code) => {
        this.log(`[AgentBridge] Process closed with code ${code}`)
        this.process = null
        this.emit('close', code)
      })

      this.process.on('error', (err) => {
        this.log(`[AgentBridge] Process error: ${err.message}`)
        reject(err)
      })

      // Ping으로 연결 확인 (최대 15회 재시도, 더 빠른 간격)
      const maxAttempts = 15
      const delayMs = 500  // 2000ms → 500ms: 더 빠른 재시도
      const attemptPing = async (attempt: number) => {
        try {
          this.log(`[AgentBridge] Ping attempt ${attempt}/${maxAttempts}`)
          const result = await this.call('ping', {}, { timeoutMs: 3000 })
          if (result?.pong) {
            this.log('[AgentBridge] Started successfully')
            resolve()
            return
          }
          throw new Error('Ping failed - no pong')
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err)
          this.log(`[AgentBridge] Ping attempt ${attempt} failed: ${message}`)
          if (attempt >= maxAttempts) {
            reject(err instanceof Error ? err : new Error(String(err)))
            return
          }
          setTimeout(() => attemptPing(attempt + 1), delayMs)
        }
      }
      // 2000ms → 500ms: 프로세스 시작 후 더 빨리 첫 ping 시도
      setTimeout(() => attemptPing(1), 500)
    })
  }

  async stop(): Promise<void> {
    const proc = this.process
    if (!proc) return
    try {
      this.send('quit', {})
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 300))
    try {
      if (!proc.killed) {
        proc.kill()
      }
    } catch (err) {
      console.error('[AgentBridge] Kill failed:', err)
    }
    this.pendingRequests.clear()
    this.buffer = ''
    this.process = null
  }

  async call(method: string, params: Record<string, any> = {}, options?: { timeoutMs?: number }): Promise<any> {
    if (!this.process) {
      throw new Error('Agent process not started')
    }

    return new Promise((resolve, reject) => {
      const id = ++this.requestId
      const request: AgentRequest = { id, method, params }

      // v6.5: stream_llm은 inactivity timeout 사용, 나머지는 기존 방식
      let timeout: NodeJS.Timeout

      if (method === 'stream_llm') {
        // Inactivity timeout: Delta/Progress 이벤트 올 때마다 리셋됨
        timeout = this.createInactivityTimeout(id, reject)
        this.activeStreamTimeouts.set(id, timeout)
        this.activeStreamRejects.set(id, reject)  // v6.5: reject callback 저장
      } else {
        // 일반 메서드: 고정 시간 타임아웃
        const timeoutMs = options?.timeoutMs ?? 30000
        timeout = setTimeout(() => {
          this.pendingRequests.delete(id)
          reject(new Error(`Request timeout: ${method}`))
        }, timeoutMs)
      }

      this.pendingRequests.set(id, (response) => {
        clearTimeout(timeout)
        this.activeStreamTimeouts.delete(id)
        this.activeStreamRejects.delete(id)  // v6.5: reject callback 정리
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
      this.process!.stdin?.write(json, 'utf-8')
    })
  }

  /**
   * v6.5: Inactivity timeout 생성
   * 마지막 활동(Delta/Progress) 이후 N초 동안 아무것도 없으면 타임아웃
   */
  private createInactivityTimeout(requestId: number, reject: (reason: Error) => void): NodeJS.Timeout {
    return setTimeout(() => {
      console.log(`[AgentBridge] Inactivity timeout for request ${requestId}`)
      this.activeStreamTimeouts.delete(requestId)
      this.pendingRequests.delete(requestId)
      reject(new Error(`Request timeout: stream_llm (no activity for ${this.INACTIVITY_TIMEOUT_MS / 1000}s)`))
    }, this.INACTIVITY_TIMEOUT_MS)
  }

  /**
   * v6.5: Inactivity timeout 리셋
   * Delta나 Progress 이벤트가 올 때마다 호출됨
   */
  private resetInactivityTimeout(requestId: number, reject: (reason: Error) => void): void {
    const existingTimeout = this.activeStreamTimeouts.get(requestId)
    if (existingTimeout) {
      clearTimeout(existingTimeout)
      const newTimeout = this.createInactivityTimeout(requestId, reject)
      this.activeStreamTimeouts.set(requestId, newTimeout)
    }
  }

  private send(method: string, params: Record<string, any> = {}): void {
    if (!this.process) return
    const request: AgentRequest = { id: ++this.requestId, method, params }
    try {
      const json = JSON.stringify(request) + '\n'
      this.process.stdin?.write(json, 'utf-8')
    } catch (err) {
      console.error('[AgentBridge] Failed to send request:', err)
    }
  }

  /**
   * LLM 스트리밍 호출 (Delta 이벤트 전송)
   *
   * 스트리밍 패턴: Agent Process가 LLM을 호출하고
   * Delta 이벤트를 실시간으로 Main Process에 전송합니다.
   *
   * v5.3: RAG Tool Use 지원 - ragContext가 제공되면 LLM이 필요 시 RAG 검색 도구 호출
   *
   * @param html - CVD 또는 DocumentView HTML
   * @param prompt - 사용자 요청
   * @param options - useDelta/useHtml/compactMode/ragContext 옵션 또는 boolean (이전 시그니처 호환)
   * @returns Promise<{success: boolean, commands: number, messages: string[]}>
   */
  async streamLLM(
    html: string,
    prompt: string,
    options: boolean | {
      useDelta?: boolean
      useHtml?: boolean
      compactMode?: boolean
      ragContext?: RagContext  // v5.3: RAG Tool Use
      openaiApiKey?: string
      promptCustomRules?: string
      promptCustomEnabled?: boolean
      promptFullOverride?: string
      model?: string
      codexMode?: boolean
      codexAccountId?: string
    } = true
  ): Promise<any> {
    const useDelta = typeof options === 'boolean' ? options : options.useDelta ?? true
    const useHtml = typeof options === 'boolean' ? false : options.useHtml ?? false
    const compactMode = typeof options === 'boolean' ? true : options.compactMode ?? true
    const ragContext = typeof options === 'boolean' ? undefined : options.ragContext
    const openaiApiKey = typeof options === 'boolean' ? undefined : options.openaiApiKey
    const promptCustomRules = typeof options === 'boolean' ? undefined : options.promptCustomRules
    const promptCustomEnabled = typeof options === 'boolean' ? false : options.promptCustomEnabled ?? false
    const promptFullOverride = typeof options === 'boolean' ? undefined : options.promptFullOverride
    const codexMode = typeof options === 'boolean' ? false : options.codexMode ?? false
    const codexAccountId = typeof options === 'boolean' ? '' : options.codexAccountId ?? ''

    // v5.3: RAG 컨텍스트를 Python snake_case로 변환
    const ragContextParams = ragContext ? {
      project_id: ragContext.projectId,
      chat_id: ragContext.chatId,
      user_data_path: ragContext.userDataPath,
      openai_api_key: ragContext.openaiApiKey,
      file_search_model: ragContext.fileSearchModel,
      embedding_model: ragContext.embeddingModel
    } : undefined

    return this.call('stream_llm', {
      html,
      prompt,
      use_delta: useDelta,
      use_html: useHtml,
      compact_mode: compactMode,
      openai_api_key: openaiApiKey,
      rag_context: ragContextParams,  // v5.3: RAG Tool Use context
      model: typeof options === 'boolean' ? undefined : options.model,
      prompt_custom_rules: promptCustomRules,
      prompt_custom_enabled: promptCustomEnabled,
      prompt_full_override: promptFullOverride,
      codex_mode: codexMode || undefined,
      codex_account_id: codexAccountId || undefined,
    })
  }

  /**
   * 현재 스트리밍 취소
   */
  async cancelStream(): Promise<void> {
    try {
      await this.call('cancel', {})
    } catch (err) {
      console.error('[AgentBridge] Cancel failed:', err)
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

        // Delta 이벤트 처리
        if (parsed.type === 'delta') {
          const deltaEvent = parsed as DeltaEvent
          // v6.5: Delta 이벤트 수신 시 inactivity timeout 리셋
          if (deltaEvent.request_id !== undefined) {
            const reject = this.activeStreamRejects.get(deltaEvent.request_id)
            if (reject) {
              this.resetInactivityTimeout(deltaEvent.request_id, reject)
            }
          }
          this.emit('delta', deltaEvent)
          continue
        }

        // v5.3: Progress 이벤트 처리 (RAG 검색 등)
        if (parsed.type === 'progress') {
          const progressEvent = parsed as ProgressEvent
          // v6.5: Progress 이벤트 수신 시 모든 활성 stream의 inactivity timeout 리셋
          for (const [requestId, reject] of this.activeStreamRejects.entries()) {
            this.resetInactivityTimeout(requestId, reject)
          }
          this.emit('progress', progressEvent.event, progressEvent.data)
          continue
        }

        // 일반 응답 처리
        const response = parsed as AgentResponse
        if (response.id !== undefined) {
          const callback = this.pendingRequests.get(response.id)
          if (callback) {
            callback(response)
          }
        }
      } catch (e) {
        console.error('[AgentBridge] Parse error:', e, 'Line:', line)
      }
    }
  }

  isRunning(): boolean {
    return this.process !== null
  }
}

let bridgeInstance: AgentBridge | null = null

export function getAgentBridge(appRoot: string): AgentBridge {
  if (!bridgeInstance) {
    bridgeInstance = new AgentBridge(appRoot)
  }
  return bridgeInstance
}

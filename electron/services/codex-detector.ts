/**
 * Codex CLI 감지 및 인증 정보 추출
 *
 * ~/.codex/auth.json에서 API키 또는 OAuth 토큰을 읽어온다.
 * InsertyAI의 Thick MCP 파이프라인이 이 인증정보로 OpenAI API를 내부 호출한다.
 */

import * as path from 'path'
import * as fs from 'fs'
import * as os from 'os'
import { execFile } from 'child_process'

export interface CodexAuthInfo {
  /** API 키 (sk-...) 또는 OAuth access_token */
  apiKey: string
  /** 인증 방식 */
  authType: 'api_key' | 'oauth'
}

/** Codex OAuth 전용 인증 정보 (chatgpt.com/backend-api/codex/responses 호출용) */
export interface CodexOAuthInfo {
  accessToken: string
  accountId: string
}

export type CodexStatus =
  | { installed: false; reason: string }
  | { installed: true; authenticated: false; reason: string }
  | { installed: true; authenticated: true; auth: CodexAuthInfo }

/**
 * Codex CLI 홈 디렉토리 (~/.codex)
 */
function getCodexHome(): string {
  return process.env.CODEX_HOME || path.join(os.homedir(), '.codex')
}

/**
 * auth.json 경로
 */
function getAuthJsonPath(): string {
  return path.join(getCodexHome(), 'auth.json')
}

/**
 * auth.json에서 인증 정보 읽기
 */
function readAuthJson(): CodexAuthInfo | null {
  const authPath = getAuthJsonPath()
  if (!fs.existsSync(authPath)) return null

  try {
    const raw = fs.readFileSync(authPath, 'utf-8')
    const data = JSON.parse(raw)

    // Case 1: API 키 인증
    if (data.OPENAI_API_KEY && typeof data.OPENAI_API_KEY === 'string') {
      const key = data.OPENAI_API_KEY.trim()
      if (key) {
        return { apiKey: key, authType: 'api_key' }
      }
    }

    // Case 2: OAuth 토큰 인증 (ChatGPT 로그인)
    if (data.tokens && typeof data.tokens === 'object') {
      const accessToken = data.tokens.access_token
      if (accessToken && typeof accessToken === 'string' && accessToken.trim()) {
        return { apiKey: accessToken.trim(), authType: 'oauth' }
      }
    }

    return null
  } catch {
    return null
  }
}

/**
 * Codex OAuth 전용 인증 정보 반환 (token + accountId)
 * chatgpt.com/backend-api/codex/responses 호출에 필요
 */
export function getCodexAuth(): CodexOAuthInfo | null {
  const authPath = getAuthJsonPath()
  if (!fs.existsSync(authPath)) return null

  try {
    const raw = fs.readFileSync(authPath, 'utf-8')
    const data = JSON.parse(raw)

    if (data.tokens && typeof data.tokens === 'object') {
      const accessToken = data.tokens.access_token
      const accountId = data.tokens.account_id ?? ''
      if (accessToken && typeof accessToken === 'string' && accessToken.trim()) {
        return { accessToken: accessToken.trim(), accountId: accountId.trim() }
      }
    }

    return null
  } catch {
    return null
  }
}

/**
 * Codex CLI 설치 여부 확인 (codex --version)
 */
function checkCodexInstalled(): Promise<boolean> {
  return new Promise((resolve) => {
    execFile('codex', ['--version'], { timeout: 5000, shell: true }, (err) => {
      resolve(!err)
    })
  })
}

/** Node.js 설치 상태 */
export interface NodeStatus {
  installed: boolean
  version?: string
  /** npm 명령 사용 가능 한지 */
  hasNpm: boolean
}

/**
 * Node.js + npm 설치 여부 확인.
 *
 * Codex CLI 설치 (`npm install -g @openai/codex`) 의 사전 조건.
 * Node.js 없으면 npm 도 없음 → 사용자 에게 Node.js 다운로드 안내 필요.
 */
export function checkNodeInstalled(): Promise<NodeStatus> {
  return new Promise((resolve) => {
    execFile('node', ['--version'], { timeout: 5000, shell: true }, (nodeErr, nodeOut) => {
      if (nodeErr) {
        resolve({ installed: false, hasNpm: false })
        return
      }
      const version = nodeOut.trim()
      // npm 도 확인 (드물게 node 만 있고 npm 없는 케이스)
      execFile('npm', ['--version'], { timeout: 5000, shell: true }, (npmErr) => {
        resolve({ installed: true, version, hasNpm: !npmErr })
      })
    })
  })
}

/**
 * Codex CLI 전체 상태 감지
 *
 * 1. codex --version으로 설치 확인
 * 2. ~/.codex/auth.json에서 인증 정보 추출
 *
 * E2E override: `INSERTY_CODEX_E2E_OVERRIDE` env 변수 가 설정된 경우
 *   "needs_install" → installed=false 강제
 *   "needs_login"   → installed=true, authenticated=false 강제
 *   "all_done"      → installed=true, authenticated=true (dummy auth)
 *   일반 사용자 환경 에서는 이 env 안 설정 → 정상 detect.
 */
export async function detectCodex(): Promise<CodexStatus> {
  // ─── E2E override (사용자 환경 무관 검증용) ────────────────────
  const override = process.env.INSERTY_CODEX_E2E_OVERRIDE
  if (override === 'needs_install') {
    return { installed: false, reason: 'E2E: 강제 미설치' }
  }
  if (override === 'needs_login') {
    return { installed: true, authenticated: false, reason: 'E2E: 강제 미로그인' }
  }
  if (override === 'all_done') {
    return {
      installed: true,
      authenticated: true,
      auth: { apiKey: 'e2e-dummy-token', authType: 'oauth' },
    }
  }
  // ────────────────────────────────────────────────────────────────

  const installed = await checkCodexInstalled()
  if (!installed) {
    return {
      installed: false,
      reason: 'Codex CLI가 설치되어 있지 않습니다. npm install -g @openai/codex 로 설치하세요.',
    }
  }

  const auth = readAuthJson()
  if (!auth) {
    return {
      installed: true,
      authenticated: false,
      reason: 'Codex CLI 로그인이 필요합니다. 터미널에서 codex login 을 실행하세요.',
    }
  }

  return {
    installed: true,
    authenticated: true,
    auth,
  }
}

/**
 * Codex 인증 정보 빠른 조회 (캐시 없이 매번 읽기)
 * chat:send에서 사용 — 설치 확인 없이 auth.json만 읽음
 */
export function getCodexApiKey(): string | null {
  const auth = readAuthJson()
  return auth?.apiKey ?? null
}

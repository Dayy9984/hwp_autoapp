/**
 * codex-detector 의 Node.js / npm detect 단위 테스트.
 *
 * child_process.execFile 를 mock 해서 실제 시스템 의 node/npm 설치 여부 와 무관 하게
 * 우리 코드 의 분기 처리 정확성 만 검증.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// child_process mock — vitest 의 hoisted vi.mock 패턴
const execFileMock = vi.fn()
vi.mock('child_process', () => ({
  execFile: (...args: any[]) => execFileMock(...args),
}))

// node:* 별칭 도 같은 mock — codex-detector 가 어느 쪽 import 해도 cover
vi.mock('node:child_process', () => ({
  execFile: (...args: any[]) => execFileMock(...args),
}))

import { checkNodeInstalled } from '../electron/services/codex-detector'

function setupExecFile(map: Record<string, { err: Error | null; stdout: string }>) {
  execFileMock.mockImplementation((cmd: string, _args: any, _opts: any, cb: any) => {
    const result = map[cmd] ?? { err: new Error('not_found'), stdout: '' }
    setImmediate(() => cb(result.err, result.stdout, ''))
  })
}

describe('checkNodeInstalled', () => {
  beforeEach(() => {
    execFileMock.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('Node.js + npm 둘 다 설치 시 installed=true, hasNpm=true', async () => {
    setupExecFile({
      node: { err: null, stdout: 'v20.10.0\n' },
      npm: { err: null, stdout: '10.2.3\n' },
    })

    const result = await checkNodeInstalled()
    expect(result.installed).toBe(true)
    expect(result.hasNpm).toBe(true)
    expect(result.version).toBe('v20.10.0')
  })

  it('Node.js 없으면 installed=false, hasNpm=false (npm check 도 안 함)', async () => {
    setupExecFile({
      node: { err: new Error('not found'), stdout: '' },
    })

    const result = await checkNodeInstalled()
    expect(result.installed).toBe(false)
    expect(result.hasNpm).toBe(false)
    expect(result.version).toBeUndefined()
  })

  it('Node.js 있지만 npm 없을 때 installed=true, hasNpm=false', async () => {
    setupExecFile({
      node: { err: null, stdout: 'v20.10.0' },
      npm: { err: new Error('not found'), stdout: '' },
    })

    const result = await checkNodeInstalled()
    expect(result.installed).toBe(true)
    expect(result.hasNpm).toBe(false)
    expect(result.version).toBe('v20.10.0')
  })

  it('version 의 \\n / 공백 strip 처리 확인', async () => {
    setupExecFile({
      node: { err: null, stdout: '   v18.19.0\r\n  ' },
      npm: { err: null, stdout: '10.0.0' },
    })

    const result = await checkNodeInstalled()
    expect(result.version).toBe('v18.19.0')
  })
})

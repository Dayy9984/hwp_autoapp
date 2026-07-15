/**
 * Codex 자동 설정 모달 — 컴퓨터 잘 모르는 사용자 도 사용 가능 한 wizard.
 *
 * 흐름 (자동 polling — 사용자 가 별도 조작 안 해도 다음 단계 자동 진행):
 *   1) Node.js 확인 — 미설치 시 "Node.js 다운로드" 버튼 (외부 브라우저)
 *   2) Codex CLI 설치 — 자동 npm install + 진행 로그 표시
 *   3) ChatGPT 로그인 — 외부 터미널 자동 실행 + 로그인 완료 대기 (polling)
 *   4) 완료 — 사용자 가 채팅 사용 가능
 *
 * 각 step 의 상태:
 *   - pending   (대기)
 *   - active    (현재 진행 중)
 *   - completed (완료)
 *   - error     (실패)
 */

import { useEffect, useState, useRef, useCallback } from 'react'
import { CheckCircle, Circle, Loader2, ExternalLink, AlertTriangle, X, Download, Terminal } from 'lucide-react'

type StepState = 'pending' | 'active' | 'completed' | 'error'

interface StepStatus {
  node: StepState
  cli: StepState
  login: StepState
}

interface Props {
  isOpen: boolean
  onClose: () => void
  /** 완료 시 (사용자 가 "사용 시작" 클릭) 콜백 */
  onComplete?: () => void
}

const CHATGPT_PLANS_URL = 'https://chatgpt.com/pricing'
const NODE_DOWNLOAD_URL = 'https://nodejs.org/en/download'

export function CodexSetupModal({ isOpen, onClose, onComplete }: Props) {
  const [step, setStep] = useState<StepStatus>({ node: 'pending', cli: 'pending', login: 'pending' })

  // 진행 stream (npm install 로그)
  const [installLog, setInstallLog] = useState<string>('')
  const [installError, setInstallError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  // 현재 무엇이 진행 중인지 표시 라벨
  const [busyLabel, setBusyLabel] = useState<string>('')

  // ── 상태 polling ───────────────────────────────────────────────
  const pollAll = useCallback(async () => {
    const api = window.electronAPI
    if (!api?.codex) return

    // Codex CLI status (가장 자주 확인 — 모달 자체 가 이 변화 으로 종료)
    const codexStatus = await api.codex.status()
    if (codexStatus.installed && codexStatus.authenticated) {
      // 모든 단계 완료
      setStep({ node: 'completed', cli: 'completed', login: 'completed' })
      return 'all_done'
    }

    // Node.js
    const nodeStatus = await api.codex.checkNode()
    const nodeOk = nodeStatus.installed && nodeStatus.hasNpm

    // CLI installed?
    const cliInstalled = codexStatus.installed

    setStep((prev) => ({
      node: nodeOk ? 'completed' : prev.node === 'error' ? 'error' : 'active',
      cli: !nodeOk
        ? 'pending'
        : cliInstalled
          ? 'completed'
          : prev.cli === 'active' || prev.cli === 'error'
            ? prev.cli
            : 'pending',
      login: !cliInstalled
        ? 'pending'
        : codexStatus.authenticated
          ? 'completed'
          : prev.login === 'active' || prev.login === 'error'
            ? prev.login
            : 'pending',
    }))

    return 'continue'
  }, [])

  useEffect(() => {
    if (!isOpen) return

    let alive = true
    let intervalId: NodeJS.Timeout | null = null

    const tick = async () => {
      if (!alive) return
      const result = await pollAll().catch(() => 'continue')
      if (result === 'all_done' && intervalId) {
        // 완료 detection 후 5 초 폴링 유지 (UI 표시 위해)
      }
    }

    void tick()
    intervalId = setInterval(tick, 3_000)
    const onFocus = () => tick()
    window.addEventListener('focus', onFocus)

    return () => {
      alive = false
      if (intervalId) clearInterval(intervalId)
      window.removeEventListener('focus', onFocus)
    }
  }, [isOpen, pollAll])

  // ── 액션 ───────────────────────────────────────────────────────

  const openNodeDownload = useCallback(async () => {
    await window.electronAPI.codex.openNodejsDownload()
  }, [])

  const installCli = useCallback(async () => {
    setStep((s) => ({ ...s, cli: 'active' }))
    setInstallLog('')
    setInstallError(null)
    setBusyLabel('Codex 설치 중…')

    const unsubscribe = window.electronAPI.codex.onInstallProgress((data) => {
      if (data.type === 'stdout' || data.type === 'stderr') {
        setInstallLog((prev) => {
          const next = prev + (data.text ?? '')
          // tail 만 보존 — 길어지면 메모리 부담
          return next.length > 8000 ? next.slice(-6000) : next
        })
        // 다음 frame 에 scroll 끝으로
        requestAnimationFrame(() => {
          if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
        })
      } else if (data.type === 'done') {
        if (data.success) {
          setStep((s) => ({ ...s, cli: 'completed' }))
          setBusyLabel('')
        } else {
          setStep((s) => ({ ...s, cli: 'error' }))
          setInstallError(`npm install 실패 (code ${data.code ?? '?'}). 관리자 권한 으로 다시 시도 하거나 수동 설치 해주세요.`)
          setBusyLabel('')
        }
      } else if (data.type === 'error') {
        setStep((s) => ({ ...s, cli: 'error' }))
        setInstallError(data.error ?? '알 수 없는 오류')
        setBusyLabel('')
      }
    })

    try {
      const result = await window.electronAPI.codex.installCli()
      if (!result.success && result.error === 'NODE_NOT_INSTALLED') {
        setStep((s) => ({ ...s, cli: 'pending', node: 'error' }))
        setInstallError('Node.js 가 먼저 설치 되어야 합니다.')
        setBusyLabel('')
      }
    } finally {
      // unsubscribe 는 done event 받은 후 해도 됨. 안전 cleanup.
      setTimeout(unsubscribe, 1000)
    }
  }, [])

  const login = useCallback(async () => {
    setStep((s) => ({ ...s, login: 'active' }))
    setBusyLabel('Codex 로그인 중… (새 창 에서 ChatGPT 로그인 하세요)')
    try {
      await window.electronAPI.codex.login()
      // 외부 터미널 창에서 OAuth 진행 — polling 으로 detection.
    } catch {
      setStep((s) => ({ ...s, login: 'error' }))
      setBusyLabel('')
    }
  }, [])

  const handleComplete = useCallback(() => {
    onComplete?.()
    onClose()
  }, [onComplete, onClose])

  // ── 자동 진행 (사용자 가 별도 조작 안 해도 다음 단계 자동) ──
  // Node.js OK + CLI 미설치 + CLI step 이 pending → 자동 설치 시작
  useEffect(() => {
    if (!isOpen) return
    if (step.node === 'completed' && step.cli === 'pending') {
      // 자동 설치 시작 (사용자 가 step 1 통과 하면 자동 진행)
      void installCli()
    }
  }, [isOpen, step.node, step.cli, installCli])

  if (!isOpen) return null

  const allDone = step.node === 'completed' && step.cli === 'completed' && step.login === 'completed'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="codex-setup-title"
      data-testid="codex-setup-modal"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onClick={(e) => {
        // backdrop 클릭 시 닫힘 — 단 완료 전엔 닫지 못 함 (사용자 가 의도 적 X 버튼 클릭)
        if (e.target === e.currentTarget && allDone) onClose()
      }}
    >
      <div className="bg-bg rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 id="codex-setup-title" className="text-base font-semibold text-text">
            Codex 자동 설정
          </h2>
          <button
            onClick={onClose}
            aria-label="닫기"
            className="text-text-tertiary hover:text-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-5">
          {/* 안내 */}
          <p className="text-sm text-text-secondary leading-relaxed">
            Inserty AI 는 <b>ChatGPT 구독</b> (Plus / Team / Enterprise) 으로 사용합니다.<br />
            아래 단계 를 자동 으로 진행 합니다.
            <a
              href={CHATGPT_PLANS_URL}
              onClick={(e) => { e.preventDefault(); window.electronAPI.codex.openNodejsDownload() }}
              className="ml-1 text-blue-600 hover:underline inline-flex items-center gap-0.5"
            >
              <ExternalLink size={11} /> 요금제
            </a>
          </p>

          {/* Step 1 — Node.js */}
          <StepRow
            num={1}
            state={step.node}
            title="Node.js 확인"
            description="Codex 설치 에 필요 한 기본 도구 입니다."
            action={
              step.node === 'active' ? (
                <button
                  data-testid="codex-setup-node-download"
                  onClick={openNodeDownload}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 text-white rounded-md text-xs font-medium hover:bg-blue-700"
                >
                  <Download size={12} />
                  Node.js 설치 페이지 열기
                </button>
              ) : null
            }
            hint={step.node === 'active' ? '설치 후 Inserty 를 다시 실행 하세요. (LTS 버전 권장)' : null}
          />

          {/* Step 2 — Codex CLI */}
          <StepRow
            num={2}
            state={step.cli}
            title="Codex CLI 설치"
            description="ChatGPT 와 통신 하는 명령 줄 도구 입니다. 자동 으로 설치 됩니다."
            action={
              step.cli === 'error' ? (
                <button
                  data-testid="codex-setup-cli-retry"
                  onClick={installCli}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-text text-bg rounded-md text-xs font-medium hover:bg-text/90"
                >
                  다시 시도
                </button>
              ) : null
            }
            hint={installError}
          />
          {step.cli === 'active' && installLog && (
            <div
              ref={logRef}
              data-testid="codex-setup-install-log"
              className="ml-9 -mt-2 max-h-32 overflow-y-auto bg-bg-secondary rounded-md p-2 text-[10px] font-mono text-text-tertiary whitespace-pre-wrap"
            >
              {installLog}
            </div>
          )}

          {/* Step 3 — ChatGPT 로그인 */}
          <StepRow
            num={3}
            state={step.login}
            title="ChatGPT 로그인"
            description="새 창 이 열리면 ChatGPT 계정 으로 로그인 하세요."
            action={
              (step.cli === 'completed' && step.login !== 'completed') ? (
                <button
                  data-testid="codex-setup-login-btn"
                  onClick={login}
                  disabled={step.login === 'active'}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-text text-bg rounded-md text-xs font-medium hover:bg-text/90 disabled:opacity-50"
                >
                  {step.login === 'active' ? (
                    <>
                      <Loader2 size={12} className="animate-spin" />
                      로그인 대기 중…
                    </>
                  ) : (
                    <>
                      <Terminal size={12} />
                      로그인 창 열기
                    </>
                  )}
                </button>
              ) : null
            }
            hint={step.login === 'active' ? '브라우저 에서 ChatGPT 로그인 완료 후 자동 으로 다음 단계 가 진행 됩니다.' : null}
          />
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-border flex items-center justify-between gap-2">
          <div className="text-xs text-text-tertiary" aria-live="polite">
            {busyLabel || (allDone ? '모든 준비 완료 — 채팅 사용 가능' : '진행 상태 를 확인 중…')}
          </div>
          {allDone ? (
            <button
              data-testid="codex-setup-complete"
              onClick={handleComplete}
              className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md text-sm font-semibold hover:bg-green-700"
            >
              <CheckCircle size={14} />
              사용 시작
            </button>
          ) : (
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-text-tertiary hover:text-text text-sm"
            >
              나중에
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 하위 컴포넌트: StepRow ───────────────────────────────────────
function StepRow({
  num,
  state,
  title,
  description,
  action,
  hint,
}: {
  num: number
  state: StepState
  title: string
  description: string
  action?: React.ReactNode
  hint?: string | null
}) {
  const icon = (() => {
    if (state === 'completed') return <CheckCircle size={20} className="text-green-500" />
    if (state === 'active') return <Loader2 size={20} className="text-blue-500 animate-spin" />
    if (state === 'error') return <AlertTriangle size={20} className="text-red-500" />
    return <Circle size={20} className="text-text-tertiary" />
  })()

  const titleColor = state === 'completed' ? 'text-text' : state === 'error' ? 'text-red-600' : 'text-text'

  return (
    <div
      data-testid={`codex-setup-step-${num}`}
      data-step-state={state}
      className="flex items-start gap-3"
    >
      <div className="flex-shrink-0 mt-0.5">{icon}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-tertiary font-mono">{num}.</span>
          <h3 className={`text-sm font-medium ${titleColor}`}>{title}</h3>
        </div>
        <p className="text-xs text-text-secondary mt-0.5">{description}</p>
        {hint && (
          <p
            className={`text-xs mt-1 ${state === 'error' ? 'text-red-600' : 'text-text-tertiary'}`}
            data-testid={`codex-setup-step-${num}-hint`}
          >
            {hint}
          </p>
        )}
        {action && <div className="mt-2">{action}</div>}
      </div>
    </div>
  )
}

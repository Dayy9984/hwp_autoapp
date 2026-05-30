// Codex 자동 설정 보장.
// - 베타 모드: 라이센스 활성화 직후 + 30초 주기 codex status 체크
// - 미설치 / 미인증 시 모달 노출 + 원클릭 "Codex 연결하기" 버튼
// - 버튼 → install (안내) → login (자동 spawn) → status 재확인

import { useEffect, useState, useCallback } from 'react'
import { Loader2, CheckCircle2, AlertCircle, ExternalLink } from 'lucide-react'
import { BETA_CODEX_ONLY } from '../config/beta'

interface CodexStatus {
  installed: boolean
  authenticated?: boolean
  reason?: string
}

export function CodexAutoSetup() {
  const [status, setStatus] = useState<CodexStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const check = useCallback(async (autoOpen = true) => {
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    if (!api?.codex?.status) return
    try {
      const result = await api.codex.status()
      setStatus(result)
      // 베타 모드 + (미설치 OR 미인증) 이면 모달 자동 노출
      if (BETA_CODEX_ONLY && autoOpen && (!result.installed || !result.authenticated)) {
        setOpen(true)
      } else if (result.installed && result.authenticated) {
        setOpen(false)  // 정상 → 닫기
      }
    } catch (e: any) {
      setError(e?.message || '상태 확인 실패')
    }
  }, [])

  useEffect(() => {
    if (!BETA_CODEX_ONLY) return
    check(true)
    const id = setInterval(() => check(false), 30_000)
    // ChatInput 등에서 dispatch — 즉시 모달 오픈
    const onForce = () => { setOpen(true); check(true) }
    window.addEventListener('beta:codex-needs-setup', onForce)
    return () => {
      clearInterval(id)
      window.removeEventListener('beta:codex-needs-setup', onForce)
    }
  }, [check])

  const handleLogin = async () => {
    setLoading(true)
    setError('')
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    try {
      const result = await api.codex.login()
      if (result?.success) {
        await new Promise((r) => setTimeout(r, 1000))
        await check(true)
      } else {
        setError(result?.reason || '로그인 실패 — 다시 시도해주세요')
      }
    } catch (e: any) {
      setError(e?.message || '오류')
    } finally {
      setLoading(false)
    }
  }

  if (!BETA_CODEX_ONLY || !open) return null

  const isInstalled = !!status?.installed
  const isAuthed = !!status?.authenticated

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: '#FFF', borderRadius: 16, padding: 32,
          width: 'min(480px, 92vw)', boxShadow: '0 20px 60px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <AlertCircle size={20} color="#E86B45" />
          <h2 style={{ margin: 0, fontSize: 18, color: '#1F2937' }}>Codex CLI 연결이 필요합니다</h2>
        </div>
        <p style={{ margin: '0 0 20px', color: '#6B7280', fontSize: 14, lineHeight: 1.6 }}>
          베타 기간에는 <strong>Codex CLI 모드</strong>만 지원됩니다. ChatGPT 구독 계정으로 로그인하시면 별도 결제 없이 사용 가능합니다.
        </p>

        {/* 단계별 상태 */}
        <div style={{ background: '#F9FAFB', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <Step
            ok={isInstalled}
            label="1. Codex CLI 설치"
            help={
              isInstalled
                ? '설치됨'
                : <>터미널에서 <code style={codeStyle}>npm install -g @openai/codex</code> 실행 후 새로고침</>
            }
          />
          <div style={{ height: 1, background: '#E5E7EB', margin: '12px 0' }} />
          <Step
            ok={isAuthed}
            label="2. ChatGPT 계정 로그인"
            help={isAuthed ? '인증됨' : '아래 버튼 클릭 → 브라우저로 로그인 → 자동으로 토큰 등록'}
          />
        </div>

        {error && (
          <div style={{ padding: 12, background: '#FEF2F2', color: '#991B1B', borderRadius: 8, fontSize: 13, marginBottom: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8 }}>
          {isInstalled && !isAuthed && (
            <button
              onClick={handleLogin}
              disabled={loading}
              data-testid="codex-login-btn"
              style={{
                flex: 1, padding: '12px 20px', background: '#E86B45', color: '#FFF',
                border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600,
                cursor: loading ? 'wait' : 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              }}
            >
              {loading ? <><Loader2 size={16} className="animate-spin" /> 로그인 중…</> : 'Codex 로그인하기'}
            </button>
          )}
          {!isInstalled && (
            <a
              href="https://github.com/openai/codex#readme"
              target="_blank"
              rel="noopener noreferrer"
              style={{
                flex: 1, padding: '12px 20px', background: '#F3F4F6', color: '#1F2937',
                borderRadius: 10, fontSize: 13, textDecoration: 'none',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              설치 가이드 <ExternalLink size={14} />
            </a>
          )}
          <button
            onClick={() => check(true)}
            disabled={loading}
            data-testid="codex-recheck-btn"
            style={{
              padding: '12px 20px', background: '#FFF', color: '#374151',
              border: '1px solid #E5E7EB', borderRadius: 10, fontSize: 13, cursor: 'pointer',
            }}
          >
            다시 확인
          </button>
        </div>

        <p style={{ margin: '14px 0 0', fontSize: 11, color: '#9CA3AF', textAlign: 'center' }}>
          문의: <a href="mailto:contact@inserty-ai.com" style={{ color: '#E86B45' }}>contact@inserty-ai.com</a>
        </p>
      </div>
    </div>
  )
}

function Step({ ok, label, help }: { ok: boolean; label: string; help: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
      <div style={{ marginTop: 2 }}>
        {ok
          ? <CheckCircle2 size={18} color="#10B981" />
          : <div style={{ width: 18, height: 18, borderRadius: 9, border: '2px solid #D1D5DB' }} />
        }
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: ok ? '#065F46' : '#1F2937' }}>{label}</div>
        <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>{help}</div>
      </div>
    </div>
  )
}

const codeStyle: React.CSSProperties = {
  background: '#1F2937', color: '#F9FAFB', padding: '2px 8px',
  borderRadius: 4, fontSize: 11, fontFamily: 'monospace',
}

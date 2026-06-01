// Codex 미인증 자동 안내 배너 — 화면 최상단 fixed bar.
// 스크롤 무관 항상 보임 + 명확한 색상 대비 + 단순 텍스트.

import { useEffect, useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { useUIStore } from '../stores/ui-store'

export function CodexAuthBanner() {
  const [status, setStatus] = useState<{ installed: boolean; authenticated?: boolean } | null>(null)
  const openModal = useUIStore((s) => s.openModal)

  useEffect(() => {
    let alive = true
    const check = async () => {
      const api = (window as unknown as { electronAPI?: any }).electronAPI
      if (!api?.codex?.status) return
      try {
        const s = await api.codex.status()
        if (alive) setStatus(s)
      } catch { /* ignore */ }
    }
    check()
    // polling 5초 + window focus 시 즉시 check (외부 codex login 직후 빠른 detection)
    const id = setInterval(check, 5_000)
    const onFocus = () => check()
    window.addEventListener('focus', onFocus)
    return () => { alive = false; clearInterval(id); window.removeEventListener('focus', onFocus) }
  }, [])

  if (!status) return null
  if (status.installed && status.authenticated) return null

  const reason = !status.installed
    ? 'Codex CLI 미설치'
    : '인증 만료 또는 미로그인'

  return (
    <div
      role="alert"
      data-testid="codex-auth-banner"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 80,
        background: '#EA580C',
        color: '#FFFFFF',
        padding: '8px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontSize: 13,
        boxShadow: '0 2px 4px rgba(0,0,0,0.08)',
      }}
    >
      <AlertCircle size={16} style={{ flexShrink: 0 }} />
      <div style={{ flex: 1, fontWeight: 500 }}>
        Codex 로그인 필요 — <span style={{ fontWeight: 400, opacity: 0.92 }}>{reason}</span>
      </div>
      <button
        onClick={() => openModal('settings', { tab: 'ai' })}
        style={{
          background: '#FFFFFF',
          color: '#C2410C',
          border: 'none',
          padding: '4px 12px',
          borderRadius: 4,
          fontSize: 12,
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        지금 로그인
      </button>
    </div>
  )
}

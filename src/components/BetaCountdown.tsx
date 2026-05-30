// 베타 라이센스 만료 카운트다운.
// 베타 종료일: 2026-06-22 23:59:59 KST (모든 사용자 동일).
// 사이드바 하단에 작게 표시 — 임박 (7일 이내) 시 강조.

import { useEffect, useState } from 'react'
import { Sparkles, Clock } from 'lucide-react'

// KST 자정 = UTC 14:59:59 of 2026-06-22
const BETA_END_AT = new Date('2026-06-22T23:59:59+09:00').getTime()

function computeRemaining(now: number) {
  const remainMs = BETA_END_AT - now
  if (remainMs <= 0) return { expired: true, days: 0, hours: 0, label: '베타 종료' }

  const totalSec = Math.floor(remainMs / 1000)
  const days = Math.floor(totalSec / 86400)
  const hours = Math.floor((totalSec % 86400) / 3600)
  return { expired: false, days, hours, label: '' }
}

interface Props {
  compact?: boolean  // 사이드바 접힘 상태
}

export function BetaCountdown({ compact = false }: Props) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const tick = () => setNow(Date.now())
    // 1분마다 업데이트 (남은 시간 표시는 분 단위 정확도면 충분)
    const t = setInterval(tick, 60_000)
    return () => clearInterval(t)
  }, [])

  const { expired, days, hours } = computeRemaining(now)
  if (expired) return null  // 만료 후엔 별도 처리 (license 가 알아서 차단)

  const urgent = days <= 7
  const veryUrgent = days <= 3

  if (compact) {
    return (
      <div
        className="mx-1 my-2 rounded-lg px-2 py-1.5 text-center"
        style={{
          backgroundColor: veryUrgent ? 'rgba(239,68,68,0.10)' : urgent ? 'rgba(232,107,69,0.10)' : 'var(--bg-tertiary)',
          color: veryUrgent ? '#ef4444' : urgent ? 'var(--accent)' : 'var(--text-tertiary)',
        }}
        title={`베타 종료까지 ${days}일 ${hours}시간 남음`}
      >
        <div className="text-[10px] font-bold leading-none">D-{days}</div>
      </div>
    )
  }

  return (
    <div
      className="mx-2 mb-2 rounded-xl border px-3 py-2.5 transition-colors"
      style={{
        backgroundColor: veryUrgent
          ? 'rgba(239,68,68,0.06)'
          : urgent
          ? 'rgba(232,107,69,0.06)'
          : 'var(--bg-secondary)',
        borderColor: veryUrgent ? 'rgba(239,68,68,0.25)' : urgent ? 'rgba(232,107,69,0.25)' : 'var(--border)',
      }}
    >
      <div className="flex items-center gap-2">
        {veryUrgent ? (
          <Clock className="h-3.5 w-3.5 flex-shrink-0" style={{ color: '#ef4444' }} strokeWidth={2.2} />
        ) : (
          <Sparkles className="h-3.5 w-3.5 flex-shrink-0" style={{ color: urgent ? 'var(--accent)' : 'var(--text-tertiary)' }} strokeWidth={2} />
        )}
        <div className="flex-1 min-w-0">
          <div
            className="text-[11px] font-semibold leading-tight"
            style={{
              color: veryUrgent ? '#ef4444' : urgent ? 'var(--accent)' : 'var(--text-secondary)',
            }}
          >
            베타 종료까지 {days}일
          </div>
          <div className="text-[10px] text-text-tertiary leading-tight mt-0.5">
            ~ 2026.06.22 자정
          </div>
        </div>
      </div>
    </div>
  )
}

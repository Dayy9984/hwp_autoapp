// 라이센스 만료 카운트다운.
// 라이센스 cache 의 expires_at 을 기준으로 표시. 어드민이 5분/30일/무기한 등
// 어떻게 발급해도 정확히 반영. 라이센스가 없거나 expires_at 이 null 이면
// 표시 안 함 (= 무기한 또는 미활성).
//
// 안전망: license 정보를 가져오지 못한 초기 단계에는 기존 베타 종료일(2026-06-22)
// 을 fallback 으로 사용 — 빈 상태로 깜빡이는 것보다 마지막에 알려진 종료일을 보여줌.

import { useEffect, useState } from 'react'
import { Sparkles, Clock } from 'lucide-react'

// fallback (라이센스 정보 fetch 전): 베타 종료일 (KST 자정)
const FALLBACK_BETA_END_AT = new Date('2026-06-22T23:59:59+09:00').getTime()

interface Remaining {
  expired: boolean
  days: number
  hours: number
  minutes: number
  seconds: number
}

function computeRemaining(targetMs: number, now: number): Remaining {
  const remainMs = targetMs - now
  if (remainMs <= 0) return { expired: true, days: 0, hours: 0, minutes: 0, seconds: 0 }

  const totalSec = Math.floor(remainMs / 1000)
  const days = Math.floor(totalSec / 86400)
  const hours = Math.floor((totalSec % 86400) / 3600)
  const minutes = Math.floor((totalSec % 3600) / 60)
  const seconds = totalSec % 60
  return { expired: false, days, hours, minutes, seconds }
}

interface Props {
  compact?: boolean  // 사이드바 접힘 상태
}

export function BetaCountdown({ compact = false }: Props) {
  const [now, setNow] = useState(() => Date.now())
  // null = 아직 license 정보 로드 안 됨 (fallback 사용)
  // undefined = license 응답 받았으나 expires_at 이 없음 (= 무기한 → 표시 안 함)
  // number = 만료 시각 ms
  const [expiresAtMs, setExpiresAtMs] = useState<number | null | undefined>(null)

  // 라이센스 만료 시각 fetch + onStatusChanged 구독
  useEffect(() => {
    let cancelled = false

    const applyStatus = (status: any) => {
      if (cancelled) return
      // ok / offline_grace 만 expires_at 을 가짐
      if (!status || (status.state !== 'ok' && status.state !== 'offline_grace')) {
        // 비-ok 상태 — license-gate 가 BlockedScreen 으로 전환하므로 카운트다운은 의미 없음.
        // fallback 유지 (혹은 마지막 알려진 값).
        return
      }
      const exp = status.expires_at
      if (!exp) {
        setExpiresAtMs(undefined)  // 무기한
        return
      }
      const t = new Date(exp).getTime()
      if (Number.isFinite(t)) {
        setExpiresAtMs(t)
      }
    }

    // 1) 초기 fetch — main 이 캐시한 lastStatus 즉시 반환 (네트워크 호출 없음)
    try {
      const api = (window as any).electronAPI?.license
      if (api?.getInitialStatus) {
        api.getInitialStatus().then(applyStatus).catch(() => {})
      } else if (api?.verify) {
        api.verify().then(applyStatus).catch(() => {})
      }
    } catch {
      // ignore — fallback 사용
    }

    // 2) main 의 license:statusChanged 구독 — verify/activate/만료 자동 verify 시 갱신
    let unsub: (() => void) | undefined
    try {
      const api = (window as any).electronAPI?.license
      if (api?.onStatusChanged) {
        unsub = api.onStatusChanged(applyStatus)
      }
    } catch {
      // ignore
    }

    return () => {
      cancelled = true
      try {
        unsub?.()
      } catch {}
    }
  }, [])

  // 카운트다운 틱 — 일 단위 / 시 단위만 표시하므로 빠른 갱신 불필요. 5분 간격.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5 * 60_000)
    return () => clearInterval(t)
  }, [expiresAtMs])

  // expires_at == undefined → 무기한 (표시 안 함)
  if (expiresAtMs === undefined) return null

  const target = expiresAtMs ?? FALLBACK_BETA_END_AT
  const { expired, days, hours } = computeRemaining(target, now)

  // 만료 — license-gate 가 BlockedScreen 전환을 담당하므로 여기선 안 보여줌
  if (expired) return null

  const urgent = days <= 7
  const veryUrgent = days <= 3

  // 타겟 종료일 텍스트 (사용자 친화)
  const targetDate = new Date(target)
  const targetLabel = `${targetDate.getFullYear()}.${String(targetDate.getMonth() + 1).padStart(2, '0')}.${String(targetDate.getDate()).padStart(2, '0')}`

  if (compact) {
    // 일 단위 우선 / 1일 미만 → 시간 / 1시간 미만 → "<1h"
    const compactLabel = days > 0 ? `D-${days}` : hours > 0 ? `${hours}h` : `<1h`
    const titleText = days > 0 ? `${days}일` : hours > 0 ? `${hours}시간` : '1시간 미만'
    return (
      <div
        className="mx-1 my-2 rounded-lg px-2 py-1.5 text-center"
        style={{
          backgroundColor: veryUrgent ? 'rgba(239,68,68,0.10)' : urgent ? 'rgba(232,107,69,0.10)' : 'var(--bg-tertiary)',
          color: veryUrgent ? '#ef4444' : urgent ? 'var(--accent)' : 'var(--text-tertiary)',
        }}
        title={`라이센스 종료까지 ${titleText} 남음`}
      >
        <div className="text-[10px] font-bold leading-none">{compactLabel}</div>
      </div>
    )
  }

  // 메인 라벨: 1일 이상=일 / 1일 미만=시간 / 1시간 미만="1시간 미만 남음"
  const mainLabel = days > 0
    ? `라이센스 종료까지 ${days}일`
    : hours > 0
    ? `라이센스 종료까지 ${hours}시간`
    : `라이센스 종료까지 1시간 미만 남음`

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
            {mainLabel}
          </div>
          <div className="text-[10px] text-text-tertiary leading-tight mt-0.5">
            ~ {targetLabel}
          </div>
        </div>
      </div>
    </div>
  )
}

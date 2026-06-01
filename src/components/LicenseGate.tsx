// 라이센스 게이트 — 앱 시작 시 표시되는 차단 화면 + 키 입력 화면 + 디바이스 관리 화면
// 디자인 시스템 토큰 (--bg, --text, --accent, --danger, --border) 사용 → 다크 모드 자동 대응

import { useEffect, useState } from 'react'
import {
  KeyRound, ShieldAlert, Ban, AlertTriangle, WifiOff, Loader2,
  Laptop, Trash2, RefreshCw, Check,
} from 'lucide-react'
import { KakaoSupportLink } from './KakaoSupportLink'

export interface DeviceRow {
  device_id: string
  device_name: string | null
  device_os: string | null
  last_seen_at: string
  first_seen_at: string
}

export type LicenseState =
  | { state: 'loading' }
  | { state: 'ok'; expires_at: string | null }
  | { state: 'expired'; license_key: string | null }
  | { state: 'leaked'; license_key: string | null; device_count?: number }
  | {
      state: 'device_limit_reached'
      license_key: string | null
      device_count?: number
      max_devices?: number
      devices?: DeviceRow[]
    }
  | { state: 'revoked'; license_key: string | null }
  | { state: 'invalid'; reason?: string }
  | { state: 'no_license' }
  | { state: 'offline_grace'; expires_at: string | null }
  | { state: 'offline_blocked' }

interface Props {
  status: LicenseState
  onActivate: (key: string) => Promise<void>
  onRetry: () => void
}

export function LicenseGate({ status, onActivate, onRetry }: Props) {
  switch (status.state) {
    case 'loading':
      return <LoadingScreen />
    case 'ok':
    case 'offline_grace':
      return null
    case 'expired':
      return (
        <KeyInputScreen
          onActivate={onActivate}
          title="라이센스가 만료되었습니다"
          subtitle="갱신을 원하시면 유효한 라이센스 키를 입력해주세요"
        />
      )
    case 'leaked':
      return (
        <BlockedScreen
          icon={<ShieldAlert className="w-7 h-7 text-danger" strokeWidth={1.5} />}
          tone="danger"
          title="비정상 사용이 감지되었습니다"
          description={
            (status.device_count
              ? `여러 기기(${status.device_count}대)에서 동일 라이센스 키 사용이 감지되어 잠금되었습니다.\n`
              : '동일 라이센스 키의 비정상 사용이 감지되어 잠금되었습니다.\n') +
            '본인 사용이 맞다면 카카오톡으로 문의 부탁드립니다.'
          }
          licenseKey={status.license_key}
        />
      )
    case 'revoked':
      return (
        <BlockedScreen
          icon={<Ban className="w-7 h-7 text-danger" strokeWidth={1.5} />}
          tone="danger"
          title="라이센스가 무효화되었습니다"
          description="이 라이센스는 더 이상 사용할 수 없습니다. 문의가 필요하면 카카오톡으로 연락해 주세요."
          licenseKey={status.license_key}
        />
      )
    case 'device_limit_reached':
      return (
        <DeviceManagerScreen
          licenseKey={status.license_key}
          maxDevices={status.max_devices ?? 2}
          initialDevices={status.devices ?? []}
          onRetry={onRetry}
        />
      )
    case 'invalid':
      return <KeyInputScreen onActivate={onActivate} initialError={status.reason} />
    case 'no_license':
      return <KeyInputScreen onActivate={onActivate} />
    case 'offline_blocked':
      return <OfflineBlockedScreen onRetry={onRetry} />
  }
}

// ────────────────────────────────────────────────────────────────────────────────
// 공통 컨테이너 — 다크/라이트 모두 var(--bg) 자동 적용
// ────────────────────────────────────────────────────────────────────────────────
function Scaffold({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-bg">
      {children}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────
// 로딩
// ────────────────────────────────────────────────────────────────────────────────
function LoadingScreen() {
  return (
    <Scaffold>
      <div className="flex items-center gap-3 text-text-secondary">
        <Loader2 className="w-5 h-5 animate-spin" strokeWidth={1.5} />
        <span className="text-sm">라이센스 확인 중</span>
      </div>
    </Scaffold>
  )
}

// ────────────────────────────────────────────────────────────────────────────────
// 키 입력 (no_license + invalid)
// ────────────────────────────────────────────────────────────────────────────────
function KeyInputScreen({
  onActivate,
  initialError,
  title = '라이센스 키 입력',
  subtitle = '결제 후 발송된 키를 입력해 주세요',
}: {
  onActivate: (k: string) => Promise<void>
  initialError?: string
  title?: string
  subtitle?: string
}) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(reasonToMessage(initialError))

  // React 가 KeyInputScreen 인스턴스를 재사용하므로 (no_license ↔ invalid 전환 시)
  // initialError prop 이 바뀌어도 useState 초기값은 다시 실행되지 않는다.
  // prop 변경을 error state 에 명시적으로 동기화.
  useEffect(() => {
    setError(reasonToMessage(initialError))
  }, [initialError])

  const handleSubmit = async () => {
    setError(null)
    setBusy(true)
    try {
      await onActivate(key)
    } catch (e: any) {
      setError(e?.message || '활성화에 실패했습니다')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Scaffold>
      <div className="w-[440px] rounded-2xl border border-border bg-bg-secondary p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10">
            <KeyRound className="w-5 h-5 text-accent" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-text">{title}</h1>
            <p className="text-xs text-text-tertiary">{subtitle}</p>
          </div>
        </div>

        <input
          type="text"
          value={key}
          onChange={(e) => setKey(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !busy && key.length === 25) handleSubmit()
          }}
          placeholder="INSRT-XXXX-XXXX-XXXX-XXXX"
          maxLength={25}
          spellCheck={false}
          autoFocus
          className="mb-3 w-full rounded-lg border border-border bg-bg px-3 py-2.5 font-mono text-sm tracking-wide text-text placeholder-text-tertiary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
        />

        {error && (
          <div className="mb-3 rounded-lg bg-danger-light px-3 py-2 text-xs text-danger">
            {error}
          </div>
        )}

        <button
          type="button"
          onClick={handleSubmit}
          disabled={busy || key.length !== 25}
          className="mb-5 inline-flex h-10 w-full items-center justify-center rounded-lg bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? (
            <>
              <Loader2 className="mr-2 w-4 h-4 animate-spin" strokeWidth={1.5} />
              활성화 중
            </>
          ) : (
            '활성화'
          )}
        </button>

        <div className="border-t border-border pt-5">
          <p className="mb-3 text-xs text-text-tertiary">
            키가 없거나 도움이 필요하신가요?
          </p>
          <KakaoSupportLink className="w-full">카카오톡 문의</KakaoSupportLink>
        </div>
      </div>
    </Scaffold>
  )
}

// ────────────────────────────────────────────────────────────────────────────────
// 차단 화면 (expired / leaked / revoked)
// ────────────────────────────────────────────────────────────────────────────────
function BlockedScreen({
  icon,
  tone,
  title,
  description,
  licenseKey,
}: {
  icon: React.ReactNode
  tone: 'warning' | 'danger'
  title: string
  description: string
  licenseKey: string | null
}) {
  return (
    <Scaffold>
      <div className="w-[460px] rounded-2xl border border-border bg-bg-secondary p-8 shadow-sm">
        <div className="mb-5 flex items-start gap-4">
          <div
            className={`flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl ${
              tone === 'danger' ? 'bg-danger-light' : 'bg-accent-light'
            }`}
          >
            {icon}
          </div>
          <div className="pt-1">
            <h1 className="text-base font-semibold text-text">{title}</h1>
          </div>
        </div>

        <p className="mb-5 whitespace-pre-line text-sm leading-relaxed text-text-secondary">
          {description}
        </p>

        {licenseKey && (
          <div className="mb-5 rounded-lg border border-border bg-bg px-3 py-2.5">
            <p className="mb-1 text-[10px] uppercase tracking-wider text-text-tertiary">
              현재 라이센스 키
            </p>
            <p className="font-mono text-xs text-text">{licenseKey}</p>
          </div>
        )}

        <KakaoSupportLink className="w-full">카카오톡으로 문의하기</KakaoSupportLink>
      </div>
    </Scaffold>
  )
}

// ────────────────────────────────────────────────────────────────────────────────
// 오프라인 차단
// ────────────────────────────────────────────────────────────────────────────────
function OfflineBlockedScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <Scaffold>
      <div className="w-[440px] rounded-2xl border border-border bg-bg-secondary p-8 shadow-sm">
        <div className="mb-5 flex items-start gap-4">
          <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl bg-bg-tertiary">
            <WifiOff className="w-6 h-6 text-text-secondary" strokeWidth={1.5} />
          </div>
          <div className="pt-1">
            <h1 className="text-base font-semibold text-text">네트워크 연결 필요</h1>
          </div>
        </div>

        <p className="mb-5 text-sm leading-relaxed text-text-secondary">
          7일 이상 오프라인 상태입니다. 네트워크에 연결한 뒤 다시 시도해 주세요.
        </p>

        <button
          type="button"
          onClick={onRetry}
          className="inline-flex h-10 w-full items-center justify-center rounded-lg bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-dark"
        >
          다시 시도
        </button>
      </div>
    </Scaffold>
  )
}

// ────────────────────────────────────────────────────────────────────────────────
// reason 코드를 사용자 친화 메시지로 변환
// ────────────────────────────────────────────────────────────────────────────────
function reasonToMessage(reason?: string): string | null {
  if (!reason) return null
  switch (reason) {
    case 'invalid_key_format':
      return '키 형식이 올바르지 않습니다. INSRT-XXXX-XXXX-XXXX-XXXX 형식이어야 합니다.'
    case 'invalid':
      return '등록되지 않은 라이센스 키입니다.'
    case 'invalid_token':
      return '저장된 인증 정보가 손상되었습니다. 키를 다시 입력해 주세요.'
    case 'device_mismatch':
    case 'device_not_registered':
      return '이 기기에서는 사용할 수 없는 라이센스입니다.'
    case 'cannot_remove_self':
      return '현재 사용 중인 기기는 삭제할 수 없습니다.'
    case 'network_error':
      return '서버에 연결할 수 없습니다. 네트워크 상태를 확인해 주세요.'
    default:
      return `활성화에 실패했습니다 (${reason})`
  }
}

// ────────────────────────────────────────────────────────────────────────────────
// 디바이스 관리 화면 — 슬롯 꽉 찼을 때 표시
// ────────────────────────────────────────────────────────────────────────────────
interface DeviceListResponse {
  ok: boolean
  reason?: string
  current_device_id?: string
  max_devices?: number
  devices?: DeviceRow[]
}

function DeviceManagerScreen({
  licenseKey,
  maxDevices,
  initialDevices,
  onRetry,
}: {
  licenseKey: string | null
  maxDevices: number
  initialDevices: DeviceRow[]
  onRetry: () => void
}) {
  // device_limit_reached 응답에 동봉된 devices 를 먼저 표시 (캐시 토큰 없어도 OK).
  // 그 다음 listDevices(management 토큰) 호출로 current_device_id 동기화 + 최신화.
  const [devices, setDevices] = useState<DeviceRow[]>(initialDevices)
  const [currentDeviceId, setCurrentDeviceId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  const fetchDevices = async () => {
    setLoading(true)
    setError(null)
    try {
      const api = (window as any).electronAPI?.license
      if (!api?.listDevices) return
      const r: DeviceListResponse = await api.listDevices()
      if (!r.ok) {
        // initialDevices 가 있어 화면 표시는 가능 → 조용히 무시 (current id 만 못 받음)
        return
      }
      if (r.devices) setDevices(r.devices)
      if (r.current_device_id) setCurrentDeviceId(r.current_device_id)
    } catch {
      // ignore — initial 데이터로 화면 유지
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchDevices()
  }, [])

  const handleRemove = async (device_id: string) => {
    if (currentDeviceId && device_id === currentDeviceId) {
      setError('현재 사용 중인 기기는 삭제할 수 없습니다.')
      return
    }
    setRemoving(device_id)
    setError(null)
    setInfo(null)
    try {
      const api = (window as any).electronAPI?.license
      const r = await api.removeDevice(device_id)
      if (!r?.ok) {
        setError(reasonToMessage(r?.reason) || '삭제에 실패했습니다.')
        return
      }
      setInfo('기기를 삭제했습니다. 이 PC 를 활성화합니다…')
      // 슬롯 정리 직후 management 흐름에서 자동 activate 재시도.
      // 성공 시 main process 의 lastStatus 가 ok 로 갱신 → onRetry(verify) 가 ok 받음.
      try {
        await api.retryActivate?.()
      } catch {}
      setTimeout(() => onRetry(), 200)
    } catch (e: any) {
      setError(e?.message || '삭제 실패')
    } finally {
      setRemoving(null)
    }
  }

  return (
    <Scaffold>
      <div className="w-[520px] max-w-[92vw] rounded-2xl border border-border bg-bg-secondary p-8 shadow-sm">
        <div className="mb-5 flex items-start gap-4">
          <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl bg-accent-light">
            <Laptop className="w-6 h-6 text-accent" strokeWidth={1.5} />
          </div>
          <div className="pt-1">
            <h1 className="text-base font-semibold text-text">기기 슬롯이 가득 찼습니다</h1>
            <p className="mt-1 text-xs text-text-tertiary">
              라이센스당 최대 {maxDevices}대까지 사용할 수 있습니다. 사용하지 않는 기기를 삭제하면
              이 기기에서 곧바로 활성화됩니다.
            </p>
          </div>
        </div>

        {licenseKey && (
          <div className="mb-5 rounded-lg border border-border bg-bg px-3 py-2.5">
            <p className="mb-1 text-[10px] uppercase tracking-wider text-text-tertiary">
              현재 라이센스 키
            </p>
            <p className="font-mono text-xs text-text">{licenseKey}</p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-text-secondary">
            <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} />
            기기 목록 불러오는 중
          </div>
        ) : (
          <ul className="mb-5 space-y-2">
            {devices.map((d) => {
              const isSelf = d.device_id === currentDeviceId
              return (
                <li
                  key={d.device_id}
                  className={`flex items-start gap-3 rounded-lg border p-3 ${
                    isSelf
                      ? 'border-accent/40 bg-accent-light'
                      : 'border-border bg-bg'
                  }`}
                >
                  <Laptop
                    className={`mt-0.5 h-5 w-5 flex-shrink-0 ${
                      isSelf ? 'text-accent' : 'text-text-tertiary'
                    }`}
                    strokeWidth={1.5}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text truncate">
                        {d.device_name || '이름 없음'}
                      </span>
                      {isSelf && (
                        <span className="inline-flex items-center gap-1 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                          <Check className="w-3 h-3" strokeWidth={2} />
                          현재 기기
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-text-tertiary">
                      {d.device_os || '운영체제 알 수 없음'}
                    </p>
                    <p className="mt-0.5 font-mono text-[10px] text-text-tertiary truncate">
                      {d.device_id}
                    </p>
                    <p className="mt-1 text-[10px] text-text-tertiary">
                      마지막 사용: {formatTime(d.last_seen_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemove(d.device_id)}
                    disabled={isSelf || removing === d.device_id}
                    title={isSelf ? '현재 사용 중인 기기는 삭제할 수 없습니다' : '기기 삭제'}
                    className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-danger-light hover:text-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-text-tertiary"
                  >
                    {removing === d.device_id ? (
                      <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} />
                    ) : (
                      <Trash2 className="w-4 h-4" strokeWidth={1.5} />
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        {error && (
          <div className="mb-3 rounded-lg bg-danger-light px-3 py-2 text-xs text-danger">
            {error}
          </div>
        )}
        {info && (
          <div className="mb-3 rounded-lg bg-accent-light px-3 py-2 text-xs text-accent">
            {info}
          </div>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-dark"
          >
            <RefreshCw className="w-4 h-4" strokeWidth={1.5} />
            다시 시도
          </button>
          <button
            type="button"
            onClick={() => void fetchDevices()}
            className="inline-flex h-10 items-center justify-center rounded-lg border border-border bg-bg px-4 text-sm font-medium text-text-secondary transition-colors hover:bg-bg-tertiary"
          >
            새로고침
          </button>
        </div>

        <div className="mt-4 border-t border-border pt-4">
          <KakaoSupportLink className="w-full">카카오톡으로 문의</KakaoSupportLink>
        </div>
      </div>
    </Scaffold>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

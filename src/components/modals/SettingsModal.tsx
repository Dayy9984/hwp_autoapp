import { useState, useEffect, useCallback } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useSettingsStore } from '../../stores/settings-store'
import openAiModels from '../../config/openai-models.json'
import { CODEX_ONLY_MODE } from '../../config/release'
import {
  Settings,
  BarChart3,
  X,
  Sun,
  Moon,
  Minus,
  Plus,
  RotateCcw,
  Sparkles,
  Key,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Loader2,
  ExternalLink,
  ShieldCheck,
  Laptop,
  Trash2,
  RefreshCw,
} from 'lucide-react'

function CodexStatusPanel() {
  const [status, setStatus] = useState<{ installed: boolean; authenticated?: boolean; reason?: string } | null>(null)
  const [checking, setChecking] = useState(false)
  const [loggingIn, setLoggingIn] = useState(false)

  const check = useCallback(async () => {
    setChecking(true)
    try {
      const result = await window.electronAPI.codex.status()
      setStatus(result)
    } catch {
      setStatus({ installed: false, reason: '상태 확인 실패' })
    }
    setChecking(false)
  }, [])

  useEffect(() => {
    check()
    // polling 5초 + window focus 시 즉시 check (외부 codex login 직후 빠른 detection)
    const id = setInterval(() => check(), 5_000)
    const onFocus = () => check()
    window.addEventListener('focus', onFocus)
    return () => { clearInterval(id); window.removeEventListener('focus', onFocus) }
  }, [check])

  const handleLogin = async () => {
    setLoggingIn(true)
    try {
      await window.electronAPI.codex.login()
      // 외부 cmd 창에서 device-auth/OAuth 진행 — 완료 시점 알 수 없음.
      // polling + focus 가 자동 detection. 추가로 즉시 1회 check.
      await new Promise((r) => setTimeout(r, 1500))
      await check()
    } catch {
      // ignore
    }
    setLoggingIn(false)
  }

  if (checking && !status) {
    return (
      <div className="flex items-center gap-2 text-xs text-text-tertiary mt-2">
        <Loader2 size={14} className="animate-spin" />
        Codex 상태 확인 중...
      </div>
    )
  }

  if (!status) return null

  // 설치 안 됨 → 자동 설정 wizard 시작 (사용자 가 직접 명령 외울 필요 X)
  if (!status.installed) {
    return (
      <div className="mt-3 space-y-2">
        <div className="flex items-center gap-2">
          <XCircle size={14} className="text-red-500 shrink-0" />
          <span className="text-xs text-red-600">Codex CLI 가 설치 되어 있지 않습니다</span>
        </div>
        <button
          data-testid="codex-panel-setup-install"
          onClick={() => useUIStore.getState().openModal('codex-setup')}
          className="inline-flex items-center gap-2 px-4 py-2 bg-text text-bg rounded-lg text-sm font-medium hover:bg-text/90 transition-colors"
        >
          설치 하기
        </button>
      </div>
    )
  }

  // 설치됨 + 로그인 안 됨 → 자동 설정 wizard 의 로그인 step 으로
  if (!status.authenticated) {
    return (
      <div className="mt-3 space-y-2">
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-yellow-500 shrink-0" />
          <span className="text-xs text-yellow-600">로그인이 필요합니다</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleLogin}
            disabled={loggingIn}
            className="inline-flex items-center gap-2 px-4 py-2 bg-text text-bg rounded-lg text-sm font-medium hover:bg-text/90 transition-colors disabled:opacity-50"
          >
            {loggingIn ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                로그인 중...
              </>
            ) : (
              <>
                <ExternalLink size={14} />
                Codex 로그인
              </>
            )}
          </button>
          <button
            data-testid="codex-panel-setup-login"
            onClick={() => useUIStore.getState().openModal('codex-setup')}
            className="text-xs text-text-tertiary hover:text-text underline"
          >
            자동 설정 마법사
          </button>
        </div>
      </div>
    )
  }

  // 설치 + 로그인 완료
  return (
    <div className="mt-3 flex items-center gap-2">
      <CheckCircle size={14} className="text-green-500 shrink-0" />
      <span className="text-xs text-green-600">Codex 인증 완료</span>
      <button onClick={check} className="text-xs text-text-tertiary hover:text-text underline ml-2">
        새로고침
      </button>
    </div>
  )
}

const formatDate = (timestamp: number) => {
  const date = new Date(timestamp)
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

const formatPrice = (value: number) => `$${value.toFixed(2)}`

type SettingsTab = 'general' | 'ai' | 'usage' | 'license'

type AiTab = 'chat' | 'embedding'

type LicensePanelStatus =
  | { state: 'loading' }
  | { state: 'ok'; expires_at: string | null }
  | { state: 'offline_grace'; expires_at: string | null }
  | { state: 'expired' }
  | { state: 'revoked' }
  | { state: 'leaked'; device_count?: number }
  | { state: 'device_limit_reached'; device_count?: number; max_devices?: number }
  | { state: 'invalid'; reason?: string }
  | { state: 'no_license' }
  | { state: 'offline_blocked' }

interface LicenseDeviceRow {
  device_id: string
  device_name: string | null
  device_os: string | null
  last_seen_at: string
  first_seen_at: string
}

const DEFAULT_MAX_LICENSE_DEVICES = 2

function formatOptionalDate(value: string | null | undefined) {
  if (!value) return '무기한'
  try {
    return new Intl.DateTimeFormat('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function licenseStateLabel(status: LicensePanelStatus | null) {
  switch (status?.state) {
    case 'ok':
      return '정상'
    case 'offline_grace':
      return '오프라인 사용 가능'
    case 'expired':
      return '만료됨'
    case 'revoked':
      return '무효화됨'
    case 'leaked':
      return '비정상 사용 감지'
    case 'device_limit_reached':
      return '기기 등록 한도 초과'
    case 'invalid':
      return '유효하지 않음'
    case 'no_license':
      return '미등록'
    case 'offline_blocked':
      return '네트워크 확인 필요'
    default:
      return '확인 중'
  }
}

function LicenseSettingsPanel() {
  const [licenseKey, setLicenseKey] = useState<string | null>(null)
  const [status, setStatus] = useState<LicensePanelStatus | null>({ state: 'loading' })
  const [newKey, setNewKey] = useState('')
  const [devices, setDevices] = useState<LicenseDeviceRow[]>([])
  const [currentDeviceId, setCurrentDeviceId] = useState<string | null>(null)
  const [maxDevices, setMaxDevices] = useState(DEFAULT_MAX_LICENSE_DEVICES)
  const [loading, setLoading] = useState(false)
  const [activating, setActivating] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadLicense = useCallback(async () => {
    const api = (window as any).electronAPI?.license
    if (!api) return
    setLoading(true)
    setError(null)
    try {
      const [rawStatus, cachedKey] = await Promise.all([
        api.getInitialStatus?.() ?? api.verify?.(),
        api.getCachedKey?.(),
      ])
      setStatus(rawStatus ?? { state: 'no_license' })
      setLicenseKey(cachedKey ?? null)
      if (typeof rawStatus?.max_devices === 'number') {
        setMaxDevices(rawStatus.max_devices)
      }

      const list = await api.listDevices?.()
      if (list?.ok) {
        setDevices(Array.isArray(list.devices) ? list.devices : [])
        setCurrentDeviceId(list.current_device_id ?? null)
        if (typeof list.max_devices === 'number') setMaxDevices(list.max_devices)
      } else if (list?.reason && list.reason !== 'no_license') {
        setError(deviceReasonToMessage(list.reason))
      } else {
        setDevices([])
        setCurrentDeviceId(null)
      }
    } catch (e: any) {
      setError(e?.message || '라이센스 정보를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadLicense()
  }, [loadLicense])

  const handleActivate = async () => {
    const key = newKey.trim().toUpperCase()
    if (!key) {
      setError('교체할 라이센스 키를 입력해주세요.')
      return
    }
    if (licenseKey && !window.confirm('현재 라이센스 키를 새 키로 교체할까요?')) return

    const api = (window as any).electronAPI?.license
    if (!api?.activate) return
    setActivating(true)
    setError(null)
    setMessage(null)
    try {
      const result = await api.activate(key)
      setStatus(result)
      if (result?.state === 'ok' || result?.state === 'offline_grace') {
        setNewKey('')
        setMessage('라이센스 키를 교체했습니다.')
        await loadLicense()
      } else if (result?.state === 'device_limit_reached') {
        setMaxDevices(result.max_devices ?? DEFAULT_MAX_LICENSE_DEVICES)
        setDevices(Array.isArray(result.devices) ? result.devices : [])
        setError(`기기 등록 한도에 도달했습니다. 최대 ${result.max_devices ?? DEFAULT_MAX_LICENSE_DEVICES}대까지 사용할 수 있습니다.`)
      } else {
        setError(`활성화에 실패했습니다. (${result?.reason || result?.state || 'unknown'})`)
      }
    } catch (e: any) {
      setError(e?.message || '활성화에 실패했습니다.')
    } finally {
      setActivating(false)
    }
  }

  const handleRemoveDevice = async (deviceId: string) => {
    if (currentDeviceId && deviceId === currentDeviceId) {
      setError('현재 사용 중인 기기는 삭제할 수 없습니다.')
      return
    }
    const target = devices.find((d) => d.device_id === deviceId)
    const label = target?.device_name || deviceId
    if (!window.confirm(`${label} 기기 등록을 해제할까요?`)) return

    const api = (window as any).electronAPI?.license
    if (!api?.removeDevice) return
    setRemoving(deviceId)
    setError(null)
    setMessage(null)
    try {
      const result = await api.removeDevice(deviceId)
      if (!result?.ok) {
        setError(deviceReasonToMessage(result?.reason) || '기기 해제에 실패했습니다.')
        return
      }
      setMessage('기기 등록을 해제했습니다.')
      await loadLicense()
    } catch (e: any) {
      setError(e?.message || '기기 해제에 실패했습니다.')
    } finally {
      setRemoving(null)
    }
  }

  const expiresAt = status?.state === 'ok' || status?.state === 'offline_grace' ? status.expires_at : null

  return (
    <div className="space-y-8 max-w-2xl">
      <section>
        <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">라이센스</h3>
        <div className="bg-bg-secondary p-5 rounded-xl border border-transparent space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs text-text-tertiary uppercase tracking-wider">현재 라이센스 키</div>
              <div className="mt-1 font-mono text-sm text-text break-all">
                {licenseKey || '등록된 라이센스가 없습니다'}
              </div>
            </div>
            <div className={`shrink-0 text-xs font-semibold px-2.5 py-1 rounded-full ${status?.state === 'ok' || status?.state === 'offline_grace'
              ? 'bg-green-100 text-green-700 border border-green-200'
              : 'bg-yellow-100 text-yellow-700 border border-yellow-200'
            }`}>
              {licenseStateLabel(status)}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg bg-bg px-3 py-2.5">
              <div className="text-[10px] text-text-tertiary uppercase tracking-wider">만료일</div>
              <div className="mt-1 text-sm text-text">{formatOptionalDate(expiresAt)}</div>
            </div>
            <div className="rounded-lg bg-bg px-3 py-2.5">
              <div className="text-[10px] text-text-tertiary uppercase tracking-wider">기기 등록</div>
              <div className="mt-1 text-sm text-text">{devices.length}/{maxDevices}대</div>
            </div>
          </div>

          <div className="flex gap-2">
            <input
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !activating && newKey.trim()) void handleActivate()
              }}
              placeholder="INSRT-XXXX-XXXX-XXXX-XXXX"
              maxLength={25}
              spellCheck={false}
              className="flex-1 min-w-0 px-3 py-2.5 rounded-lg bg-bg border border-transparent font-mono text-sm text-text focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all shadow-sm"
            />
            <button
              type="button"
              onClick={handleActivate}
              disabled={activating || !newKey.trim()}
              className="inline-flex items-center justify-center gap-2 px-5 py-2.5 bg-text text-bg rounded-lg text-sm font-medium hover:bg-text/90 transition-colors disabled:opacity-50"
            >
              {activating && <Loader2 size={14} className="animate-spin" />}
              교체
            </button>
            <button
              type="button"
              onClick={() => void loadLicense()}
              disabled={loading}
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-bg border border-border text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors disabled:opacity-50"
              title="새로고침"
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          {message && <div className="rounded-lg bg-accent-light px-3 py-2 text-xs text-accent">{message}</div>}
          {error && <div className="rounded-lg bg-danger-light px-3 py-2 text-xs text-danger">{error}</div>}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">등록 기기</h3>
        <div className="bg-bg-secondary rounded-xl border border-transparent overflow-hidden">
          {loading && devices.length === 0 ? (
            <div className="flex items-center gap-2 p-5 text-sm text-text-secondary">
              <Loader2 size={16} className="animate-spin" />
              기기 목록 불러오는 중
            </div>
          ) : devices.length === 0 ? (
            <div className="p-8 text-center text-sm text-text-tertiary">
              등록된 기기 목록이 없습니다.
            </div>
          ) : (
            <div className="divide-y divide-border">
              {devices.map((device) => {
                const isSelf = device.device_id === currentDeviceId
                return (
                  <div key={device.device_id} className="p-4 flex items-start gap-3">
                    <div className={`p-2 rounded-lg ${isSelf ? 'bg-accent-light text-accent' : 'bg-bg text-text-tertiary'}`}>
                      <Laptop size={18} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="font-medium text-sm text-text truncate">
                          {device.device_name || '이름 없음'}
                        </div>
                        {isSelf && (
                          <span className="shrink-0 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                            현재 기기
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-text-tertiary mt-0.5">
                        {device.device_os || '운영체제 알 수 없음'}
                      </div>
                      <div className="font-mono text-[10px] text-text-tertiary truncate mt-1">
                        {device.device_id}
                      </div>
                      <div className="text-[10px] text-text-tertiary mt-1">
                        마지막 사용: {formatOptionalDate(device.last_seen_at)}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveDevice(device.device_id)}
                      disabled={isSelf || removing === device.device_id}
                      title={isSelf ? '현재 사용 중인 기기는 삭제할 수 없습니다' : '기기 등록 해제'}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-danger-light hover:text-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-text-tertiary"
                    >
                      {removing === device.device_id ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <Trash2 size={15} />
                      )}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function deviceReasonToMessage(reason?: string): string | null {
  switch (reason) {
    case 'no_license':
      return '등록된 라이센스가 없습니다.'
    case 'cannot_remove_self':
      return '현재 사용 중인 기기는 삭제할 수 없습니다.'
    case 'network_error':
      return '서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.'
    case 'device_mismatch':
      return '이 기기에서 사용할 수 없는 라이센스입니다.'
    default:
      return reason ? `요청에 실패했습니다. (${reason})` : null
  }
}

export function SettingsModal() {
  const { activeModal, closeModal, theme, setTheme, modalData } = useUIStore()
  const {
    textSize,
    setTextSize,
    usageHistory,
    prepaidInitialAmount,
    prepaidBalance,
    rechargeDate,
    rechargeBalance,
    setPrepaidInitialAmount,
    resetPrepaidBalance,
    resetUsageOnly,
    openaiApiKey,
    setOpenaiApiKey,
    clearOpenaiApiKey,
    openaiDefaultModel,
    openaiEmbeddingModel,
    setOpenaiDefaultModel,
    setOpenaiEmbeddingModel,
    connectionMode,
    setConnectionMode,
    diagnosticConsent,
    setDiagnosticConsent
  } = useSettingsStore()
  const [activeTab, setActiveTab] = useState<SettingsTab>('general')
  const [activeAiTab, setActiveAiTab] = useState<AiTab>('chat')
  const [apiKeyInput, setApiKeyInput] = useState('')
  const isOpen = activeModal === 'settings'
  const appVersion = (() => {
    try {
      return window.electronAPI?.getVersion?.() || '1.0.0'
    } catch {
      return '1.0.0'
    }
  })()

  // 충전 이후 사용 기록 (rechargeDate 기준)
  const currentChargeHistory = rechargeDate
    ? usageHistory.filter(record => record.date >= rechargeDate)
    : usageHistory

  const totalTokens = currentChargeHistory.reduce((total, record) => total + record.tokensUsed, 0)
  const usedAmount = prepaidInitialAmount - prepaidBalance
  const percentRemaining = prepaidInitialAmount > 0 ? (prepaidBalance / prepaidInitialAmount) * 100 : 0
  const formatCurrency = (value: number) => {
    const currency = 'USD'
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
      }).format(value)
    } catch {
      return `${value.toFixed(2)} ${currency}`
    }
  }

  const handleClose = () => {
    closeModal()
  }


  useEffect(() => {
    if (!isOpen) return
    const requestedTab = (modalData?.tab as SettingsTab) ?? 'general'
    setActiveTab(requestedTab)
    if (requestedTab === 'ai') {
      const requestedAiTab = (modalData?.aiTab as AiTab) ?? 'chat'
      setActiveAiTab(requestedAiTab)
    } else {
      setActiveAiTab('chat')
    }
    setApiKeyInput('')
  }, [isOpen, modalData])


  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose()
      }
    }

    if (isOpen) {
      window.addEventListener('keydown', handleEscape)
      return () => window.removeEventListener('keydown', handleEscape)
    }
  }, [isOpen, handleClose])

  const handleThemeChange = (newTheme: 'light' | 'dark') => {
    setTheme(newTheme)
  }

  const handleTextSizeChange = (delta: number) => {
    setTextSize(textSize + delta)
  }

  const handleResetTextSize = () => {
    setTextSize(16)
  }

  const handleSaveApiKey = () => {
    const trimmed = apiKeyInput.trim()
    if (!trimmed) {
      alert('API 키를 입력해주세요.')
      return
    }
    setOpenaiApiKey(trimmed)
    setApiKeyInput('')
  }

  const handleClearApiKey = () => {
    if (!openaiApiKey) return
    const confirmed = window.confirm('저장된 API 키를 삭제할까요?')
    if (!confirmed) return
    clearOpenaiApiKey()
  }


  if (!isOpen) return null

  const tabs: { id: SettingsTab; label: string; icon: React.ReactNode }[] = [
    { id: 'general', label: '일반', icon: <Settings size={20} /> },
    { id: 'license', label: '라이센스', icon: <ShieldCheck size={20} /> },
    { id: 'ai', label: '모델 및 AI', icon: <Sparkles size={20} /> },
    { id: 'usage', label: '사용량', icon: <BarChart3 size={20} /> },
  ]

  return (
    <>
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity"
        onClick={handleClose}
      />

      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="w-full max-w-4xl rounded-2xl shadow-2xl overflow-hidden animate-scale-up flex h-[600px]"
          style={{ backgroundColor: theme === 'dark' ? '#1F1D1B' : '#ffffff' }}
        >
          {/* Sidebar */}
          <div className="w-64 bg-bg-secondary p-4 flex flex-col gap-1">
            <h2 className="px-3 py-2 text-lg font-serif font-medium text-text mb-4">설정</h2>

            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full px-3 py-2.5 rounded-xl flex items-center gap-3 transition-colors ${activeTab === tab.id
                  ? 'bg-bg-tertiary text-accent font-medium'
                  : 'text-text-secondary hover:bg-bg-tertiary hover:text-text'
                  }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}

            <div className="mt-auto px-3 text-xs text-text-tertiary">
              v{appVersion.replace(/-beta(?:\.\d+)?$/i, '')}
            </div>
          </div>
          {/* Main Content */}
          <div className="flex-1 flex flex-col min-w-0 bg-bg">
            {/* Header */}
            <div className="h-16 flex items-center justify-between px-6">
              <h3 className="text-lg font-semibold text-text">
                {tabs.find(t => t.id === activeTab)?.label}
              </h3>
              <button
                onClick={handleClose}
                className="p-1 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
                title="닫기"
              >
                <X size={24} />
              </button>
            </div>

            {/* Scrollable Content */}
            <div className="flex-1 overflow-y-auto p-8">
              {activeTab === 'general' && (
                <div className="space-y-8 max-w-2xl">
                  {/* Theme */}
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">테마</h3>
                    <div className="grid grid-cols-2 gap-4">
                      <button
                        onClick={() => handleThemeChange('light')}
                        className={`p-4 rounded-xl border flex items-center gap-3 transition-all ${theme === 'light'
                          ? 'border-accent bg-accent/5 ring-1 ring-accent/20'
                          : 'border-transparent bg-bg-secondary hover:bg-bg-tertiary'
                          }`}
                      >
                        <div className={`p-2 rounded-lg ${theme === 'light' ? 'bg-accent text-white' : 'bg-bg text-text-secondary'}`}>
                          <Sun size={20} />
                        </div>
                        <div className="text-left">
                          <div className={`font-medium ${theme === 'light' ? 'text-accent' : 'text-text'}`}>라이트 모드</div>
                          <div className="text-xs text-text-tertiary">밝고 깨끗한 화면</div>
                        </div>
                      </button>

                      <button
                        onClick={() => handleThemeChange('dark')}
                        className={`p-4 rounded-xl border flex items-center gap-3 transition-all ${theme === 'dark'
                          ? 'border-accent bg-accent/5 ring-1 ring-accent/20'
                          : 'border-transparent bg-bg-secondary hover:bg-bg-tertiary'
                          }`}
                      >
                        <div className={`p-2 rounded-lg ${theme === 'dark' ? 'bg-accent text-white' : 'bg-bg text-text-secondary'}`}>
                          <Moon size={20} />
                        </div>
                        <div className="text-left">
                          <div className={`font-medium ${theme === 'dark' ? 'text-accent' : 'text-text'}`}>다크 모드</div>
                          <div className="text-xs text-text-tertiary">눈이 편안한 화면</div>
                        </div>
                      </button>
                    </div>
                  </section>

                  {/* Text Size */}
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">글자 크기</h3>
                    <div className="flex items-center gap-4 bg-bg-secondary p-4 rounded-xl border border-transparent">
                      <button
                        onClick={() => handleTextSizeChange(-1)}
                        className="p-2 rounded-lg bg-bg border border-transparent text-text hover:bg-bg-tertiary transition-colors shadow-sm"
                      >
                        <Minus size={20} />
                      </button>

                      <div className="flex-1 text-center font-medium text-text text-lg">
                        {textSize}px
                      </div>

                      <button
                        onClick={() => handleTextSizeChange(1)}
                        className="p-2 rounded-lg bg-bg border border-transparent text-text hover:bg-bg-tertiary transition-colors shadow-sm"
                      >
                        <Plus size={20} />
                      </button>

                      <div className="w-px h-8 bg-border mx-2" />

                      <button
                        onClick={handleResetTextSize}
                        className="px-3 py-2 rounded-lg text-sm bg-bg border border-transparent text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors flex items-center gap-2 shadow-sm"
                      >
                        <RotateCcw size={14} />
                        초기화
                      </button>
                    </div>
                    <div className="mt-4 p-4 rounded-xl bg-bg-tertiary border border-transparent">
                      <p style={{ fontSize: `${textSize}px` }} className="text-text transition-all duration-200">
                        글자 크기 미리보기입니다. Inserty는 사용자의 편안한 독서 환경을 지원합니다.
                      </p>
                    </div>
                  </section>

                  {/* Diagnostic Data Consent */}
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">개인정보</h3>
                    <div className="flex items-center justify-between gap-4 bg-bg-secondary p-5 rounded-xl border border-transparent">
                      <div className="min-w-0">
                        <div className="font-medium text-text">익명 진단 데이터 제공 동의</div>
                        <div className="text-xs text-text-tertiary mt-1">
                          편집 품질 개선을 위해 식별 정보를 제외한 익명 진단 데이터를 전송합니다. 언제든지 끌 수 있습니다.
                        </div>
                      </div>
                      <div className="inline-flex p-1 bg-bg rounded-xl border border-transparent shrink-0">
                        <button
                          onClick={() => setDiagnosticConsent(false)}
                          className={`px-4 py-2 text-sm rounded-lg transition-colors ${!diagnosticConsent ? 'bg-bg-secondary text-text shadow-sm font-medium' : 'text-text-tertiary hover:text-text'}`}
                        >
                          끄기
                        </button>
                        <button
                          onClick={() => setDiagnosticConsent(true)}
                          className={`px-4 py-2 text-sm rounded-lg transition-colors ${diagnosticConsent ? 'bg-bg-secondary text-text shadow-sm font-medium' : 'text-text-tertiary hover:text-text'}`}
                        >
                          켜기
                        </button>
                      </div>
                    </div>
                  </section>
                </div>
              )}

              {activeTab === 'license' && <LicenseSettingsPanel />}

              {activeTab === 'ai' && (
                <div className="space-y-8 max-w-2xl">
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">연결 모드</h3>
                    <div className="bg-bg-secondary p-5 rounded-xl border border-transparent space-y-3">
                      {CODEX_ONLY_MODE ? (
                        <>
                          <p className="text-sm text-text-secondary"><strong>Codex CLI 모드</strong> — ChatGPT 구독 계정으로 로그인하시면 별도 API 결제 없이 사용 가능합니다.</p>
                          <CodexStatusPanel />
                        </>
                      ) : (
                        <>
                          <p className="text-sm text-text-secondary">API 키 또는 Codex CLI 인증으로 연결할 수 있습니다.</p>
                          <div className="inline-flex p-1 bg-bg rounded-xl border border-transparent">
                            <button
                              onClick={() => setConnectionMode('api')}
                              className={`px-5 py-2 text-sm rounded-lg transition-colors ${connectionMode === 'api' ? 'bg-bg-secondary text-text shadow-sm font-medium' : 'text-text-tertiary hover:text-text'}`}
                            >
                              API Key
                            </button>
                            <button
                              onClick={() => setConnectionMode('codex')}
                              className={`px-5 py-2 text-sm rounded-lg transition-colors ${connectionMode === 'codex' ? 'bg-bg-secondary text-text shadow-sm font-medium' : 'text-text-tertiary hover:text-text'}`}
                            >
                              Codex CLI
                            </button>
                          </div>
                          {connectionMode === 'codex' && <CodexStatusPanel />}
                        </>
                      )}
                    </div>
                  </section>

                  {!CODEX_ONLY_MODE && (
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">OpenAI API Key</h3>
                    <div className="bg-bg-secondary p-5 rounded-xl border border-transparent space-y-4">
                      <div className="flex items-center justify-between">
                        <p className="text-sm text-text-secondary leading-relaxed">
                          개인 API Key가 반드시 필요합니다. 키는 로컬 DB에만 저장됩니다.
                        </p>
                        <div className={`text-xs font-semibold px-2.5 py-1 rounded-full ${openaiApiKey ? 'bg-green-100 text-green-700 border border-green-200' : 'bg-yellow-100 text-yellow-700 border border-yellow-200'}`}>
                          {openaiApiKey ? '등록됨' : '미등록'}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <div className="relative flex-1">
                          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary">
                            <Key size={16} />
                          </div>
                          <input
                            type="password"
                            value={apiKeyInput}
                            onChange={(e) => setApiKeyInput(e.target.value)}
                            placeholder="sk-..."
                            className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-bg border border-transparent text-sm text-text focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all shadow-sm"
                          />
                        </div>
                        <button
                          onClick={handleSaveApiKey}
                          className="px-5 py-2.5 bg-text text-bg rounded-lg text-sm font-medium hover:bg-text/90 transition-colors disabled:opacity-50"
                          disabled={!apiKeyInput.trim()}
                        >
                          저장
                        </button>
                        {openaiApiKey && (
                          <button
                            onClick={handleClearApiKey}
                            className="px-4 py-2.5 bg-bg border border-border rounded-lg text-sm font-medium text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors"
                          >
                            삭제
                          </button>
                        )}
                      </div>
                    </div>
                  </section>
                  )}

                  {!CODEX_ONLY_MODE && (
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">모델 선택</h3>
                    <div className="inline-flex p-1 bg-bg-secondary rounded-xl border border-transparent">
                      <button
                        onClick={() => setActiveAiTab('chat')}
                        className={`px-4 py-2 text-sm rounded-lg transition-colors ${activeAiTab === 'chat' ? 'bg-bg text-text shadow-sm' : 'text-text-tertiary hover:text-text'}`}
                      >
                        기본 모델
                      </button>
                      <button
                        onClick={() => setActiveAiTab('embedding')}
                        className={`px-4 py-2 text-sm rounded-lg transition-colors ${activeAiTab === 'embedding' ? 'bg-bg text-text shadow-sm' : 'text-text-tertiary hover:text-text'}`}
                      >
                        임베딩 모델 (RAG Store)
                      </button>
                    </div>
                    <p className="mt-3 text-xs text-text-tertiary">
                      파일서치 모델은 기본 모델과 동일하게 사용됩니다.
                    </p>
                    <p className="mt-1 text-xs text-text-tertiary">
                      임베딩 모델은 RAG 스토어 인덱싱에 적용됩니다.
                    </p>

                    <div className="mt-4 space-y-3">
                      {(activeAiTab === 'chat' ? openAiModels.chatModels : openAiModels.embeddingModels).map((model) => {
                        const isSelected = activeAiTab === 'chat'
                          ? model.id === openaiDefaultModel
                          : model.id === openaiEmbeddingModel
                        return (
                          <button
                            key={`${activeAiTab}-${model.id}`}
                            onClick={() => {
                              if (activeAiTab === 'chat') {
                                setOpenaiDefaultModel(model.id)
                              } else {
                                setOpenaiEmbeddingModel(model.id)
                              }
                            }}
                            className={`w-full text-left flex items-center gap-4 p-4 rounded-xl border transition-all ${isSelected
                              ? 'border-accent bg-accent/5'
                              : 'border-transparent bg-bg-secondary hover:bg-bg-tertiary'
                              }`}
                          >
                            <div className={`w-5 h-5 rounded-full border-2 ${isSelected ? 'border-accent bg-accent/20' : 'border-text-tertiary'}`} />
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-text">{model.label}</span>
                                {activeAiTab === 'chat' && model.id === openAiModels.defaultModel && (
                                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-accent text-white uppercase">Default</span>
                                )}
                                {activeAiTab === 'embedding' && model.id === openAiModels.defaultEmbeddingModel && (
                                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-accent text-white uppercase">Default</span>
                                )}
                              </div>
                              <div className="text-xs text-text-secondary mt-1">{model.description}</div>
                              {activeAiTab === 'chat' ? (
                                <div className="text-xs text-text-tertiary mt-2">
                                  입력 {formatPrice(model.pricing.input)} / 1M · 출력 {formatPrice((model.pricing as { output?: number }).output ?? 0)} / 1M
                                </div>
                              ) : (
                                <div className="text-xs text-text-tertiary mt-2">
                                  입력 {formatPrice(model.pricing.input)} / 1M
                                </div>
                              )}
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </section>
                  )}
                </div>
              )}


              {activeTab === 'usage' && (
                <div className="space-y-8 max-w-2xl">
                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">충전 잔액 현황</h3>
                    <div className="p-6 bg-bg-secondary rounded-xl border border-transparent">
                      <div className="flex items-end justify-between mb-4">
                        <div>
                          <span className="text-4xl font-bold text-text">{formatCurrency(prepaidBalance)}</span>
                          <span className="text-text-tertiary ml-2 font-medium">남은 잔액(예상)</span>
                        </div>
                        <div className="text-sm font-medium text-accent bg-accent/10 px-3 py-1 rounded-full">
                          {prepaidInitialAmount > 0 ? `${percentRemaining.toFixed(1)}% 남음` : '충전 금액 미설정'}
                        </div>
                      </div>

                      <div className="h-3 bg-bg-tertiary rounded-full overflow-hidden mb-2">
                        <div
                          className="h-full bg-accent transition-all duration-1000 ease-out"
                          style={{ width: `${Math.min(percentRemaining, 100)}%` }}
                        />
                      </div>
                      <div className="flex justify-between text-xs text-text-tertiary">
                        <span>{formatCurrency(0)}</span>
                        <span>
                          {prepaidInitialAmount > 0
                            ? `충전 금액: ${formatCurrency(prepaidInitialAmount)}`
                            : '충전 금액을 설정하세요'}
                        </span>
                      </div>

                      <div className="mt-3 text-xs text-text-tertiary">
                        사용 금액: {formatCurrency(usedAmount)}
                      </div>
                      <div className="mt-1 text-xs text-text-tertiary">
                        {rechargeDate
                          ? `충전일: ${new Date(rechargeDate).toLocaleDateString('ko-KR')}`
                          : '충전 기록 없음'}
                      </div>
                      <div className="mt-1 text-xs text-text-tertiary">
                        토큰 사용량: {totalTokens.toLocaleString()} tokens
                      </div>

                      <div className="mt-4 grid grid-cols-3 gap-2">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault()
                            console.log('[Settings] 새로 충전 버튼 클릭')
                            const amount = prompt('충전 금액을 입력하세요 (USD):', prepaidInitialAmount.toString())
                            if (amount !== null) {
                              const parsed = parseFloat(amount)
                              if (!isNaN(parsed) && parsed >= 0) {
                                console.log('[Settings] rechargeBalance 호출:', parsed)
                                rechargeBalance(parsed)
                              } else {
                                alert('올바른 금액을 입력하세요.')
                              }
                            }
                          }}
                          className="px-4 py-2 bg-accent hover:bg-accent/90 active:scale-95 text-white text-sm font-medium rounded-lg transition-all duration-200"
                        >
                          새로 충전
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault()
                            console.log('[Settings] 사용량 리셋 버튼 클릭')
                            if (confirm('사용량을 리셋하시겠습니까?\n\n잔액이 충전 금액으로 복구되지만, 최근 활동 기록은 유지됩니다.')) {
                              console.log('[Settings] resetUsageOnly 호출')
                              resetUsageOnly()
                            }
                          }}
                          className="px-4 py-2 bg-bg-tertiary hover:bg-bg text-text-secondary hover:text-text active:scale-95 text-sm font-medium rounded-lg transition-all duration-200"
                        >
                          사용량 리셋
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault()
                            console.log('[Settings] 모두 초기화 버튼 클릭')
                            if (confirm('모든 충전 정보를 초기화하시겠습니까?\n\n충전 금액, 잔액, 날짜가 모두 초기화됩니다. (최근 활동 기록은 유지)')) {
                              console.log('[Settings] resetPrepaidBalance 호출')
                              resetPrepaidBalance()
                            }
                          }}
                          className="px-4 py-2 bg-bg-tertiary hover:bg-bg text-text-secondary hover:text-text active:scale-95 text-sm font-medium rounded-lg transition-all duration-200"
                        >
                          모두 초기화
                        </button>
                      </div>
                    </div>
                  </section>

                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">충전 금액 설정</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="p-4 bg-bg-secondary rounded-xl border border-transparent">
                        <label className="text-xs text-text-tertiary uppercase tracking-wider">충전 금액 (USD)</label>
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={Number.isFinite(prepaidInitialAmount) ? prepaidInitialAmount : 0}
                          onChange={(e) => setPrepaidInitialAmount(Number(e.target.value) || 0)}
                          className="mt-2 w-full px-3 py-2 rounded-lg bg-bg border border-transparent text-sm text-text focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all"
                        />
                      </div>

                      <div className="p-4 bg-bg-secondary rounded-xl border border-transparent">
                        <label className="text-xs text-text-tertiary uppercase tracking-wider">통화</label>
                        <div className="mt-2 px-3 py-2 rounded-lg bg-bg border border-transparent text-sm text-text">
                          USD (고정)
                        </div>
                      </div>
                    </div>
                    <p className="mt-3 text-xs text-text-tertiary">
                      OpenAI Prepaid Credits 방식: 충전한 금액만큼 사용 가능합니다.
                    </p>
                  </section>

                  <section>
                    <h3 className="text-sm font-semibold text-text mb-4 uppercase tracking-wider">최근 활동</h3>
                    <div className="border border-transparent rounded-xl bg-bg overflow-hidden shadow-sm">
                      {usageHistory.length === 0 ? (
                        <div className="p-12 text-center text-text-tertiary text-sm flex flex-col items-center gap-3">
                          <BarChart3 size={32} className="opacity-50" />
                          <p>사용 기록이 없습니다.</p>
                        </div>
                      ) : (
                        <div className="divide-y divide-border">
                          {[...usageHistory].reverse().map((record) => {
                            const isFileSearch = record.eventType === 'rag_indexing'
                            const recordCost = typeof record.costUsd === 'number' ? record.costUsd : null

                            // File Search: 스토리지 비용 표시, 일반: 토큰 또는 비용 표시
                            const amountLabel = isFileSearch
                              ? (recordCost !== null ? `-${formatCurrency(recordCost)}/day` : 'File Search')
                              : (recordCost !== null
                                  ? `-${formatCurrency(recordCost)}`
                                  : `-${record.tokensUsed.toLocaleString()} 토큰`)

                            // 상세 정보: File Search는 바이트, 일반은 토큰
                            const usageBytes = record.eventData?.usageBytes as number | undefined
                            const detailLabel = isFileSearch
                              ? `스토리지: ${usageBytes ? (usageBytes / 1024).toFixed(1) : 0} KB`
                              : `${record.tokensUsed.toLocaleString()} 토큰`

                            return (
                              <div key={record.id} className="p-4 flex items-center justify-between hover:bg-bg-secondary/50 transition-colors">
                                <div>
                                  <div className="font-medium text-text text-sm">{record.feature}</div>
                                  <div className="text-xs text-text-tertiary">
                                    {formatDate(record.date)} · {detailLabel}
                                  </div>
                                </div>
                                <div className="font-mono text-sm text-text-secondary">
                                  {amountLabel}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </section>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

// 라이센스 게이트 — 앱 시작 시 표시되는 4가지 차단 화면 + 키 입력 화면
// 상태에 따라 적절한 모달 표시

import React, { useEffect, useState } from 'react'
import { KakaoSupportLink } from './KakaoSupportLink'

export type LicenseState =
  | { state: 'loading' }
  | { state: 'ok'; expires_at: string | null }
  | { state: 'expired'; license_key: string | null }
  | { state: 'leaked'; license_key: string | null; device_count?: number }
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
  // 정상 상태면 자식 컴포넌트 표시는 호출자가 처리. 여기는 차단 화면만.
  switch (status.state) {
    case 'loading':
      return <LoadingScreen />
    case 'ok':
    case 'offline_grace':
      return null  // 메인 UI 진입 (호출자가 처리)
    case 'expired':
      return <ExpiredModal licenseKey={status.license_key} />
    case 'leaked':
      return (
        <LeakedModal
          licenseKey={status.license_key}
          deviceCount={status.device_count}
        />
      )
    case 'revoked':
      return <RevokedModal licenseKey={status.license_key} />
    case 'invalid':
      return <InvalidModal reason={status.reason} onActivate={onActivate} />
    case 'no_license':
      return <KeyInputScreen onActivate={onActivate} />
    case 'offline_blocked':
      return <OfflineBlockedModal onRetry={onRetry} />
  }
}

// ====================================================================
// 로딩
// ====================================================================
function LoadingScreen() {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-white">
      <div className="text-gray-600">라이센스 확인 중...</div>
    </div>
  )
}

// ====================================================================
// 키 입력 (첫 실행 + invalid)
// ====================================================================
function KeyInputScreen({
  onActivate,
}: {
  onActivate: (k: string) => Promise<void>
}) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    setError(null)
    setBusy(true)
    try {
      await onActivate(key)
    } catch (e: any) {
      setError(e?.message || '활성화 실패')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-white">
      <div className="w-[420px] rounded-xl border border-gray-200 bg-white p-8 shadow-lg">
        <h1 className="mb-1 text-2xl font-semibold">라이센스 키 입력</h1>
        <p className="mb-6 text-sm text-gray-600">
          결제 후 발송된 라이센스 키를 입력하세요.
        </p>
        <input
          type="text"
          value={key}
          onChange={(e) => setKey(e.target.value.toUpperCase())}
          placeholder="INSRT-XXXX-XXXX-XXXX-XXXX"
          maxLength={24}
          className="mb-3 w-full rounded-md border border-gray-300 px-3 py-2 font-mono uppercase focus:border-blue-500 focus:outline-none"
        />
        {error && (
          <div className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
        <button
          type="button"
          onClick={handleSubmit}
          disabled={busy || key.length !== 24}
          className="mb-4 w-full rounded-md bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '활성화 중...' : '활성화'}
        </button>
        <div className="border-t pt-4 text-center">
          <p className="mb-3 text-sm text-gray-600">키가 없으시거나 도움이 필요하신가요?</p>
          <KakaoSupportLink>카카오톡 문의</KakaoSupportLink>
        </div>
      </div>
    </div>
  )
}

// ====================================================================
// 만료
// ====================================================================
function ExpiredModal({ licenseKey }: { licenseKey: string | null }) {
  return (
    <BlockedModal
      icon="⚠"
      title="라이센스가 만료되었습니다"
      message="갱신을 원하시면 아래로 문의해주세요."
      licenseKey={licenseKey}
      severity="warning"
    />
  )
}

// ====================================================================
// 유출 의심
// ====================================================================
function LeakedModal({
  licenseKey,
  deviceCount,
}: {
  licenseKey: string | null
  deviceCount?: number
}) {
  return (
    <BlockedModal
      icon="🚨"
      title="비정상 사용 감지"
      message={`여러 기기에서 동일 라이센스 키 사용이 감지되어 잠겼습니다.${
        deviceCount ? ` (감지된 디바이스: ${deviceCount}대)` : ''
      }\n본인 사용이라면 문의해주세요.`}
      licenseKey={licenseKey}
      severity="danger"
    />
  )
}

// ====================================================================
// 무효화 (환불)
// ====================================================================
function RevokedModal({ licenseKey }: { licenseKey: string | null }) {
  return (
    <BlockedModal
      icon="🛑"
      title="라이센스가 무효화되었습니다"
      message="라이센스가 비활성화되었습니다. 문의가 필요하면 아래로 연락해주세요."
      licenseKey={licenseKey}
      severity="warning"
    />
  )
}

// ====================================================================
// 잘못된 키
// ====================================================================
function InvalidModal({
  reason,
  onActivate,
}: {
  reason?: string
  onActivate: (k: string) => Promise<void>
}) {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-white">
      <div className="w-[420px] rounded-xl border border-red-200 bg-white p-8 shadow-lg">
        <h1 className="mb-2 text-xl font-semibold text-red-700">잘못된 라이센스 키</h1>
        <p className="mb-4 text-sm text-gray-600">
          {reason ? `사유: ${reason}` : '키를 다시 확인해주세요.'}
        </p>
        <KeyInputScreen onActivate={onActivate} />
      </div>
    </div>
  )
}

// ====================================================================
// 오프라인 차단
// ====================================================================
function OfflineBlockedModal({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-white">
      <div className="w-[420px] rounded-xl border border-gray-200 bg-white p-8 shadow-lg">
        <h1 className="mb-2 text-xl font-semibold">네트워크 연결 필요</h1>
        <p className="mb-4 text-sm text-gray-600">
          7일 이상 오프라인 상태입니다. 네트워크 연결 후 다시 시도해주세요.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="w-full rounded-md bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700"
        >
          다시 시도
        </button>
      </div>
    </div>
  )
}

// ====================================================================
// 공통 차단 모달
// ====================================================================
function BlockedModal({
  icon,
  title,
  message,
  licenseKey,
  severity,
}: {
  icon: string
  title: string
  message: string
  licenseKey: string | null
  severity: 'warning' | 'danger'
}) {
  const accent =
    severity === 'danger' ? 'border-red-300 bg-red-50' : 'border-yellow-300 bg-yellow-50'

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-white">
      <div className={`w-[480px] rounded-xl border-2 p-8 shadow-lg ${accent}`}>
        <div className="mb-3 text-4xl">{icon}</div>
        <h1 className="mb-2 text-xl font-semibold">{title}</h1>
        <p className="mb-4 whitespace-pre-line text-sm text-gray-700">{message}</p>
        {licenseKey && (
          <div className="mb-4 rounded bg-white/60 px-3 py-2 text-xs font-mono text-gray-600">
            현재 키: {licenseKey}
          </div>
        )}
        <KakaoSupportLink className="w-full justify-center">
          카카오톡으로 문의하기
        </KakaoSupportLink>
      </div>
    </div>
  )
}

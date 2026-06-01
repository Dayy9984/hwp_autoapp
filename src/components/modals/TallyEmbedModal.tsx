// Tally 폼 iframe 모달.
// license_key 를 URL hidden field 로 주입하여 응답과 사용자 연결.

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { TALLY_FORMS } from '../../config/beta'
import { track } from '../../lib/telemetry'

export type TallyFormKey = keyof typeof TALLY_FORMS

interface Props {
  formKey: TallyFormKey
  title: string
  onClose: () => void
}

export function TallyEmbedModal({ formKey, title, onClose }: Props) {
  const [licenseKey, setLicenseKey] = useState('')
  // mount 시 1회 timestamp — 매번 모달 열 때마다 새 값 → Tally 가 이전 partial 응답을 재로드하지 않음.
  const [mountKey] = useState(() => Date.now())
  // 매 mount 마다 새 random suffix — license_key 와 결합해 Tally 가 다른 사용자로 인식.
  // Worker 측에서 `__` 이후 제거하여 원본 license_key 로 정규화.
  const [sessionSuffix] = useState(() => Math.random().toString(36).slice(2, 10))

  useEffect(() => {
    let alive = true
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    // Tally 도메인 storage clear — fire-and-forget (iframe 마운트 막지 않음 → 깜박거림 없음).
    try { api?.tally?.clearStorage?.() } catch {}
    api?.license?.getCachedKey?.().then((k: string | null) => {
      if (alive && k) setLicenseKey(k)
    })
    track('tally_form_opened', { form: formKey })
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { alive = false; window.removeEventListener('keydown', onKey) }
  }, [formKey, onClose])

  const baseUrl = TALLY_FORMS[formKey]
  // license_key 에 매번 새 suffix 추가 → Tally 가 다른 사용자로 인식 → server-side partial draft 잔존 회피.
  // Worker tally-webhook.ts 가 `__` 이후 제거 후 D1 form_responses 에 원본 license_key 로 저장.
  const decoratedKey = licenseKey ? `${licenseKey}__${mountKey}-${sessionSuffix}` : ''
  const params = new URLSearchParams()
  if (decoratedKey) params.set('license_key', decoratedKey)
  params.set('_t', String(mountKey))
  params.set('autoSave', '0')
  params.set('dontPrefill', '1')
  const sep = baseUrl.includes('?') ? '&' : '?'
  const url = `${baseUrl}${sep}${params.toString()}`

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      // backdrop click 으로 닫지 않음 — 응답 작성 중 무심코 밖 클릭해서 입력 사라지는 것 방지.
      // X 버튼 또는 ESC 로만 닫힘.
    >
      <div
        className="bg-white dark:bg-bg rounded-2xl shadow-2xl w-[min(800px,94vw)] h-[min(760px,92vh)] flex flex-col overflow-hidden"
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-bg-tertiary">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-text-tertiary hover:text-text hover:bg-bg-secondary transition-colors"
            aria-label="닫기"
          >
            <X size={18} />
          </button>
        </div>
        <iframe
          key={mountKey}
          src={url}
          className="flex-1 w-full border-0"
          title={title}
          allow="clipboard-write"
        />
      </div>
    </div>
  )
}

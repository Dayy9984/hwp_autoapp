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

  useEffect(() => {
    let alive = true
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    api?.license?.getCachedKey?.().then((k: string | null) => {
      if (alive && k) setLicenseKey(k)
    })
    track('tally_form_opened', { form: formKey })
    return () => { alive = false }
  }, [formKey])

  const baseUrl = TALLY_FORMS[formKey]
  // Tally embed 은 ?변수명=값 으로 hidden field 채움 가능
  const url = licenseKey
    ? `${baseUrl}&license_key=${encodeURIComponent(licenseKey)}`
    : baseUrl

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-bg rounded-2xl shadow-2xl w-[min(640px,92vw)] h-[min(720px,90vh)] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
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
          src={url}
          className="flex-1 w-full border-0"
          title={title}
          allow="clipboard-write"
        />
      </div>
    </div>
  )
}

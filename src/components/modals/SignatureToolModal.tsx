import { useRef, useEffect } from 'react'
import { useToolStore } from '../../stores/tool-store'
import { useUIStore } from '../../stores/ui-store'
import { PenIcon } from '../icons'
import { X } from 'lucide-react'

export function SignatureToolModal() {
  const { signatures, addSignature } = useToolStore()
  const { activeModal, closeModal, openPopover } = useUIStore()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isOpen = activeModal === 'signature-tool'

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeModal()
      }
    }

    if (isOpen) {
      window.addEventListener('keydown', handleEscape)
      return () => window.removeEventListener('keydown', handleEscape)
    }
  }, [isOpen, closeModal])

  if (!isOpen) return null

  const handleAddClick = () => {
    fileInputRef.current?.click()
  }

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !file.type.startsWith('image/')) return

    const reader = new FileReader()
    reader.onload = (e) => {
      const imageData = e.target?.result as string
      const fileName = file.name.replace(/\.[^/.]+$/, '')
      const name = fileName || '내 서명'
      addSignature(name, imageData)
    }
    reader.readAsDataURL(file)

    // Reset input
    event.target.value = ''
  }

  const handleSignatureClick = (signatureId: string) => {
    console.log('문서에 삽입:', signatureId)
  }

  const handleOptionsClick = (event: React.MouseEvent, signatureId: string) => {
    event.stopPropagation()

    // Calculate position relative to clicked button
    const button = event.currentTarget as HTMLElement
    const rect = button.getBoundingClientRect()

    // Position popover to the left of the button
    const position = {
      top: rect.top,
      left: rect.left - 210, // 200px popover width + 10px spacing
    }

    openPopover('signature-options', { signatureId, position })
  }

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      closeModal()
    }
  }

  return (
    <div
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
    >
      <div
        className="rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col border overflow-hidden animate-scale-up"
        style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0"
          style={{ borderColor: 'var(--border)' }}
        >
          <h2 className="text-lg font-serif font-bold" style={{ color: 'var(--text)' }}>서명/도장 도구</h2>
          <button
            onClick={closeModal}
            className="p-1.5 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {/* Add Button */}
          <button
            onClick={handleAddClick}
            className="w-full px-4 py-3 bg-accent hover:bg-accent-dark text-white font-medium rounded-xl transition-colors duration-200 shadow-sm hover:shadow-md mb-6"
          >
            + 서명/도장 추가
          </button>

          {/* Hidden File Input */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleFileUpload}
            className="hidden"
          />

          {/* Signature List */}
          <div className="space-y-3">
            {signatures.length === 0 ? (
              <div className="text-center py-12">
                <div className="mb-3 flex justify-center" style={{ color: 'var(--text-secondary)' }}>
                  <PenIcon size={48} />
                </div>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  아직 등록된 서명/도장이 없습니다.
                </p>
                <p className="text-xs mt-2" style={{ color: 'var(--text-tertiary)' }}>
                  위 버튼을 클릭하여 서명이나 도장 이미지를 추가하세요.
                </p>
              </div>
            ) : (
              signatures.map((signature) => (
                <div
                  key={signature.id}
                  onClick={() => handleSignatureClick(signature.id)}
                  className="flex items-center gap-4 p-4 rounded-xl cursor-pointer transition-all duration-200 group border"
                  style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                    e.currentTarget.style.borderColor = 'var(--accent)'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = 'var(--bg-secondary)'
                    e.currentTarget.style.borderColor = 'var(--border)'
                  }}
                >
                  {/* Thumbnail */}
                  <div className="flex-shrink-0 w-16 h-16 bg-white rounded-lg overflow-hidden flex items-center justify-center border" style={{ borderColor: 'var(--border)' }}>
                    {signature.imageData ? (
                      <img
                        src={signature.imageData}
                        alt={signature.name}
                        className="max-w-full max-h-full object-contain"
                      />
                    ) : (
                      <PenIcon size={24} style={{ color: 'var(--text-tertiary)' }} />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-medium mb-1 truncate" style={{ color: 'var(--text)' }}>
                      {signature.name}
                    </h3>
                    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                      {new Date(signature.createdAt).toLocaleDateString('ko-KR', {
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric',
                      })}
                    </p>
                  </div>

                  {/* Options Button */}
                  <button
                    onClick={(e) => handleOptionsClick(e, signature.id)}
                    className="flex-shrink-0 p-2 rounded-lg transition-colors duration-200 opacity-0 group-hover:opacity-100"
                    style={{ color: 'var(--text-secondary)' }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = 'var(--text)'
                      e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = 'var(--text-secondary)'
                      e.currentTarget.style.backgroundColor = 'transparent'
                    }}
                    aria-label="옵션"
                  >
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
                    </svg>
                  </button>
                </div>
              ))
            )}
          </div>

          {/* Info Box */}
          {signatures.length > 0 && (
            <div className="mt-6 p-4 bg-accent/5 border border-accent/20 rounded-lg">
              <div className="flex gap-3">
                <div className="flex-shrink-0 text-accent">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                      clipRule="evenodd"
                    />
                  </svg>
                </div>
                <div className="text-sm text-text-secondary">
                  <p className="font-medium mb-1 text-text">사용 안내</p>
                  <p className="text-xs">
                    추가된 서명/도장을 클릭시 연결된 문서에 서명/도장을 자동으로 추가합니다.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { X, AlertTriangle } from 'lucide-react'
import { useUIStore } from '../../stores/ui-store'
import { useSettingsStore } from '../../stores/settings-store'

export function PromptFullOverrideModal() {
  const { activeModal, closeModal } = useUIStore()
  const { promptFullOverride, setPromptFullOverride } = useSettingsStore()
  const [value, setValue] = useState(promptFullOverride)
  const [activeTab, setActiveTab] = useState<'custom' | 'default'>('custom')
  const [defaultPrompt, setDefaultPrompt] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const isOpen = activeModal === 'prompt-full'

  useEffect(() => {
    if (isOpen) {
      setValue(promptFullOverride)
      setActiveTab('custom')
    }
  }, [isOpen, promptFullOverride])

  // 기본 프롬프트 로드 (v7.11 고정)
  useEffect(() => {
    if (isOpen && activeTab === 'default') {
      setLoading(true)
      setDefaultPrompt('')
      window.electronAPI.invoke('llm:getDefaultSystemPrompt', { version: 'v7_11' })
        .then((result: any) => {
          if (result.success && result.data?.prompt) {
            setDefaultPrompt(result.data.prompt)
          } else {
            setDefaultPrompt('기본 프롬프트를 불러올 수 없습니다.')
          }
        })
        .catch((err: any) => {
          console.error('[PromptFullOverrideModal] Failed to load default prompt:', err)
          setDefaultPrompt('기본 프롬프트를 불러오는 중 오류가 발생했습니다.')
        })
        .finally(() => {
          setLoading(false)
        })
    }
  }, [isOpen, activeTab])

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

  const handleSave = () => {
    setPromptFullOverride(value.trim())
    closeModal()
  }

  const handleReset = () => {
    setValue('')
  }

  const handleOverlayClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      closeModal()
    }
  }

  return (
    <div
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
    >
      <div
        className="rounded-2xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col border overflow-hidden animate-scale-up"
        style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}
      >
        <div className="flex-shrink-0" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
            <h2 className="text-lg font-serif font-bold" style={{ color: 'var(--text)' }}>
              시스템 프롬프트 수정
            </h2>
            <button
              onClick={closeModal}
              className="p-1.5 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
            >
              <X size={20} />
            </button>
          </div>

          {/* Tabs */}
          <div className="flex border-b" style={{ borderColor: 'var(--border)' }}>
            <button
              onClick={() => setActiveTab('custom')}
              className={`flex-1 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === 'custom'
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-text-tertiary hover:text-text'
              }`}
            >
              사용자 정의
            </button>
            <button
              onClick={() => setActiveTab('default')}
              className={`flex-1 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === 'default'
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-text-tertiary hover:text-text'
              }`}
            >
              기본 프롬프트 보기
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {activeTab === 'custom' ? (
            <>
              <div className="flex gap-3 items-start p-4 rounded-xl bg-danger/5 border border-red-500/20">
                <AlertTriangle size={18} className="text-danger mt-0.5" />
                <div className="text-sm text-text-secondary">
                  <p className="font-semibold text-danger mb-1">주의</p>
                  <p className="text-xs text-text-tertiary">
                    이 설정은 전체 시스템 프롬프트를 교체합니다. 잘못된 수정은 결과 품질에 큰 영향을 줄 수 있습니다.
                  </p>
                </div>
              </div>

              {/* Current Status */}
              <div className="flex gap-3 items-start p-4 rounded-xl" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)', border: '1px solid' }}>
                <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  <p className="font-semibold mb-1" style={{ color: 'var(--text)' }}>현재 상태</p>
                  <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    {promptFullOverride.trim().length > 0
                      ? '사용자 정의 시스템 프롬프트를 사용 중입니다.'
                      : '기본 시스템 프롬프트를 사용 중입니다.'}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-xs font-semibold text-text-tertiary uppercase tracking-wider">
                  전체 시스템 프롬프트
                </label>
                <textarea
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  rows={12}
                  className="w-full rounded-xl bg-bg-secondary border border-transparent text-sm text-text p-4 focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all"
                  placeholder="전체 시스템 프롬프트를 입력하세요. 비워두면 기본 프롬프트를 사용합니다."
                />
                <p className="text-xs text-text-tertiary">
                  {value.trim().length > 0
                    ? `${value.trim().length}자 입력됨`
                    : '입력된 내용이 없습니다. 기본 프롬프트가 사용됩니다.'}
                </p>
              </div>
            </>
          ) : (
            <>
              <div className="flex gap-3 items-start p-4 rounded-xl" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)', border: '1px solid' }}>
                <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  <p className="font-semibold mb-1" style={{ color: 'var(--text)' }}>안내</p>
                  <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    현재 적용 중인 기본 시스템 프롬프트(v7.11)를 확인하고 수정 기준으로 활용할 수 있습니다.
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-xs font-semibold text-text-tertiary uppercase tracking-wider">
                  기본 시스템 프롬프트 (v7.11)
                </label>
                <textarea
                  value={loading ? '로딩 중...' : defaultPrompt}
                  readOnly
                  rows={12}
                  className="w-full rounded-xl bg-bg-secondary border border-transparent text-sm text-text p-4 outline-none cursor-default"
                  style={{ resize: 'vertical' }}
                />
                <p className="text-xs text-text-tertiary">
                  {loading ? '기본 프롬프트를 불러오는 중...' : `${defaultPrompt.length}자`}
                </p>
              </div>
            </>
          )}
        </div>

        <div
          className="flex items-center justify-between px-6 py-4 border-t"
          style={{ borderColor: 'var(--border)' }}
        >
          {activeTab === 'custom' ? (
            <>
              <button
                onClick={handleReset}
                className="px-4 py-2 text-sm rounded-lg bg-bg-secondary text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors flex items-center gap-2"
                title="기본 시스템 프롬프트로 복구"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                기본으로 복구
              </button>
              <div className="flex items-center gap-2">
                <button
                  onClick={closeModal}
                  className="px-4 py-2 text-sm rounded-lg bg-bg-secondary text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors"
                >
                  취소
                </button>
                <button
                  onClick={handleSave}
                  className="px-5 py-2 text-sm rounded-lg bg-accent text-white font-medium hover:bg-accent-dark transition-colors"
                >
                  저장
                </button>
              </div>
            </>
          ) : (
            <div className="flex-1 flex justify-between">
              <button
                onClick={() => {
                  if (defaultPrompt && !loading) {
                    setValue(defaultPrompt)
                    setPromptFullOverride(defaultPrompt)
                    setActiveTab('custom')
                  }
                }}
                disabled={loading || !defaultPrompt}
                className="px-4 py-2 text-sm rounded-lg bg-bg-secondary text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                title="v7.11 버전을 사용자 정의로 적용"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                </svg>
                이 버전 적용
              </button>
              <button
                onClick={closeModal}
                className="px-5 py-2 text-sm rounded-lg bg-accent text-white font-medium hover:bg-accent-dark transition-colors"
              >
                닫기
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

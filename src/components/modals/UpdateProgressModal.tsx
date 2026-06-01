// 업데이트 진행 모달 — 다운로드부터 설치 시작까지 단일 창에서 진행률 표시.
// 사용자 클릭은 "업데이트" 1회만; 이후 자동 진행.

import { useEffect, useState } from 'react'

interface ProgressInfo {
  total: number
  transferred: number
  percent: number
  bytesPerSecond: number
}

interface UpdateStatus {
  status: 'idle' | 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  progress?: ProgressInfo
  info?: { version: string }
  error?: string
}

interface Props {
  open: boolean
  onClose: () => void
  targetVersion?: string
}

function formatBytes(n: number): string {
  if (!n) return '0 MB'
  const mb = n / (1024 * 1024)
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`
  return `${mb.toFixed(1)} MB`
}

function formatSpeed(bps: number): string {
  if (!bps) return ''
  const mbps = (bps * 8) / (1024 * 1024)
  if (mbps >= 1) return `${mbps.toFixed(1)} Mbps`
  return `${(bps / 1024).toFixed(0)} KB/s`
}

export function UpdateProgressModal({ open, onClose, targetVersion }: Props) {
  const [status, setStatus] = useState<UpdateStatus>({ status: 'downloading' })
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const api = (window as any).electronAPI?.update
    if (!api) return

    // progress window 가 열리는 시점에 이미 'downloaded' 상태일 수 있음 (autoDownload 미리 완료).
    // 그 경우 onStatus 가 호출 안 될 수 있으므로 getLastStatus 로도 한 번 확인.
    api.getLastStatus?.().then((r: any) => {
      const last = r?.status
      if (last?.status === 'downloaded' && !installing) {
        setStatus(last)
        setInstalling(true)
        setTimeout(() => { try { api.install?.() } catch (e) { setError(String(e)) } }, 1500)
      }
    }).catch(() => {})

    if (!api.onStatus) return
    const unsubscribe = api.onStatus((s: UpdateStatus) => {
      setStatus(s)
      if (s.status === 'error') {
        setError(s.error || '업데이트 중 오류가 발생했습니다.')
      }
      if (s.status === 'downloaded' && !installing) {
        setInstalling(true)
        setTimeout(() => {
          try { api.install?.() } catch (e) { setError(String(e)) }
        }, 1500)
      }
    })
    return () => { unsubscribe?.() }
  }, [open, installing])

  if (!open) return null

  const percent = status.progress?.percent ?? 0
  const isDownloading = status.status === 'downloading'
  const isDownloaded = status.status === 'downloaded' || installing
  const isError = !!error

  let phaseText = '준비 중...'
  if (isError) phaseText = '오류 발생'
  else if (installing) phaseText = '설치를 시작합니다...'
  else if (isDownloaded) phaseText = '다운로드 완료'
  else if (isDownloading) phaseText = '다운로드 중'

  let subText = ''
  if (isError) subText = error || ''
  else if (installing) subText = '곧 앱이 종료되고 설치가 진행됩니다.'
  else if (isDownloaded) subText = '설치를 준비하고 있습니다.'
  else if (status.progress) {
    subText = `${formatBytes(status.progress.transferred)} / ${formatBytes(status.progress.total)} · ${formatSpeed(status.progress.bytesPerSecond)}`
  }

  return (
    <>
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 transition-opacity duration-200"
        aria-hidden="true"
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
        <div
          className="w-full max-w-md mx-4 pointer-events-auto animate-fade-in"
          role="dialog"
          aria-labelledby="update-progress-title"
        >
          <div
            className="rounded-2xl shadow-2xl border animate-slide-up overflow-hidden"
            style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--border)' }}
          >
            <div className="px-6 py-5 border-b" style={{ borderColor: 'var(--border)' }}>
              <div className="flex items-center gap-3">
                <div
                  className="flex h-10 w-10 items-center justify-center rounded-xl"
                  style={{ backgroundColor: 'var(--accent-light)' }}
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2}
                    stroke="var(--accent)" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round"
                      d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" />
                  </svg>
                </div>
                <div>
                  <h2 id="update-progress-title" className="text-base font-semibold text-text">
                    {isError ? '업데이트 실패' : `버전 ${targetVersion || ''} 업데이트`}
                  </h2>
                  <p className="text-xs text-text-tertiary mt-0.5">
                    {phaseText}
                  </p>
                </div>
              </div>
            </div>

            <div className="px-6 py-5">
              {!isError && (
                <>
                  <div className="relative h-2 rounded-full overflow-hidden"
                    style={{ backgroundColor: 'var(--bg-tertiary)' }}>
                    <div
                      className="absolute inset-y-0 left-0 transition-all duration-300 rounded-full"
                      style={{
                        width: isDownloaded || installing ? '100%' : `${percent}%`,
                        backgroundColor: 'var(--accent)',
                      }}
                    />
                  </div>
                  <div className="flex items-center justify-between mt-3">
                    <p className="text-xs text-text-secondary truncate">
                      {subText}
                    </p>
                    {isDownloading && (
                      <p className="text-xs font-medium text-accent ml-3 flex-shrink-0">
                        {percent.toFixed(0)}%
                      </p>
                    )}
                  </div>
                </>
              )}
              {isError && (
                <div className="rounded-lg bg-danger-light px-3 py-2.5">
                  <p className="text-xs text-danger">{subText}</p>
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t flex items-center justify-end"
              style={{ borderColor: 'var(--border)' }}>
              {isError ? (
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-text bg-bg-tertiary hover:bg-bg-secondary transition-colors"
                >
                  닫기
                </button>
              ) : (
                <p className="text-[11px] text-text-tertiary">
                  완료 시 앱이 자동으로 재시작됩니다.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

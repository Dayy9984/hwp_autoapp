import { useState, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useSettingsStore } from '../../stores/settings-store'

/**
 * Notification types
 */
export interface Notification {
  id: string
  type: 'version-update' | 'critical-update'
  title: string
  content: string
  imageUrl?: string // 알림 이미지 URL (R2에 업로드된 이미지)
  actionLabel?: string // "업데이트", "자세히 보기" 등
  actionUrl?: string // TODO: 미구현, UI만
  isCritical?: boolean // 필수 업데이트 - 닫기/다시보지않기 비활성화
}

interface NotificationModalProps {
  notification: Notification | null
  onClose: () => void
  onDontShowAgain?: (notificationId: string) => void
  onAction?: (notification: Notification) => void | Promise<void>
}

export function NotificationModal({
  notification,
  onClose,
  onDontShowAgain,
  onAction,
}: NotificationModalProps) {
  const { activeModal } = useUIStore()
  const { dismissNotification } = useSettingsStore()
  const [dontShowAgain, setDontShowAgain] = useState(false)

  const isOpen = activeModal === 'notification' && notification !== null

  const isCritical = notification?.isCritical ?? notification?.type === 'critical-update'

  // Handle ESC key to close modal (except for critical updates)
  useEffect(() => {
    if (!isOpen) return
    if (isCritical) return // 필수 업데이트는 ESC로 닫을 수 없음

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isCritical])

  // Reset "don't show again" checkbox when modal opens
  useEffect(() => {
    if (isOpen) {
      setDontShowAgain(false)
    }
  }, [isOpen])

  const handleClose = () => {
    if (dontShowAgain && notification && onDontShowAgain) {
      dismissNotification(notification.id)
      onDontShowAgain(notification.id)
    }
    onClose()
  }

  const handleAction = async () => {
    if (!notification) return
    try {
      if (onAction) {
        await onAction(notification)
      } else if (notification.actionUrl) {
        if (typeof window !== 'undefined') {
          window.open(notification.actionUrl, '_blank', 'noopener,noreferrer')
        }
      }
    } finally {
      handleClose()
    }
  }

  if (!isOpen || !notification) return null

  const isVersionUpdate = notification.type === 'version-update'
  const isCriticalUpdate = notification.type === 'critical-update' || notification.isCritical

  // 아이콘 및 버튼 색상 설정
  const iconColor = isCriticalUpdate ? '#ef4444' : isVersionUpdate ? 'var(--accent)' : 'var(--text-secondary)'
  const buttonBgColor = isCriticalUpdate ? '#ef4444' : isVersionUpdate ? 'var(--accent)' : 'var(--bg-secondary)'
  const buttonTextColor = isCriticalUpdate || isVersionUpdate ? 'white' : 'var(--text)'

  return (
    <>
      {/* Overlay - 필수 업데이트는 클릭으로 닫을 수 없음 */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 transition-opacity duration-200"
        onClick={isCriticalUpdate ? undefined : handleClose}
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
        <div
          className="w-full max-w-md mx-4 pointer-events-auto animate-fade-in"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-labelledby="notification-title"
          aria-describedby="notification-content"
        >
          <div
            className="rounded-2xl shadow-2xl border transition-all duration-200 animate-slide-up"
            style={{
              backgroundColor: 'var(--bg)',
              borderColor: 'var(--border)',
            }}
          >
            {/* Header */}
            <div
              className="px-6 py-4 border-b flex items-center justify-between"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className="flex items-center gap-3">
                {/* Icon based on type */}
                {isCriticalUpdate ? (
                  // 경고 아이콘 (필수 업데이트)
                  <svg
                    className="w-6 h-6"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke={iconColor}
                    aria-hidden="true"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                    />
                  </svg>
                ) : isVersionUpdate ? (
                  <svg
                    className="w-6 h-6"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke={iconColor}
                    aria-hidden="true"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                ) : (
                  <svg
                    className="w-6 h-6"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke={iconColor}
                    aria-hidden="true"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                    />
                  </svg>
                )}
                <h2
                  id="notification-title"
                  className="text-lg font-semibold"
                  style={{ color: isCriticalUpdate ? '#ef4444' : 'var(--text)' }}
                >
                  {notification.title}
                </h2>
              </div>
              {/* 필수 업데이트는 닫기 버튼 숨김 */}
              {!isCriticalUpdate && (
                <button
                  onClick={handleClose}
                  className="w-8 h-8 rounded-lg flex items-center justify-center transition-colors hover:opacity-70"
                  style={{
                    backgroundColor: 'var(--bg-secondary)',
                    color: 'var(--text-secondary)',
                  }}
                  aria-label="닫기"
                >
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              )}
            </div>

            {/* Body */}
            <div className="p-6">
              {/* 이미지가 있으면 표시 */}
              {notification.imageUrl && (
                <div className="mb-4 rounded-lg overflow-hidden bg-black/5">
                  <img
                    src={notification.imageUrl}
                    alt=""
                    className="w-full h-auto max-h-64 object-contain"
                    onError={(e) => {
                      // 이미지 로드 실패 시 숨김
                      (e.target as HTMLImageElement).style.display = 'none'
                    }}
                  />
                </div>
              )}
              <p
                id="notification-content"
                className="text-sm leading-relaxed whitespace-pre-wrap"
                style={{ color: 'var(--text-secondary)' }}
              >
                {notification.content}
              </p>
            </div>

            {/* Footer */}
            <div
              className="px-6 py-4 border-t flex items-center justify-between"
              style={{ borderColor: 'var(--border)' }}
            >
              {/* "Don't show again" checkbox
                  - 필수 업데이트: 숨김
                  - 실제 업데이트 알림 (업데이트 관리): 숨김 (ID가 update-available-* 또는 update-downloaded-*)
                  - 알림 관리의 업데이트 알림: 표시 (ID가 UUID)
              */}
              {(() => {
                // 실제 업데이트 알림인지 확인 (electron-updater에서 온 알림)
                const isAutoUpdateNotification = notification.id.startsWith('update-available-') || notification.id.startsWith('update-downloaded-')

                if (isCriticalUpdate) {
                  return (
                    <span className="text-xs" style={{ color: '#ef4444' }}>
                      이 업데이트는 필수입니다
                    </span>
                  )
                } else if (isAutoUpdateNotification) {
                  return (
                    <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      업데이트 알림은 앱 실행 시 다시 표시됩니다
                    </span>
                  )
                } else {
                  return (
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={dontShowAgain}
                        onChange={(e) => setDontShowAgain(e.target.checked)}
                        className="w-4 h-4 rounded border transition-colors cursor-pointer"
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      <span className="text-xs select-none" style={{ color: 'var(--text-secondary)' }}>
                        이 알림 다시 보지 않기
                      </span>
                    </label>
                  )
                }
              })()}

              {/* Action button */}
              {notification.actionLabel && (
                <button
                  onClick={handleAction}
                  className="px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 hover:opacity-90"
                  style={{
                    backgroundColor: buttonBgColor,
                    color: buttonTextColor,
                  }}
                >
                  {notification.actionLabel}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

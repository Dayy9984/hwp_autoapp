import { useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { Settings } from 'lucide-react'

export function ProfilePopover() {
  const { activePopover, popoverData, closePopover, openModal } = useUIStore()
  const popoverRef = useRef<HTMLDivElement>(null)

  const isOpen = activePopover === 'profile'

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        closePopover()
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closePopover()
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
      return () => {
        document.removeEventListener('mousedown', handleClickOutside)
        document.removeEventListener('keydown', handleEscape)
      }
    }
  }, [isOpen, closePopover])

  const handleSettingsClick = () => {
    closePopover()
    openModal('settings')
  }

  if (!isOpen) return null

  const position = popoverData?.position as { bottom?: number; left?: number, top?: number } | undefined
  const style: React.CSSProperties = {
    position: 'fixed',
    zIndex: 50,
    top: 'auto',
    backgroundColor: 'var(--bg)',
  }

  // 위치 조정 로직
  // 위치 조정 로직
  if (position?.bottom) style.bottom = position.bottom + 10
  if (position?.left) style.left = position.left
  if (position?.top) style.top = position.top

  return (
    <div
      ref={popoverRef}
      className="bg-bg rounded-xl shadow-xl border border-transparent min-w-[200px] overflow-hidden animate-scale-up"
      style={style}
    >
      <div className="p-1">
        <button
          onClick={handleSettingsClick}
          className="w-full px-3 py-2 rounded-lg flex items-center gap-2.5 text-sm text-text hover:bg-bg-secondary transition-colors text-left"
        >
          <Settings size={18} className="text-text-secondary" />
          <span className="font-medium">설정</span>
        </button>

      </div>
    </div>
  )
}

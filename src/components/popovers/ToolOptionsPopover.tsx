import { useState, useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useToolStore } from '../../stores/tool-store'
import { AlertTriangle, X } from 'lucide-react'

export function ToolOptionsPopover() {
  const { activePopover, popoverData, closePopover } = useUIStore()
  const { removeTool } = useToolStore()
  const [showRemoveConfirm, setShowRemoveConfirm] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  const isOpen = activePopover === 'tool-options'
  const toolId = popoverData?.toolId as string | undefined

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        closePopover()
        setShowRemoveConfirm(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (showRemoveConfirm) {
          setShowRemoveConfirm(false)
        } else {
          closePopover()
        }
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
  }, [isOpen, closePopover, showRemoveConfirm])

  const handleRemove = () => {
    if (!toolId) return

    removeTool(toolId)
    closePopover()
    setShowRemoveConfirm(false)
  }

  if (!isOpen || !toolId) return null

  const position = popoverData?.position as { top: number; left: number } | undefined

  return (
    <div
      ref={popoverRef}
      className="fixed z-50 rounded-xl shadow-2xl border min-w-[200px] overflow-hidden"
      style={{
        backgroundColor: 'var(--bg)',
        borderColor: 'var(--border)',
        top: position?.top || 0,
        left: position?.left || 0,
      }}
    >
      {showRemoveConfirm ? (
        <div className="p-4">
          <div className="mb-3">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle
                className="w-5 h-5 flex-shrink-0"
                style={{ color: 'var(--danger)' }}
              />
              <h3
                className="font-semibold text-sm"
                style={{ color: 'var(--text)' }}
              >
                도구 제거
              </h3>
            </div>
            <p
              className="text-sm"
              style={{ color: 'var(--text-secondary)' }}
            >
              이 도구를 목록에서 제거하시겠습니까?
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowRemoveConfirm(false)}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text)',
              }}
            >
              취소
            </button>
            <button
              onClick={handleRemove}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium text-white transition-all duration-150"
              style={{
                backgroundColor: 'var(--danger)',
              }}
            >
              제거
            </button>
          </div>
        </div>
      ) : (
        <div className="py-1">
          <button
            onClick={() => setShowRemoveConfirm(true)}
            className="w-full px-4 py-2.5 flex items-center gap-3 transition-all duration-150 text-left"
            style={{
              color: 'var(--danger)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--danger-light)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
            }}
          >
            <X
              className="w-4 h-4 flex-shrink-0"
            />
            <span className="text-sm font-medium">선택 해제</span>
          </button>
        </div>
      )}
    </div>
  )
}

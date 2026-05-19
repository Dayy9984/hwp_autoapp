import { useState, useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useToolStore } from '../../stores/tool-store'
import { Pencil, Trash2, AlertTriangle } from 'lucide-react'

export function SignatureOptionsPopover() {
  const { activePopover, popoverData, closePopover } = useUIStore()
  const { updateSignature, deleteSignature, getSignature } = useToolStore()
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  const isOpen = activePopover === 'signature-options'
  const signatureId = popoverData?.signatureId as string | undefined

  const signature = getSignature(signatureId || '')

  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [isRenaming])

  useEffect(() => {
    if (isOpen && signature) {
      setRenameValue(signature.name)
    }
  }, [isOpen, signature])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        closePopover()
        setIsRenaming(false)
        setShowDeleteConfirm(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (isRenaming) {
          setIsRenaming(false)
          setRenameValue(signature?.name || '')
        } else if (showDeleteConfirm) {
          setShowDeleteConfirm(false)
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
  }, [isOpen, closePopover, isRenaming, showDeleteConfirm, signature?.name])

  const handleRename = () => {
    if (!signatureId || !renameValue.trim()) {
      setIsRenaming(false)
      setRenameValue(signature?.name || '')
      return
    }

    updateSignature(signatureId, renameValue.trim())
    setIsRenaming(false)
    closePopover()
  }

  const handleDelete = () => {
    if (!signatureId) return

    deleteSignature(signatureId)
    closePopover()
    setShowDeleteConfirm(false)
  }

  if (!isOpen || !signatureId || !signature) return null

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
      {showDeleteConfirm ? (
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
                서명/도장 삭제
              </h3>
            </div>
            <p
              className="text-sm"
              style={{ color: 'var(--text-secondary)' }}
            >
              "{signature.name}"을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowDeleteConfirm(false)}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text)',
              }}
            >
              취소
            </button>
            <button
              onClick={handleDelete}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium text-white transition-all duration-150"
              style={{
                backgroundColor: 'var(--danger)',
              }}
            >
              삭제
            </button>
          </div>
        </div>
      ) : isRenaming ? (
        <div className="p-3">
          <input
            ref={renameInputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleRename()
              }
            }}
            className="w-full px-3 py-2 rounded-lg border outline-none transition-all duration-150 focus:ring-2 text-sm"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              borderColor: 'var(--border)',
              color: 'var(--text)',
              '--tw-ring-color': 'var(--accent)',
            } as React.CSSProperties}
            placeholder="서명/도장 이름"
          />
          <div className="flex gap-2 mt-2">
            <button
              onClick={() => {
                setIsRenaming(false)
                setRenameValue(signature.name)
              }}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-secondary)',
              }}
            >
              취소
            </button>
            <button
              onClick={handleRename}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-all duration-150"
              style={{
                backgroundColor: 'var(--accent)',
              }}
            >
              저장
            </button>
          </div>
        </div>
      ) : (
        <div className="py-1">
          <button
            onClick={() => setIsRenaming(true)}
            className="w-full px-4 py-2.5 flex items-center gap-3 transition-all duration-150 text-left"
            style={{
              color: 'var(--text)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-secondary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
            }}
          >
            <Pencil
              className="w-4 h-4 flex-shrink-0"
              style={{ color: 'var(--text-secondary)' }}
            />
            <span className="text-sm font-medium">이름 변경</span>
          </button>

          <div
            className="my-1 border-t"
            style={{ borderColor: 'var(--border)' }}
          />

          <button
            onClick={() => setShowDeleteConfirm(true)}
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
            <Trash2
              className="w-4 h-4 flex-shrink-0"
              color="currentColor"
            />
            <span className="text-sm font-medium">삭제</span>
          </button>
        </div>
      )}
    </div>
  )
}

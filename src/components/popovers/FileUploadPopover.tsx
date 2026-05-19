import { useRef, useEffect } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { Upload } from 'lucide-react'

export function FileUploadPopover() {
  const { activePopover, popoverData, closePopover } = useUIStore()
  const popoverRef = useRef<HTMLDivElement>(null)

  const isOpen = activePopover === 'file-upload'
  const onFileSelect = popoverData?.onFileSelect as (() => void) | undefined

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

  const handleFileSelect = () => {
    if (onFileSelect) {
      onFileSelect()
    }
    closePopover()
  }

  if (!isOpen) return null

  const position = popoverData?.position as { top: number; left: number } | undefined

  return (
    <div
      ref={popoverRef}
      className="fixed z-50 rounded-xl shadow-2xl border min-w-[220px] overflow-hidden"
      style={{
        backgroundColor: 'var(--bg)',
        borderColor: 'var(--border)',
        top: position?.top || 0,
        left: position?.left || 0,
      }}
    >
      <div className="py-1">
        <button
          onClick={handleFileSelect}
          className="w-full px-4 py-3 flex items-center gap-3 transition-all duration-150 text-left"
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
          <Upload
            className="w-5 h-5 flex-shrink-0"
            style={{ color: 'var(--text-secondary)' }}
          />
          <div className="flex-1">
            <div className="text-sm font-medium">파일 업로드</div>
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              한글(HWP/HWPX), PDF, Word(DOCX), Excel(XLS/XLSX/XLSM), PowerPoint(PPTX), Markdown(MD), TXT 파일 지원 (최대 50MB)
            </div>
          </div>
        </button>
      </div>
    </div>
  )
}

import { useState, useEffect, useRef, KeyboardEvent } from 'react'
import { useUIStore } from '../../stores/ui-store'
import { useToolStore, type Tool } from '../../stores/tool-store'
import { X } from 'lucide-react'

const TOOL_TYPES: Array<{ value: Tool['type']; label: string }> = [
  { value: 'signature-stamp', label: '서명/도장' },
]

export function ToolEditModal() {
  const { activeModal, modalData, closeModal } = useUIStore()
  const { getTool, importTool, removeTool } = useToolStore()

  const [toolName, setToolName] = useState('')
  const [toolType, setToolType] = useState<Tool['type']>('signature-stamp')
  const [isEditMode, setIsEditMode] = useState(false)
  const [editingToolId, setEditingToolId] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)

  const isOpen = activeModal === 'tool-edit'

  useEffect(() => {
    if (isOpen) {
      // Check if we're editing an existing tool
      const toolId = modalData?.toolId as string | undefined

      if (toolId) {
        const tool = getTool(toolId)
        if (tool) {
          setToolName(tool.name)
          setToolType(tool.type)
          setIsEditMode(true)
          setEditingToolId(toolId)
        }
      } else {
        // Reset for new tool creation
        setToolName('')
        setToolType('signature-stamp')
        setIsEditMode(false)
        setEditingToolId(null)
      }

      // Focus input
      if (inputRef.current) {
        inputRef.current.focus()
      }
    }
  }, [isOpen, modalData, getTool])

  const handleSaveTool = () => {
    const trimmedName = toolName.trim()
    if (!trimmedName) {
      return
    }

    if (isEditMode && editingToolId) {
      // For edit mode, we would need an updateTool function in the store
      // For now, we'll remove and re-add (not ideal but functional)
      removeTool(editingToolId)
      importTool(trimmedName, toolType)
    } else {
      // Create new tool
      importTool(trimmedName, toolType)
    }

    handleCancel()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      handleCancel()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      handleSaveTool()
    }
  }

  const handleCancel = () => {
    setToolName('')
    setToolType('signature-stamp')
    setIsEditMode(false)
    setEditingToolId(null)
    closeModal()
  }

  if (!isOpen) return null

  return (
    <>
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 transition-opacity duration-200"
        onClick={handleCancel}
        aria-hidden="true"
      />

      <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
        <div
          className="w-full max-w-md mx-4 pointer-events-auto"
          onClick={(e) => e.stopPropagation()}
        >
          <div
            className="rounded-2xl shadow-2xl border transition-all duration-200"
            style={{
              backgroundColor: 'var(--bg)',
              borderColor: 'var(--border)',
            }}
          >
            <div
              className="px-6 py-4 border-b flex items-center justify-between"
              style={{ borderColor: 'var(--border)' }}
            >
              <h2
                className="text-lg font-serif font-bold"
                style={{ color: 'var(--text)' }}
              >
                {isEditMode ? '도구 편집' : '도구 추가'}
              </h2>
              <button
                onClick={handleCancel}
                className="p-1 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
                aria-label="닫기"
              >
                <X size={20} />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div>
                <label
                  className="block text-sm font-medium mb-2"
                  style={{ color: 'var(--text)' }}
                >
                  도구 이름
                </label>
                <input
                  ref={inputRef}
                  type="text"
                  value={toolName}
                  onChange={(e) => setToolName(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="도구 이름 입력"
                  className="w-full px-4 py-3 rounded-xl border outline-none transition-all duration-200 focus:ring-2"
                  style={{
                    backgroundColor: 'var(--bg-secondary)',
                    borderColor: 'var(--border)',
                    color: 'var(--text)',
                    '--tw-ring-color': 'var(--accent)',
                  } as React.CSSProperties}
                />
              </div>

              <div>
                <label
                  className="block text-sm font-medium mb-2"
                  style={{ color: 'var(--text)' }}
                >
                  도구 유형
                </label>
                <div className="grid grid-cols-2 gap-2">
                  {TOOL_TYPES.map((type) => (
                    <button
                      key={type.value}
                      onClick={() => setToolType(type.value)}
                      className={`px-4 py-2.5 rounded-xl border transition-all duration-200 ${toolType === type.value
                          ? 'ring-2'
                          : 'hover:opacity-80'
                        }`}
                      style={{
                        backgroundColor:
                          toolType === type.value
                            ? 'var(--accent)'
                            : 'var(--bg-secondary)',
                        borderColor:
                          toolType === type.value
                            ? 'var(--accent)'
                            : 'var(--border)',
                        color:
                          toolType === type.value
                            ? 'white'
                            : 'var(--text)',
                        '--tw-ring-color': 'var(--accent)',
                      } as React.CSSProperties}
                    >
                      {type.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={handleCancel}
                  className="flex-1 px-4 py-2.5 rounded-xl border transition-all duration-200 hover:opacity-80"
                  style={{
                    backgroundColor: 'var(--bg-secondary)',
                    borderColor: 'var(--border)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  취소
                </button>
                <button
                  onClick={handleSaveTool}
                  disabled={!toolName.trim()}
                  className="flex-1 px-4 py-2.5 rounded-xl transition-all duration-200 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed text-white"
                  style={{
                    backgroundColor: 'var(--accent)',
                  }}
                >
                  {isEditMode ? '저장' : '추가'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

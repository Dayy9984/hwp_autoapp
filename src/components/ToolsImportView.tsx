import { useState, useEffect, useMemo } from 'react'
import { useToolStore, type Tool } from '../stores/tool-store'
import { useUIStore } from '../stores/ui-store'
import { Sparkles, ShieldCheck, type LucideIcon } from 'lucide-react'

interface AvailableTool {
  name: string
  type: Tool['type']
  icon: LucideIcon
  description: string
  requiresScope?: 'partial' | 'full'
}

const availableTools: AvailableTool[] = [
  {
    name: '작성 커스텀',
    type: 'prompt-custom',
    icon: Sparkles,
    description: '문서 품질/규칙/문체를 부분적으로 추가합니다.',
    requiresScope: 'partial',
  },
]

export function ToolsImportView() {
  const { tools, importTool, removeTool } = useToolStore()
  const { navigateBack, canNavigateBack } = useUIStore()
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set())
  const visibleTools = useMemo(() => availableTools, [])
  const visibleToolNames = useMemo(() => new Set(visibleTools.map((tool) => tool.name)), [visibleTools])

  // Initialize selected tools from already imported tools
  useEffect(() => {
    const imported = new Set(
      tools
        .map(tool => availableTools.find(at => at.name === tool.name)?.name)
        .filter((name): name is string => name !== undefined && visibleToolNames.has(name))
    )
    setSelectedTools(imported)
  }, [tools, visibleToolNames])

  const handleToggleTool = (toolName: string) => {
    const newSelected = new Set(selectedTools)
    const availableTool = visibleTools.find(t => t.name === toolName)

    if (!availableTool) return

    if (newSelected.has(toolName)) {
      // Remove tool
      newSelected.delete(toolName)
      const existingTool = tools.find(t => t.name === toolName)
      if (existingTool) {
        removeTool(existingTool.id)
      }
    } else {
      // Add tool
      newSelected.add(toolName)
      importTool(availableTool.name, availableTool.type)
    }

    setSelectedTools(newSelected)
  }

  const handleClose = () => {
    // Use navigateBack if available, otherwise this is a direct entry and we do nothing
    if (canNavigateBack()) {
      navigateBack()
    }
  }

  const selectedCount = selectedTools.size

  return (
    <div
      className="flex-1 flex flex-col overflow-hidden"
      style={{ backgroundColor: 'var(--bg-secondary)' }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-6 py-4 border-b"
        style={{
          backgroundColor: 'var(--bg)',
          borderColor: 'var(--border)',
        }}
      >
        <div className="flex items-center gap-3">
          <button
            onClick={handleClose}
            className="p-2 rounded-lg transition-all duration-200"
            style={{ color: 'var(--text-tertiary)' }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-tertiary)'
              e.currentTarget.style.backgroundColor = 'transparent'
            }}
            aria-label="뒤로 가기"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>
            도구 가져오기
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            {selectedCount}개 선택됨
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {/* Section Header */}
        <div className="mb-4">
          <h2
            className="text-xs font-bold uppercase tracking-wider mb-1"
            style={{ color: 'var(--text-secondary)' }}
          >
            사용 가능한 도구
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            문서 작업에 필요한 도구를 선택하세요. 선택한 도구는 사이드바에 추가됩니다.
          </p>
        </div>

        {/* Tool List */}
        <div className="space-y-3">
          {visibleTools.map(tool => {
            const isSelected = selectedTools.has(tool.name)

            return (
              <div
                key={tool.name}
                onClick={() => handleToggleTool(tool.name)}
                className={`group flex items-center gap-4 p-4 border rounded-xl transition-all duration-200 ${
                  isSelected
                    ? 'shadow-sm cursor-pointer'
                    : 'hover:shadow-md cursor-pointer'
                }`}
                style={
                  isSelected
                    ? {
                        backgroundColor: 'var(--accent-light, rgba(255, 152, 0, 0.1))',
                        borderColor: 'var(--accent, #ff9800)',
                      }
                    : {
                        backgroundColor: 'var(--bg)',
                        borderColor: 'var(--border)',
                      }
                }
              >
                {/* Checkbox */}
                <div className="flex-shrink-0">
                  <div
                    className="w-5 h-5 rounded border-2 flex items-center justify-center transition-all duration-200"
                    style={
                      isSelected
                        ? {
                            backgroundColor: 'var(--accent)',
                            borderColor: 'var(--accent)',
                          }
                        : {
                            backgroundColor: 'var(--bg)',
                            borderColor: 'var(--border)',
                          }
                    }
                  >
                    {isSelected && (
                      <svg className="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>
                </div>

                {/* Icon */}
                <div
                  className="flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center shadow-sm"
                  style={{
                    background: 'linear-gradient(to bottom right, var(--bg-secondary), var(--bg-tertiary))',
                  }}
                >
                  <tool.icon size={24} style={{ color: 'var(--text)' }} />
                </div>

                {/* Tool Info */}
                <div className="flex-1 min-w-0">
                  <h3
                    className="text-sm font-semibold mb-1 transition-colors duration-200"
                    style={
                      isSelected
                        ? { color: 'var(--accent)' }
                        : { color: 'var(--text)' }
                    }
                  >
                    {tool.name}
                  </h3>
                  <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {tool.description}
                  </p>
                </div>

                {/* Status Badge */}
                <div className="flex-shrink-0">
                  {isSelected ? (
                    <span
                      className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium"
                      style={{
                        backgroundColor: 'var(--accent-light, rgba(255, 152, 0, 0.15))',
                        color: 'var(--accent)',
                      }}
                    >
                      가져옴
                    </span>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>

        {/* Info Message */}
        <div className="mt-6 p-4 rounded-lg" style={{ backgroundColor: 'var(--accent-light, rgba(255, 152, 0, 0.1))', border: '1px solid var(--accent, #ff9800)' }}>
          <div className="flex gap-3">
            <div className="flex-shrink-0" style={{ color: 'var(--accent)' }}>
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
              </svg>
            </div>
            <div className="text-sm" style={{ color: 'var(--text)' }}>
              <p className="font-medium mb-1">도구 사용 안내</p>
              <p style={{ color: 'var(--text-secondary)' }}>
                선택한 도구는 즉시 사이드바에 추가됩니다. 언제든지 다시 이 화면으로 돌아와 도구를 추가하거나 제거할 수 있습니다.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div
        className="px-6 py-4 border-t"
        style={{
          backgroundColor: 'var(--bg)',
          borderColor: 'var(--border)',
        }}
      >
        <div className="flex items-center justify-between">
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            총 {visibleTools.length}개 중 {selectedCount}개 선택
          </p>
          <button
            onClick={handleClose}
            className="px-6 py-2.5 text-white font-medium rounded-lg transition-all duration-200 shadow-sm hover:shadow-md"
            style={{
              backgroundColor: 'var(--accent)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.opacity = '0.9'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.opacity = '1'
            }}
          >
            완료
          </button>
        </div>
      </div>
    </div>
  )
}

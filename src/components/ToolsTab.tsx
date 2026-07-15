import type { ComponentType } from 'react'
import { Sparkles, ShieldCheck, PackagePlus } from 'lucide-react'
import type { LucideProps } from 'lucide-react'
import { useUIStore } from '../stores/ui-store'
import { useSettingsStore } from '../stores/settings-store'
import { useToolStore } from '../stores/tool-store'

interface ToolsTabProps {
  isCollapsed?: boolean
}

type ToolCardProps = {
  title: string
  description: string
  status: string
  disabled?: boolean
  icon: ComponentType<LucideProps>
  onClick?: () => void
}

const ToolCard = ({ title, description, status, disabled, icon: Icon, onClick }: ToolCardProps) => (
  <button
    onClick={onClick}
    disabled={disabled}
    className={`w-full text-left p-4 rounded-xl border transition-all ${
      disabled
        ? 'border-transparent bg-bg-secondary/60 text-text-tertiary'
        : 'border-transparent bg-bg-secondary hover:bg-bg-tertiary'
    }`}
  >
    <div className="flex items-start gap-3">
      <div className={`p-2 rounded-lg ${disabled ? 'bg-bg' : 'bg-accent/10 text-accent'}`}>
        <Icon size={20} className={disabled ? 'text-text-tertiary' : 'text-accent'} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-semibold text-text truncate">{title}</h4>
          <span
            className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
              disabled
                ? 'bg-bg text-text-tertiary'
                : status === '사용 중'
                  ? 'bg-accent/15 text-accent'
                  : 'bg-bg text-text-secondary'
            }`}
          >
            {status}
          </span>
        </div>
        <p className={`text-xs mt-1 ${disabled ? 'text-text-tertiary' : 'text-text-secondary'}`}>
          {description}
        </p>
      </div>
    </div>
  </button>
)

export function ToolsTab({ isCollapsed = false }: ToolsTabProps) {
  const { openModal, navigateToView } = useUIStore()
  const { promptCustomEnabled, promptCustomRules, promptFullOverride } = useSettingsStore()
  const { tools } = useToolStore()
  const canPartial = true
  const canFull = true
  const customActive = promptCustomEnabled && promptCustomRules.trim().length > 0
  const fullActive = promptFullOverride.trim().length > 0

  // Get imported tools
  const promptCustomTool = tools.find((tool) => tool.type === 'prompt-custom')

  if (isCollapsed) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-3" title="도구">
        {promptCustomTool && (
          <button
            onClick={() => canPartial && openModal('prompt-custom')}
            className="p-2 rounded-lg bg-bg-tertiary text-text-secondary"
            disabled={!canPartial}
          >
            <Sparkles size={18} />
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col p-4 gap-6 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-text-tertiary">AI 도구</h3>
        <button
          onClick={() => navigateToView('tools-import')}
          className="text-xs text-accent hover:text-accent-dark font-semibold flex items-center gap-1"
        >
          <PackagePlus size={14} />
          도구 가져오기
        </button>
      </div>

      {promptCustomTool && (
        <div className="space-y-3">
          <ToolCard
            title="작성 커스텀"
            description="문서 품질/규칙/문체를 부분적으로 추가합니다."
            status={canPartial ? (customActive ? '사용 중' : '미설정') : '권한 필요'}
            disabled={!canPartial}
            icon={Sparkles}
            onClick={() => openModal('prompt-custom')}
          />
        </div>
      )}
    </div>
  )
}


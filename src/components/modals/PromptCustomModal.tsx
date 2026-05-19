import { useEffect, useState, useMemo } from 'react'
import { X, AlertTriangle } from 'lucide-react'
import { useUIStore } from '../../stores/ui-store'
import { useSettingsStore } from '../../stores/settings-store'

// 위험 키워드 목록 (프롬프트 인젝션/탈옥 시도 감지)
const DANGEROUS_KEYWORDS = [
  // === 프롬프트 인젝션 ===
  '무시', 'ignore', '이전 지시', 'forget', 'previous instruction',
  '시스템 프롬프트', 'system prompt', 'initial instruction',
  '지시사항', 'instructions', 'ignore above', 'disregard',

  // === 역할 탈출 / 탈옥 ===
  '역할을 바꿔', 'pretend to be', 'act as', 'you are now',
  'dan', 'jailbreak', '제한 해제', '제한을 풀어',
  '개발자 모드', 'developer mode', 'dan mode', 'unlock',

  // === 보안 규칙 ===
  '보안 규칙', '안전 규칙', 'safety', 'security rule',
  '보안 정책', 'security policy', 'bypass',

  // === 출력 명령어 조작 ===
  'op:', 'op: thinking', 'op: message',
  'replace_cell', 'delete_cell', 'replace_paragraph',
  'append_paragraph', 'replace_list', 'append_list',
  'find_and_replace', 'search_file',
  '도구 호출', 'tool call', 'function call',

  // === 내부 구현 ===
  'cvd', 'filledvalue', 'templatevalue', 'positionkey',
  '블록id', '셀id', 'block_id', 'cell_id',

  // === 컨텍스트 태그 ===
  '<user_request>', '<document_html>', '<document_cvd>',
  '<available_files>', '<matched_template_pair>',
  '<security_policy>', '<user_writing_preferences>',

  // === Encoding ===
  'base64', 'encode', 'decode', 'eval', 'exec',
]

// 키워드 감지 함수
function detectDangerousKeywords(text: string): string[] {
  const lowerText = text.toLowerCase()
  return DANGEROUS_KEYWORDS.filter(keyword =>
    lowerText.includes(keyword.toLowerCase())
  )
}

export function PromptCustomModal() {
  const { activeModal, closeModal } = useUIStore()
  const {
    promptCustomEnabled,
    promptCustomRules,
    setPromptCustomEnabled,
    setPromptCustomRules,
  } = useSettingsStore()

  const [enabled, setEnabled] = useState(promptCustomEnabled)
  const [rules, setRules] = useState(promptCustomRules)
  const isOpen = activeModal === 'prompt-custom'

  // 위험 키워드 감지
  const detectedKeywords = useMemo(() => detectDangerousKeywords(rules), [rules])
  const hasWarning = detectedKeywords.length > 0

  useEffect(() => {
    if (isOpen) {
      setEnabled(promptCustomEnabled)
      setRules(promptCustomRules)
    }
  }, [isOpen, promptCustomEnabled, promptCustomRules])

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
    // 위험 키워드 감지 시 저장 차단
    if (hasWarning && enabled) {
      return
    }
    setPromptCustomEnabled(enabled)
    setPromptCustomRules(rules.trim())
    closeModal()
  }

  const handleReset = () => {
    setEnabled(false)
    setRules('')
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
        <div
          className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0"
          style={{ borderColor: 'var(--border)' }}
        >
          <h2 className="text-lg font-serif font-bold" style={{ color: 'var(--text)' }}>
            작성 커스텀
          </h2>
          <button
            onClick={closeModal}
            className="p-1.5 rounded-full text-text-tertiary hover:bg-bg-tertiary hover:text-text transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          <div className="flex items-center justify-between bg-bg-secondary p-4 rounded-xl">
            <div>
              <div className="text-sm font-semibold text-text">커스텀 규칙 사용</div>
              <p className="text-xs text-text-secondary">
                문서 품질/규칙/문체 영역에 한해 규칙을 추가합니다.
              </p>
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-text-secondary">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="accent-accent"
              />
              사용
            </label>
          </div>

          {/* 위험 키워드 경고 - 저장 차단됨 */}
          {hasWarning && enabled && (
            <div className="flex gap-3 items-start p-4 rounded-xl bg-red-500/10 border border-red-500/30">
              <AlertTriangle size={18} className="text-red-500 mt-0.5 flex-shrink-0" />
              <div className="text-sm text-text-secondary">
                <p className="font-semibold text-red-600 dark:text-red-400 mb-1">
                  저장 불가: 위험 키워드 감지됨
                </p>
                <p className="text-xs text-text-tertiary mb-2">
                  아래 키워드가 규칙에 포함되어 있어 저장할 수 없습니다. 해당 키워드를 제거해 주세요:
                </p>
                <div className="flex flex-wrap gap-1">
                  {detectedKeywords.slice(0, 5).map((keyword, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 rounded text-xs bg-red-500/20 text-red-700 dark:text-red-300"
                    >
                      {keyword}
                    </span>
                  ))}
                  {detectedKeywords.length > 5 && (
                    <span className="px-2 py-0.5 rounded text-xs bg-red-500/20 text-red-700 dark:text-red-300">
                      +{detectedKeywords.length - 5}개 더
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <label className="text-xs font-semibold text-text-tertiary uppercase tracking-wider">
              커스텀 규칙
            </label>
            <textarea
              value={rules}
              onChange={(e) => setRules(e.target.value)}
              disabled={!enabled}
              rows={10}
              className={`w-full rounded-xl bg-bg-secondary border text-sm text-text p-4 focus:ring-1 outline-none transition-all disabled:opacity-60 ${
                hasWarning && enabled
                  ? 'border-red-500/50 focus:border-red-500 focus:ring-red-500/30'
                  : 'border-transparent focus:border-accent focus:ring-accent'
              }`}
              placeholder="예: 문서는 간결한 서술형으로 작성하고, 소제목마다 핵심 요약을 한 줄로 제공합니다."
            />
          </div>

          <div className="p-4 rounded-xl bg-accent/5 border border-accent/20 text-sm text-text-secondary">
            <p className="font-semibold text-text mb-2">허용 범위</p>
            <ul className="space-y-1 text-xs">
              <li>• 문서 작성 품질, 문체, 서술 규칙</li>
              <li>• 소제목 구성, 세부 항목 전개 방식</li>
              <li>• 금지: 보안 규칙, 도구 호출, 시스템 보호 지시</li>
            </ul>
          </div>
        </div>

        <div
          className="flex items-center justify-between px-6 py-4 border-t"
          style={{ borderColor: 'var(--border)' }}
        >
          <button
            onClick={handleReset}
            className="px-4 py-2 text-sm rounded-lg bg-bg-secondary text-text-secondary hover:text-text hover:bg-bg-tertiary transition-colors"
          >
            초기화
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
              disabled={hasWarning && enabled}
              className={`px-5 py-2 text-sm rounded-lg font-medium transition-colors ${
                hasWarning && enabled
                  ? 'bg-gray-400 text-gray-200 cursor-not-allowed'
                  : 'bg-accent text-white hover:bg-accent-dark'
              }`}
            >
              저장
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

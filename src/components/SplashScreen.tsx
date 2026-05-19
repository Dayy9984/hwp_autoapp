import { useEffect, useState } from 'react'
import logo from '@/components/icons/logo.png'

interface SplashScreenProps {
  pythonReady: boolean
  statusMessage?: string
  progress?: number
  onComplete: () => void
}

export function SplashScreen({ pythonReady, statusMessage, progress, onComplete }: SplashScreenProps) {
  const [minTimeElapsed, setMinTimeElapsed] = useState(false)

  // 최소 표시 시간 (너무 빨리 사라지면 깜빡임처럼 보임)
  useEffect(() => {
    const timer = setTimeout(() => {
      setMinTimeElapsed(true)
    }, 800)
    return () => clearTimeout(timer)
  }, [])

  // Python 준비 완료 + 최소 시간 경과 시 완료
  useEffect(() => {
    if (pythonReady && minTimeElapsed) {
      // 짧은 지연 후 완료 (애니메이션용)
      const timer = setTimeout(() => {
        onComplete()
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [pythonReady, minTimeElapsed, onComplete])

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-bg-secondary animate-fade-in text-text">
      <div className="text-center">
        {/* Logo */}
        <div className="mb-8 flex flex-col items-center">
          <div className="w-32 h-32 flex items-center justify-center mb-2 animate-slide-up overflow-hidden">
            <img src={logo} alt="Inserty" className="w-full h-full object-contain scale-125" />
          </div>
          <h1 className="text-4xl font-medium text-text mb-2 animate-slide-up font-serif tracking-tight" style={{ fontFamily: 'Georgia, serif', animationDelay: '0.1s' }}>
            Inserty
          </h1>
          <p className="text-sm text-text-tertiary animate-fade-in" style={{ animationDelay: '0.3s' }}>
            AI 문서 편집의 새로운 기준
          </p>
        </div>

        {/* Loading indicator */}
        <div className="flex flex-col items-center gap-4 mt-8" style={{ animationDelay: '0.4s' }}>
          <div className="flex justify-center items-center gap-2">
            <div className="w-2 h-2 bg-accent rounded-full animate-bounce" style={{ animationDelay: '0s' }}></div>
            <div className="w-2 h-2 bg-accent rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
            <div className="w-2 h-2 bg-accent rounded-full animate-bounce" style={{ animationDelay: '0.4s' }}></div>
          </div>
          {typeof progress === 'number' && (
            <div className="w-40 h-1 bg-bg-tertiary rounded-full overflow-hidden">
              <div
                className="h-full bg-accent transition-all duration-300"
                style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
              />
            </div>
          )}
          <p className="text-xs text-text-tertiary">
            {statusMessage ?? (pythonReady ? '준비 완료' : '초기화 중...')}
          </p>
        </div>
      </div>
    </div>
  )
}

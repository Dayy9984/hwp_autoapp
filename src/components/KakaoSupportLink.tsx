// 카카오톡 오픈채팅 문의 링크 공통 컴포넌트
// 카카오 브랜드 컬러(#FEE500 노랑 + 검정 텍스트)는 가이드라인이므로 유지하되,
// 다크 모드에서도 가독성이 유지되도록 명도 고정.
import { MessageCircle } from 'lucide-react'

const KAKAO_OPEN_CHAT = 'https://open.kakao.com/o/sSm9ZXei'
const KAKAO_YELLOW = '#FEE500'
const KAKAO_YELLOW_HOVER = '#F5D900'
const KAKAO_TEXT = '#191919'

interface Props {
  children?: React.ReactNode
  className?: string
}

export function KakaoSupportLink({
  children = '카카오톡으로 문의하기',
  className = '',
}: Props) {
  const handleClick = () => {
    const api = (window as any).electronAPI?.license?.openExternal
    if (typeof api === 'function') {
      api(KAKAO_OPEN_CHAT)
    } else {
      window.open(KAKAO_OPEN_CHAT, '_blank')
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      style={{ backgroundColor: KAKAO_YELLOW, color: KAKAO_TEXT }}
      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = KAKAO_YELLOW_HOVER)}
      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = KAKAO_YELLOW)}
      className={`inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition-colors ${className}`}
    >
      <MessageCircle className="w-4 h-4" strokeWidth={1.75} />
      <span>{children}</span>
    </button>
  )
}

export { KAKAO_OPEN_CHAT }

// 카카오톡 오픈채팅 문의 링크 공통 컴포넌트
import React from 'react'

const KAKAO_OPEN_CHAT = 'https://open.kakao.com/o/sSm9ZXei'

interface Props {
  children?: React.ReactNode
  className?: string
}

export function KakaoSupportLink({ children = '카카오톡 문의하기', className = '' }: Props) {
  const handleClick = () => {
    // Electron main 프로세스 IPC로 shell.openExternal
    if ((window as any).electronAPI?.openExternal) {
      ;(window as any).electronAPI.openExternal(KAKAO_OPEN_CHAT)
    } else {
      window.open(KAKAO_OPEN_CHAT, '_blank')
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`inline-flex items-center gap-2 rounded-md bg-yellow-400 hover:bg-yellow-500 text-black px-4 py-2 font-medium transition ${className}`}
    >
      <span>💬</span>
      <span>{children}</span>
    </button>
  )
}

export { KAKAO_OPEN_CHAT }

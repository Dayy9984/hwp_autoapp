import React from 'react'
import { SelectionInfo } from '../stores/document-store'

interface SelectionPreviewProps {
  selectionInfo: SelectionInfo | null
}

/**
 * 선택 영역 미리보기 컴포넌트
 * HWP 문서에서 사용자가 선택한 영역을 간결하게 표시
 */
export const SelectionPreview: React.FC<SelectionPreviewProps> = ({ selectionInfo }) => {
  if (!selectionInfo || !selectionInfo.hasSelection) {
    return null
  }

  const { selectedText, isTableSelection } = selectionInfo

  // 미리보기 텍스트 정리 (줄바꿈 제거, 공백 정리)
  const cleanText = selectedText.replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').trim()

  // 빈 텍스트면 표시 안함
  if (!cleanText) {
    return null
  }

  const prefix = isTableSelection ? '표 선택함' : '선택함'

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 bg-accent-light border border-accent/20 rounded-md text-xs">
      <span className="font-medium text-accent whitespace-nowrap">{prefix} :</span>
      <span className="text-gray-600 truncate max-w-[300px]">{cleanText}</span>
    </div>
  )
}

export default SelectionPreview

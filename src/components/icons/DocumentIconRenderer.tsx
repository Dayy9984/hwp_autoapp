import { 
  HwpFileIcon, 
  PdfFileIcon, 
  WordFileIcon, 
  ExcelFileIcon, 
  PptFileIcon, 
  TxtFileIcon, 
  UnknownFileIcon 
} from './FileIcons'
import type { OpenDocument } from '../../stores/document-store'

interface DocumentIconProps {
  type: OpenDocument['type'] | 'txt' | 'pdf' | string
  size?: number
  className?: string
}

export function DocumentIconRenderer({ type, size = 24, className = '' }: DocumentIconProps) {
  // Normalize type string
  const normalizedType = (type || '').toLowerCase()

  // Common props
  const iconProps = { size, className }

  // HWP / HWPX
  if (normalizedType === 'hwp' || normalizedType === 'hwpx' || normalizedType.endsWith('.hwp') || normalizedType.endsWith('.hwpx')) {
    return <HwpFileIcon {...iconProps} />
  }
  
  // PDF
  if (normalizedType === 'pdf' || normalizedType.endsWith('.pdf')) {
    return <PdfFileIcon {...iconProps} />
  }
  
  // Word
  if (normalizedType === 'word' || normalizedType === 'docx' || normalizedType === 'doc' || normalizedType.endsWith('.docx') || normalizedType.endsWith('.doc')) {
    return <WordFileIcon {...iconProps} />
  }
  
  // Excel
  if (normalizedType === 'excel' || normalizedType === 'xlsx' || normalizedType === 'xls' || normalizedType === 'csv' || normalizedType.endsWith('.xlsx') || normalizedType.endsWith('.xls') || normalizedType.endsWith('.csv')) {
    return <ExcelFileIcon {...iconProps} />
  }
  
  // PPT
  if (normalizedType === 'ppt' || normalizedType === 'pptx' || normalizedType.endsWith('.pptx') || normalizedType.endsWith('.ppt')) {
    return <PptFileIcon {...iconProps} />
  }
  
  // Text
  if (normalizedType === 'txt' || normalizedType === 'md' || normalizedType === 'text' || normalizedType.endsWith('.txt') || normalizedType.endsWith('.md')) {
    return <TxtFileIcon {...iconProps} />
  }

  return <UnknownFileIcon {...iconProps} />
}

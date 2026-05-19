import React from 'react'

interface IconProps {
  size?: number
  className?: string
}

const BaseFileIcon = ({ 
  size = 24, 
  className = '', 
  gradientFrom, 
  gradientTo, 
  shadowColor,
  BadgeContent,
  label
}: IconProps & { 
  gradientFrom: string, 
  gradientTo: string, 
  shadowColor: string,
  BadgeContent: React.ReactNode,
  label?: string
}) => {
  return (
    <svg 
      width={size} 
      height={size} 
      viewBox="0 0 48 48" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg"
      className={`drop-shadow-sm ${className}`}
    >
      {/* 
        Main Paper Shape with Gradient
        Bounds: x=4, y=2, w=40, h=44
      */}
      <path 
        d="M4 8C4 4.68629 6.68629 2 10 2H32L44 14V42C44 44.2091 42.2091 46 40 46H10C6.68629 46 4 43.3137 4 40V8Z" 
        fill={`url(#grad-${label})`}
      />
      
      {/* Folded Corner Area - Lighter for softer look */}
      <path 
        d="M32 2V14H44" 
        fill="white" 
        fillOpacity="0.4"
      />
      <path 
        d="M32 2L44 14H34C32.8954 14 32 13.1046 32 12V2Z" 
        fill="black" 
        fillOpacity="0.1"
      />
      
      {/* Content/Logo Area */}
      <g transform="translate(24, 28)">
        {BadgeContent}
      </g>

      {/* Definitions for Gradients */}
      <defs>
        <linearGradient id={`grad-${label}`} x1="4" y1="2" x2="44" y2="46" gradientUnits="userSpaceOnUse">
          <stop stopColor={gradientFrom} />
          <stop offset="1" stopColor={gradientTo} />
        </linearGradient>
      </defs>
    </svg>
  )
}

// HWP Icon - Blue (Softer)
export const HwpFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="hwp"
    gradientFrom="#60A5FA" 
    gradientTo="#2563EB"
    shadowColor="#1D4ED8"
    BadgeContent={
      <g>
        <text 
          x="0" 
          y="6" 
          fontFamily="'NanumMyeongjo', 'Nanum Myeongjo', serif" 
          fontSize="22" 
          fontWeight="900" 
          fill="white" 
          textAnchor="middle"
          letterSpacing="-1"
          style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.15))' }}
        >
          ᄒᆞᆫ
        </text>
      </g>
    }
  />
)

// PDF Icon - Red (Softer)
export const PdfFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="pdf"
    gradientFrom="#F87171"
    gradientTo="#DC2626"
    shadowColor="#B91C1C"
    BadgeContent={
      <g>
        {/* Slightly reduced size from previous version */}
        <text 
          x="0" 
          y="5" 
          fontFamily="'Pretendard', sans-serif" 
          fontSize="13" 
          fontWeight="900" 
          fill="white" 
          textAnchor="middle"
          style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.15))' }}
        >
          PDF
        </text>
      </g>
    }
  />
)

// Word Icon - Indigo (Softer)
export const WordFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="word"
    gradientFrom="#818CF8"
    gradientTo="#4F46E5"
    shadowColor="#4338CA"
    BadgeContent={
      <text 
        x="0" 
        y="9" 
        fontFamily="serif" 
        fontSize="24" 
        fontWeight="900" 
        fill="white" 
        textAnchor="middle" 
        style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.2))' }}
      >
        W
      </text>
    }
  />
)

// Excel Icon - Emerald (Softer)
export const ExcelFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="excel"
    gradientFrom="#34D399"
    gradientTo="#059669"
    shadowColor="#047857"
    BadgeContent={
       <text 
         x="0" 
         y="9" 
         fontFamily="'Pretendard', sans-serif" 
         fontSize="24" 
         fontWeight="900" 
         fill="white" 
         textAnchor="middle" 
         style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.2))' }}
       >
         X
       </text>
    }
  />
)

// PPT Icon - Orange (Softer)
export const PptFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="ppt"
    gradientFrom="#FB923C"
    gradientTo="#EA580C"
    shadowColor="#C2410C"
    BadgeContent={
      <g>
         <text 
           x="0" 
           y="9" 
           fontFamily="'Pretendard', sans-serif" 
           fontSize="24" 
           fontWeight="900" 
           fill="white" 
           textAnchor="middle" 
           style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.2))' }}
         >
           P
         </text>
      </g>
    }
  />
)

// Text Icon - Slate (Softer)
export const TxtFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="txt"
    gradientFrom="#CBD5E1"
    gradientTo="#64748B"
    shadowColor="#475569"
    BadgeContent={
      <g transform="translate(-12, -10)">
        <rect x="0" y="0" width="24" height="3" rx="1.5" fill="white" fillOpacity="0.9" />
        <rect x="0" y="7" width="24" height="3" rx="1.5" fill="white" fillOpacity="0.9" />
        <rect x="0" y="14" width="16" height="3" rx="1.5" fill="white" fillOpacity="0.9" />
      </g>
    }
  />
)

// Default/Unknown Icon
export const UnknownFileIcon = (props: IconProps) => (
  <BaseFileIcon
    {...props}
    label="unknown"
    gradientFrom="#E2E8F0"
    gradientTo="#94A3B8"
    shadowColor="#64748B"
    BadgeContent={
      <circle cx="0" cy="0" r="8" fill="white" fillOpacity="0.9" />
    }
  />
)
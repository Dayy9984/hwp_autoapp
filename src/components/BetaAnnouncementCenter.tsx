// 베타 알림 센터.
// - 사이드바 하단에 배지 (읽지 않은 알림 수)
// - 클릭 시 패널 열림 → 알림 목록
// - 각 알림: title/body + (선택) 임베드 폼 버튼 또는 외부 링크
// - dismiss 시 main process 에 디스크 저장 (영구)

import { useEffect, useState } from 'react'
import { Bell, X } from 'lucide-react'
import { TallyEmbedModal, type TallyFormKey } from './modals/TallyEmbedModal'
import { TALLY_FORMS } from '../config/beta'

interface Announcement {
  id: string
  title: string
  body: string | null
  embed_form_url: string | null
  action_label: string | null
  action_url: string | null
  priority: number
  type: string
  starts_at: number | null
  ends_at: number | null
}

interface Props {
  compact?: boolean
}

// embed_form_url 에서 form key 추출 (예: https://tally.so/embed/xxxxxx → 매칭되는 key)
function embedUrlToKey(url: string): TallyFormKey | null {
  const m = url.match(/embed\/([A-Za-z0-9]+)/)
  if (!m) return null
  const id = m[1]
  for (const [k, v] of Object.entries(TALLY_FORMS)) {
    if (v.includes(`/embed/${id}`)) return k as TallyFormKey
  }
  return null
}

export function BetaAnnouncementCenter({ compact = false }: Props) {
  const [items, setItems] = useState<Announcement[]>([])
  const [open, setOpen] = useState(false)
  const [embedKey, setEmbedKey] = useState<{ key: TallyFormKey; title: string } | null>(null)

  const reload = async () => {
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    const list = (await api?.announcements?.list?.()) || []
    setItems(list)
  }

  useEffect(() => {
    reload()
    const id = setInterval(reload, 60_000)
    return () => clearInterval(id)
  }, [])

  const dismiss = async (annId: string) => {
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    await api?.announcements?.dismiss?.(annId)
    setItems((prev) => prev.filter((a) => a.id !== annId))
  }

  const openEmbed = (url: string, title: string) => {
    const key = embedUrlToKey(url)
    if (key) setEmbedKey({ key, title })
  }

  if (items.length === 0) return null

  const critical = items.find((a) => a.type === 'critical-update' || a.priority >= 90)
  const badgeCount = items.length

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        aria-label={`알림 ${badgeCount}건`}
        className={`relative flex items-center gap-2 px-3 py-2 rounded-xl transition-all hover:bg-sidebar-hover group ${critical ? 'text-red-500' : 'text-text-secondary'} hover:text-text ${compact ? 'justify-center' : ''}`}
        style={{ width: '100%' }}
      >
        <Bell size={16} />
        {!compact && <span className="text-xs">알림</span>}
        <span style={{
          position: 'absolute', top: 4, right: compact ? 4 : 8,
          background: critical ? '#DC2626' : '#E86B45', color: '#FFF',
          fontSize: 10, fontWeight: 600,
          padding: '1px 6px', borderRadius: 10,
        }}>{badgeCount}</span>
      </button>

      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.3)', zIndex:40 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position:'absolute', bottom:24, left:240, width:380, maxHeight:'70vh',
              background:'#FFF', borderRadius:12, boxShadow:'0 10px 30px rgba(0,0,0,0.15)',
              overflow:'hidden', display:'flex', flexDirection:'column',
            }}
          >
            <div style={{ padding:'12px 16px', borderBottom:'1px solid #F3F4F6', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <div style={{ fontSize:13, fontWeight:600 }}>알림 {items.length}건</div>
              <button onClick={() => setOpen(false)} style={{ background:'none', border:'none', cursor:'pointer', color:'#9CA3AF' }}><X size={16} /></button>
            </div>
            <div style={{ overflowY:'auto', flex:1 }}>
              {items.map((a) => (
                <div key={a.id} style={{ padding:'12px 16px', borderBottom:'1px solid #F9FAFB', background: a.priority >= 90 ? '#FEF2F2' : 'transparent' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8 }}>
                    <div style={{ flex:1 }}>
                      <div style={{ fontSize:13, fontWeight:600, color:'#1F2937' }}>{a.title}</div>
                      {a.body && <div style={{ fontSize:12, color:'#6B7280', marginTop:4, whiteSpace:'pre-wrap' }}>{a.body}</div>}
                      <div style={{ display:'flex', gap:6, marginTop:8 }}>
                        {a.embed_form_url && (
                          <button onClick={() => openEmbed(a.embed_form_url!, a.action_label || a.title)} style={{ background:'#E86B45', color:'#FFF', border:'none', borderRadius:6, padding:'4px 10px', fontSize:11, cursor:'pointer' }}>
                            {a.action_label || '응답하기'}
                          </button>
                        )}
                        {a.action_url && (
                          <a href={a.action_url} target="_blank" rel="noopener noreferrer" style={{ background:'#F3F4F6', color:'#374151', textDecoration:'none', borderRadius:6, padding:'4px 10px', fontSize:11 }}>
                            {a.action_label || '자세히'}
                          </a>
                        )}
                        <button onClick={() => dismiss(a.id)} style={{ background:'transparent', color:'#9CA3AF', border:'none', borderRadius:6, padding:'4px 10px', fontSize:11, cursor:'pointer', marginLeft:'auto' }}>
                          닫기
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {embedKey && (
        <TallyEmbedModal
          formKey={embedKey.key}
          title={embedKey.title}
          onClose={() => setEmbedKey(null)}
        />
      )}
    </>
  )
}

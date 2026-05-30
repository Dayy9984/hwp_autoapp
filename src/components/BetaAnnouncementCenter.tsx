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

  const critical = items.find((a) => a.type === 'critical-update' || a.priority >= 90)
  const badgeCount = items.length
  const hasItems = items.length > 0

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        aria-label={`알림 ${badgeCount}건`}
        className={`relative w-full flex items-center gap-2 px-2 py-1.5 rounded-lg transition-all hover:bg-sidebar-hover group ${critical ? 'text-red-500' : 'text-text-tertiary'} hover:text-text ${compact ? 'justify-center' : ''}`}
      >
        <Bell size={15} />
        {!compact && <span className="text-xs">알림{hasItems ? ` (${badgeCount})` : ''}</span>}
        {hasItems && (
          <span style={{
            position: 'absolute', top: 2, right: compact ? 2 : 6,
            background: critical ? '#DC2626' : '#E86B45', color: '#FFF',
            fontSize: 9, fontWeight: 600,
            padding: '1px 5px', borderRadius: 8,
          }}>{badgeCount}</span>
        )}
      </button>

      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.45)', backdropFilter:'blur(2px)', zIndex:60, display:'flex', alignItems:'center', justifyContent:'center', padding:24 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width:'min(560px, 92vw)', maxHeight:'80vh',
              background:'#FFF', borderRadius:16, boxShadow:'0 20px 60px rgba(0,0,0,0.25)',
              overflow:'hidden', display:'flex', flexDirection:'column',
            }}
          >
            <div style={{ padding:'16px 20px', borderBottom:'1px solid #F3F4F6', display:'flex', justifyContent:'space-between', alignItems:'center', background:'linear-gradient(180deg, #FFF8F5 0%, #FFF 100%)' }}>
              <div>
                <div style={{ fontSize:15, fontWeight:600, color:'#1F2937' }}>알림</div>
                <div style={{ fontSize:11, color:'#9CA3AF', marginTop:2 }}>{items.length > 0 ? `${items.length}건의 새 알림이 있습니다` : '새 알림 없음'}</div>
              </div>
              <button onClick={() => setOpen(false)} style={{ background:'#F3F4F6', border:'none', cursor:'pointer', color:'#6B7280', padding:'6px 8px', borderRadius:8 }} aria-label="닫기"><X size={16} /></button>
            </div>
            <div style={{ overflowY:'auto', flex:1 }}>
              {items.length === 0 && (
                <div style={{ padding:'40px 20px', textAlign:'center', color:'#9CA3AF', fontSize:13 }}>
                  새 알림이 없습니다
                </div>
              )}
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

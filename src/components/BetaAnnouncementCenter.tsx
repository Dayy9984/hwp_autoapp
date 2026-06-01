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
  // 새로 도착한 알림 자동 모달 노출 (main process 의 'announcement:new' 이벤트 수신).
  const [autoShown, setAutoShown] = useState<Announcement | null>(null)

  const reload = async () => {
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    const list = (await api?.announcements?.list?.()) || []
    setItems(list)
  }

  useEffect(() => {
    reload()
    const id = setInterval(reload, 60_000)
    // 신규 알림 자동 모달 노출 listener.
    const api = (window as unknown as { electronAPI?: any }).electronAPI
    const unsub = api?.announcements?.onNew?.((a: Announcement) => {
      setAutoShown(a)
      reload()
    })
    // ESC 키로 자동 모달 닫기 — backdrop click 비활성화한 대신 키보드 단축키 제공.
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setAutoShown(null) }
    window.addEventListener('keydown', onKey)
    return () => {
      clearInterval(id)
      try { unsub?.() } catch {}
      window.removeEventListener('keydown', onKey)
    }
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

      {/* SaaS 표준: 풀스크린 모달 대신 사이드바 anchor popover. backdrop 없음, 클릭 outside 시 닫힘. */}
      {open && (
        <>
          {/* 보이지 않는 click-outside 영역 */}
          <div
            onClick={() => setOpen(false)}
            style={{ position:'fixed', inset:0, zIndex:55, background:'transparent' }}
            aria-hidden
          />
          <div
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="알림 센터"
            style={{
              position:'fixed',
              left: compact ? 60 : 240,
              bottom: 50,
              width:'min(360px, calc(100vw - 80px))',
              maxHeight:'min(480px, calc(100vh - 80px))',
              background:'#FFF',
              border:'1px solid #E5E7EB',
              borderRadius:12,
              boxShadow:'0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.05)',
              zIndex:60,
              display:'flex', flexDirection:'column',
              overflow:'hidden',
            }}
          >
            <div style={{ padding:'12px 14px', borderBottom:'1px solid #F3F4F6', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <div style={{ fontSize:13, fontWeight:600, color:'#1F2937' }}>
                알림{items.length > 0 ? ` · ${items.length}` : ''}
              </div>
              <button onClick={() => setOpen(false)} style={{ background:'transparent', border:'none', cursor:'pointer', color:'#9CA3AF', padding:2, lineHeight:0 }} aria-label="닫기"><X size={14} /></button>
            </div>
            <div style={{ overflowY:'auto', flex:1 }}>
              {items.length === 0 && (
                <div style={{ padding:'32px 16px', textAlign:'center', color:'#9CA3AF', fontSize:12 }}>
                  새 알림이 없습니다
                </div>
              )}
              {items.map((a) => (
                <div key={a.id} style={{ padding:'10px 14px', borderBottom:'1px solid #F9FAFB', background: a.priority >= 90 ? '#FEF7F2' : 'transparent' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:6 }}>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ fontSize:12, fontWeight:600, color:'#1F2937' }}>{a.title}</div>
                      {a.body && <div style={{ fontSize:11, color:'#6B7280', marginTop:3, whiteSpace:'pre-wrap', lineHeight:1.5 }}>{a.body}</div>}
                      <div style={{ display:'flex', gap:4, marginTop:6, flexWrap:'wrap' }}>
                        {a.embed_form_url && (
                          <button onClick={() => openEmbed(a.embed_form_url!, a.action_label || a.title)} style={{ background:'#E86B45', color:'#FFF', border:'none', borderRadius:4, padding:'3px 8px', fontSize:10, cursor:'pointer' }}>
                            {a.action_label || '응답'}
                          </button>
                        )}
                        {a.action_url && (
                          <a href={a.action_url} target="_blank" rel="noopener noreferrer" style={{ background:'#F3F4F6', color:'#374151', textDecoration:'none', borderRadius:4, padding:'3px 8px', fontSize:10 }}>
                            {a.action_label || '자세히'}
                          </a>
                        )}
                        <button onClick={() => dismiss(a.id)} style={{ background:'transparent', color:'#9CA3AF', border:'none', borderRadius:4, padding:'3px 8px', fontSize:10, cursor:'pointer', marginLeft:'auto' }}>
                          닫기
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {embedKey && (
        <TallyEmbedModal
          formKey={embedKey.key}
          title={embedKey.title}
          onClose={() => setEmbedKey(null)}
        />
      )}

      {/* 자동 모달 — admin 이 새 알림 발행하면 main 이 push, renderer 가 즉시 노출.
          critical (priority>=90) 은 빨간 헤더 + 닫기 위치 조정 */}
      {autoShown && (
        <div
          // backdrop click 으로 닫지 않음 — 사용자 의도 (X 또는 다시 보지 않기 명시 클릭만 닫힘).
          style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.55)', backdropFilter:'blur(3px)', zIndex:120, display:'flex', alignItems:'center', justifyContent:'center', padding:24 }}
        >
          <div
            role="dialog"
            aria-label="새 알림"
            style={{
              width:'min(440px, 92vw)',
              background:'#FFF', borderRadius:14, boxShadow:'0 25px 50px -12px rgba(0,0,0,0.25)',
              overflow:'hidden',
            }}
          >
            <div style={{
              padding:'14px 18px',
              borderBottom:'1px solid #F3F4F6',
              display:'flex', justifyContent:'space-between', alignItems:'center',
              background: (autoShown.priority || 0) >= 90 ? '#FEF2F2' : '#FFF8F5',
            }}>
              <div style={{ fontSize:14, fontWeight:600, color: (autoShown.priority || 0) >= 90 ? '#991B1B' : '#1F2937' }}>
                {(autoShown.priority || 0) >= 90 ? '⚠ 중요 알림' : '알림'}
              </div>
              <button onClick={() => setAutoShown(null)} style={{ background:'transparent', border:'none', cursor:'pointer', color:'#9CA3AF', padding:2 }} aria-label="닫기"><X size={16} /></button>
            </div>
            <div style={{ padding:'16px 18px' }}>
              <div style={{ fontSize:15, fontWeight:600, color:'#1F2937', marginBottom:6 }}>{autoShown.title}</div>
              {autoShown.body && <div style={{ fontSize:13, color:'#4B5563', whiteSpace:'pre-wrap', lineHeight:1.55 }}>{autoShown.body}</div>}
              <div style={{ display:'flex', gap:6, marginTop:14, flexWrap:'wrap' }}>
                {autoShown.embed_form_url && (
                  <button
                    onClick={() => { openEmbed(autoShown.embed_form_url!, autoShown.action_label || autoShown.title); setAutoShown(null) }}
                    style={{ background:'#E86B45', color:'#FFF', border:'none', borderRadius:6, padding:'7px 14px', fontSize:12, cursor:'pointer' }}
                  >
                    {autoShown.action_label || '응답하기'}
                  </button>
                )}
                {autoShown.action_url && (
                  <a href={autoShown.action_url} target="_blank" rel="noopener noreferrer" style={{ background:'#F3F4F6', color:'#374151', textDecoration:'none', borderRadius:6, padding:'7px 14px', fontSize:12 }}>
                    {autoShown.action_label || '자세히'}
                  </a>
                )}
                <button
                  onClick={() => { dismiss(autoShown.id); setAutoShown(null) }}
                  style={{ background:'transparent', color:'#9CA3AF', border:'1px solid #E5E7EB', borderRadius:6, padding:'7px 14px', fontSize:12, cursor:'pointer', marginLeft:'auto' }}
                >
                  다시 보지 않기
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

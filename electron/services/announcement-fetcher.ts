// Worker /announcements 주기적 fetch + dismissed 캐시.
//
// 흐름:
//   1. 5분마다 (그리고 앱 시작 직후) GET /announcements?since=last
//   2. 사용자가 dismiss 한 id 는 disk 캐시에 저장
//   3. renderer 가 IPC announcement:list 호출 → dismiss 안 된 것만 반환

import { app, ipcMain, BrowserWindow } from 'electron'
import * as fs from 'fs'
import * as path from 'path'
import { licenseBridge } from './license-bridge'
import { getDeviceInfo } from './license-device-id'

const ANNOUNCEMENTS_URL = 'https://inserty-beta-worker.snsoffice.workers.dev/announcements'
const FETCH_INTERVAL_MS = 5 * 60 * 1000  // 5분

export interface Announcement {
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

class AnnouncementFetcher {
  private items: Announcement[] = []
  private dismissed: Set<string> = new Set()
  private dismissedPath = ''
  private timer: NodeJS.Timeout | null = null
  // 사용자가 한번이라도 본 (dismiss 안 했어도 사용자가 알림 popover 를 연 적이 있는) id.
  // 같은 알림이 매 fetch 마다 모달로 반복 표시되는 것 방지.
  private notifiedSet: Set<string> = new Set()
  private notifiedPath = ''

  init() {
    this.dismissedPath = path.join(app.getPath('userData'), 'announcement-dismissed.json')
    this.notifiedPath = path.join(app.getPath('userData'), 'announcement-notified.json')
    try {
      if (fs.existsSync(this.dismissedPath)) {
        const arr = JSON.parse(fs.readFileSync(this.dismissedPath, 'utf8')) as string[]
        this.dismissed = new Set(Array.isArray(arr) ? arr : [])
      }
    } catch {}
    try {
      if (fs.existsSync(this.notifiedPath)) {
        const arr = JSON.parse(fs.readFileSync(this.notifiedPath, 'utf8')) as string[]
        this.notifiedSet = new Set(Array.isArray(arr) ? arr : [])
      }
    } catch {}
    this.fetchNow().catch(() => {})
    if (!this.timer) {
      this.timer = setInterval(() => this.fetchNow().catch(() => {}), FETCH_INTERVAL_MS)
      if (this.timer.unref) this.timer.unref()
    }
  }

  async fetchNow(): Promise<void> {
    const token = (licenseBridge as any).pickAuthToken?.() as string | null
    if (!token) {
      console.log('[announcement-fetcher] skip — no license token')
      return
    }
    const deviceId = getDeviceInfo().device_id
    console.log(`[announcement-fetcher] fetch — token=${token.slice(0,20)}... device=${deviceId.slice(0,12)}...`)
    try {
      const r = await fetch(ANNOUNCEMENTS_URL, {
        headers: { Authorization: `Bearer ${token}`, 'X-Device-Id': deviceId },
      })
      if (!r.ok) {
        console.log(`[announcement-fetcher] fetch failed ${r.status}: ${(await r.text()).slice(0, 100)}`)
        return
      }
      const data = (await r.json()) as { items?: Announcement[] }
      if (Array.isArray(data.items)) {
        this.items = data.items
        console.log(`[announcement-fetcher] fetched ${data.items.length} items`)
        // 신규 알림 자동 노출: 한 번도 본 적 없는 & dismiss 안 한 & 활성 기간 내인 알림 중 최우선 1건.
        this.maybeNotifyNew()
      }
    } catch (e) {
      console.log('[announcement-fetcher] exception:', (e as Error).message)
    }
  }

  private maybeNotifyNew(): void {
    const now = Date.now()
    const candidates = this.items
      .filter((a) => !this.dismissed.has(a.id))
      .filter((a) => !this.notifiedSet.has(a.id))
      .filter((a) => !a.starts_at || a.starts_at <= now)
      .filter((a) => !a.ends_at || a.ends_at >= now)
      .sort((a, b) => (b.priority || 0) - (a.priority || 0))
    if (candidates.length === 0) return
    const a = candidates[0]
    // renderer 에 'announcement:new' 이벤트 송신 — 자동 모달 노출 트리거.
    const wins = BrowserWindow.getAllWindows()
    for (const w of wins) {
      try { w.webContents.send('announcement:new', a) } catch {}
    }
    this.notifiedSet.add(a.id)
    try { fs.writeFileSync(this.notifiedPath, JSON.stringify(Array.from(this.notifiedSet))) } catch {}
  }

  list(): Announcement[] {
    const now = Date.now()
    return this.items
      .filter((a) => !this.dismissed.has(a.id))
      .filter((a) => !a.starts_at || a.starts_at <= now)
      .filter((a) => !a.ends_at || a.ends_at >= now)
      .sort((a, b) => (b.priority || 0) - (a.priority || 0))
  }

  dismiss(id: string) {
    this.dismissed.add(id)
    try {
      fs.writeFileSync(this.dismissedPath, JSON.stringify(Array.from(this.dismissed)))
    } catch {}
  }
}

export const announcementFetcher = new AnnouncementFetcher()

export function registerAnnouncementHandlers() {
  ipcMain.handle('announcements:list', () => announcementFetcher.list())
  ipcMain.handle('announcements:dismiss', (_, id: string) => { announcementFetcher.dismiss(id); return { ok: true } })
  ipcMain.handle('announcements:refresh', async () => { await announcementFetcher.fetchNow(); return { ok: true } })
}

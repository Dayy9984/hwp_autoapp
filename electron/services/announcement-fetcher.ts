// Worker /announcements 주기적 fetch + dismissed 캐시.
//
// 흐름:
//   1. 5분마다 (그리고 앱 시작 직후) GET /announcements?since=last
//   2. 사용자가 dismiss 한 id 는 disk 캐시에 저장
//   3. renderer 가 IPC announcement:list 호출 → dismiss 안 된 것만 반환

import { app, ipcMain } from 'electron'
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

  init() {
    this.dismissedPath = path.join(app.getPath('userData'), 'announcement-dismissed.json')
    try {
      if (fs.existsSync(this.dismissedPath)) {
        const arr = JSON.parse(fs.readFileSync(this.dismissedPath, 'utf8')) as string[]
        this.dismissed = new Set(Array.isArray(arr) ? arr : [])
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
      }
    } catch (e) {
      console.log('[announcement-fetcher] exception:', (e as Error).message)
    }
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

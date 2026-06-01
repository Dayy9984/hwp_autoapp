import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'

const WORKER = 'https://inserty-beta-worker.snsoffice.workers.dev'
const KEY = 'gquktB0RUgePlUSJjUiyh6MNGwafXKmhTVXj0-vKtkg'
const H = { 'X-Admin-Key': KEY, 'Content-Type': 'application/json' }

// 이전 state 있으면 알림 삭제
const stateFile = 'tmp_show_state.json'
if (fs.existsSync(stateFile)) {
  const s = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
  if (s.annId) {
    await fetch(`${WORKER}/admin/announcements/${s.annId}`, { method: 'DELETE', headers: H })
    console.log('이전 알림 삭제:', s.annId)
  }
}

// notifiedSet 초기화
const notifiedPath = path.join(os.homedir(), 'AppData/Roaming/Inserty AI/announcement-notified.json')
try { fs.writeFileSync(notifiedPath, '[]'); console.log('notifiedSet 초기화') } catch {}

// 신규 알림
const ann = await fetch(`${WORKER}/admin/announcements`, {
  method: 'POST', headers: H,
  body: JSON.stringify({
    title: '베타 사용자 안내',
    body: '이번 주 신규 기능 안내입니다.\n응답하기 클릭 시 만족도 폼이 노출됩니다.',
    priority: 80, type: 'general',
    starts_at: null, ends_at: null,
    embed_form_url: 'https://tally.so/embed/zxLqYa?alignLeft=1&hideTitle=0&transparentBackground=1',
    action_label: '응답하기',
    action_url: null,
  }),
})
const data = await ann.json()
console.log('신규 알림:', data.id)

// Inserty refresh
const br = await chromium.connectOverCDP('http://localhost:9222')
const ctx = br.contexts()[0]
const p = ctx.pages().find((p) => p.url().includes('localhost:5173') && !p.url().includes('devtools'))
if (p) {
  await p.evaluate(async () => { await (window).electronAPI?.announcements?.refresh?.() })
  console.log('자동 모달 노출')
}
await br.close()
fs.writeFileSync(stateFile, JSON.stringify({ annId: data.id }))

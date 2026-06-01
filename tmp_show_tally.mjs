// Tally 폼 모달 직접 띄움 — fix 검증 (LINEAR_SCALE 1-5, autoSave=0, sandbox)
import { chromium } from 'playwright'

const br = await chromium.connectOverCDP('http://localhost:9222')
const ctx = br.contexts()[0]
const p = ctx.pages().find((p) => p.url().includes('localhost:5173') && !p.url().includes('devtools'))
if (!p) { console.error('no main page'); process.exit(1) }

await p.evaluate(() => {
  const raw = localStorage.getItem('inserty-beta-survey')
  const cur = raw ? JSON.parse(raw) : { state: {}, version: 0 }
  cur.state = cur.state || {}
  cur.state.activeModal = { formKey: 'satisfaction_accept', title: '결과 만족도' }
  cur.state.pendingToast = null
  localStorage.setItem('inserty-beta-survey', JSON.stringify(cur))
})
await p.reload()
await p.waitForLoadState('domcontentloaded')
await p.waitForTimeout(2000)
console.log('Tally 폼 모달 직접 노출')
await br.close()

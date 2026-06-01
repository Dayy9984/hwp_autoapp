import { chromium } from 'playwright'
const br = await chromium.connectOverCDP('http://127.0.0.1:9223')
const ctx = br.contexts()[0]
const p = ctx.pages().find((p) => p.url().includes('localhost:5173') && !p.url().includes('devtools'))
if (!p) { console.error('no main page'); process.exit(1) }
await p.evaluate(() => {
  const raw = localStorage.getItem('inserty-beta-survey') || '{"state":{},"version":0}'
  const cur = JSON.parse(raw)
  cur.state = cur.state || {}
  cur.state.pendingToast = { kind: 'accept', formKey: 'satisfaction_accept', title: '결과 만족도' }
  cur.state.activeModal = null
  localStorage.setItem('inserty-beta-survey', JSON.stringify(cur))
})
await p.reload()
await p.waitForLoadState('domcontentloaded')
await p.waitForTimeout(2000)
console.log('inline 카드 (accept) 노출')
await br.close()

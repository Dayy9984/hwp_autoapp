// 항목 2/9: chat inline 피드백 카드 (accept 버전 — "문서 작성 결과는 만족스러운가요?")
import { chromium } from 'playwright'

const br = await chromium.connectOverCDP('http://localhost:9222')
const ctx = br.contexts()[0]
const p = ctx.pages().find((p) => p.url().includes('localhost:5173') && !p.url().includes('devtools'))
if (!p) { console.error('no main page'); process.exit(1) }

// pendingToast 주입 (accept 버전)
await p.evaluate(() => {
  const raw = localStorage.getItem('inserty-beta-survey')
  const cur = raw ? JSON.parse(raw) : { state: {}, version: 0 }
  cur.state = cur.state || {}
  cur.state.pendingToast = { kind: 'accept', formKey: 'satisfaction_accept', title: '결과 만족도' }
  cur.state.activeModal = null
  localStorage.setItem('inserty-beta-survey', JSON.stringify(cur))
})
await p.reload()
await p.waitForLoadState('domcontentloaded')
await p.waitForTimeout(2000)
console.log('inline 피드백 카드 노출됨 (accept 버전)')
await br.close()

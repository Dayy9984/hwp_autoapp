// 자동 라이센스 활성화 + 알림 fetch + Tally iframe 폼 검증 (사용자 화면에 보임)
const { chromium } = require('playwright')
const fs = require('fs')
const LICENSE_KEY = 'INSRT-YHJM-9TFC-NVLT-46KY'

async function shot(p, n) { fs.mkdirSync('tmp-act', { recursive: true }); await p.screenshot({ path: `tmp-act/${n}.png`, fullPage: true }) }

;(async () => {
  const b = await chromium.connectOverCDP('http://127.0.0.1:9222')
  const p = b.contexts()[0].pages()[0]
  const stats = { pass: 0, fail: 0 }
  const check = (l, c) => { if (c) { stats.pass++; console.log(`  ✓ ${l}`) } else { stats.fail++; console.log(`  ✗ ${l}`) } }
  await p.bringToFront()
  await shot(p, '01-gate')

  console.log('1. License activate')
  await p.locator('input[placeholder*="INSRT"]').fill(LICENSE_KEY)
  await p.locator('button:has-text("활성화")').first().click()
  console.log('   activate clicked, wait 12s for verify + announcement fetch + creds push')
  await p.waitForTimeout(12000)
  await shot(p, '02-after-activate')

  console.log('\n2. Main UI checks')
  const text = await p.evaluate(() => document.body.innerText)
  const isMain = !/라이센스 키 입력/.test(text)
  check('main UI loaded (no license gate)', isMain)
  await shot(p, '03-main')

  console.log('\n3. Bell + announcements')
  const bell = p.locator('button[aria-label*="알림"]').first()
  const bellCount = await bell.count()
  check(`alert bell visible (count=${bellCount})`, bellCount > 0)
  if (bellCount > 0) {
    await bell.click()
    await p.waitForTimeout(1500)
    await shot(p, '04-bell-panel')
    const annAny = await p.locator('text=/풀워크플로우|테스트 공지|E2E 테스트/').count()
    const empty = await p.locator('text=새 알림이 없습니다').count()
    check(`announcement items in panel (got ${annAny}, empty=${empty})`, annAny > 0)
    await p.mouse.click(900, 100).catch(() => {})
    await p.waitForTimeout(500)
  }

  console.log('\n4. Feature request — Tally iframe form visible?')
  const featBtn = p.locator('button[aria-label="기능 요청"]').first()
  if (await featBtn.count() > 0) {
    await featBtn.click()
    await p.waitForTimeout(4000)  // iframe 로딩 충분
    const iframeEl = p.locator('iframe[src*="tally.so"]').first()
    const iframeCount = await iframeEl.count()
    check(`feature_request iframe (count=${iframeCount})`, iframeCount > 0)
    if (iframeCount > 0) {
      // iframe 안 폼 inputs 개수 확인
      const frame = await iframeEl.contentFrame()
      if (frame) {
        await frame.waitForLoadState('domcontentloaded').catch(() => {})
        await p.waitForTimeout(2000)
        const frameTextarea = await frame.locator('textarea, input').count()
        const frameVisibleText = (await frame.evaluate(() => document.body.innerText).catch(() => '')).slice(0, 200)
        console.log(`   iframe inputs: ${frameTextarea}, text snippet: ${frameVisibleText.replace(/\n/g, ' | ')}`)
        check(`feature_request form has inputs (got ${frameTextarea})`, frameTextarea > 0)
      } else {
        console.log('   ✗ contentFrame() returned null (CSP?)')
        stats.fail++
      }
    }
    await shot(p, '05-feature-modal')
    await p.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
    await p.waitForTimeout(500)
  }

  console.log(`\n=== ${stats.pass} PASS / ${stats.fail} FAIL ===`)
  await b.close().catch(() => {})
})().catch((e) => { console.error('FATAL:', e.message); process.exit(1) })

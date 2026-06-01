const { chromium } = require('playwright')
;(async () => {
  const b = await chromium.connectOverCDP('http://127.0.0.1:9222')
  const p = b.contexts()[0].pages()[0]
  const text = await p.evaluate(() => document.body.innerText)
  console.log('=== body innerText (first 500 chars) ===')
  console.log(text.slice(0, 500))
  console.log('\n=== all visible inputs ===')
  const inputs = await p.locator('input:visible').evaluateAll((els) => els.map(e => ({
    type: e.type, placeholder: e.placeholder, readonly: e.readOnly, name: e.name,
  })))
  console.log(JSON.stringify(inputs, null, 2))
  console.log('\n=== alert bell ===')
  const bell = await p.locator('button[aria-label*="알림"]').count()
  console.log('bell:', bell)
  // 라이센스 게이트 흔적
  const hasLicGate = await p.locator('text=/INSRT|라이센스 키|라이센스 입력/').count()
  console.log('license gate markers:', hasLicGate)
  await b.close().catch(() => {})
})()

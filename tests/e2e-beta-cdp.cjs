// Playwright chromium.connectOverCDP 로 dev Electron 의 BrowserWindow attach.
// pnpm dev 가 띄운 Electron + remote-debugging-port 9222.

const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const LICENSE_KEY = 'INSRT-YHJM-9TFC-NVLT-46KY'

async function shot(page, name) {
  fs.mkdirSync('tmp-e2e-beta', { recursive: true })
  await page.screenshot({ path: `tmp-e2e-beta/${name}.png`, fullPage: true })
}

;(async () => {
  console.log('Connecting to Electron CDP @ 9222...')
  const browser = await chromium.connectOverCDP('http://localhost:9222')
  console.log('connected, contexts:', browser.contexts().length)

  // 모든 page (BrowserWindow) 중 메인 (5173) 찾기
  const contexts = browser.contexts()
  let page = null
  for (const ctx of contexts) {
    for (const p of ctx.pages()) {
      const url = p.url()
      console.log('  page:', url.slice(0, 80))
      if (url.includes('5173') || url.includes('index.html')) {
        page = p
        break
      }
    }
    if (page) break
  }
  if (!page) {
    // 첫 페이지로 fallback
    page = contexts[0]?.pages()[0]
  }
  if (!page) {
    console.error('no page found')
    process.exit(1)
  }
  console.log('using page:', page.url().slice(0, 80))
  await page.waitForLoadState('domcontentloaded').catch(() => {})
  await page.waitForTimeout(2000)

  const stats = { pass: 0, fail: 0 }
  const check = (label, cond) => { if (cond) { stats.pass++; console.log(`  ✓ ${label}`) } else { stats.fail++; console.log(`  ✗ ${label}`) } }

  await shot(page, '01-current')

  // ─── License gate skip — 메인 UI 가 떴는지 확인 ───
  console.log('\n1. Check main UI loaded')
  const isMain = await page.locator('text=/새 채팅|Inserty|새 채팅을 시작/').count()
  check(`main UI loaded (markers=${isMain})`, isMain > 0)

  // ─── 메인 UI / BetaCountdown ───
  console.log('\n2. Main UI')
  await page.waitForTimeout(2000)
  await shot(page, '04-main')
  const countdown = await page.locator('text=/베타 종료|D-\\d|2026.06.22|2026-06-22|남은/').count()
  check(`BetaCountdown (count=${countdown})`, countdown > 0)

  // ─── 알림 종 ───
  console.log('\n3. Announcement bell')
  const bell = page.locator('button[aria-label*="알림"]').first()
  const bellCount = await bell.count()
  check(`bell button (count=${bellCount})`, bellCount > 0)
  if (bellCount > 0) {
    await bell.click()
    await page.waitForTimeout(800)
    await shot(page, '05-bell-panel')
    const annTitles = await page.locator('text=/테스트 공지|E2E 테스트|풀E2E/').count()
    check(`announcement titles in panel (count=${annTitles})`, annTitles > 0)
    // 패널 닫기 (외부 클릭)
    await page.mouse.click(1000, 100).catch(() => {})
    await page.waitForTimeout(500)
  }

  // ─── 기능 요청 모달 ───
  console.log('\n4. Feature request modal')
  const featBtn = page.locator('button[aria-label="기능 요청"]').first()
  if (await featBtn.count() > 0) {
    await featBtn.click()
    await page.waitForTimeout(2500)
    const iframe = await page.locator('iframe[src*="tally.so"]').count()
    check(`feature_request Tally iframe (count=${iframe})`, iframe > 0)
    await shot(page, '06-feature-modal')
    await page.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
    await page.waitForTimeout(500)
  }

  // ─── 버그 신고 모달 ───
  console.log('\n5. Bug report modal')
  const bugBtn = page.locator('button[aria-label="버그 신고"]').first()
  if (await bugBtn.count() > 0) {
    await bugBtn.click()
    await page.waitForTimeout(2500)
    const iframe = await page.locator('iframe[src*="tally.so"]').count()
    check(`bug_report Tally iframe (count=${iframe})`, iframe > 0)
    await shot(page, '07-bug-modal')
    await page.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
    await page.waitForTimeout(500)
  }

  // ─── 설정 모달 ───
  console.log('\n6. Settings modal — Codex only')
  const setBtn = page.locator('button[aria-label="설정"]').first()
  if (await setBtn.count() > 0) {
    await setBtn.click()
    await page.waitForTimeout(800)
    // popover 안 '설정' 메뉴
    const setMenu = page.locator('button:has-text("설정")').nth(1)  // first is sidebar btn
    if (await setMenu.count() > 0) {
      await setMenu.click().catch(() => {})
      await page.waitForTimeout(1500)
    }
    await shot(page, '08-settings')
    // AI 탭
    const aiTab = page.locator('button:has-text("AI"), button:has-text("인공지능")').first()
    if (await aiTab.count() > 0) {
      await aiTab.click().catch(() => {})
      await page.waitForTimeout(800)
      await shot(page, '09-settings-ai')
    }
    const betaMsg = await page.locator('text=/베타 기간 동안.*Codex|Codex CLI.*베타|Codex CLI 모드만/').count()
    check(`Codex-only message (count=${betaMsg})`, betaMsg > 0)
    const apiKeyToggle = await page.locator('button:has-text("API Key")').count()
    check(`API Key toggle hidden (count=${apiKeyToggle}, should be 0)`, apiKeyToggle === 0)
  }

  console.log(`\n=== Beta CDP E2E: ${stats.pass} PASS / ${stats.fail} FAIL ===`)
  await browser.close().catch(() => {})
  if (stats.fail > 0) process.exit(1)
})().catch((e) => {
  console.error('FATAL:', e.message)
  console.error(e.stack)
  process.exit(1)
})

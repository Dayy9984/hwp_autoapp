// Packaged win-unpacked Inserty AI.exe 를 INSERTY_E2E=1 으로 실행 + Playwright CDP attach.
// 빌드된 0.1.9-beta.2 의 win-unpacked 사용.
//
// 흐름:
//   1. spawn 'Inserty AI.exe' (env: INSERTY_E2E=1 → main process 가 remote-debugging-port 9222 enable)
//   2. CDP /json/list 폴링 → 메인 BrowserWindow 잡힐 때까지 대기
//   3. Playwright chromium.connectOverCDP → 페이지 attach
//   4. 라이센스 활성화 (이미 활성화 안 됐으면 INSRT-... 입력)
//   5. 사이드바 알림 종 / Codex 전용 설정 / Tally 모달 검증 — 사용자 화면에 보임
//   6. 종료 + 결과 보고

const { spawn } = require('node:child_process')
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const ROOT = path.join(__dirname, '..')
const EXE_PATH = path.join(ROOT, 'release', '0.1.9-beta.2', 'win-unpacked', 'Inserty AI.exe')
const LICENSE_KEY = 'INSRT-YHJM-9TFC-NVLT-46KY'

async function shot(page, name) {
  fs.mkdirSync('tmp-e2e-packaged', { recursive: true })
  await page.screenshot({ path: `tmp-e2e-packaged/${name}.png`, fullPage: true })
}

async function waitForCdp(timeoutMs = 90000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch('http://127.0.0.1:9222/json/version').catch(() => null)
      if (r && r.ok) return true
    } catch {}
    await new Promise((rs) => setTimeout(rs, 1000))
  }
  return false
}

;(async () => {
  if (!fs.existsSync(EXE_PATH)) {
    console.error('exe not found:', EXE_PATH)
    process.exit(1)
  }

  console.log('1. spawn packaged Inserty AI.exe with INSERTY_E2E=1')
  const child = spawn(EXE_PATH, [], {
    env: { ...process.env, INSERTY_E2E: '1' },
    detached: true,
    stdio: 'ignore',
    windowsHide: false,
  })
  child.unref()  // 독립 GUI process 로 떨어뜨림
  console.log('   spawned, pid:', child.pid)

  console.log('2. wait for CDP 9222...')
  const ok = await waitForCdp(120000)
  if (!ok) {
    console.error('CDP timeout')
    try { child.kill() } catch {}
    process.exit(1)
  }
  console.log('   CDP ready')

  // 추가 대기 — splash → main window 전환
  await new Promise((r) => setTimeout(r, 5000))

  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222')
  const stats = { pass: 0, fail: 0 }
  const check = (label, cond) => { if (cond) { stats.pass++; console.log(`  ✓ ${label}`) } else { stats.fail++; console.log(`  ✗ ${label}`) } }

  // 모든 page 중 메인 (index.html 또는 splash 아님)
  let page = null
  for (let i = 0; i < 30 && !page; i++) {
    for (const ctx of browser.contexts()) {
      for (const p of ctx.pages()) {
        const u = p.url()
        if (u.includes('index.html') && !u.includes('splash')) { page = p; break }
      }
      if (page) break
    }
    if (!page) await new Promise((r) => setTimeout(r, 1500))
  }
  if (!page) {
    // splash 만 있을 수도 — 첫 페이지 사용
    page = browser.contexts()[0]?.pages()[0]
  }
  if (!page) { console.error('no page'); try { child.kill() } catch {}; process.exit(1) }
  console.log('   page:', page.url().slice(0, 80))
  await page.waitForLoadState('domcontentloaded').catch(() => {})
  await page.waitForTimeout(3000)
  await shot(page, '01-loaded')

  // ─── 라이센스 게이트 ───
  console.log('\n3. License gate')
  const licInput = page.locator('input[placeholder*="INSRT" i], input[placeholder*="라이센스" i], input[type="password"], input[type="text"]:visible').first()
  const hasLic = await licInput.count() > 0
  if (hasLic) {
    const placeholder = await licInput.getAttribute('placeholder').catch(() => '')
    const isLicInput = /INSRT|라이센스|키/i.test(placeholder || '')
    if (isLicInput) {
      check('license input', true)
      await licInput.fill(LICENSE_KEY)
      await shot(page, '02-license-typed')
      const actBtn = page.locator('button:has-text("활성화"), button:has-text("등록"), button:has-text("Activate"), button:has-text("입력")').first()
      if (await actBtn.count() > 0) {
        await actBtn.click()
        await page.waitForTimeout(8000)
        await shot(page, '03-activated')
      }
    } else {
      console.log('  (already in main UI)')
    }
  }

  // ─── 메인 UI ───
  console.log('\n4. Main UI')
  await page.waitForTimeout(2000)
  await shot(page, '04-main')
  const countdown = await page.locator('text=/베타 종료|D-\\d|2026.06.22|남은/').count()
  check(`BetaCountdown (count=${countdown})`, countdown > 0)

  // ─── 알림 종 ───
  console.log('\n5. Announcement bell + panel')
  const bell = page.locator('button[aria-label*="알림"]').first()
  const bellCount = await bell.count()
  check(`bell visible (count=${bellCount})`, bellCount > 0)
  if (bellCount > 0) {
    await bell.click()
    await page.waitForTimeout(1000)
    await shot(page, '05-bell-panel')
    const annTitles = await page.locator('text=/테스트 공지|E2E 테스트|풀E2E/').count()
    check(`announcement in panel (count=${annTitles})`, annTitles > 0)
    // empty state 라도 OK — "새 알림이 없습니다" 가 보이면 종이 작동하는 것 확인
    const emptyState = await page.locator('text=새 알림이 없습니다').count()
    if (emptyState > 0) {
      console.log('  (empty state shown — bell works but Worker fetch may be 401 due to no license token)')
    }
    await page.mouse.click(1000, 100).catch(() => {})
    await page.waitForTimeout(500)
  }

  // ─── 기능 요청 / 버그 신고 ───
  console.log('\n6. Feature/Bug modals')
  for (const [label, name] of [['기능 요청', '06-feature'], ['버그 신고', '07-bug']]) {
    const btn = page.locator(`button[aria-label="${label}"]`).first()
    if (await btn.count() > 0) {
      await btn.click()
      await page.waitForTimeout(2500)
      const iframe = await page.locator('iframe[src*="tally.so"]').count()
      check(`${label} Tally iframe (count=${iframe})`, iframe > 0)
      await shot(page, name)
      await page.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
      await page.waitForTimeout(500)
    }
  }

  // ─── 설정 Codex 전용 ───
  console.log('\n7. Settings Codex-only')
  // 설정 popover 버튼 (사이드바 하단 "설정")
  const settingsTrigger = page.locator('button[aria-label="설정"]').first()
  if (await settingsTrigger.count() > 0) {
    await settingsTrigger.click()
    await page.waitForTimeout(700)
    // popover 안 "설정" 메뉴 클릭
    const settingsMenuItem = page.locator('button, [role="menuitem"]').filter({ hasText: /^설정$/ }).nth(1)
    if (await settingsMenuItem.count() > 0) await settingsMenuItem.click().catch(() => {})
    await page.waitForTimeout(1500)
    // AI 탭
    const aiTab = page.locator('button:has-text("AI"), button:has-text("인공지능")').first()
    if (await aiTab.count() > 0) {
      await aiTab.click().catch(() => {})
      await page.waitForTimeout(800)
    }
    await shot(page, '08-settings')
    const betaMsg = await page.locator('text=/베타 기간 동안.*Codex|Codex CLI 모드만/').count()
    check(`Codex-only message (count=${betaMsg})`, betaMsg > 0)
    const apiToggle = await page.locator('button:has-text("API Key")').count()
    check(`API Key toggle hidden (count=${apiToggle}, should be 0)`, apiToggle === 0)
  }

  console.log(`\n=== Packaged Beta E2E: ${stats.pass} PASS / ${stats.fail} FAIL ===`)
  try { child.kill() } catch {}
  await new Promise((r) => setTimeout(r, 2000))
  if (stats.fail > 0) process.exit(1)
})().catch((e) => {
  console.error('FATAL:', e.message)
  console.error(e.stack)
  process.exit(1)
})

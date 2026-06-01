// Inserty 베타 Electron 자동 E2E (Playwright headed).
// dist-electron/main/index.mjs 를 Playwright Electron 으로 띄움 +
// VITE_DEV_SERVER_URL=http://localhost:5173 환경변수로 dev React 로딩.
//
// 검증:
//   1. 라이센스 게이트 노출 → INSRT-... 입력 → 메인 UI 진입
//   2. BetaCountdown 사이드바 표시
//   3. 알림 종 (BetaAnnouncementCenter) 표시 + 배지 + 패널 열기 → admin 발행 알림 노출
//   4. SettingsModal Codex 전용 (API Key 토글/섹션 없음)
//   5. 기능 요청 / 버그 신고 버튼 → Tally iframe 모달 노출
//
// 모든 단계 스크린샷 + PASS/FAIL 카운트.

const { _electron: electron } = require('playwright')
const path = require('path')
const fs = require('fs')

const ROOT = path.join(__dirname, '..')
const MAIN_ENTRY = path.join(ROOT, 'dist-electron', 'main', 'index.mjs')
const LICENSE_KEY = 'INSRT-YHJM-9TFC-NVLT-46KY'

async function shot(win, name) {
  fs.mkdirSync('tmp-e2e-beta', { recursive: true })
  await win.screenshot({ path: `tmp-e2e-beta/${name}.png`, fullPage: true })
}

;(async () => {
  if (!fs.existsSync(MAIN_ENTRY)) {
    console.error('main entry not found:', MAIN_ENTRY)
    process.exit(1)
  }

  const stats = { pass: 0, fail: 0 }
  const check = (label, cond) => {
    if (cond) { stats.pass++; console.log(`  ✓ ${label}`) }
    else { stats.fail++; console.log(`  ✗ ${label}`) }
  }

  console.log('Launching Electron via Playwright...')
  const app = await electron.launch({
    args: [MAIN_ENTRY, '--no-sandbox'],
    cwd: ROOT,
    timeout: 90000,
    env: {
      ...process.env,
      VITE_DEV_SERVER_URL: 'http://localhost:5173',
      NODE_ENV: 'development',
    },
  })

  // 첫 BrowserWindow 가 splash 일 수 있음 — main window 찾을 때까지 대기
  let win = await app.firstWindow({ timeout: 30000 })
  console.log('first window:', win.url())

  // main window 가 splash 가 아니라 dev URL 로딩 될 때까지 대기 (splash → main 전환)
  const startTime = Date.now()
  while (Date.now() - startTime < 90000) {
    const wins = app.windows()
    const mainWin = wins.find((w) => w.url().includes('localhost:5173') || w.url().includes('5173/index.html'))
    if (mainWin) { win = mainWin; break }
    await new Promise((r) => setTimeout(r, 1000))
  }
  console.log('main window:', win.url())
  await win.waitForLoadState('domcontentloaded')
  await win.waitForTimeout(2000)
  await shot(win, '01-loaded')

  // ─── 1. 라이센스 게이트 검증 + 입력 ───
  console.log('\n1. License gate')
  const licenseInput = win.locator('input[placeholder*="INSRT" i], input[placeholder*="라이센스" i], input[type="text"]').first()
  if (await licenseInput.count() > 0) {
    check('license input visible', true)
    await licenseInput.fill(LICENSE_KEY)
    await shot(win, '02-license-entered')
    // 활성화 버튼
    const activateBtn = win.locator('button:has-text("활성화"), button:has-text("Activate"), button:has-text("등록"), button:has-text("입력")').first()
    if (await activateBtn.count() > 0) {
      await activateBtn.click()
      await win.waitForTimeout(5000)
      await shot(win, '03-after-activate')
    }
  } else {
    // 이미 라이센스 활성화된 상태일 수도 — 메인 UI 곧장
    console.log('  (no license input — already activated)')
  }

  // ─── 2. 메인 UI / 사이드바 ───
  console.log('\n2. Main UI / Sidebar')
  await win.waitForTimeout(3000)
  // sidebar 존재 확인 (Sidebar.tsx 안 "새 채팅" 또는 채팅 목록)
  const sidebar = win.locator('aside, [class*="sidebar" i], [class*="Sidebar"]').first()
  check('sidebar visible', await sidebar.count() > 0 || await win.locator('text=/설정|채팅|기능 요청|버그 신고/').count() > 0)
  await shot(win, '04-main-ui')

  // ─── 3. BetaCountdown ───
  console.log('\n3. BetaCountdown')
  const countdown = await win.locator('text=/베타 종료|D-\\d|2026.06.22|2026-06-22|남은/').count()
  check(`BetaCountdown visible (got ${countdown})`, countdown > 0)

  // ─── 4. 알림 종 + 패널 ───
  console.log('\n4. Announcement bell + panel')
  const bellBtn = win.locator('button[aria-label*="알림"]').first()
  const bellCount = await bellBtn.count()
  check(`alert bell visible (count=${bellCount})`, bellCount > 0)
  if (bellCount > 0) {
    await bellBtn.click()
    await win.waitForTimeout(800)
    await shot(win, '05-bell-clicked')
    const annTitles = await win.locator('text=/테스트 공지|E2E 테스트|풀E2E/').count()
    check(`announcement titles in panel (got ${annTitles})`, annTitles > 0)
    // 패널 닫기
    await win.keyboard.press('Escape').catch(() => {})
    await win.waitForTimeout(500)
  }

  // ─── 5. 기능 요청 / 버그 신고 모달 ───
  console.log('\n5. Feature request / Bug report')
  const featureBtn = win.locator('button[aria-label="기능 요청"]').first()
  if (await featureBtn.count() > 0) {
    await featureBtn.click()
    await win.waitForTimeout(2500)
    const iframe = await win.locator('iframe[src*="tally.so"]').count()
    check(`feature_request Tally iframe (count=${iframe})`, iframe > 0)
    await shot(win, '06-feature-modal')
    // 모달 닫기 (X 버튼 또는 외부 클릭)
    await win.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
    await win.waitForTimeout(500)
  } else { check('feature_request button', false) }

  const bugBtn = win.locator('button[aria-label="버그 신고"]').first()
  if (await bugBtn.count() > 0) {
    await bugBtn.click()
    await win.waitForTimeout(2500)
    const iframe = await win.locator('iframe[src*="tally.so"]').count()
    check(`bug_report Tally iframe (count=${iframe})`, iframe > 0)
    await shot(win, '07-bug-modal')
    await win.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
    await win.waitForTimeout(500)
  } else { check('bug_report button', false) }

  // ─── 6. 설정 모달 (Codex 전용) ───
  console.log('\n6. Settings modal — Codex only')
  const settingsBtn = win.locator('button[aria-label="설정"]').first()
  if (await settingsBtn.count() > 0) {
    await settingsBtn.click()
    await win.waitForTimeout(800)
    // popover 열린 상태에서 "설정" 메뉴 클릭
    const settingsMenu = win.locator('button:has-text("설정"), [role="menuitem"]:has-text("설정")').first()
    if (await settingsMenu.count() > 0) await settingsMenu.click().catch(() => {})
    await win.waitForTimeout(1500)
    await shot(win, '08-settings-open')
    // AI 탭 클릭
    const aiTab = win.locator('button:has-text("AI"), button:has-text("인공지능"), [role="tab"]:has-text("AI")').first()
    if (await aiTab.count() > 0) {
      await aiTab.click().catch(() => {})
      await win.waitForTimeout(800)
    }
    await shot(win, '09-settings-ai-tab')
    // 베타 전용 메시지
    const betaMsg = await win.locator('text=/베타 기간 동안.*Codex|Codex CLI.*베타/').count()
    check(`Codex-only 베타 message (got ${betaMsg})`, betaMsg > 0)
    // API Key 토글이 없어야 함
    const apiKeyToggle = await win.locator('button:has-text("API Key")').count()
    check(`API Key toggle hidden (got ${apiKeyToggle}, should be 0)`, apiKeyToggle === 0)
  } else { check('settings button', false) }

  await win.waitForTimeout(1500)
  await app.close()

  console.log(`\n=== Beta App Auto E2E: ${stats.pass} PASS / ${stats.fail} FAIL ===`)
  if (stats.fail > 0) process.exit(1)
})().catch((e) => {
  console.error('FATAL:', e.message)
  console.error(e.stack)
  process.exit(1)
})

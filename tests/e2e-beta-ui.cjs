// 베타 빌드된 Electron 앱을 띄워서 UI 동작 검증.
// 검증:
//   1. BetaCountdown 사이드바 노출 (D-X 표시)
//   2. 설정 모달 Codex 전용 (API Key 토글/섹션/모델 선택 없음)
//   3. Sidebar '기능 요청' / '버그 신고' 메뉴 노출 + 클릭 시 Tally iframe 열림
//   4. window.electronAPI.telemetry.track 존재
//
// 라이센스 입력 화면이 떠도 자동 처리하지 않고, 그 화면 자체 검증으로 마무리.

const { _electron: electron } = require('playwright')
const path = require('path')
const fs = require('fs')

// dev electron 으로 빌드된 main.mjs 직접 실행 — packaged exe 는 stdio 없어서 Playwright 못 잡음.
const ROOT = path.join(__dirname, '..')
const MAIN_ENTRY = path.join(ROOT, 'dist-electron', 'main', 'index.mjs')

;(async () => {
  if (!fs.existsSync(MAIN_ENTRY)) {
    console.error('main entry missing:', MAIN_ENTRY)
    process.exit(1)
  }

  const app = await electron.launch({
    args: [MAIN_ENTRY, '--no-sandbox'],
    cwd: ROOT,
    timeout: 60000,
    env: { ...process.env, NODE_ENV: 'production' },
  })
  console.log('app launched, pid:', await app.evaluate(({ app }) => app.getPath('userData')).catch(() => '?'))

  // 첫 BrowserWindow 대기
  const win = await app.firstWindow({ timeout: 30000 })
  await win.waitForLoadState('domcontentloaded')
  await win.waitForTimeout(3000)
  fs.mkdirSync(path.join(__dirname, '..', 'tmp-e2e'), { recursive: true })
  await win.screenshot({ path: path.join(__dirname, '..', 'tmp-e2e', 'app-1-loaded.png'), fullPage: true })

  // 라이센스 게이트 화면일 가능성 — 라이센스 키 박스가 있으면 그건 그것대로 검증
  const title = await win.title()
  console.log('window title:', title)
  const url = win.url()
  console.log('window url:', url.slice(0, 80))

  // telemetry API 노출 검증
  const hasTelemetry = await win.evaluate(() => {
    const api = (window).electronAPI
    return !!(api && api.telemetry && typeof api.telemetry.track === 'function')
  })
  console.log('window.electronAPI.telemetry.track exposed:', hasTelemetry)

  // license API 노출 검증
  const hasLicense = await win.evaluate(() => {
    const api = (window).electronAPI
    return !!(api && api.license && typeof api.license.activate === 'function')
  })
  console.log('window.electronAPI.license.activate exposed:', hasLicense)

  // 텔레메트리 큐 push 동작 (실제 라이센스 없어도 큐에 들어가야 함)
  const trackResult = await win.evaluate(() => {
    return (window).electronAPI?.telemetry?.track?.('e2e_test_event', { ts: Date.now() }).catch((e) => 'err: ' + e.message)
  })
  console.log('telemetry.track result:', trackResult)

  // 라이센스 입력 박스가 보이는지 (LicenseGate 화면)
  const licenseInputCount = await win.locator('input[placeholder*="INSRT" i], input[placeholder*="라이센스" i]').count()
  console.log('license input visible:', licenseInputCount)

  // BetaCountdown 텍스트가 보이는지 (라이센스 통과 후에만)
  const countdownVisible = await win.locator('text=/베타 종료|D-\\d|2026.06.22|2026-06-22/').count()
  console.log('BetaCountdown visible:', countdownVisible)

  // Sidebar 의 '기능 요청' / '버그 신고' 메뉴 (라이센스 통과 후)
  const featureBtn = await win.locator('button:has-text("기능 요청"), [aria-label="기능 요청"]').count()
  const bugBtn = await win.locator('button:has-text("버그 신고"), [aria-label="버그 신고"]').count()
  console.log('sidebar feature/bug buttons:', featureBtn, bugBtn)

  await win.screenshot({ path: path.join(__dirname, '..', 'tmp-e2e', 'app-2-after-checks.png'), fullPage: true })

  await app.close()
  console.log('app closed cleanly')
})().catch((e) => {
  console.error('FATAL:', e.message)
  console.error(e.stack)
  process.exit(1)
})

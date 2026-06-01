// 빌드된 Electron 앱의 renderer JS 에러 진단.
// page console / pageerror / unhandledrejection 모두 캡처 + DOM 내용 확인.

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')

const APP_EXE = path.resolve(
  __dirname,
  '..',
  'release',
  '0.1.9',
  'win-unpacked',
  'Inserty AI.exe',
)

delete process.env.ELECTRON_RUN_AS_NODE
const env = Object.entries(process.env).map(([name, value]) => ({ name, value }))

;(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-err-'))
  console.log('userDataDir:', userDataDir)

  const app = await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })
  console.log('— app launched')

  // 모든 윈도우의 console + pageerror 수집
  const events = []
  app.on('window', (win) => {
    const url = win.url?.() || '?'
    console.log(`[window opened] ${url}`)
    win.on('pageerror', (e) => {
      events.push({ kind: 'pageerror', msg: String(e), stack: e.stack })
      console.log(`[PAGEERROR] ${e.message || e}\n${e.stack || ''}`)
    })
    win.on('console', (msg) => {
      const t = msg.type()
      if (t === 'error' || t === 'warning') {
        const text = msg.text()
        events.push({ kind: 'console:' + t, text })
        console.log(`[${t.toUpperCase()}] ${text}`)
      }
    })
    win.on('crash', () => {
      events.push({ kind: 'crash' })
      console.log('[CRASH]')
    })
  })

  // first window 대기
  const first = await app.firstWindow({ timeout: 60000 })
  console.log('— first window:', first.url())
  // 위에서 등록한 핸들러는 firstWindow에는 이미 늦었을 수 있으니 직접도 등록
  first.on('pageerror', (e) => {
    events.push({ kind: 'pageerror:first', msg: String(e), stack: e.stack })
    console.log(`[PAGEERROR:first] ${e.message || e}\n${e.stack || ''}`)
  })
  first.on('console', (msg) => {
    const t = msg.type()
    if (t === 'error' || t === 'warning') {
      const text = msg.text()
      events.push({ kind: 'console:first:' + t, text })
      console.log(`[${t.toUpperCase()}:first] ${text}`)
    }
  })

  // 5초 대기 — 메인 UI까지 로딩 시간
  await new Promise((r) => setTimeout(r, 5000))

  // 모든 윈도우 조회
  const windows = app.windows()
  console.log(`— total windows: ${windows.length}`)
  for (let i = 0; i < windows.length; i++) {
    const w = windows[i]
    console.log(`  window[${i}]: ${w.url()}`)
    try {
      const title = await w.title()
      console.log(`    title: ${title}`)
      const ready = await w.evaluate(() => ({
        readyState: document.readyState,
        bodyHTML: document.body ? document.body.innerHTML.slice(0, 400) : '(no body)',
        electronAPI: typeof (window).electronAPI,
        licenseAPI: typeof (window).electronAPI?.license,
        hasLicenseActivate: typeof (window).electronAPI?.license?.activate,
        hasGetInitial: typeof (window).electronAPI?.license?.getInitialStatus,
      }))
      console.log(`    readyState: ${ready.readyState}`)
      console.log(`    electronAPI: ${ready.electronAPI}, license: ${ready.licenseAPI}`)
      console.log(`    license.activate: ${ready.hasLicenseActivate}, getInitial: ${ready.hasGetInitial}`)
      console.log(`    body[0..400]: ${ready.bodyHTML.replace(/\s+/g, ' ').slice(0, 400)}`)
    } catch (e) {
      console.log(`    inspect err: ${e.message}`)
    }
  }

  // 라이센스 게이트 텍스트 / 메인 UI 텍스트 탐지
  console.log('\n— UI 탐지 시도')
  let foundLicenseGate = false
  let foundMainUI = false
  for (const w of windows) {
    try {
      const detect = await w.evaluate(() => ({
        hasKeyInput: !!document.querySelector('input[placeholder*="INSRT"]'),
        hasLicenseGateText: document.body && document.body.innerText.includes('라이센스'),
        hasSidebar: !!document.querySelector('aside') || !!document.querySelector('[class*="sidebar"]') ||
                    !!document.querySelector('[class*="Sidebar"]'),
        innerText: document.body ? document.body.innerText.slice(0, 200) : '',
      }))
      console.log(`  ${w.url().slice(-30)}:`, JSON.stringify(detect))
      if (detect.hasKeyInput || detect.hasLicenseGateText) foundLicenseGate = true
      if (detect.hasSidebar) foundMainUI = true
    } catch (e) {
      console.log('  detect err:', e.message)
    }
  }

  console.log('\n=== SUMMARY ===')
  console.log(`LicenseGate detected: ${foundLicenseGate}`)
  console.log(`Main UI (sidebar) detected: ${foundMainUI}`)
  console.log(`Total events: ${events.length}`)
  const errors = events.filter((e) => e.kind.includes('error') || e.kind === 'crash')
  console.log(`Errors+crashes: ${errors.length}`)
  if (errors.length) {
    console.log('\n--- ALL ERRORS ---')
    errors.forEach((e, i) => {
      console.log(`[${i}] ${e.kind}: ${e.msg || e.text || ''}`)
      if (e.stack) console.log(e.stack)
    })
  }

  // 스크린샷
  if (windows.length > 0) {
    const ss = path.join(__dirname, '..', 'release', '0.1.9', 'e2e-screenshot.png')
    try {
      await windows[windows.length - 1].screenshot({ path: ss, fullPage: false })
      console.log(`\nScreenshot saved: ${ss}`)
    } catch (e) {
      console.log('screenshot err:', e.message)
    }
  }

  await app.close()
})().catch((e) => {
  console.error('FATAL:', e)
  process.exit(2)
})

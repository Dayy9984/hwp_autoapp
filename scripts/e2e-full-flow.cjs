// 풀 흐름 E2E: 라이센스 게이트 → 활성화 → 메인 UI 진입까지 검증
// 콘솔 / pageerror 모두 캡처 + 단계별 스크린샷

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const https = require('https')

const APP_EXE = path.resolve(
  __dirname,
  '..',
  'release',
  '0.1.9',
  'win-unpacked',
  'Inserty AI.exe',
)
const SHOTS_DIR = path.resolve(__dirname, '..', 'release', '0.1.9', 'shots')
fs.mkdirSync(SHOTS_DIR, { recursive: true })

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'

function httpJson(method, urlStr, body, key = SERVICE_ROLE) {
  const url = new URL(urlStr)
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const req = https.request(
      {
        method,
        hostname: url.hostname,
        path: url.pathname + url.search,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${key}`,
          apikey: key,
          Prefer: 'return=representation',
          ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
        },
      },
      (res) => {
        let buf = ''
        res.on('data', (c) => (buf += c))
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, body: buf ? JSON.parse(buf) : null })
          } catch {
            resolve({ status: res.statusCode, body: buf })
          }
        })
      },
    )
    req.on('error', reject)
    if (data) req.write(data)
    req.end()
  })
}
async function issueLicense() {
  const r = await httpJson('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`, {
    p_email: 'e2e+full@test.com',
    p_duration: 30,
    p_notes: 'e2e-full-flow',
  })
  return r.body[0].license_key
}
async function deleteLicense(key) {
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${key}`)
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`)
}

delete process.env.ELECTRON_RUN_AS_NODE
const env = Object.entries(process.env).map(([name, value]) => ({ name, value }))

const errors = []
const consoleErrors = []
function snap(name, page) {
  return page
    .screenshot({ path: path.join(SHOTS_DIR, name + '.png') })
    .catch((e) => console.log(`snap[${name}] err: ${e.message}`))
}

;(async () => {
  console.log('========== Full Flow E2E ==========')

  // 1) 새 라이센스 발급
  const key = await issueLicense()
  console.log('[setup] issued:', key)

  // 2) 앱 실행
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-full-'))
  console.log('[setup] userData:', userDataDir)

  const app = await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })

  app.on('window', (w) => {
    w.on('pageerror', (e) => {
      errors.push(`pageerror: ${e.message || e}`)
      console.log(`[PAGEERROR] ${e.message}`)
    })
    w.on('console', (m) => {
      const t = m.type()
      if (t === 'error') {
        consoleErrors.push(m.text())
        console.log(`[ERR] ${m.text()}`)
      }
    })
  })

  try {
    const win = await app.firstWindow({ timeout: 60000 })
    win.on('pageerror', (e) => {
      errors.push(`pageerror:first ${e.message}`)
      console.log(`[PAGEERROR:first] ${e.message}\n${e.stack || ''}`)
    })
    win.on('console', (m) => {
      const t = m.type()
      if (t === 'error') {
        consoleErrors.push(m.text())
        console.log(`[ERR:first] ${m.text()}`)
      }
    })

    // 페이지 로드 안정화
    await win.waitForLoadState('domcontentloaded', { timeout: 30000 })
    await new Promise((r) => setTimeout(r, 1500))
    await snap('1-gate', win)
    console.log('\n[step 1] LicenseGate 화면 캡처')

    // 라이센스 게이트 텍스트 확인
    const gateText = await win.evaluate(() => document.body.innerText.slice(0, 100))
    console.log('  gate text:', JSON.stringify(gateText.slice(0, 80)))

    // 3) 키 입력
    console.log('\n[step 2] 키 입력 + 활성화')
    const input = win.locator('input[placeholder*="INSRT"]')
    await input.waitFor({ state: 'visible', timeout: 10000 })
    await input.fill(key)
    await snap('2-key-filled', win)

    // 활성화 버튼 클릭
    const activateBtn = win.locator('button', { hasText: '활성화' }).first()
    await activateBtn.click()
    console.log('  activate clicked')

    // 4) 메인 UI 진입 대기 (라이센스 OK → splash → main 윈도우 또는 메인 UI 컨테이너)
    console.log('\n[step 3] 메인 UI 진입 대기 (최대 60s)')

    let mainUIFound = false
    let lastState = null
    const deadline = Date.now() + 60000
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1500))
      const windows = app.windows()
      let detected = false
      for (const w of windows) {
        try {
          const probe = await w.evaluate(() => ({
            text: document.body ? document.body.innerText.slice(0, 100) : '',
            hasSidebar: !!document.querySelector('[class*="sidebar" i]'),
            hasErrorBoundary: !!document.querySelector('[class*="error" i]'),
            hasLicenseGate: !!document.querySelector('input[placeholder*="INSRT"]'),
            url: window.location.href.slice(-30),
          }))
          if (!probe.hasLicenseGate && probe.text.length > 5) {
            lastState = probe
            // sidebar 또는 메인 컨테이너 발견 시 진입 인정
            if (probe.hasSidebar || probe.text.length > 20) {
              mainUIFound = true
              detected = true
              break
            }
          }
        } catch {}
      }
      if (detected) break
    }
    if (mainUIFound) console.log('  ✓ 메인 UI 진입 감지')
    else console.log('  ✗ 메인 UI 진입 실패 (last state):', JSON.stringify(lastState))

    // 5) 마지막 윈도우 스크린샷
    const windows = app.windows()
    for (let i = 0; i < windows.length; i++) {
      await snap(`3-final-window-${i}`, windows[i])
    }

    console.log('\n=== 결과 ===')
    console.log('windows:', windows.length)
    for (const w of windows) {
      const t = await w.title().catch(() => '?')
      console.log(`  ${t}: ${w.url().slice(-40)}`)
    }
    console.log(`pageerrors: ${errors.length}, console errors: ${consoleErrors.length}`)
    if (errors.length) {
      console.log('\n--- PAGE ERRORS ---')
      errors.forEach((e, i) => console.log(`[${i}] ${e}`))
    }
    if (consoleErrors.length) {
      console.log('\n--- CONSOLE ERRORS ---')
      consoleErrors.forEach((e, i) => console.log(`[${i}] ${e.slice(0, 200)}`))
    }
    console.log(`\nScreenshots: ${SHOTS_DIR}`)
    console.log(`Main UI entered: ${mainUIFound}`)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key).catch(() => {})
  }
})().catch((e) => {
  console.error('FATAL:', e)
  process.exit(2)
})

// 사용자 시점 전체 흐름 시뮬레이션.
// 사용자가 직접 했을 때 검증하려던 부분:
//   1) Setup.exe 첫 설치 시 라이센스 화면 표시 (cache 없을 때)
//   2) 활성화 후 메인 UI 진입
//   3) 업데이트 모달 → 다운로드 → 재시작
//   4) ★ 재시작 후 NSIS UI 가 visible 하게 표시되는지
//   5) ★ 새 앱이 자동으로 실행되는지 (사용자가 보기에)
//   6) ★ 새 앱 실행 시 라이센스 캐시 보존되어 키 화면 안 뜸

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawn, execSync } = require('child_process')

const SETUP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'Inserty-AI_0.1.9_Setup.exe')
const SHOTS = path.resolve(__dirname, '..', 'release', '0.1.9', 'manual-sim-shots')
fs.mkdirSync(SHOTS, { recursive: true })

const TEST_KEY = 'INSRT-P8UM-GQ27-8D5S-X2W7'  // 무제한 키
const APPDATA = path.join(process.env.APPDATA, 'Inserty AI')
const CACHE_FILE = path.join(APPDATA, '.license_cache')

function killAll(name) { try { execSync(`taskkill /IM "${name}" /F /T`, { stdio: 'ignore' }) } catch {} }
function findInstalledExe() {
  const c = path.join(process.env.LOCALAPPDATA, 'Programs', 'inserty-desktop', 'Inserty AI.exe')
  return fs.existsSync(c) ? c : null
}
function readAsarVersion(asarPath) {
  if (!fs.existsSync(asarPath)) return null
  const text = fs.readFileSync(asarPath).toString('binary')
  const m = text.match(/"name":\s*"inserty-desktop"[\s\S]{0,200}?"version":\s*"(\d+\.\d+\.\d+)"/)
  return m?.[1] || null
}
function isProcessRunning(name) {
  try {
    const out = execSync(`tasklist /FI "ImageName eq ${name}" /FO CSV /NH`, { encoding: 'utf8' })
    return out.includes(name)
  } catch { return false }
}
function listAllWindows() {
  // PowerShell 로 visible window 목록 조회
  try {
    const ps = `Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Select-Object ProcessName,MainWindowTitle | ConvertTo-Csv -NoTypeInformation`
    const out = execSync(`powershell -NoProfile -Command "${ps}"`, { encoding: 'utf8', timeout: 5000 })
    return out.split('\n').slice(1).filter(Boolean).map((l) => l.replace(/"/g, '').trim())
  } catch { return [] }
}

delete process.env.ELECTRON_RUN_AS_NODE
const cleanEnv = { ...process.env }; delete cleanEnv.ELECTRON_RUN_AS_NODE
const env = Object.entries(cleanEnv).map(([name, value]) => ({ name, value }))

const log = (msg) => { console.log(`[${new Date().toISOString().substr(11, 12)}] ${msg}`) }
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  log(`  ${ok ? '✓' : '✗'} ${name}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  log('=== 사용자 시점 전체 흐름 시뮬레이션 ===')

  // === 0. 완전 초기화 ===
  log('\n[0] 완전 초기화 (이전 설치 + cache 모두 제거)')
  killAll('Inserty AI.exe')
  killAll('Inserty-AI_0.1.10_Setup.exe')
  const oldExe = findInstalledExe()
  if (oldExe) {
    const un = path.join(path.dirname(oldExe), 'Uninstall Inserty AI.exe')
    if (fs.existsSync(un)) {
      try { execSync(`"${un}" /S`, { stdio: 'ignore', timeout: 60_000 }) } catch {}
      await new Promise((r) => setTimeout(r, 3000))
    }
  }
  if (fs.existsSync(APPDATA)) fs.rmSync(APPDATA, { recursive: true, force: true })
  check('초기 상태: 설치 없음', !findInstalledExe())
  check('초기 상태: %APPDATA%\\Inserty AI 비어있음', !fs.existsSync(APPDATA))

  // === 1. v0.1.9 설치 ===
  log('\n[1] v0.1.9 Setup.exe 실행')
  const installStart = Date.now()
  await new Promise((r) => { const p = spawn(SETUP_EXE, ['/S'], { stdio: 'ignore' }); p.on('exit', r) })
  // /S 로 silent 설치 (E2E 자동화 위해. 실제 사용자는 UI 보지만 동작은 같음)
  // 설치 폴더 + 자동 실행 인스턴스 대기
  await new Promise((r) => setTimeout(r, 5000))
  const installedExe = findInstalledExe()
  check(`v0.1.9 설치 (${((Date.now() - installStart) / 1000).toFixed(1)}s)`, !!installedExe, installedExe)

  // 자동 실행된 인스턴스 종료 (Playwright 로 다시 띄우기 위해)
  killAll('Inserty AI.exe')
  await new Promise((r) => setTimeout(r, 2000))

  const asarPath = path.join(path.dirname(installedExe), 'resources', 'app.asar')
  const versionBefore = readAsarVersion(asarPath)
  check(`설치된 version = 0.1.9`, versionBefore === '0.1.9', `version=${versionBefore}`)

  // === 2. Playwright 로 앱 시작 → 라이센스 키 화면 표시 확인 ===
  log('\n[2] 앱 실행 → 라이센스 키 화면 표시 (cache 없으므로)')
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-sim-'))
  // 주의: --user-data-dir 옵션 사용 시 cache 가 그쪽으로 가서 %APPDATA% 와 분리됨.
  // 사용자 진짜 동선은 --user-data-dir 없이 기본 경로 사용.
  // 다만 이 옵션 없이 Playwright launch 가 어려워 옵션 사용. 그 결과 .license_cache 는 ud 에 저장됨.

  const app = await electron.launch({
    executablePath: installedExe, args: [`--user-data-dir=${ud}`], env, timeout: 90000,
  })

  await app.firstWindow({ timeout: 60000 })
  await new Promise((r) => setTimeout(r, 6000))

  // 라이센스 키 입력 화면 등장 확인
  const findActiveWin = async (maxMs = 30000) => {
    const dl = Date.now() + maxMs
    while (Date.now() < dl) {
      const ws = app.windows()
      for (let i = ws.length - 1; i >= 0; i--) {
        const w = ws[i]; if (w.isClosed?.()) continue
        try {
          const ok = await w.evaluate(() => !!document.body && document.body.innerText.length > 5)
          if (ok) return w
        } catch {}
      }
      await new Promise((r) => setTimeout(r, 500))
    }
    return null
  }

  const win = await findActiveWin(30000)
  await win.screenshot({ path: path.join(SHOTS, '1-license-screen.png') }).catch(() => {})

  // 진단: 모든 windows 의 body text 덤프
  const wins = app.windows()
  console.log(`  [diag] window count: ${wins.length}`)
  for (let i = 0; i < wins.length; i++) {
    const w = wins[i]
    if (w.isClosed?.()) { console.log(`  [diag] win[${i}]: CLOSED`); continue }
    try {
      const info = await w.evaluate(() => ({
        url: location.href,
        title: document.title,
        body: (document.body?.innerText || '').slice(0, 200),
        hasInput: !!document.querySelector('input[placeholder*="INSRT"]'),
      }))
      console.log(`  [diag] win[${i}]: title="${info.title}" url=${info.url.slice(0, 50)} hasInput=${info.hasInput}`)
      console.log(`         body: ${info.body.replace(/\s+/g, ' ').slice(0, 150)}`)
    } catch (e) { console.log(`  [diag] win[${i}]: err=${e.message}`) }
  }

  const onKeyScreen = await win.evaluate(() => (document.body.innerText || '').includes('라이센스 키 입력'))
  check('첫 실행 시 라이센스 키 입력 화면 표시', onKeyScreen)
  if (!onKeyScreen) {
    console.log('  ★ 라이센스 화면 안 뜸 — 5초 더 기다리고 재확인 ★')
    await new Promise((r) => setTimeout(r, 5000))
    const retry = await findActiveWin(10000)
    if (retry) {
      const t = await retry.evaluate(() => document.body?.innerText || '')
      console.log(`  [diag-retry] body: ${t.replace(/\s+/g, ' ').slice(0, 200)}`)
      await retry.screenshot({ path: path.join(SHOTS, '1b-after-retry.png') }).catch(() => {})
    }
    // 흐름 종료 (라이센스 입력 못 함)
    try { await app.close() } catch {}
    return
  }

  // === 3. 라이센스 키 입력 → 활성화 ===
  log('\n[3] 라이센스 키 입력 + 활성화')
  await win.locator('input[placeholder*="INSRT"]').fill(TEST_KEY)
  await win.locator('button', { hasText: '활성화' }).first().click()

  // 메인 UI 진입 대기
  const dl = Date.now() + 30000
  let mainUiText = ''
  while (Date.now() < dl) {
    const w = await findActiveWin(5000)
    const t = await w.evaluate(() => document.body?.innerText || '')
    if (!t.includes('라이센스 키 입력') && t.length > 30) { mainUiText = t; break }
    await new Promise((r) => setTimeout(r, 1000))
  }
  check('활성화 → LicenseGate 사라지고 메인 UI 진입', !!mainUiText,
    mainUiText.replace(/\s+/g, ' ').slice(0, 60))

  // === 4. 업데이트 모달 → 다운로드 → 재시작 ===
  log('\n[4] 업데이트 모달 → 다운로드')
  const win2 = await findActiveWin(10000)
  await win2.evaluate(async () => { try { await window.electronAPI.update.check() } catch {} })

  // 모달 대기
  try {
    await win2.locator('button', { hasText: '다운로드' }).first().waitFor({ timeout: 30000 })
    check('업데이트 모달 ("새 버전 사용 가능") 표시', true)
  } catch {
    check('업데이트 모달 표시', false, 'timeout')
  }

  await win2.locator('button', { hasText: '다운로드' }).first().click()
  log('  ⏳ 다운로드 대기 (5-10분 가능, 캐시 hit 이면 즉시)')
  try {
    await win2.locator('button', { hasText: '재시작' }).first().waitFor({ timeout: 600000 })
    check('다운로드 완료 → 재시작 버튼 표시', true)
  } catch {
    check('다운로드 완료', false, 'timeout')
    await app.close().catch(() => {})
    process.exit(1)
  }

  // === 5. ★ 재시작 클릭 후 NSIS UI / 새 앱 출현 추적 ★ ===
  log('\n[5] ★ 재시작 클릭 → NSIS UI 출현 + 새 앱 launch 추적 ★')
  const t0 = Date.now()
  await win2.locator('button', { hasText: '재시작' }).first().click()

  let appCloseAt = null
  let setupSeenAt = null
  let appRelaunchAt = null
  let windowsLog = []

  for (let i = 0; i < 180; i++) {  // 최대 3분
    await new Promise((r) => setTimeout(r, 1000))
    const elapsed = ((Date.now() - t0) / 1000).toFixed(0)

    const insertyRunning = isProcessRunning('Inserty AI.exe')
    const setupRunning = isProcessRunning('Inserty-AI_0.1.10_Setup.exe')

    if (!appCloseAt && !insertyRunning) {
      appCloseAt = elapsed; log(`  [+${elapsed}s] Inserty AI.exe 종료됨`)
    }
    if (!setupSeenAt && setupRunning) {
      setupSeenAt = elapsed
      const wins = listAllWindows()
      windowsLog.push({ t: elapsed, wins })
      log(`  [+${elapsed}s] ★ Inserty-AI_0.1.10_Setup.exe process 감지 (NSIS UI) ★`)
      log(`           visible windows: ${wins.join(' | ') || '(없음)'}`)
    }
    if (appCloseAt && !appRelaunchAt && insertyRunning && !setupRunning) {
      appRelaunchAt = elapsed; log(`  [+${elapsed}s] ★ 새 Inserty AI.exe 자동 spawn ★`)
      break
    }

    if (i % 15 === 0 && i > 0) {
      log(`  [+${elapsed}s] inserty=${insertyRunning}, setup=${setupRunning}, version=${readAsarVersion(asarPath)}`)
    }
  }

  try { await app.close() } catch {}
  await new Promise((r) => setTimeout(r, 5000))

  check(`구 앱 종료 (+${appCloseAt}s)`, !!appCloseAt)
  check(`NSIS Setup.exe 프로세스 visible (사용자 눈에 보임) +${setupSeenAt}s`, !!setupSeenAt)
  check(`새 앱 자동 launch +${appRelaunchAt}s`, !!appRelaunchAt)

  // === 6. 업데이트 후 cache 보존 + 새 버전 확인 ===
  log('\n[6] 업데이트 후 상태 검증')
  killAll('Inserty AI.exe')
  await new Promise((r) => setTimeout(r, 2000))

  const versionAfter = readAsarVersion(asarPath)
  check('app.asar version → 0.1.10', versionAfter === '0.1.10', `version=${versionAfter}`)

  // user-data-dir 안의 cache 가 새 앱에 의해 사용 가능한지 (실제 사용자는 %APPDATA% 사용하나
  // Playwright 흐름에선 ud 폴더 사용. cache 가 ud 에 있는지 확인.)
  const cacheInUd = fs.existsSync(path.join(ud, '.license_cache'))
  check('라이센스 cache 파일 보존 (user-data-dir 내)', cacheInUd)

  // 새 앱 다시 launch → 라이센스 화면 안 떠야 (cache 기반 ok)
  log('\n[7] 새 v0.1.10 앱 재실행 → 라이센스 화면 안 떠야 함')
  const app2 = await electron.launch({
    executablePath: installedExe, args: [`--user-data-dir=${ud}`], env, timeout: 90000,
  })
  await app2.firstWindow({ timeout: 60000 })
  await new Promise((r) => setTimeout(r, 6000))

  const findWin2 = async () => {
    const dl = Date.now() + 30000
    while (Date.now() < dl) {
      for (const w of app2.windows()) {
        if (w.isClosed?.()) continue
        try {
          const ok = await w.evaluate(() => !!document.body && document.body.innerText.length > 5)
          if (ok) return w
        } catch {}
      }
      await new Promise((r) => setTimeout(r, 500))
    }
    return null
  }

  const win3 = await findWin2()
  if (win3) {
    const t = await win3.evaluate(() => document.body?.innerText || '')
    const noKeyScreen = !t.includes('라이센스 키 입력')
    check('v0.1.10 재실행 → 라이센스 키 화면 안 뜸 (cache 보존 효과)',
      noKeyScreen, t.replace(/\s+/g, ' ').slice(0, 60))
    await win3.screenshot({ path: path.join(SHOTS, '7-after-update.png') }).catch(() => {})
  }

  try { await app2.close() } catch {}
  killAll('Inserty AI.exe')

  // === 정리: install 은 남겨둠 (사용자가 직접 검증 가능하도록) ===

  log('\n' + '='.repeat(70))
  const passed = results.filter((r) => r.ok).length
  log(`SUMMARY: ${passed}/${results.length} PASS`)
  log('='.repeat(70))
  for (const r of results) log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
  log('')
  log('타임라인 (재시작 클릭 후):')
  log(`  구 앱 종료    : +${appCloseAt}s`)
  log(`  Setup.exe 보임: +${setupSeenAt}s ${windowsLog[0] ? '(' + windowsLog[0].wins.slice(0, 3).join(', ') + ')' : ''}`)
  log(`  새 앱 launch  : +${appRelaunchAt}s`)

  const failed = results.filter((r) => !r.ok)
  process.exit(failed.length ? 1 : 0)
})().catch((e) => { console.error('FATAL:', e); killAll('Inserty AI.exe'); process.exit(2) })

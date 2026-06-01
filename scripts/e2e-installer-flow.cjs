// 실제 사용자 흐름 E2E:
//   1) NSIS Setup.exe 를 silent (/S) 실행 → per-user 설치 + 자동 실행
//   2) 자동 실행된 인스턴스 종료 (Playwright 가 다시 띄우기 위해)
//   3) 설치된 exe 를 Playwright 로 launch
//   4) splash 화면 / LicenseGate KeyInputScreen DOM 확인
//   5) main process uncaughtException / EPIPE / 다이얼로그 발생 여부 확인
//   6) cleanup: 앱 종료 + 설치본 uninstall

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawn, execSync } = require('child_process')

const SETUP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'Inserty-AI_0.1.9_Setup.exe')
// electron-builder per-user 설치 위치는 package.json name(inserty-desktop) 기준.
// productName("Inserty AI") 폴더는 일부 환경에서만. 두 후보 모두 검색.
function findInstalledExe() {
  const candidates = [
    path.join(process.env.LOCALAPPDATA, 'Programs', 'inserty-desktop', 'Inserty AI.exe'),
    path.join(process.env.LOCALAPPDATA, 'Programs', 'Inserty AI', 'Inserty AI.exe'),
  ]
  for (const c of candidates) {
    if (fs.existsSync(c)) return { exe: c, dir: path.dirname(c) }
  }
  return null
}

delete process.env.ELECTRON_RUN_AS_NODE
const cleanEnv = { ...process.env }
delete cleanEnv.ELECTRON_RUN_AS_NODE
const env = Object.entries(cleanEnv)
  .filter(([k]) => k !== 'ELECTRON_RUN_AS_NODE')
  .map(([name, value]) => ({ name, value }))

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

function killAll(processName) {
  try {
    execSync(`taskkill /IM "${processName}" /F /T`, { stdio: 'ignore' })
  } catch {}
}

;(async () => {
  console.log('========== Real Installer Flow E2E ==========')
  console.log('Setup:', SETUP_EXE)
  if (!fs.existsSync(SETUP_EXE)) {
    console.error('Setup.exe 가 존재하지 않습니다.')
    process.exit(2)
  }

  // 기존 Inserty 인스턴스가 있으면 정리
  killAll('Inserty AI.exe')

  // 기존 설치본이 있으면 uninstall (clean slate)
  let existing = findInstalledExe()
  if (existing) {
    const un = path.join(existing.dir, 'Uninstall Inserty AI.exe')
    if (fs.existsSync(un)) {
      console.log('\n[setup] 기존 설치본 uninstall:', un)
      try {
        execSync(`"${un}" /S`, { stdio: 'ignore', timeout: 60_000 })
      } catch {}
      killAll('Inserty AI.exe')
      await new Promise((r) => setTimeout(r, 2000))
    }
  }

  // ─── 1) NSIS silent install ────────────────────────────────────────
  console.log('\n[1] NSIS silent install')
  const installStart = Date.now()
  await new Promise((resolve, reject) => {
    const p = spawn(SETUP_EXE, ['/S'], { stdio: 'ignore' })
    p.on('exit', (code) => resolve(code))
    p.on('error', reject)
  })
  const installSec = ((Date.now() - installStart) / 1000).toFixed(1)
  const installed = findInstalledExe()
  check('installer 종료 + exe 설치됨', !!installed, `${installSec}s, exe=${installed?.exe}`)
  if (!installed) {
    console.error('설치 실패 — 다음 단계 진행 불가')
    process.exit(2)
  }
  const INSTALLED_EXE = installed.exe
  const INSTALL_DIR = installed.dir
  const UNINSTALLER = path.join(INSTALL_DIR, 'Uninstall Inserty AI.exe')
  check('uninstaller 생성됨', fs.existsSync(UNINSTALLER), UNINSTALLER)

  // ─── 2) 자동 실행된 인스턴스 종료 ──────────────────────────────────
  // NSIS oneClick + runAfterFinish=true → 설치 후 앱 spawn 됨.
  // Playwright 로 다시 띄우기 위해 정리.
  console.log('\n[2] 자동 실행 인스턴스 정리')
  // 자동 실행이 시작될 시간 좀 줌
  await new Promise((r) => setTimeout(r, 3000))
  killAll('Inserty AI.exe')
  await new Promise((r) => setTimeout(r, 1500))

  // ─── 3) Playwright launch ─────────────────────────────────────────
  console.log('\n[3] Playwright 로 설치본 launch')
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-installer-e2e-'))
  const app = await electron.launch({
    executablePath: INSTALLED_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })

  // 모든 윈도우의 pageerror / console error 수집
  const pageErrors = []
  const consoleErrors = []
  let mainProcessErrors = 0
  app.on('window', (w) => {
    w.on('pageerror', (e) => {
      pageErrors.push(e.message)
      console.log(`    [pageerror] ${e.message}`)
    })
    w.on('console', (m) => {
      if (m.type() === 'error') {
        const text = m.text()
        consoleErrors.push(text)
        if (/EPIPE|Object has been destroyed|Cannot find module|Uncaught Exception/i.test(text)) {
          mainProcessErrors++
        }
      }
    })
  })

  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 4000))

    // 활성 window 찾기 (splash → main 전환 race condition 회피)
    async function getActiveWindow(maxMs = 30000) {
      const deadline = Date.now() + maxMs
      while (Date.now() < deadline) {
        const wins = app.windows()
        for (let i = wins.length - 1; i >= 0; i--) {
          const w = wins[i]
          if (w.isClosed?.()) continue
          try {
            const ok = await w.evaluate(() => !!document.body && document.body.innerText.length > 5)
            if (ok) return w
          } catch {}
        }
        await new Promise((r) => setTimeout(r, 500))
      }
      throw new Error('no active window')
    }

    const win = await getActiveWindow(30000)

    // ─── 4) 초기 화면 검증 (splash 또는 LicenseGate — 둘 다 정상 흐름) ─
    // 캐시/pending 없는 깨끗한 설치 후 첫 진입이라 splash 는 매우 빠르게 끝나고
    // LicenseGate KeyInputScreen 이 먼저 잡히는 경우가 일반적.
    console.log('\n[4] 초기 화면 정상 진입 확인 (splash 또는 LicenseGate)')
    const initialState = await win.evaluate(() => ({
      isInsertyApp: (document.body.innerText || '').includes('Inserty') ||
                    (document.body.innerText || '').includes('라이센스 키 입력') ||
                    (document.body.innerText || '').includes('HWP'),
      innerText: (document.body.innerText || '').slice(0, 120),
    }))
    check('인서티 앱 초기 화면 진입', initialState.isInsertyApp,
      initialState.innerText.replace(/\s+/g, ' ').slice(0, 80))

    // splash → LicenseGate 전환 polling (최대 40s)
    console.log('\n[5] LicenseGate KeyInputScreen DOM 확인')
    let gateFound = false
    let gateText = ''
    const gateDeadline = Date.now() + 40_000
    while (Date.now() < gateDeadline) {
      await new Promise((r) => setTimeout(r, 1500))
      try {
        const w = await getActiveWindow(5000)
        const probe = await w.evaluate(() => ({
          hasKeyInput: !!document.querySelector('input[placeholder*="INSRT"]'),
          hasLicenseTitle: (document.body.innerText || '').includes('라이센스 키 입력'),
          text: (document.body.innerText || '').slice(0, 150),
        }))
        if (probe.hasKeyInput || probe.hasLicenseTitle) {
          gateFound = true
          gateText = probe.text
          break
        }
      } catch {}
    }
    check('LicenseGate KeyInputScreen DOM 렌더링', gateFound, gateText.slice(0, 80))

    // ─── 6) EPIPE / 다이얼로그 / uncaughtException 발생 여부 ──────
    console.log('\n[6] main process 에러 / EPIPE 다이얼로그 검사')
    check('pageerror 없음', pageErrors.length === 0, `count=${pageErrors.length}`)
    check('치명적 main error 없음 (EPIPE / Object destroyed / Cannot find module)',
      mainProcessErrors === 0, `count=${mainProcessErrors}`)
  } finally {
    await app.close().catch(() => {})
    killAll('Inserty AI.exe')

    // ─── 7) uninstall (test 후 정리) ──────────────────────────────
    console.log('\n[7] uninstall')
    if (fs.existsSync(UNINSTALLER)) {
      try {
        execSync(`"${UNINSTALLER}" /S`, { stdio: 'ignore', timeout: 60_000 })
      } catch {}
      await new Promise((r) => setTimeout(r, 1500))
      check('uninstall 후 exe 제거됨', !fs.existsSync(INSTALLED_EXE))
    }
  }

  const passed = results.filter((r) => r.ok).length
  console.log('\n' + '='.repeat(70))
  console.log(`SUMMARY: ${passed}/${results.length} PASS`)
  for (const r of results) console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
  console.log('='.repeat(70))
  process.exit(passed === results.length ? 0 : 1)
})().catch((e) => {
  console.error('FATAL:', e)
  killAll('Inserty AI.exe')
  process.exit(2)
})

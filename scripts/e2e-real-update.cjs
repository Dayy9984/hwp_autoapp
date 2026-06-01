// 진짜 업데이트 전체 흐름 — Setup.exe 설치 → 앱 실행 → 다운로드 클릭 → 재시작 클릭 →
// 실제로 binary 가 v0.1.9 → v0.1.10 으로 바뀌는지 검증.
//
// 단계:
//   1) 기존 설치본 정리 (uninstall + 잔존 프로세스 kill)
//   2) Inserty-AI_0.1.9_Setup.exe /S 로 silent 설치
//   3) 설치된 app.asar 의 package.json 에서 version 읽음 → expect "0.1.9"
//   4) 자동 실행된 인스턴스 종료
//   5) Playwright 로 v0.1.9 설치본 실행 (pending_license 로 자동 활성화)
//   6) update.check 트리거 → "새 버전 사용 가능" 모달 대기 → 다운로드 클릭
//   7) "업데이트 준비 완료" 모달 대기 → 재시작 클릭
//   8) Playwright 연결 끊김 (앱 종료) → NSIS silent 인스톨러 spawn
//   9) 설치된 .exe 의 mtime 변경 대기 (최대 60s)
//   10) 다시 app.asar 의 version 읽음 → expect "0.1.10"
//   11) 정리: uninstall + license 삭제

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const https = require('https')
const { spawn, execSync } = require('child_process')

const SETUP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'Inserty-AI_0.1.9_Setup.exe')
const SHOTS = path.resolve(__dirname, '..', 'release', '0.1.9', 'real-update-shots')
fs.mkdirSync(SHOTS, { recursive: true })

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'

function httpJson(method, urlStr, body) {
  const url = new URL(urlStr)
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const req = https.request({
      method, hostname: url.hostname, path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${SERVICE_ROLE}`, apikey: SERVICE_ROLE,
        Prefer: 'return=representation',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let buf = ''
      res.on('data', (c) => (buf += c))
      res.on('end', () => { try { resolve({ status: res.statusCode, body: buf ? JSON.parse(buf) : null }) } catch { resolve({ status: res.statusCode, body: buf }) } })
    })
    req.on('error', reject); if (data) req.write(data); req.end()
  })
}
async function issueLicense(email) {
  const r = await httpJson('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`,
    { p_email: email, p_duration: 30, p_notes: 'real-update' })
  return r.body[0].license_key
}
async function deleteLicense(key) {
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${key}`)
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`)
}

delete process.env.ELECTRON_RUN_AS_NODE
const cleanEnv = { ...process.env }
delete cleanEnv.ELECTRON_RUN_AS_NODE
const env = Object.entries(cleanEnv)
  .filter(([k]) => k !== 'ELECTRON_RUN_AS_NODE')
  .map(([name, value]) => ({ name, value }))

const results = []
function record(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

function killAll(processName) {
  try { execSync(`taskkill /IM "${processName}" /F /T`, { stdio: 'ignore' }) } catch {}
}

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

// app.asar 안의 package.json 에서 version 추출 (asar lib 의존 없이).
// package.json 은 prettier 포맷 그대로 박혀있어 콜론 뒤 공백 포함.
function readAsarVersion(asarPath) {
  const buf = fs.readFileSync(asarPath)
  const text = buf.toString('binary')
  // 패턴 우선순위: inserty-desktop 컨텍스트 → 0.1.X 범위
  const ctx = text.match(/"name":\s*"inserty-desktop"[\s\S]{0,200}?"version":\s*"(\d+\.\d+\.\d+)"/)
    || text.match(/"version":\s*"(\d+\.\d+\.\d+)"[\s\S]{0,200}?"name":\s*"inserty-desktop"/)
  if (ctx) return ctx[1]
  // 폴백: 0.1.X 패턴 중 마지막 매치 (package.json 보다 의존성이 먼저 나올 수 있음)
  const all = [...text.matchAll(/"version":\s*"(0\.1\.\d+)"/g)].map((m) => m[1])
  return all[all.length - 1] || null
}

async function waitForText(app, predicate, maxMs = 30000) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    try {
      const wins = app.windows()
      for (let i = wins.length - 1; i >= 0; i--) {
        const w = wins[i]
        if (w.isClosed?.()) continue
        const text = await w.evaluate(() => document.body?.innerText || '').catch(() => '')
        if (text && predicate(text)) return { win: w, text }
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 1000))
  }
  return null
}

;(async () => {
  console.log('=' + '='.repeat(68))
  console.log('  Real Update E2E — Setup.exe → 클릭 업데이트 → binary 교체 검증')
  console.log('=' + '='.repeat(68))

  if (!fs.existsSync(SETUP_EXE)) {
    console.error('Setup.exe 없음:', SETUP_EXE); process.exit(2)
  }

  // 1) 기존 정리
  console.log('\n[1] 기존 인스턴스/설치 정리')
  killAll('Inserty AI.exe')
  let existing = findInstalledExe()
  if (existing) {
    const un = path.join(existing.dir, 'Uninstall Inserty AI.exe')
    if (fs.existsSync(un)) {
      try { execSync(`"${un}" /S`, { stdio: 'ignore', timeout: 60_000 }) } catch {}
      killAll('Inserty AI.exe')
      await new Promise((r) => setTimeout(r, 2500))
    }
  }

  // 2) v0.1.9 silent 설치
  console.log('\n[2] v0.1.9 Setup.exe /S 설치')
  const t0 = Date.now()
  await new Promise((resolve, reject) => {
    const p = spawn(SETUP_EXE, ['/S'], { stdio: 'ignore' })
    p.on('exit', () => resolve())
    p.on('error', reject)
  })
  await new Promise((r) => setTimeout(r, 3000))
  const installed = findInstalledExe()
  record('v0.1.9 설치 성공', !!installed, `${((Date.now() - t0) / 1000).toFixed(1)}s, exe=${installed?.exe}`)
  if (!installed) process.exit(1)

  // 3) 설치된 app.asar 의 version 검증
  const asarPath = path.join(installed.dir, 'resources', 'app.asar')
  const versionBefore = readAsarVersion(asarPath)
  record('설치본 version 읽음 = 0.1.9', versionBefore === '0.1.9', `version=${versionBefore}`)

  // 검증 기준은 app.asar 안의 package.json version 변경.
  // mtime 은 NSIS 가 source 의 mtime 을 보존해서 신뢰 불가
  // (v0.1.10 빌드가 v0.1.9 빌드보다 먼저 패키징됐으면 mtime 이 뒤로 갈 수도 있음).

  // 4) 자동 실행 인스턴스 종료
  console.log('\n[3] 자동 실행 인스턴스 정리')
  await new Promise((r) => setTimeout(r, 3000))
  killAll('Inserty AI.exe')
  await new Promise((r) => setTimeout(r, 1500))

  // 5) Playwright 로 설치본 실행 + pending_license
  console.log('\n[4] Playwright 로 v0.1.9 설치본 실행')
  const key = await issueLicense('real-update@test.com')
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-real-update-'))
  fs.writeFileSync(path.join(ud, '.pending_license'), key, 'utf8')

  const app = await electron.launch({
    executablePath: installed.exe,
    args: [`--user-data-dir=${ud}`],
    env, timeout: 90000,
  })

  let updateClicked = false
  let restartClicked = false
  let newVersion = null

  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 6000))

    // 6) 메인 UI 진입 확인 + update.check 트리거
    const main = await waitForText(app, (t) => !t.includes('라이센스 키 입력') && t.length > 10, 40000)
    record('자동 활성화 후 메인 UI 진입', !!main, main?.text?.replace(/\s+/g, ' ').slice(0, 60))
    if (!main) throw new Error('main UI 미진입')

    await main.win.evaluate(async () => {
      try { await window.electronAPI.update.check() } catch {}
    })

    // 7) "새 버전 사용 가능" 모달 대기 → 다운로드 클릭
    const updModal = await waitForText(app, (t) => t.includes('새 버전 사용 가능') || t.includes('필수 업데이트'), 30000)
    record('"새 버전 사용 가능" 모달 표시', !!updModal)
    if (!updModal) throw new Error('update modal not shown')
    await updModal.win.screenshot({ path: path.join(SHOTS, '1-update-modal.png') }).catch(() => {})

    await updModal.win.locator('button', { hasText: '다운로드' }).first().click()
    updateClicked = true
    console.log('  [info] 다운로드 버튼 클릭 (414MB → 5-10분 소요 예상)')

    // 8) "업데이트 준비 완료" 대기 (최대 10분)
    const dlModal = await waitForText(app, (t) =>
      t.includes('업데이트 준비 완료') || t.includes('필수 업데이트 준비 완료'), 600000)
    record('"업데이트 준비 완료" 모달 표시', !!dlModal)
    if (!dlModal) throw new Error('download not complete')
    await dlModal.win.screenshot({ path: path.join(SHOTS, '2-download-complete.png') }).catch(() => {})

    // 9) 재시작 클릭 → quitAndInstall → 앱 종료 + NSIS 인스톨러 spawn (detached)
    console.log('\n[5] 재시작 버튼 클릭 → quitAndInstall')

    // 다운로드된 인스톨러가 캐시에 있는지 확인 (electron-updater 가 여기에 둠)
    const pendingDir = path.join(process.env.LOCALAPPDATA, 'inserty-desktop-updater', 'pending')
    if (fs.existsSync(pendingDir)) {
      const pendingFiles = fs.readdirSync(pendingDir)
      console.log('  [info] electron-updater pending dir:', pendingFiles.join(', '))
    } else {
      console.log('  [info] electron-updater pending dir not found:', pendingDir)
    }

    await dlModal.win.locator('button', { hasText: '재시작' }).first().click().catch(() => {})
    restartClicked = true

    // quitAndInstall 이 installer 를 spawn 할 시간 확보 (await close 전에 8s 대기)
    await new Promise((r) => setTimeout(r, 8000))
  } catch (e) {
    console.error('  [error]', e.message)
  } finally {
    // app 이 아직 살아있으면 정리. 재시작 흐름에서는 이미 quit 되어있을 것.
    try { await app.close() } catch {}
  }

  if (!updateClicked || !restartClicked) {
    record('업데이트 흐름 진행', false, `updateClicked=${updateClicked}, restartClicked=${restartClicked}`)
    await deleteLicense(key)
    process.exit(1)
  }

  // 10) NSIS silent installer 완료 대기 — app.asar 의 package.json version 이 바뀔 때까지 polling
  console.log('\n[6] NSIS silent installer 완료 대기 — version 0.1.10 으로 갱신 대기 (최대 120s)')
  const installDeadline = Date.now() + 120_000
  let detectedNewVersion = null
  while (Date.now() < installDeadline) {
    await new Promise((r) => setTimeout(r, 3000))
    try {
      const v = readAsarVersion(asarPath)
      if (v === '0.1.10') {
        detectedNewVersion = v
        console.log(`  [info] app.asar version 변경 감지: ${v}`)
        break
      }
    } catch {}
  }
  // 새 앱이 자동 spawn 됐을 가능성 → 다음 read 전에 종료
  await new Promise((r) => setTimeout(r, 2000))

  // 11) 새 app.asar 의 version 확인 → 0.1.10 기대
  killAll('Inserty AI.exe') // 자동 실행된 새 인스턴스가 있다면 종료 후 파일 읽기
  await new Promise((r) => setTimeout(r, 2000))

  try {
    newVersion = readAsarVersion(asarPath)
  } catch (e) {
    console.error('  [error] asar 읽기 실패:', e.message)
  }
  record('업데이트 후 version = 0.1.10 (실제 binary 교체 확인)',
    newVersion === '0.1.10', `before=0.1.9, after=${newVersion}`)

  // 12) 정리
  console.log('\n[7] 정리')
  const finalExisting = findInstalledExe()
  if (finalExisting) {
    const un = path.join(finalExisting.dir, 'Uninstall Inserty AI.exe')
    if (fs.existsSync(un)) {
      try { execSync(`"${un}" /S`, { stdio: 'ignore', timeout: 60_000 }) } catch {}
    }
  }
  await deleteLicense(key)

  const passed = results.filter((r) => r.ok).length
  console.log('\n' + '='.repeat(70))
  console.log(`REAL UPDATE SUMMARY: ${passed}/${results.length} PASS`)
  console.log('='.repeat(70))
  for (const r of results) console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
  process.exit(passed === results.length ? 0 : 1)
})().catch((e) => {
  console.error('FATAL:', e)
  killAll('Inserty AI.exe')
  process.exit(2)
})

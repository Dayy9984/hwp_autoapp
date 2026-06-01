// 사용자 동선 기준 E2E.
// 이번 세션에서 만든/수정한 모든 기능을 실제 사용자가 클릭/입력하는 순서로 검증.
//
// 각 시나리오 = 격리된 userDataDir + 필요한 사전 상태 (백엔드에서 라이센스 발급/조작)
//   + Playwright 로 win-unpacked 앱 실행
//   + UI 조작 (input.fill / button.click)
//   + DOM/screenshot/에러 검증
//   + cleanup

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const https = require('https')
const crypto = require('crypto')
const { execSync } = require('child_process')

const APP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'win-unpacked', 'Inserty AI.exe')
const SHOTS = path.resolve(__dirname, '..', 'release', '0.1.9', 'user-flow-shots')
fs.mkdirSync(SHOTS, { recursive: true })

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'

function httpJson(method, urlStr, body) {
  const url = new URL(urlStr)
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const req = https.request({
      method,
      hostname: url.hostname,
      path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${SERVICE_ROLE}`,
        apikey: SERVICE_ROLE,
        Prefer: 'return=representation',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let buf = ''
      res.on('data', (c) => (buf += c))
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: buf ? JSON.parse(buf) : null }) }
        catch { resolve({ status: res.statusCode, body: buf }) }
      })
    })
    req.on('error', reject)
    if (data) req.write(data)
    req.end()
  })
}
async function issueLicense(email, duration = 30, notes = 'user-flow-e2e') {
  const r = await httpJson('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`,
    { p_email: email, p_duration: duration, p_notes: notes })
  return r.body[0].license_key
}
async function deleteLicense(key) {
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${key}`)
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`)
}
async function setField(key, field, value) {
  await httpJson('PATCH', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`, { [field]: value })
}
async function preRegisterDevice(key, deviceId, name = 'Other-PC') {
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/license_devices`, {
    license_key: key, device_id: deviceId, device_name: name, device_os: 'win32 fake',
    last_seen_at: new Date().toISOString(),
  })
}

delete process.env.ELECTRON_RUN_AS_NODE
const cleanEnv = { ...process.env }
delete cleanEnv.ELECTRON_RUN_AS_NODE
const env = Object.entries(cleanEnv)
  .filter(([k]) => k !== 'ELECTRON_RUN_AS_NODE')
  .map(([name, value]) => ({ name, value }))

const ALL = []
function record(scenario, name, ok, detail = '') {
  ALL.push({ scenario, name, ok, detail })
  console.log(`    [${ok ? 'PASS' : 'FAIL'}] ${scenario}: ${name}${detail ? ' — ' + detail : ''}`)
}

async function launchApp(userDataDir) {
  return await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })
}

async function getActiveWindow(app, maxMs = 30000) {
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

// 캡처할 화면 텍스트 일부가 포함될 때까지 polling
async function waitForText(app, predicate, maxMs = 30000) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    try {
      const w = await getActiveWindow(app, 5000)
      const text = await w.evaluate(() => document.body?.innerText || '')
      if (predicate(text)) return { win: w, text }
    } catch {}
    await new Promise((r) => setTimeout(r, 1000))
  }
  return null
}

function trackErrors(app) {
  const errors = []
  app.on('window', (w) => {
    w.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    w.on('console', (m) => {
      if (m.type() === 'error') {
        const t = m.text()
        if (/EPIPE|Object has been destroyed|Cannot find module|Uncaught Exception|TypeError/i.test(t)) {
          errors.push(`console: ${t.slice(0, 200)}`)
        }
      }
    })
  })
  return errors
}

function tmpUserData(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `inserty-user-${tag}-`))
}

// ─── Scenario A: 신규 사용자 — 키 입력 + 활성화 + 메인 UI ───────────────
async function scenarioA_newActivation() {
  console.log('\n[A] 신규 사용자: 키 입력 → 활성화 → 진입')
  const key = await issueLicense('e2e+a@test.com')
  const ud = tmpUserData('a')
  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))

    // LicenseGate 가 뜰 때까지 대기
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    record('A', 'LicenseGate KeyInputScreen 표시됨', !!gate, gate?.text?.slice(0, 60))
    if (!gate) return

    await gate.win.screenshot({ path: path.join(SHOTS, 'A-1-keyinput.png') }).catch(() => {})

    // 잘못된 키 먼저 입력해 → 활성화 버튼 disabled 인지 확인
    const input = gate.win.locator('input[placeholder*="INSRT"]')
    await input.fill('WRONG-KEY')
    const activateBtn = gate.win.locator('button', { hasText: '활성화' }).first()
    const disabledShort = await activateBtn.isDisabled().catch(() => null)
    record('A', '활성화 버튼: 24자 미만 → disabled', disabledShort === true,
      `disabled=${disabledShort}`)

    // 형식 잘못 (24자지만 INSRT 접두사 X)
    await input.fill('WRONG-FAKE-XXXX-YYYY-ZZZZ')
    await activateBtn.click()
    await new Promise((r) => setTimeout(r, 2500))
    const invalidErr = await gate.win.evaluate(() => {
      const txt = document.body?.innerText || ''
      return txt.includes('등록되지 않은') || txt.includes('형식이 올바르지') ||
             txt.includes('INSRT-')
    })
    record('A', '잘못된 키 입력 → 에러 메시지', invalidErr)
    await gate.win.screenshot({ path: path.join(SHOTS, 'A-2-invalid.png') }).catch(() => {})

    // 정상 키 입력 + 활성화
    await input.fill(key)
    await activateBtn.click()

    // 활성화 성공 후 splash 또는 main UI 진입 (라이센스 게이트가 사라져야)
    const left = await waitForText(app,
      (t) => !t.includes('라이센스 키 입력') && t.length > 5,
      30000)
    record('A', '활성화 후 LicenseGate 사라짐 (다음 화면 진입)', !!left,
      left?.text?.replace(/\s+/g, ' ').slice(0, 80))
    if (left) await left.win.screenshot({ path: path.join(SHOTS, 'A-3-after-activate.png') }).catch(() => {})

    record('A', 'main process 에러 다이얼로그 없음', errors.length === 0,
      `count=${errors.length}`)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key)
  }
}

// ─── Scenario B: 카카오톡 문의 버튼 클릭 ──────────────────────────────
async function scenarioB_kakaoClick() {
  console.log('\n[B] LicenseGate 의 카카오톡 버튼 클릭 → shell.openExternal')
  const ud = tmpUserData('b')
  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    // shell.openExternal 스파이
    await app.evaluate(({ shell }) => {
      ;(global)._capturedOpenExternal = []
      shell.openExternal = async (url) => {
        ;(global)._capturedOpenExternal.push(url)
        return undefined
      }
    })

    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('카카오톡 문의'), 30000)
    record('B', 'KakaoSupportLink 버튼 텍스트 표시', !!gate)
    if (!gate) return

    const btn = gate.win.locator('button', { hasText: '카카오톡 문의' }).first()
    await btn.click()
    await new Promise((r) => setTimeout(r, 800))

    const captured = await app.evaluate(() => (global)._capturedOpenExternal || [])
    record('B', 'shell.openExternal 호출됨', Array.isArray(captured) && captured.length > 0,
      JSON.stringify(captured))
    record('B', '카카오톡 오픈채팅 URL 정확',
      captured?.[0] === 'https://open.kakao.com/o/sSm9ZXei', captured?.[0] || '')
    record('B', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
  }
}

// ─── Scenario C: 만료된 키 입력 → ExpiredModal ──────────────────────
async function scenarioC_expiredKey() {
  console.log('\n[C] 만료된 키 입력 → ExpiredModal 차단 화면')
  const key = await issueLicense('e2e+c@test.com')
  await setField(key, 'expires_at', new Date(Date.now() - 24 * 3600 * 1000).toISOString())
  const ud = tmpUserData('c')
  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    if (!gate) { record('C', 'LicenseGate 표시', false); return }

    const input = gate.win.locator('input[placeholder*="INSRT"]')
    await input.fill(key)
    await gate.win.locator('button', { hasText: '활성화' }).first().click()

    const expired = await waitForText(app, (t) => t.includes('라이센스가 만료'), 20000)
    record('C', 'ExpiredModal "라이센스가 만료되었습니다" 표시', !!expired)
    if (expired) await expired.win.screenshot({ path: path.join(SHOTS, 'C-expired.png') }).catch(() => {})

    // 차단 화면에도 카카오톡 버튼 있어야
    const hasKakao = expired?.text.includes('카카오톡')
    record('C', '만료 화면 카카오톡 안내 표시', !!hasKakao)
    record('C', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key)
  }
}

// ─── Scenario D: revoked 키 → RevokedModal ──────────────────────────
async function scenarioD_revokedKey() {
  console.log('\n[D] revoked 키 → RevokedModal 차단 화면')
  const key = await issueLicense('e2e+d@test.com')
  await setField(key, 'status', 'revoked')
  const ud = tmpUserData('d')
  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    if (!gate) { record('D', 'LicenseGate 표시', false); return }
    await gate.win.locator('input[placeholder*="INSRT"]').fill(key)
    await gate.win.locator('button', { hasText: '활성화' }).first().click()
    const revoked = await waitForText(app, (t) => t.includes('라이센스가 무효화'), 20000)
    record('D', 'RevokedModal "라이센스가 무효화되었습니다" 표시', !!revoked)
    if (revoked) await revoked.win.screenshot({ path: path.join(SHOTS, 'D-revoked.png') }).catch(() => {})
    record('D', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key)
  }
}

// ─── Scenario E: 디바이스 슬롯 꽉참 → 다른 기기 삭제 → 활성화 진입 ─
async function scenarioE_deviceManager() {
  console.log('\n[E] DeviceManager: 2대 슬롯 꽉참 → 다른 기기 삭제 → 자동 활성화')
  const key = await issueLicense('e2e+e@test.com')
  // 다른 두 기기 미리 등록
  const fakeA = crypto.randomBytes(16).toString('hex')
  const fakeB = crypto.randomBytes(16).toString('hex')
  await preRegisterDevice(key, fakeA, 'Old-Laptop')
  await preRegisterDevice(key, fakeB, 'Office-PC')
  const ud = tmpUserData('e')
  // pending key 로 자동 activate 시도
  fs.writeFileSync(path.join(ud, '.pending_license'), key, 'utf8')

  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 4000))
    const mgr = await waitForText(app, (t) => t.includes('기기 슬롯이 가득'), 30000)
    record('E', 'DeviceManagerScreen 자동 표시', !!mgr)
    if (!mgr) return
    await mgr.win.screenshot({ path: path.join(SHOTS, 'E-1-device-mgr.png') }).catch(() => {})

    const hasOldLaptop = mgr.text.includes('Old-Laptop')
    const hasOfficePc = mgr.text.includes('Office-PC')
    record('E', 'Old-Laptop 디바이스 목록 표시', hasOldLaptop)
    record('E', 'Office-PC 디바이스 목록 표시', hasOfficePc)

    // 첫 번째 기기 삭제 버튼 클릭 (Trash icon 버튼)
    const deleteButtons = mgr.win.locator('button[title*="삭제"], button:has(svg.lucide-trash-2), button:has(svg[class*="trash" i])')
    const btnCount = await deleteButtons.count().catch(() => 0)
    record('E', '삭제 버튼 표시 (Trash 아이콘)', btnCount >= 1, `count=${btnCount}`)
    if (btnCount === 0) return

    await deleteButtons.first().click()
    // 삭제 + retryActivate → 메인 UI 진입 (또는 splash) — 라이센스 게이트 사라짐
    const left = await waitForText(app,
      (t) => !t.includes('기기 슬롯이 가득') && t.length > 5,
      30000)
    record('E', '디바이스 삭제 후 limit 화면 사라짐 + 진입', !!left,
      left?.text?.replace(/\s+/g, ' ').slice(0, 80))
    if (left) await left.win.screenshot({ path: path.join(SHOTS, 'E-2-after-remove.png') }).catch(() => {})
    record('E', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key)
  }
}

// ─── Scenario F: 자동 업데이트 알림 + 다운로드 + 설치 API ──────────
async function scenarioF_autoUpdate() {
  console.log('\n[F] 자동 업데이트: 알림 → 다운로드 → 설치 API')
  // 라이센스 정상 활성화된 사용자 상태 시뮬: pending key 자동 활성화
  const key = await issueLicense('e2e+f@test.com')
  const ud = tmpUserData('f')
  fs.writeFileSync(path.join(ud, '.pending_license'), key, 'utf8')

  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 5000))
    const win = await getActiveWindow(app, 30000)

    // onStatus listener 일찍 등록
    await win.evaluate(() => {
      ;(window).__updateEvents = []
      const api = (window).electronAPI?.update
      api?.onStatus?.((s) => (window).__updateEvents.push(s))
    })

    // update.check 호출 — 사용자가 메뉴 등을 눌렀을 때의 동선
    const checkResp = await win.evaluate(async () => await window.electronAPI.update.check())
    record('F', 'update.check → available v0.1.10',
      checkResp?.success && checkResp?.updateAvailable && checkResp?.version === '0.1.10',
      `version=${checkResp?.version}, available=${checkResp?.updateAvailable}`)

    // 사용자가 "업데이트 다운로드" 클릭
    const dl = await win.evaluate(async () => await window.electronAPI.update.download())
    record('F', 'update.download 호출 성공', dl?.success === true)

    // download progress 폴링
    let downloaded = false
    let lastPct = 0
    const dlDeadline = Date.now() + 180_000
    while (Date.now() < dlDeadline) {
      await new Promise((r) => setTimeout(r, 1500))
      const evs = await win.evaluate(() => (window).__updateEvents || []).catch(() => [])
      for (const e of evs) {
        if (e.status === 'downloading' && e.progress?.percent > lastPct) {
          lastPct = e.progress.percent
        }
        if (e.status === 'downloaded') downloaded = true
        if (e.status === 'error') { record('F', 'update error', false, e.error); break }
      }
      if (downloaded) break
    }
    record('F', '다운로드 완료 (downloaded 이벤트)', downloaded,
      `lastPct=${lastPct.toFixed(1)}`)

    // update.install API 호출 가능 (실제 install 은 앱 종료 → 인스톨러 실행이라 호출만 검증)
    const installable = await win.evaluate(() =>
      typeof window.electronAPI.update.install === 'function')
    record('F', 'update.install API 호출 가능', installable)
    record('F', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
    await deleteLicense(key)
  }
}

// ─── Scenario G: 오프라인 grace → 메인 UI 유지 ────────────────────
async function scenarioG_offlineGrace() {
  console.log('\n[G] 오프라인 grace: 캐시 + fetch 차단 → offline_grace 유지')
  const ud = tmpUserData('g')
  // 1일 전 verify 캐시 직접 작성 (license-bridge 와 동일 형식)
  const ENC_KEY = crypto.createHash('sha256').update('insertyai-license-store-v1').digest()
  const plain = Buffer.from(JSON.stringify({
    token: 'eyJfake.token.value',
    license_key: 'INSRT-CACHE-TEST-XXXX-YYYY',
    expires_at: '2027-01-01T00:00:00.000Z',
    last_verified_at: Date.now() - 24 * 3600 * 1000,
    server_clock_offset_ms: 0,
  }), 'utf8')
  const iv = crypto.randomBytes(12)
  const cipher = crypto.createCipheriv('aes-256-gcm', ENC_KEY, iv)
  const enc = Buffer.concat([cipher.update(plain), cipher.final()])
  const tag = cipher.getAuthTag()
  fs.writeFileSync(path.join(ud, '.license_cache'), Buffer.concat([iv, tag, enc]))

  const app = await launchApp(ud)
  const errors = trackErrors(app)
  try {
    // 네트워크 차단
    await app.evaluate(() => {
      global.fetch = async () => { throw new Error('NETWORK_BLOCKED') }
    })
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 4000))

    const win = await getActiveWindow(app, 30000)
    const status = await win.evaluate(async () =>
      await window.electronAPI.license.verify())
    record('G', '캐시 1일 전 + fetch 실패 → offline_grace',
      status?.state === 'offline_grace', JSON.stringify(status))
    record('G', 'main process 에러 다이얼로그 없음', errors.length === 0)
  } finally {
    await app.close().catch(() => {})
  }
}

// ─── 실행 ──────────────────────────────────────────────────────────
;(async () => {
  console.log('=================================================')
  console.log('  사용자 동선 기준 E2E (7 시나리오)')
  console.log('=================================================')

  const scenarios = [
    scenarioA_newActivation,
    scenarioB_kakaoClick,
    scenarioC_expiredKey,
    scenarioD_revokedKey,
    scenarioE_deviceManager,
    scenarioF_autoUpdate,
    scenarioG_offlineGrace,
  ]
  for (const s of scenarios) {
    try { await s() }
    catch (e) {
      record(s.name, 'EXCEPTION', false, e?.message || String(e))
      try { execSync('taskkill /IM "Inserty AI.exe" /F /T', { stdio: 'ignore' }) } catch {}
    }
  }

  const passed = ALL.filter((r) => r.ok).length
  console.log('\n' + '='.repeat(70))
  console.log(`USER FLOW SUMMARY: ${passed}/${ALL.length} PASS`)
  console.log('='.repeat(70))
  const failed = ALL.filter((r) => !r.ok)
  if (failed.length) {
    console.log('\n실패:')
    for (const f of failed) console.log(`  ✗ ${f.scenario}: ${f.name}${f.detail ? ' — ' + f.detail : ''}`)
  }
  console.log(`\n스크린샷: ${SHOTS}`)
  process.exit(failed.length ? 1 : 0)
})().catch((e) => {
  console.error('FATAL:', e)
  try { execSync('taskkill /IM "Inserty AI.exe" /F /T', { stdio: 'ignore' }) } catch {}
  process.exit(2)
})

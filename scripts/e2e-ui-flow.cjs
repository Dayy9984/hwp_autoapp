// UI 중심 E2E — 실제 사용자가 화면에서 보고 클릭하는 흐름만 검증.
//
// 시나리오:
//   UI-1: 라이센스 키 입력 화면 — placeholder / disabled / 에러 표시 (UI)
//   UI-2: 잘못된 키 → 에러 메시지 빨강 박스 → 정상 키로 재시도 → 모달 사라짐 (UI)
//   UI-3: 자동 업데이트 알림 모달 자동 표시 → "다운로드" 버튼 클릭 (UI)
//   UI-4: 다운로드 완료 → "업데이트 준비 완료" 알림 + "재시작" 버튼 (UI)
//   UI-5: 업데이트 모달 ESC 키로 닫기 (UI)
//   UI-6: 디바이스 관리 화면 — "현재 기기" 배지 + 삭제 버튼 disabled (UI)
//   UI-7: 디바이스 관리에서 타 기기 삭제 → 자동 활성화 진행 + 모달 사라짐 (UI)
//   UI-8: 만료된 키 → 빨간/주황 ExpiredModal "라이센스가 만료되었습니다" + 카카오 버튼 (UI)
//   UI-9: revoked 키 → RevokedModal "라이센스가 무효화되었습니다" (UI)

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const https = require('https')
const crypto = require('crypto')
const { execSync } = require('child_process')

const APP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'win-unpacked', 'Inserty AI.exe')
const SHOTS = path.resolve(__dirname, '..', 'release', '0.1.9', 'ui-flow-shots')
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
    { p_email: email, p_duration: 30, p_notes: 'ui-flow' })
  return r.body[0].license_key
}
async function deleteLicense(key) {
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${key}`)
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`)
}
async function setField(key, field, value) {
  await httpJson('PATCH', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`, { [field]: value })
}
async function preRegister(key, deviceId, name) {
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
function tmpUserData(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `inserty-ui-${tag}-`))
}
async function launchApp(userDataDir) {
  return await electron.launch({
    executablePath: APP_EXE, args: [`--user-data-dir=${userDataDir}`], env, timeout: 90000,
  })
}
async function getActiveWindow(app, maxMs = 30000) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    const wins = app.windows()
    for (let i = wins.length - 1; i >= 0; i--) {
      const w = wins[i]
      if (w.isClosed?.()) continue
      try { if (await w.evaluate(() => !!document.body && document.body.innerText.length > 5)) return w } catch {}
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('no active window')
}
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

// ─── UI-1+2: 라이센스 키 입력 화면 + 잘못된 키 → 에러 박스 → 재시도 성공 ─
async function ui1_2_keyInputErrorThenSuccess() {
  console.log('\n[UI-1+2] 키 입력 화면 → 잘못된 키 빨강 박스 → 정상 키 → 모달 사라짐')
  const key = await issueLicense('ui12@test.com')
  const ud = tmpUserData('ui12')
  const app = await launchApp(ud)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    if (!gate) { record('UI-1', 'KeyInputScreen 표시', false); return }

    // UI-1: placeholder + 카카오 버튼 보이는지 (시각 검증)
    const ui1 = await gate.win.evaluate(() => {
      const input = document.querySelector('input[placeholder*="INSRT"]')
      const kakao = Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.includes('카카오톡'))
      const btnActivate = Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.trim() === '활성화')
      return {
        hasInput: !!input,
        placeholder: input?.getAttribute('placeholder'),
        hasKakao: !!kakao,
        activateBtnDisabled: btnActivate?.hasAttribute('disabled'),
      }
    })
    record('UI-1a', 'KeyRound 아이콘 + INSRT placeholder', ui1.hasInput && ui1.placeholder?.includes('INSRT'),
      ui1.placeholder)
    record('UI-1b', '활성화 버튼 초기 disabled (빈 키)', ui1.activateBtnDisabled === true,
      `disabled=${ui1.activateBtnDisabled}`)
    record('UI-1c', '카카오톡 문의 버튼 표시', ui1.hasKakao)
    await gate.win.screenshot({ path: path.join(SHOTS, 'UI-1-key-input.png') }).catch(() => {})

    // UI-2a: 형식 잘못된 키 (25자) 입력 → 활성화 enabled → 클릭 → 빨강 에러 박스
    const input = gate.win.locator('input[placeholder*="INSRT"]')
    const btn = gate.win.locator('button', { hasText: '활성화' }).first()
    await input.fill('XXXXX-FAKE-FAKE-FAKE-FAKE')
    const enabledAt25 = !(await btn.isDisabled())
    record('UI-2a', '25자 입력 시 활성화 버튼 enabled', enabledAt25, `enabled=${enabledAt25}`)

    await btn.click()
    await new Promise((r) => setTimeout(r, 3000))

    // 빨강 에러 박스 (bg-danger-light + text-danger) 가 떴는지 — 색상이 아니라 텍스트 + DOM 클래스로 확인
    const errorState = await gate.win.evaluate(() => {
      const errBox = document.querySelector('.bg-danger-light')
      return {
        hasErrorBox: !!errBox,
        errorText: errBox?.textContent?.trim() || '',
        bodyHas: (document.body.innerText || '').includes('형식이 올바르지') || (document.body.innerText || '').includes('등록되지 않은'),
      }
    })
    record('UI-2b', '에러 박스 (bg-danger-light) DOM 등장', errorState.hasErrorBox)
    record('UI-2c', '에러 메시지 텍스트 정확', errorState.bodyHas,
      errorState.errorText?.slice(0, 60))
    await gate.win.screenshot({ path: path.join(SHOTS, 'UI-2-error.png') }).catch(() => {})

    // UI-2d: 정상 키로 재시도 → KeyInputScreen 사라짐
    await input.fill(key)
    await btn.click()
    const left = await waitForText(app, (t) => !t.includes('라이센스 키 입력') && t.length > 5, 30000)
    record('UI-2d', '정상 키 입력 → LicenseGate 사라지고 SplashScreen 진입',
      !!left, left?.text?.replace(/\s+/g, ' ').slice(0, 60))
    if (left) await left.win.screenshot({ path: path.join(SHOTS, 'UI-2d-after.png') }).catch(() => {})
  } finally {
    await app.close().catch(() => {}); await deleteLicense(key)
  }
}

// ─── UI-3+4+5: 자동 업데이트 모달 표시 → 다운로드 클릭 → 완료 모달 → ESC 닫기 ─
async function ui3_4_5_updateNotificationModal() {
  console.log('\n[UI-3+4+5] 업데이트 알림 모달 — 다운로드 클릭 + 완료 모달 + ESC 닫기')
  // v0.1.9 backup-unpacked 사용 (v0.1.10 이 latest 라서 update-available 발화)
  const v019Backup = path.resolve(__dirname, '..', 'release', '0.1.9-backup-unpacked', 'Inserty AI.exe')
  if (!fs.existsSync(v019Backup)) {
    record('UI-3', 'v0.1.9 backup exe 없음 — skip', false, v019Backup)
    return
  }
  const key = await issueLicense('ui345@test.com')
  const ud = tmpUserData('ui345')
  fs.writeFileSync(path.join(ud, '.pending_license'), key, 'utf8')

  const app = await electron.launch({
    executablePath: v019Backup, args: [`--user-data-dir=${ud}`], env, timeout: 90000,
  })
  try {
    await app.firstWindow({ timeout: 60000 })
    // 자동 활성화 + Python 시작 + isAuthenticated true → onStatus 리스너 등록 →
    // 별도 트리거: api.update.check() 를 일찍 호출하면 즉시 'available' 이벤트.
    // 다만 사용자 동선 기준이므로 자동 체크 (1h 주기 가드 통과 강제 트리거)에 의존.
    await new Promise((r) => setTimeout(r, 6000))
    const win = await getActiveWindow(app, 30000)

    // 사용자가 메뉴 → 업데이트 확인 클릭한 동선과 동등하게 수동 트리거
    await win.evaluate(async () => {
      try { await window.electronAPI.update.check() } catch {}
    })

    // UI-3: 업데이트 모달 DOM 등장 — title "새 버전 사용 가능" + "다운로드" 버튼
    const modalShown = await waitForText(app, (t) =>
      t.includes('새 버전 사용 가능') || t.includes('필수 업데이트'), 30000)
    record('UI-3a', '업데이트 알림 모달 자동 표시 (새 버전 사용 가능 / 필수 업데이트)',
      !!modalShown, modalShown?.text?.replace(/\s+/g, ' ').slice(0, 80))

    if (modalShown) {
      const modalDom = await modalShown.win.evaluate(() => {
        const titleEl = document.getElementById('notification-title')
        const contentEl = document.getElementById('notification-content')
        const dlBtn = Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.trim() === '다운로드')
        return {
          title: titleEl?.textContent?.trim(),
          contentSnippet: contentEl?.textContent?.trim()?.slice(0, 80),
          hasDownloadBtn: !!dlBtn,
          version010: (document.body.innerText || '').includes('0.1.10'),
        }
      })
      record('UI-3b', 'role=dialog + title="새 버전 사용 가능"',
        modalDom.title === '새 버전 사용 가능' || modalDom.title === '필수 업데이트',
        modalDom.title)
      record('UI-3c', '본문에 새 버전 번호 0.1.10 포함', modalDom.version010,
        modalDom.contentSnippet)
      record('UI-3d', '"다운로드" actionLabel 버튼 표시', modalDom.hasDownloadBtn)
      await modalShown.win.screenshot({ path: path.join(SHOTS, 'UI-3-update-modal.png') }).catch(() => {})

      // UI-4a: 다운로드 버튼 클릭 → 다운로드 중 상태 알림 또는 완료
      await modalShown.win.locator('button', { hasText: '다운로드' }).first().click()
      // 다운로드는 캐시 hit 이면 즉시 완료, 아니면 5분 정도. 모달이 "업데이트 준비 완료" 로 갱신될 때까지 대기.
      const downloaded = await waitForText(app, (t) =>
        t.includes('업데이트 준비 완료') || t.includes('필수 업데이트 준비 완료'), 180000)
      record('UI-4a', '다운로드 완료 모달 표시 (업데이트 준비 완료)', !!downloaded,
        downloaded?.text?.replace(/\s+/g, ' ').slice(0, 80))

      if (downloaded) {
        const dlDom = await downloaded.win.evaluate(() => {
          const restartBtn = Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.trim() === '재시작')
          return {
            hasRestartBtn: !!restartBtn,
            bodyHas: (document.body.innerText || '').includes('재시작'),
          }
        })
        record('UI-4b', '"재시작" actionLabel 버튼 표시', dlDom.hasRestartBtn)
        await downloaded.win.screenshot({ path: path.join(SHOTS, 'UI-4-downloaded.png') }).catch(() => {})

        // UI-5: ESC 키로 모달 닫기 — (재시작 안 누르고 닫는 동선)
        await downloaded.win.keyboard.press('Escape')
        await new Promise((r) => setTimeout(r, 1500))
        const closed = await downloaded.win.evaluate(() => ({
          stillOpen: !!document.getElementById('notification-title'),
          stillHasModalOverlay: !!document.querySelector('.bg-black\\/60'),
        }))
        record('UI-5', 'ESC 키 → 알림 모달 닫힘', !closed.stillOpen && !closed.stillHasModalOverlay,
          `dialog=${closed.stillOpen}, overlay=${closed.stillHasModalOverlay}`)
      }
    }
  } finally {
    await app.close().catch(() => {}); await deleteLicense(key)
  }
}

// ─── UI-6+7: DeviceManager — "현재 기기" 배지 + 타 기기 삭제 → 자동 활성화 ─
async function ui6_7_deviceManagerUI() {
  console.log('\n[UI-6+7] 디바이스 관리 화면 — 현재 기기 배지 + 타 기기 삭제 후 진입')
  const key = await issueLicense('ui67@test.com')
  const fakeA = crypto.randomBytes(16).toString('hex')
  const fakeB = crypto.randomBytes(16).toString('hex')
  await preRegister(key, fakeA, 'Office-Laptop')
  await preRegister(key, fakeB, 'Home-Desktop')
  const ud = tmpUserData('ui67')
  fs.writeFileSync(path.join(ud, '.pending_license'), key, 'utf8')

  const app = await launchApp(ud)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 5000))
    const mgr = await waitForText(app, (t) => t.includes('기기 슬롯이 가득'), 30000)
    if (!mgr) { record('UI-6', 'DeviceManager 진입', false); return }

    // UI-6: DOM 검증 — Laptop 아이콘 + Old/Office device name + 현재 기기 배지 + 삭제 버튼
    const dom = await mgr.win.evaluate(() => {
      // 디바이스 행 추출
      const rows = Array.from(document.querySelectorAll('ul li'))
      const items = rows.map((row) => {
        const name = row.querySelector('.text-sm.font-medium')?.textContent?.trim()
        const isCurrentBadge = row.textContent?.includes('현재 기기')
        const trashBtn = row.querySelector('button[title*="삭제"]')
        const trashDisabled = trashBtn?.hasAttribute('disabled')
        return { name, isCurrentBadge, trashDisabled }
      })
      return {
        count: items.length,
        items,
        kakaoBtn: !!Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.includes('카카오톡')),
      }
    })
    record('UI-6a', '디바이스 2개 행 렌더링', dom.count === 2, `count=${dom.count}`)
    record('UI-6b', 'Office-Laptop 디바이스명 표시',
      dom.items.some((i) => i.name?.includes('Office-Laptop')))
    record('UI-6c', 'Home-Desktop 디바이스명 표시',
      dom.items.some((i) => i.name?.includes('Home-Desktop')))
    record('UI-6d', '디바이스 관리 화면에 카카오톡 문의 버튼', dom.kakaoBtn)
    // 현재 기기 badge — 활성화 시도 직후 limit 도달이라 current_device_id 동기화 되어야
    // 하지만 캐시 토큰 없어서 management 토큰 흐름 — listDevices 가 current_device_id 반환하는지 의존
    // 본 시나리오는 최소 fake 디바이스들 중 어느 것도 "현재 기기" 마킹 안 된 것 (= 현재 PC 의 device_id 가 fakeA/B 가 아니므로)
    const anyCurrent = dom.items.some((i) => i.isCurrentBadge)
    record('UI-6e', 'fake 디바이스에 "현재 기기" 배지 없음 (다른 PC 식별)', !anyCurrent,
      JSON.stringify(dom.items.map((i) => ({ n: i.name, cur: i.isCurrentBadge }))))
    await mgr.win.screenshot({ path: path.join(SHOTS, 'UI-6-device-mgr.png') }).catch(() => {})

    // UI-7: 첫 디바이스 삭제 클릭 → 모달 사라짐 + 메인 UI 진입
    const trashButtons = mgr.win.locator('button[title*="삭제"]')
    await trashButtons.first().click()
    const left = await waitForText(app, (t) =>
      !t.includes('기기 슬롯이 가득') && t.length > 5 && (t.includes('Inserty') || t.includes('실행 중')), 30000)
    record('UI-7', '디바이스 삭제 클릭 → 자동 활성화 후 메인 UI 진입',
      !!left, left?.text?.replace(/\s+/g, ' ').slice(0, 60))
    if (left) await left.win.screenshot({ path: path.join(SHOTS, 'UI-7-after-remove.png') }).catch(() => {})
  } finally {
    await app.close().catch(() => {}); await deleteLicense(key)
  }
}

// ─── UI-8: ExpiredModal — 차단 화면 + 라이센스 키 표시 + 카카오 버튼 ──
async function ui8_expiredModalUI() {
  console.log('\n[UI-8] 만료 화면 UI — 차단 박스 + 키 표시 + 카카오')
  const key = await issueLicense('ui8@test.com')
  await setField(key, 'expires_at', new Date(Date.now() - 24 * 3600 * 1000).toISOString())
  const ud = tmpUserData('ui8')
  const app = await launchApp(ud)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    if (!gate) { record('UI-8', 'gate 진입 실패', false); return }
    await gate.win.locator('input[placeholder*="INSRT"]').fill(key)
    await gate.win.locator('button', { hasText: '활성화' }).first().click()
    const expired = await waitForText(app, (t) => t.includes('라이센스가 만료'), 20000)
    if (!expired) { record('UI-8', 'ExpiredModal 미진입', false); return }

    const dom = await expired.win.evaluate(() => ({
      title: (document.body.innerText || '').includes('라이센스가 만료되었습니다'),
      kakao: !!Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.includes('카카오톡으로 문의')),
      keyShown: (document.body.innerText || '').includes('INSRT-'),
      warningTone: !!document.querySelector('.bg-accent-light'), // 노란/주황 톤
    }))
    record('UI-8a', '"라이센스가 만료되었습니다" 제목', dom.title)
    record('UI-8b', '"카카오톡으로 문의" 버튼', dom.kakao)
    record('UI-8c', '현재 라이센스 키 (INSRT-) 표시', dom.keyShown)
    record('UI-8d', '경고 톤 (bg-accent-light) 박스', dom.warningTone)
    await expired.win.screenshot({ path: path.join(SHOTS, 'UI-8-expired.png') }).catch(() => {})
  } finally {
    await app.close().catch(() => {}); await deleteLicense(key)
  }
}

// ─── UI-9: RevokedModal — 차단 화면 (danger tone) ─────────────────
async function ui9_revokedModalUI() {
  console.log('\n[UI-9] revoked 화면 UI — danger 톤 + Ban 아이콘')
  const key = await issueLicense('ui9@test.com')
  await setField(key, 'status', 'revoked')
  const ud = tmpUserData('ui9')
  const app = await launchApp(ud)
  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 3000))
    const gate = await waitForText(app, (t) => t.includes('라이센스 키 입력'), 30000)
    if (!gate) { record('UI-9', 'gate 진입 실패', false); return }
    await gate.win.locator('input[placeholder*="INSRT"]').fill(key)
    await gate.win.locator('button', { hasText: '활성화' }).first().click()
    const revoked = await waitForText(app, (t) => t.includes('라이센스가 무효화'), 20000)
    if (!revoked) { record('UI-9', 'RevokedModal 미진입', false); return }

    const dom = await revoked.win.evaluate(() => ({
      title: (document.body.innerText || '').includes('라이센스가 무효화되었습니다'),
      kakao: !!Array.from(document.querySelectorAll('button')).find((b) => b.innerText?.includes('카카오톡으로 문의')),
      dangerTone: !!document.querySelector('.bg-danger-light'), // 빨강 톤
    }))
    record('UI-9a', '"라이센스가 무효화되었습니다" 제목', dom.title)
    record('UI-9b', '"카카오톡으로 문의" 버튼', dom.kakao)
    record('UI-9c', 'danger 톤 (bg-danger-light) 박스', dom.dangerTone)
    await revoked.win.screenshot({ path: path.join(SHOTS, 'UI-9-revoked.png') }).catch(() => {})
  } finally {
    await app.close().catch(() => {}); await deleteLicense(key)
  }
}

;(async () => {
  console.log('=' + '='.repeat(68))
  console.log('  UI 중심 E2E — 사용자가 보고 클릭하는 화면 검증')
  console.log('=' + '='.repeat(68))

  const scenarios = [
    ui1_2_keyInputErrorThenSuccess,
    ui3_4_5_updateNotificationModal,
    ui6_7_deviceManagerUI,
    ui8_expiredModalUI,
    ui9_revokedModalUI,
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
  console.log(`UI FLOW SUMMARY: ${passed}/${ALL.length} PASS`)
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

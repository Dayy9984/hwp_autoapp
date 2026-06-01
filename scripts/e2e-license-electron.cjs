// 빌드된 Electron 앱 + Supabase 백엔드 E2E
// Playwright._electron으로 win-unpacked 앱을 실제 실행하면서 라이센스 모든 상태를 검증.
//
// 시나리오:
//   1. no_license:    캐시 X → KeyInputScreen + 카카오톡 버튼 클릭 → shell.openExternal 호출
//   2. invalid:       잘못된 형식 키 입력 → InvalidModal
//   3. ok (정상):     발급한 정상 키 입력 → 메인 UI 진입 (LicenseGate 사라짐)
//   4. expired:       만료된 키로 재시작 → ExpiredModal
//   5. revoked:       revoked 키로 재시작 → RevokedModal
//   6. leaked:        같은 키 5대 디바이스 활성화 후 검증 → LeakedModal
//   7. offline_grace: 네트워크 차단 시뮬레이션 → offline_grace 표시
//
// 격리: 각 시나리오마다 임시 userData 디렉터리 사용 (실제 사용자 데이터 보호)

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')
const crypto = require('crypto')
const https = require('https')

// ─── 상수 ──────────────────────────────────────────────────────────────────────
const APP_EXE = path.resolve(
  __dirname,
  '..',
  'release',
  '0.1.9',
  'win-unpacked',
  'Inserty AI.exe',
)
const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'
const ANON_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjkzMjY3MDcsImV4cCI6MjA4NDkwMjcwN30.zZTHOiJmBd6nOEtLN4F5Lt8KS_v5VXePIco09anHK3c'

// ─── env 정리 (VSCode가 주입한 ELECTRON_RUN_AS_NODE 제거) ───────────────────────
delete process.env.ELECTRON_RUN_AS_NODE
const ELECTRON_ENV = Object.entries(process.env)
  .filter(([k]) => k !== 'ELECTRON_RUN_AS_NODE')
  .map(([name, value]) => ({ name, value }))

// ─── 백엔드 헬퍼 ───────────────────────────────────────────────────────────────
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

async function issueLicense(email, durationDays = 30, notes = 'e2e') {
  const res = await httpJson('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`, {
    p_email: email,
    p_duration: durationDays,
    p_notes: notes,
  })
  if (res.status !== 200) throw new Error(`issue_license ${res.status}: ${JSON.stringify(res.body)}`)
  return res.body[0].license_key
}

async function setField(license_key, field, value) {
  const res = await httpJson(
    'PATCH',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${license_key}`,
    { [field]: value },
  )
  if (res.status >= 400) throw new Error(`PATCH ${field} ${res.status}`)
}

async function deleteLicense(license_key) {
  await httpJson(
    'DELETE',
    `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${license_key}`,
  )
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${license_key}`)
}

async function activateOnce(license_key, deviceId) {
  return httpJson(
    'POST',
    `${SUPABASE_URL}/functions/v1/activate`,
    {
      license_key,
      device_id: deviceId,
      device_name: 'E2E',
      device_os: 'win32 26.0.0',
    },
    ANON_KEY,
  )
}

// ─── 캐시 파일 직접 작성 ─────────────────────────────────────────────────────
// license-bridge가 사용하는 AES-256-GCM 형식과 동일하게 미리 작성하여
// 특정 상태(만료 토큰 보유 등)를 시뮬레이션.
const ENC_KEY = crypto.createHash('sha256').update('insertyai-license-store-v1').digest()
function writeCache(userDataDir, cacheObj) {
  const plain = Buffer.from(JSON.stringify(cacheObj), 'utf8')
  const iv = crypto.randomBytes(12)
  const cipher = crypto.createCipheriv('aes-256-gcm', ENC_KEY, iv)
  const enc = Buffer.concat([cipher.update(plain), cipher.final()])
  const tag = cipher.getAuthTag()
  fs.mkdirSync(userDataDir, { recursive: true })
  fs.writeFileSync(path.join(userDataDir, '.license_cache'), Buffer.concat([iv, tag, enc]))
}

// ─── Playwright Electron 헬퍼 ───────────────────────────────────────────────
async function launch(userDataDir, extraArgs = []) {
  return await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`, ...extraArgs],
    env: ELECTRON_ENV,
    timeout: 120000,
  })
}

async function getInitialStatus(app) {
  return app.evaluate(async ({ ipcMain }) => {
    // main 프로세스에서 등록된 license:getInitialStatus 핸들러 직접 호출
    // (renderer가 호출하는 것과 동일한 경로)
    const handlers = ipcMain['_invokeHandlers'] || ipcMain._handlers || ipcMain['_eventsToInvokeHandlers']
    return null  // placeholder; 대신 renderer에서 확인
  })
}

function tmpUserData(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `inserty-e2e-${label}-`))
}

// ─── 검증 결과 ──────────────────────────────────────────────────────────────
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

// ─── 시나리오 1: no_license + 카카오톡 클릭 ──────────────────────────────────
async function scenario_no_license_and_kakao() {
  console.log('\n[1] no_license → KeyInputScreen + 카카오톡 버튼 클릭')
  const userDataDir = tmpUserData('no-lic')
  const app = await launch(userDataDir)
  try {
    // shell.openExternal 스파이 설치
    await app.evaluate(({ shell }) => {
      ;(global)._capturedOpenExternal = []
      const original = shell.openExternal.bind(shell)
      shell.openExternal = async (url) => {
        ;(global)._capturedOpenExternal.push(url)
        return undefined  // 실제 브라우저 열지 않음
      }
    })

    const win = await app.firstWindow({ timeout: 30000 })
    // 라이센스 상태 직접 조회 (renderer가 호출하는 IPC와 동일)
    const status = await win.evaluate(async () =>
      await window.electronAPI.license.getInitialStatus(),
    )
    check('initial status = no_license', status?.state === 'no_license', JSON.stringify(status))

    // KakaoSupportLink가 노출하는 IPC 호출
    await win.evaluate(async () => {
      await window.electronAPI.license.openExternal('https://open.kakao.com/o/sSm9ZXei')
    })
    // 잠깐 대기 (IPC 비동기)
    await new Promise((r) => setTimeout(r, 500))
    const captured = await app.evaluate(() => (global)._capturedOpenExternal)
    check(
      'shell.openExternal 호출됨',
      Array.isArray(captured) && captured.length === 1,
      JSON.stringify(captured),
    )
    check(
      '카카오톡 URL 정확',
      captured?.[0] === 'https://open.kakao.com/o/sSm9ZXei',
      captured?.[0] || '(none)',
    )
  } finally {
    await app.close()
  }
}

// ─── 시나리오 2: 잘못된 형식 키 ─────────────────────────────────────────────
async function scenario_invalid_format() {
  console.log('\n[2] 잘못된 형식 키 입력 → invalid 상태')
  const userDataDir = tmpUserData('invalid-fmt')
  const app = await launch(userDataDir)
  try {
    const win = await app.firstWindow({ timeout: 30000 })
    const result = await win.evaluate(async () =>
      await window.electronAPI.license.activate('WRONG-FORMAT-KEY'),
    )
    check(
      'invalid_key_format 반환',
      result?.state === 'invalid' && result?.reason === 'invalid_key_format',
      JSON.stringify(result),
    )
  } finally {
    await app.close()
  }
}

// ─── 시나리오 3: 정상 활성화 → ok ───────────────────────────────────────────
async function scenario_normal_activate() {
  console.log('\n[3] 정상 키 입력 → ok')
  const key = await issueLicense('e2e+ok@test.com', 30, 'e2e-ok')
  const userDataDir = tmpUserData('ok')
  const app = await launch(userDataDir)
  try {
    const win = await app.firstWindow({ timeout: 30000 })
    const result = await win.evaluate(async (k) =>
      await window.electronAPI.license.activate(k),
      key,
    )
    check('activate.ok', result?.state === 'ok', JSON.stringify(result))
    check('expires_at 응답', !!result?.expires_at, result?.expires_at || '(null)')

    // 캐시 파일 생성 확인
    const cachePath = path.join(userDataDir, '.license_cache')
    check('.license_cache 생성됨', fs.existsSync(cachePath))

    // 캐시된 키 조회
    const cached = await win.evaluate(async () =>
      await window.electronAPI.license.getCachedKey(),
    )
    check('getCachedKey 동일', cached === key, `cached=${cached} expected=${key}`)
  } finally {
    await app.close()
    await deleteLicense(key)
  }
}

// ─── 시나리오 4: 만료된 키 → expired ─────────────────────────────────────────
async function scenario_expired() {
  console.log('\n[4] 만료된 키 → expired')
  const key = await issueLicense('e2e+expired@test.com', 30)
  const userDataDir = tmpUserData('expired')
  const app = await launch(userDataDir)
  try {
    const win = await app.firstWindow({ timeout: 30000 })
    // expires_at 과거로
    const past = new Date(Date.now() - 24 * 3600 * 1000).toISOString()
    await setField(key, 'expires_at', past)
    const result = await win.evaluate(async (k) =>
      await window.electronAPI.license.activate(k),
      key,
    )
    check('expired 상태', result?.state === 'expired', JSON.stringify(result))
  } finally {
    await app.close()
    await deleteLicense(key)
  }
}

// ─── 시나리오 5: revoked ─────────────────────────────────────────────────────
async function scenario_revoked() {
  console.log('\n[5] revoked 키 → revoked')
  const key = await issueLicense('e2e+revoked@test.com', 30)
  const userDataDir = tmpUserData('revoked')
  const app = await launch(userDataDir)
  try {
    const win = await app.firstWindow({ timeout: 30000 })
    await setField(key, 'status', 'revoked')
    const result = await win.evaluate(async (k) =>
      await window.electronAPI.license.activate(k),
      key,
    )
    check('revoked 상태', result?.state === 'revoked', JSON.stringify(result))
  } finally {
    await app.close()
    await deleteLicense(key)
  }
}

// ─── 시나리오 6: 정책 v2 — 3대째 활성화 → device_limit_reached ─────────────
async function scenario_leaked() {
  console.log('\n[6] 3대 디바이스 활성화 → device_limit_reached (정책 v2)')
  const key = await issueLicense('e2e+devlimit@test.com', 30)
  try {
    // 백엔드로 2대 미리 활성화 (서로 다른 device_id) — 슬롯 꽉 채움
    for (let i = 0; i < 2; i++) {
      const did = crypto.randomBytes(16).toString('hex')
      const r = await activateOnce(key, did)
      if (!r.body?.ok) {
        check(`setup activate #${i + 1}`, false, JSON.stringify(r.body))
        return
      }
    }
    // 3번째는 앱 안에서 (이번 PC 의 device_id)
    const userDataDir = tmpUserData('devlimit')
    const app = await launch(userDataDir)
    try {
      const win = await app.firstWindow({ timeout: 30000 })
      const result = await win.evaluate(async (k) =>
        await window.electronAPI.license.activate(k),
        key,
      )
      check(
        'device_limit_reached + count=2 + max=2',
        result?.state === 'device_limit_reached'
          && (result?.device_count ?? 0) === 2
          && (result?.max_devices ?? 0) === 2,
        JSON.stringify(result),
      )
    } finally {
      await app.close()
    }
  } finally {
    await deleteLicense(key)
  }
}

// ─── 시나리오 7: offline grace (네트워크 끊김) ───────────────────────────────
async function scenario_offline_grace() {
  console.log('\n[7] offline grace: 캐시 있음 + 네트워크 실패 → offline_grace')
  const userDataDir = tmpUserData('offline')
  // 사전: 정상 캐시 직접 작성 (1일 전 검증 → grace 윈도우 내)
  writeCache(userDataDir, {
    token: 'eyJtest.invalid.token',  // 어차피 verify는 네트워크 단계에서 실패할 것
    license_key: 'INSRT-AAAA-AAAA-AAAA-AAAA',
    expires_at: '2027-01-01T00:00:00.000Z',
    last_verified_at: Date.now() - 24 * 3600 * 1000,
    server_clock_offset_ms: 0,
  })

  // 네트워크 차단: hosts 파일 수정은 위험 → 대신 main에서 fetch 가로채기
  const app = await launch(userDataDir)
  try {
    await app.evaluate(() => {
      const origFetch = global.fetch
      ;(global)._origFetch = origFetch
      global.fetch = async () => {
        throw new Error('NETWORK_DISABLED_FOR_TEST')
      }
    })
    const win = await app.firstWindow({ timeout: 30000 })
    const result = await win.evaluate(async () =>
      await window.electronAPI.license.verify(),
    )
    check('offline_grace 상태', result?.state === 'offline_grace', JSON.stringify(result))
  } finally {
    await app.close()
  }
}

// ─── 시나리오 8: 7일 이상 오프라인 → offline_blocked ───────────────────────
async function scenario_offline_blocked() {
  console.log('\n[8] offline blocked: 캐시 있음 + 8일 경과 + 네트워크 실패 → offline_blocked')
  const userDataDir = tmpUserData('offline-blocked')
  writeCache(userDataDir, {
    token: 'eyJtest.invalid.token',
    license_key: 'INSRT-BBBB-BBBB-BBBB-BBBB',
    expires_at: '2027-01-01T00:00:00.000Z',
    last_verified_at: Date.now() - 8 * 24 * 3600 * 1000,  // 8일 전
    server_clock_offset_ms: 0,
  })

  const app = await launch(userDataDir)
  try {
    await app.evaluate(() => {
      global.fetch = async () => {
        throw new Error('NETWORK_DISABLED_FOR_TEST')
      }
    })
    const win = await app.firstWindow({ timeout: 30000 })
    const result = await win.evaluate(async () =>
      await window.electronAPI.license.verify(),
    )
    check('offline_blocked 상태', result?.state === 'offline_blocked', JSON.stringify(result))
  } finally {
    await app.close()
  }
}

// ─── Main ──────────────────────────────────────────────────────────────────
;(async () => {
  console.log('='.repeat(70))
  console.log('Electron 빌드된 앱 — 라이센스 모든 기능 E2E')
  console.log('exe:', APP_EXE)
  console.log('='.repeat(70))

  const scenarios = [
    scenario_no_license_and_kakao,
    scenario_invalid_format,
    scenario_normal_activate,
    scenario_expired,
    scenario_revoked,
    scenario_leaked,
    scenario_offline_grace,
    scenario_offline_blocked,
  ]
  for (const sc of scenarios) {
    try {
      await sc()
    } catch (e) {
      check(sc.name, false, `EXCEPTION: ${e.message}`)
    }
  }

  const passed = results.filter((r) => r.ok).length
  const total = results.length
  console.log('\n' + '='.repeat(70))
  console.log(`SUMMARY: ${passed}/${total} PASS`)
  for (const r of results) {
    console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
  }
  console.log('='.repeat(70))
  process.exit(passed === total ? 0 : 1)
})()

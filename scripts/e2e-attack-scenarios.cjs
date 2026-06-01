// 공격 시나리오 E2E.
// 상업 출시 전 — 라이센스 시스템이 현실적인 공격 패턴을 견디는지 검증.
//
// 시나리오:
//   ATK-1: ANON 키로 licenses/license_devices 직접 CRUD 시도 (RLS 우회)
//   ATK-2: 서명 없는 JWT 위조 → verify 호출
//   ATK-3: 만료된 JWT 사용 (exp 가 과거인 토큰)
//   ATK-4: 다른 device_id 에서 정식 토큰 재사용 (라이센스 캐시 P2P 공유 시뮬)
//   ATK-5: management_token 으로 verify 호출 (scope 우회)
//   ATK-6: 자기 자신 device 삭제 시도 (cannot_remove_self 가드)
//   ATK-7: 활성화 직후 토큰으로 다른 device_id verify (token-device 바인딩)
//   ATK-8: 폐기된 PAT 가 source repo 접근 시도 (이미 검증, 재확인)
//   ATK-9: 업데이트 binary 변조 탐지 (blockmap signature)
//   ATK-10: license_key 무차별 대입 (random 100건)
//
// 합격 기준:
//   모든 공격이 서버/클라이언트 가드에 의해 차단되어야 함.

const https = require('https')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const ANON =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjkzMjY3MDcsImV4cCI6MjA4NDkwMjcwN30.zZTHOiJmBd6nOEtLN4F5Lt8KS_v5VXePIco09anHK3c'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'

function httpJson(method, urlStr, body, authToken = ANON, extraHeaders = {}) {
  const url = new URL(urlStr)
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const req = https.request({
      method,
      hostname: url.hostname,
      path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
        apikey: authToken,
        ...extraHeaders,
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let buf = ''
      res.on('data', (c) => (buf += c))
      res.on('end', () => {
        let parsed = buf
        try { parsed = buf ? JSON.parse(buf) : null } catch {}
        resolve({ status: res.statusCode, body: parsed })
      })
    })
    req.on('error', reject)
    if (data) req.write(data)
    req.end()
  })
}

async function issueLicense(email) {
  const r = await httpJson('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`,
    { p_email: email, p_duration: 30, p_notes: 'attack-e2e' }, SERVICE_ROLE,
    { Prefer: 'return=representation' })
  return r.body[0].license_key
}
async function deleteLicense(key) {
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/license_devices?license_key=eq.${key}`,
    null, SERVICE_ROLE)
  await httpJson('DELETE', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`,
    null, SERVICE_ROLE)
}
async function activate(license_key, deviceId, name = 'attacker-pc') {
  return await httpJson('POST', `${SUPABASE_URL}/functions/v1/activate`, {
    license_key, device_id: deviceId, device_name: name, device_os: 'attacker-os',
  })
}
async function verify(token, deviceId) {
  return await httpJson('POST', `${SUPABASE_URL}/functions/v1/verify`, {
    token, device_id: deviceId,
  })
}
async function devicesRemove(token, target_device_id, deviceId) {
  return await httpJson('POST', `${SUPABASE_URL}/functions/v1/devices-remove`, {
    token, target_device_id, device_id: deviceId,
  })
}

const results = []
function record(id, name, blocked, detail = '') {
  results.push({ id, name, blocked, detail })
  const tag = blocked ? '✓ BLOCKED' : '✗ EXPLOITABLE'
  console.log(`  [${tag}] ${id}: ${name}${detail ? ' — ' + detail : ''}`)
}

// ─── ATK-1: ANON 키로 라이센스 테이블 직접 CRUD ───────────────────
// Supabase RLS 는 권한 없으면 403 대신 status=200/204 + empty body 로 silent fail.
// 따라서 status 만 보면 안 되고, SERVICE 로 read-back 해서 실제 DB 변경 여부 확인 필수.
async function atk1_anonRlsBypass() {
  console.log('\n[ATK-1] ANON 키로 licenses / license_devices RLS 우회 시도')

  // 실제 라이센스 발급 → ANON 으로 조작 시도 → SERVICE 로 read-back 비교
  const key = await issueLicense('atk1@test.com')
  const initial = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}&select=expires_at,status`,
    null, SERVICE_ROLE)
  const initialExp = initial.body[0]?.expires_at
  const initialStatus = initial.body[0]?.status

  // 1a) SELECT — 다른 사람의 라이센스 정보를 보려는 시도
  const sel = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?select=license_key,email&limit=10`,
    null, ANON)
  const selBlocked = !Array.isArray(sel.body) || sel.body.length === 0
  record('ATK-1a', 'ANON SELECT licenses (다른 사람 라이센스 조회)', selBlocked,
    `count=${Array.isArray(sel.body) ? sel.body.length : 'n/a'}`)

  // 1b) INSERT — 가짜 라이센스 자가 발급
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/licenses`, {
    license_key: 'INSRT-HACK-HACK-HACK-HACK',
    email: 'attacker@test.com', status: 'active',
    expires_at: '2030-12-31T00:00:00Z',
  }, ANON)
  const insVerify = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.INSRT-HACK-HACK-HACK-HACK&select=license_key`,
    null, SERVICE_ROLE)
  const insBlocked = !Array.isArray(insVerify.body) || insVerify.body.length === 0
  record('ATK-1b', 'ANON INSERT licenses (가짜 라이센스 자가 발급)', insBlocked,
    `inserted_rows=${insVerify.body?.length || 0}`)

  // 1c) UPDATE — 실제 라이센스의 만료일을 영원으로 연장 시도
  await httpJson('PATCH', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`,
    { expires_at: '2099-12-31T00:00:00Z' }, ANON)
  const verify1c = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}&select=expires_at`,
    null, SERVICE_ROLE)
  const updBlocked = verify1c.body[0]?.expires_at === initialExp
  record('ATK-1c', 'ANON UPDATE expires_at (만료일 무한 연장)', updBlocked,
    `before=${initialExp?.slice(0, 10)}, after=${verify1c.body[0]?.expires_at?.slice(0, 10)}`)

  // 1d) UPDATE status — revoked 라이센스를 active 로 복원 시도
  await httpJson('PATCH', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`,
    { status: 'active' }, ANON)
  const verify1d = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}&select=status`,
    null, SERVICE_ROLE)
  // 이미 active 인 상태로 UPDATE 시도라 변경되어도 의미 없음. status 가 바뀔 수 있는지만 확인.
  // 더 의미있는 테스트: status 를 invalid value 로 변경 시도
  await httpJson('PATCH', `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`,
    { status: 'compromised' }, ANON)
  const verify1d2 = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}&select=status`,
    null, SERVICE_ROLE)
  const updStatusBlocked = verify1d2.body[0]?.status === initialStatus
  record('ATK-1d', 'ANON UPDATE status (상태 변경)', updStatusBlocked,
    `status remained: ${verify1d2.body[0]?.status}`)

  // 1e) DELETE license_devices — 슬롯 비우기 시도
  // 먼저 device row 1개 등록
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/license_devices`, {
    license_key: key, device_id: 'atk1-target', device_name: 'target',
    device_os: 'target', last_seen_at: new Date().toISOString(),
  }, SERVICE_ROLE)
  await httpJson('DELETE',
    `${SUPABASE_URL}/rest/v1/license_devices?device_id=eq.atk1-target`,
    null, ANON)
  const verify1e = await httpJson('GET',
    `${SUPABASE_URL}/rest/v1/license_devices?device_id=eq.atk1-target&select=device_id`,
    null, SERVICE_ROLE)
  const delBlocked = Array.isArray(verify1e.body) && verify1e.body.length > 0
  record('ATK-1e', 'ANON DELETE license_devices (슬롯 우회)', delBlocked,
    `row_still_exists=${verify1e.body?.length > 0}`)

  // cleanup
  await deleteLicense(key)
  await httpJson('DELETE',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.INSRT-HACK-HACK-HACK-HACK`,
    null, SERVICE_ROLE)
}

// ─── ATK-2: 서명 없는 JWT 위조 ────────────────────────────────────
async function atk2_jwtForge() {
  console.log('\n[ATK-2] 서명 검증 우회 — 위조 JWT 로 verify')
  const forgedPayload = Buffer.from(JSON.stringify({
    sub: 'INSRT-FAKE-FAKE-FAKE-FAKE',
    device_id: 'forged-device-id',
    exp: Math.floor(Date.now() / 1000) + 3600,
    iat: Math.floor(Date.now() / 1000),
  })).toString('base64url')
  const fakeHeader = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const forgedToken = `${fakeHeader}.${forgedPayload}.`

  const r = await verify(forgedToken, 'forged-device-id')
  const blocked = r.body?.ok === false
  record('ATK-2', '위조 JWT (alg=none) → verify 거부', blocked,
    `status=${r.status}, reason=${r.body?.reason}`)

  // 2b) ES256 인척 random signature 붙임
  const fakeHeader2 = Buffer.from(JSON.stringify({ alg: 'ES256', typ: 'JWT' })).toString('base64url')
  const randomSig = crypto.randomBytes(64).toString('base64url')
  const forgedToken2 = `${fakeHeader2}.${forgedPayload}.${randomSig}`
  const r2 = await verify(forgedToken2, 'forged-device-id')
  const blocked2 = r2.body?.ok === false
  record('ATK-2b', '위조 JWT (random ES256 sig) → verify 거부', blocked2,
    `reason=${r2.body?.reason}`)
}

// ─── ATK-3: 만료된 JWT (exp 가 과거) ───────────────────────────────
async function atk3_expiredJwt() {
  console.log('\n[ATK-3] exp 가 과거인 JWT')
  // 실제 토큰을 발급받아 exp 만 손대는 건 서명 깨지므로,
  // verify 가 JWT 만료 검사를 하는지만 확인.
  const expiredPayload = Buffer.from(JSON.stringify({
    sub: 'INSRT-X', device_id: 'x', exp: 1000, iat: 1000,
  })).toString('base64url')
  const h = Buffer.from(JSON.stringify({ alg: 'ES256', typ: 'JWT' })).toString('base64url')
  const sig = crypto.randomBytes(64).toString('base64url')
  const r = await verify(`${h}.${expiredPayload}.${sig}`, 'x')
  const blocked = r.body?.ok === false
  record('ATK-3', '만료 + 위조 JWT → verify 거부', blocked, `reason=${r.body?.reason}`)
}

// ─── ATK-4: 정식 토큰 + 다른 device_id (라이센스 캐시 P2P 시뮬) ─
async function atk4_tokenReuseOtherDevice() {
  console.log('\n[ATK-4] 정식 토큰 + 다른 device_id (캐시 복제 P2P)')
  const key = await issueLicense('atk4@test.com')
  const deviceA = crypto.randomBytes(16).toString('hex')
  const deviceB = crypto.randomBytes(16).toString('hex')

  // A 에서 정식 활성화
  const actA = await activate(key, deviceA)
  if (!actA.body?.ok) {
    record('ATK-4 setup', 'activate failed', false, `reason=${actA.body?.reason}`)
    return await deleteLicense(key)
  }
  const realToken = actA.body.token

  // B 가 A 의 토큰을 훔쳐서 verify 시도 (cache 복제 시뮬)
  const r = await verify(realToken, deviceB)
  const blocked = r.body?.ok === false
  record('ATK-4', 'A 의 토큰을 B device_id 로 verify → 차단', blocked,
    `reason=${r.body?.reason}`)
  await deleteLicense(key)
}

// ─── ATK-5: management_token 으로 verify (scope 우회) ──────────────
async function atk5_managementTokenScopeAbuse() {
  console.log('\n[ATK-5] management_token 으로 verify (scope:manage → scope:verify 우회)')
  const key = await issueLicense('atk5@test.com')
  const fakeA = crypto.randomBytes(16).toString('hex')
  const fakeB = crypto.randomBytes(16).toString('hex')

  // 사전 등록 (슬롯 꽉 채워서 activate 가 management_token 발급하게 함)
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/license_devices`, {
    license_key: key, device_id: fakeA, device_name: 'Fake-A',
    device_os: 'fake', last_seen_at: new Date().toISOString(),
  }, SERVICE_ROLE)
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/license_devices`, {
    license_key: key, device_id: fakeB, device_name: 'Fake-B',
    device_os: 'fake', last_seen_at: new Date().toISOString(),
  }, SERVICE_ROLE)

  const attackerDevice = crypto.randomBytes(16).toString('hex')
  const act = await activate(key, attackerDevice)
  if (act.body?.reason !== 'device_limit_reached' || !act.body?.management_token) {
    record('ATK-5 setup', 'management_token 미발급', false, JSON.stringify(act.body))
    return await deleteLicense(key)
  }
  const mgmtToken = act.body.management_token

  // 공격: management_token 으로 verify 호출
  const r = await verify(mgmtToken, attackerDevice)
  const blocked = r.body?.ok === false
  record('ATK-5', 'management_token → verify 거부 (scope 검사)', blocked,
    `reason=${r.body?.reason}`)

  await deleteLicense(key)
}

// ─── ATK-6: 자기 자신 device 삭제 (cannot_remove_self) ────────────
async function atk6_removeSelf() {
  console.log('\n[ATK-6] 정상 토큰으로 자기 자신 디바이스 삭제 시도')
  const key = await issueLicense('atk6@test.com')
  const device = crypto.randomBytes(16).toString('hex')

  const act = await activate(key, device)
  if (!act.body?.ok) {
    record('ATK-6 setup', 'activate failed', false, JSON.stringify(act.body))
    return await deleteLicense(key)
  }

  const r = await devicesRemove(act.body.token, device, device)
  const blocked = r.body?.ok === false && r.body?.reason === 'cannot_remove_self'
  record('ATK-6', '자기 device_id 삭제 → cannot_remove_self', blocked,
    `reason=${r.body?.reason}`)

  await deleteLicense(key)
}

// ─── ATK-7: 활성화 토큰 + 다른 device_id (재확인 — token-device 바인딩) ─
async function atk7_devicesRemoveCrossDevice() {
  console.log('\n[ATK-7] A 의 verify-token 으로 B 가 다른 디바이스 삭제 시도')
  const key = await issueLicense('atk7@test.com')
  const deviceA = crypto.randomBytes(16).toString('hex')
  const deviceB = crypto.randomBytes(16).toString('hex')

  // 사전 등록
  await httpJson('POST', `${SUPABASE_URL}/rest/v1/license_devices`, {
    license_key: key, device_id: deviceB, device_name: 'Fake-B',
    device_os: 'fake', last_seen_at: new Date().toISOString(),
  }, SERVICE_ROLE)
  const act = await activate(key, deviceA)
  if (!act.body?.ok) {
    record('ATK-7 setup', 'activate failed', false)
    return await deleteLicense(key)
  }
  // A 가 정상 토큰으로 B 삭제 → 정상적인 동작 (자기 라이센스 내 타 디바이스 삭제 가능해야 디바이스 관리 화면이 작동)
  const r = await devicesRemove(act.body.token, deviceB, deviceA)
  // 이게 차단되면 안 됨 (이건 정상 흐름). 본 케이스는 BLOCKED 가 아닌 ALLOWED 가 정답.
  const allowedAsExpected = r.body?.ok === true
  record('ATK-7', '자기 라이센스 내 타 디바이스 삭제 (정상)',
    allowedAsExpected, `ok=${r.body?.ok}, count=${r.body?.device_count}`)

  await deleteLicense(key)
}

// ─── ATK-8: 폐기된 PAT 의 source repo 접근 (재확인) ───────────────
async function atk8_oldPatSourceAccess() {
  console.log('\n[ATK-8] 신규 PAT 의 권한 제한 재확인')
  // 클라이언트에 내장된 새 PAT
  const newPat = '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'
  const tryFetch = (path) => new Promise((resolve) => {
    const r = https.request({
      method: 'GET', hostname: 'api.github.com', path,
      headers: { Authorization: `token ${newPat}`, 'User-Agent': 'atk-test', Accept: 'application/vnd.github+json' },
    }, (res) => {
      res.on('data', () => {}); res.on('end', () => resolve(res.statusCode))
    })
    r.on('error', () => resolve(-1)); r.end()
  })
  const srcContents = await tryFetch('/repos/OpenScoutAI/insertyai/contents/package.json')
  const srcReleases = await tryFetch('/repos/OpenScoutAI/insertyai/releases')
  const relRel = await tryFetch('/repos/OpenScoutAI/insertyai_release/releases')

  record('ATK-8a', '신규 PAT → source repo contents', srcContents === 404 || srcContents === 403,
    `HTTP ${srcContents}`)
  record('ATK-8b', '신규 PAT → source repo releases', srcReleases === 404 || srcReleases === 403,
    `HTTP ${srcReleases}`)
  // 정상 동작 확인 (release repo 는 200 이어야)
  record('ATK-8c', '신규 PAT → release repo (정상 200 확인)', relRel === 200,
    `HTTP ${relRel}`)
}

// ─── ATK-9: 업데이트 binary 변조 탐지 (blockmap) ────────────────────
async function atk9_updaterIntegrity() {
  console.log('\n[ATK-9] 자동 업데이트 무결성 — blockmap + SHA512 검증')
  // latest.yml 안에 sha512 가 있는지 확인 (electron-updater 가 다운로드 후 비교)
  const latestYml = path.resolve(__dirname, '..', 'release', '0.1.9', 'latest.yml')
  if (!fs.existsSync(latestYml)) {
    record('ATK-9', 'latest.yml 없음', false)
    return
  }
  const content = fs.readFileSync(latestYml, 'utf8')
  const hasSha512 = /sha512:\s*[A-Za-z0-9+/=]{40,}/.test(content)
  const hasBlockmap = /\.blockmap/.test(content) || fs.existsSync(latestYml.replace('latest.yml', 'Inserty-AI_0.1.9_Setup.exe.blockmap'))
  record('ATK-9a', 'latest.yml 에 sha512 해시 포함 (변조 탐지)',
    hasSha512, `매치: ${content.match(/sha512:.*/)?.[0]?.slice(0, 50) || 'none'}`)
  record('ATK-9b', 'blockmap 파일 존재 (delta 무결성)', hasBlockmap)
}

// ─── ATK-10: 무차별 대입 license_key ───────────────────────────────
async function atk10_bruteforce() {
  console.log('\n[ATK-10] 무작위 license_key 30회 시도 (서버가 응답하는지만 확인)')
  let blockedCount = 0
  let serverErrors = 0
  const start = Date.now()
  for (let i = 0; i < 30; i++) {
    const randKey = 'INSRT-' + Array.from({ length: 4 }, () =>
      crypto.randomBytes(2).toString('hex').toUpperCase()).join('-')
    const r = await activate(randKey, crypto.randomBytes(16).toString('hex'))
    if (r.body?.ok === false) blockedCount++
    if (r.status >= 500) serverErrors++
  }
  const elapsed = ((Date.now() - start) / 1000).toFixed(1)
  record('ATK-10', `30 random keys 모두 차단 (서버 stable)`,
    blockedCount === 30 && serverErrors === 0,
    `blocked=${blockedCount}/30, 500-errors=${serverErrors}, ${elapsed}s`)
}

// ─── 실행 ──────────────────────────────────────────────────────────
;(async () => {
  console.log('=' + '='.repeat(68))
  console.log('  공격 시나리오 E2E — 보안 가드 검증')
  console.log('=' + '='.repeat(68))

  const scenarios = [
    atk1_anonRlsBypass,
    atk2_jwtForge,
    atk3_expiredJwt,
    atk4_tokenReuseOtherDevice,
    atk5_managementTokenScopeAbuse,
    atk6_removeSelf,
    atk7_devicesRemoveCrossDevice,
    atk8_oldPatSourceAccess,
    atk9_updaterIntegrity,
    atk10_bruteforce,
  ]
  for (const s of scenarios) {
    try { await s() }
    catch (e) { record(s.name, 'EXCEPTION', false, e?.message || String(e)) }
  }

  const passed = results.filter((r) => r.blocked).length
  console.log('\n' + '='.repeat(70))
  console.log(`ATTACK SUMMARY: ${passed}/${results.length} 차단됨`)
  console.log('='.repeat(70))
  const exploitable = results.filter((r) => !r.blocked)
  if (exploitable.length) {
    console.log('\n취약 (확인 필요):')
    for (const r of exploitable) {
      console.log(`  ✗ ${r.id}: ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
    }
  }
  process.exit(exploitable.length ? 1 : 0)
})().catch((e) => { console.error('FATAL:', e); process.exit(2) })

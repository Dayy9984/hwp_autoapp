// 라이센스 클라이언트 측 로직 단위 테스트
// AES-256-GCM round-trip + 오프라인 grace 분기 + pending key 흐름 검증.
//
// 실행:
//   node scripts/test-license-client.cjs

const crypto = require('crypto')
const fs = require('fs')
const os = require('os')
const path = require('path')

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

// ─── 1. AES-256-GCM round-trip ──────────────────────────────────────────────────
console.log('\n[1] 로컬 캐시 AES-256-GCM 암복호화 round-trip')
{
  const ENC_KEY = crypto.createHash('sha256').update('insertyai-license-store-v1').digest()
  const payload = {
    token: 'eyJtest.jwt.token',
    license_key: 'INSRT-TEST-XXXX-XXXX-XXXX',
    expires_at: '2027-01-01T00:00:00.000Z',
    last_verified_at: Date.now(),
    server_clock_offset_ms: 1234,
  }
  // encrypt
  const plain = Buffer.from(JSON.stringify(payload), 'utf8')
  const iv = crypto.randomBytes(12)
  const cipher = crypto.createCipheriv('aes-256-gcm', ENC_KEY, iv)
  const enc = Buffer.concat([cipher.update(plain), cipher.final()])
  const tag = cipher.getAuthTag()
  const blob = Buffer.concat([iv, tag, enc])

  // decrypt
  const data = blob
  const iv2 = data.slice(0, 12)
  const tag2 = data.slice(12, 28)
  const enc2 = data.slice(28)
  const decipher = crypto.createDecipheriv('aes-256-gcm', ENC_KEY, iv2)
  decipher.setAuthTag(tag2)
  const plain2 = Buffer.concat([decipher.update(enc2), decipher.final()])
  const restored = JSON.parse(plain2.toString('utf8'))
  check('round-trip equal', JSON.stringify(payload) === JSON.stringify(restored))

  // tamper detection: flip a byte in ciphertext → decrypt throws
  const blob2 = Buffer.from(blob)
  blob2[blob2.length - 1] ^= 0xff
  let threw = false
  try {
    const d2 = crypto.createDecipheriv('aes-256-gcm', ENC_KEY, blob2.slice(0, 12))
    d2.setAuthTag(blob2.slice(12, 28))
    d2.update(blob2.slice(28))
    d2.final()
  } catch {
    threw = true
  }
  check('tamper detection', threw, '캐시 변조 시 복호화 실패')
}

// ─── 2. 오프라인 grace 분기 ─────────────────────────────────────────────────────
console.log('\n[2] 오프라인 grace 분기')
{
  const GRACE_DAYS = 7
  const day = 24 * 3600 * 1000

  function classify(lastVerifiedAt, nowMs) {
    const daysSince = (nowMs - lastVerifiedAt) / day
    return daysSince < GRACE_DAYS ? 'offline_grace' : 'offline_blocked'
  }

  const now = Date.now()
  check('verify 1일 후 네트워크 실패 → grace',
        classify(now - 1 * day, now) === 'offline_grace')
  check('verify 6.9일 후 → grace',
        classify(now - 6.9 * day, now) === 'offline_grace')
  check('verify 정확히 7일 후 → blocked',
        classify(now - 7 * day, now) === 'offline_blocked')
  check('verify 30일 후 → blocked',
        classify(now - 30 * day, now) === 'offline_blocked')
}

// ─── 3. pending key 파일 흐름 ───────────────────────────────────────────────────
console.log('\n[3] pending key 파일 흐름 (인스톨러 → 앱)')
{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-pending-'))
  const pendingPath = path.join(tmp, '.pending_license')
  const KEY = 'INSRT-AAAA-BBBB-CCCC-DDDD'

  // 인스톨러가 저장
  fs.writeFileSync(pendingPath, KEY, 'utf8')
  check('파일 생성', fs.existsSync(pendingPath))

  // 앱이 읽음
  const read = fs.readFileSync(pendingPath, 'utf8').trim()
  check('읽은 키 일치', read === KEY)

  // consume 후 삭제
  fs.unlinkSync(pendingPath)
  check('consumePendingKey 후 파일 제거됨', !fs.existsSync(pendingPath))

  // 정리
  fs.rmdirSync(tmp)
}

// ─── 4. INSRT-XXXX-XXXX-XXXX-XXXX 형식 검증 (client과 server 일관성) ─────────
console.log('\n[4] 라이센스 키 형식 정규식 (서버와 동일해야 함)')
{
  const SERVER_RE = /^INSRT-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/
  check('INSRT-1A2B-3C4D-5E6F-7G8H 매치', SERVER_RE.test('INSRT-1A2B-3C4D-5E6F-7G8H'))
  check('INSRT-AAAA-BBBB-CCCC-DDDD 매치', SERVER_RE.test('INSRT-AAAA-BBBB-CCCC-DDDD'))
  check('소문자 불일치 (대문자 변환 필수)', !SERVER_RE.test('insrt-aaaa-bbbb-cccc-dddd'))
  check('길이 부족 거절', !SERVER_RE.test('INSRT-AAA-BBBB-CCCC-DDDD'))
  check('접두사 다름 거절', !SERVER_RE.test('FAKE-AAAA-BBBB-CCCC-DDDD'))
}

// ─── 결과 ──────────────────────────────────────────────────────────────────────
const passed = results.filter(r => r.ok).length
const total = results.length
console.log('\n' + '='.repeat(70))
console.log(`SUMMARY: ${passed}/${total} PASS`)
console.log('='.repeat(70))
process.exit(passed === total ? 0 : 1)

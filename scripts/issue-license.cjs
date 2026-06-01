// 라이센스 키 발급 헬퍼.
// 사용법:
//   node scripts/issue-license.cjs [days] [email] [notes]
//   node scripts/issue-license.cjs 30
//   node scripts/issue-license.cjs unlimited contact@ecarbon.kr
//
// 주의: issue_license RPC 의 p_duration 은 interval 타입.
// 숫자 30 을 보내면 30초로 해석되는 PostgreSQL 캐스팅 함정이 있어
// 항상 "X days" 형태의 문자열로 전달.

const https = require('https')

const SUPABASE_URL = 'https://mpgnblfaiheovmzhdudj.supabase.co'
const SERVICE_ROLE =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wZ25ibGZhaWhlb3ZtemhkdWRqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTMyNjcwNywiZXhwIjoyMDg0OTAyNzA3fQ.32aiQv1q20kWM_wFWfFXju81jcS4YCKsX24ubzLa-dI'

function http(method, urlStr, body, headers = {}) {
  const url = new URL(urlStr)
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const req = https.request({
      method, hostname: url.hostname, path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${SERVICE_ROLE}`, apikey: SERVICE_ROLE,
        Prefer: 'return=representation',
        ...headers,
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
    req.on('error', reject); if (data) req.write(data); req.end()
  })
}

const args = process.argv.slice(2)
const daysArg = args[0] || '30'
const email = args[1] || 'manual@inserty.test'
const notes = args[2] || `manual-issue-${new Date().toISOString().slice(0, 10)}`

// 'unlimited' 또는 'inf' → 2099 만료 (사실상 무제한)
// 숫자 N → "N days"
const isUnlimited = /^(unlimited|inf|infinite|max)$/i.test(daysArg)
const durationStr = isUnlimited ? null : `${parseInt(daysArg, 10)} days`

;(async () => {
  const payload = isUnlimited
    ? { p_email: email, p_notes: notes }  // p_duration 생략 → 함수 default (확인 후 수동 PATCH)
    : { p_email: email, p_duration: durationStr, p_notes: notes }

  const issued = await http('POST', `${SUPABASE_URL}/rest/v1/rpc/issue_license`, payload)
  if (issued.status !== 200 && issued.status !== 201) {
    console.error('발급 실패:', issued.status, issued.body)
    process.exit(1)
  }
  const key = Array.isArray(issued.body) ? issued.body[0].license_key : issued.body?.license_key
  if (!key) {
    console.error('응답에 license_key 없음:', issued.body)
    process.exit(1)
  }

  // 무제한이면 PATCH 로 expires_at 을 2099 로
  if (isUnlimited) {
    await http('PATCH',
      `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}`,
      { expires_at: '2099-12-31T23:59:59Z' })
  }

  // 결과 read-back
  const verify = await http('GET',
    `${SUPABASE_URL}/rest/v1/licenses?license_key=eq.${key}&select=license_key,email,status,issued_at,expires_at,notes`)
  const row = verify.body[0]
  const diffDays = ((new Date(row.expires_at) - new Date(row.issued_at)) / (1000 * 86400)).toFixed(1)

  console.log('━'.repeat(50))
  console.log('  라이센스 키 발급 완료')
  console.log('━'.repeat(50))
  console.log(`  키       : ${row.license_key}`)
  console.log(`  이메일   : ${row.email}`)
  console.log(`  상태     : ${row.status}`)
  console.log(`  발급일   : ${row.issued_at}`)
  console.log(`  만료일   : ${row.expires_at}`)
  console.log(`  유효기간 : ${isUnlimited ? '무제한' : diffDays + '일'}`)
  console.log(`  notes    : ${row.notes}`)
  console.log('━'.repeat(50))
})().catch((e) => { console.error('FATAL:', e); process.exit(2) })

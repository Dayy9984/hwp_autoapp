// 최종 통합 E2E — 이번 세션의 모든 구현/수정 사항.
const { execSync } = require('child_process')
const path = require('path')

const MIRROR = path.resolve(__dirname, '..')
const OSS = path.resolve(MIRROR, '..', 'InsertyAI')

const stages = [
  { name: '(1) Supabase 라이센스 백엔드 12종',
    cmd: `"${OSS}/python/.venv/Scripts/python.exe" "${OSS}/python/docs/e2e_test_license_scenarios.py"` },
  { name: '(2) 디바이스 정책 v2 백엔드 13종',
    cmd: `"${OSS}/python/.venv/Scripts/python.exe" "${OSS}/python/docs/e2e_test_device_policy.py"` },
  { name: '(3) 클라이언트 단위 (AES + grace + pending + regex) 14종',
    cmd: `node "${MIRROR}/scripts/test-license-client.cjs"` },
  { name: '(4) 빌드 산출물 정적 79종',
    cmd: `node "${MIRROR}/scripts/e2e-build-artifacts.cjs"` },
  { name: '(5) Playwright 라이센스 게이트 13종',
    cmd: `node "${MIRROR}/scripts/e2e-license-electron.cjs"` },
  { name: '(6) Playwright DeviceManager 8종',
    cmd: `node "${MIRROR}/scripts/e2e-device-manager.cjs"` },
  { name: '(7) Playwright 자동 업데이트 IPC 4종',
    cmd: `node "${MIRROR}/scripts/e2e-auto-update.cjs"` },
  { name: '(8) Playwright 자동 업데이트 다운로드 6종 (v0.1.9 → v0.1.10)',
    cmd: `node "${MIRROR}/scripts/e2e-update-download.cjs"` },
]

function sh(cmd) {
  try {
    execSync(cmd, { stdio: 'inherit', timeout: 12 * 60 * 1000 })
    return true
  } catch {
    return false
  }
}

const summary = []
for (const s of stages) {
  console.log('\n' + '═'.repeat(70))
  console.log('▶ ' + s.name)
  console.log('═'.repeat(70))
  const ok = sh(s.cmd)
  summary.push({ name: s.name, ok })
}

console.log('\n' + '═'.repeat(70))
console.log('전체 통합 E2E 결과')
console.log('═'.repeat(70))
for (const r of summary) console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}`)
const passed = summary.filter((r) => r.ok).length
console.log(`\n  ${passed}/${summary.length} 스테이지 통과`)
process.exit(summary.every((r) => r.ok) ? 0 : 1)

// 이번 세션에서 지시받은 모든 기능 통합 E2E.
// (1) 라이센스 백엔드 12/12  — python/.venv 로 호출 (다른 디렉터리)
// (2) 디바이스 정책 v2 13/13 — python/.venv
// (3) 클라이언트 단위 14/14
// (4) 빌드 산출물 정적 79/79
// (5) Playwright 라이센스 게이트 E2E
// (6) Playwright DeviceManager E2E
// (7) Playwright 자동 업데이트 E2E
// (8) Codex thinking-only fix 백엔드 (이전 세션) — 인스타 OSS 측 스크립트

const { execSync } = require('child_process')
const path = require('path')

const MIRROR = path.resolve(__dirname, '..')
const OSS = path.resolve(MIRROR, '..', 'InsertyAI')

function sh(cmd, opts = {}) {
  console.log('\n' + '═'.repeat(70))
  console.log('▶ ' + cmd)
  console.log('═'.repeat(70))
  try {
    execSync(cmd, { stdio: 'inherit', ...opts })
    return true
  } catch (e) {
    return false
  }
}

const stages = [
  {
    name: '(1) 라이센스 백엔드 E2E (Supabase 직접)',
    cmd: `"${OSS}/python/.venv/Scripts/python.exe" "${OSS}/python/docs/e2e_test_license_scenarios.py"`,
  },
  {
    name: '(2) 디바이스 정책 v2 백엔드 E2E',
    cmd: `"${OSS}/python/.venv/Scripts/python.exe" "${OSS}/python/docs/e2e_test_device_policy.py"`,
  },
  {
    name: '(3) 클라이언트 단위 (AES + grace + pending + regex)',
    cmd: `node "${MIRROR}/scripts/test-license-client.cjs"`,
  },
  {
    name: '(4) 빌드 산출물 정적 E2E (main/preload/renderer/nsis/builder/supabase/env/github)',
    cmd: `node "${MIRROR}/scripts/e2e-build-artifacts.cjs"`,
  },
  {
    name: '(5) Playwright Electron — 라이센스 게이트 흐름',
    cmd: `node "${MIRROR}/scripts/e2e-license-electron.cjs"`,
  },
  {
    name: '(6) Playwright Electron — DeviceManager',
    cmd: `node "${MIRROR}/scripts/e2e-device-manager.cjs"`,
  },
  {
    name: '(7) Playwright Electron — 자동 업데이트',
    cmd: `node "${MIRROR}/scripts/e2e-auto-update.cjs"`,
  },
]

const summary = []
for (const s of stages) {
  const ok = sh(s.cmd)
  summary.push({ name: s.name, ok })
}

console.log('\n' + '═'.repeat(70))
console.log('통합 E2E 요약')
console.log('═'.repeat(70))
for (const r of summary) {
  console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}`)
}
const passed = summary.filter((r) => r.ok).length
console.log(`\n  ${passed}/${summary.length} 스테이지 통과`)
process.exit(summary.every((r) => r.ok) ? 0 : 1)

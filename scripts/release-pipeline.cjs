// Python 빌드 완료 후 자동 실행될 릴리즈 파이프라인:
//   1) Python dist 검증 (4개 entry exe 존재 + 크기)
//   2) Python dist 를 미러 레포 python/dist 로 복사
//   3) Electron 전체 빌드 (vite + electron-builder NSIS) — PAT inject
//   4) Playwright E2E 3종 실행
//   5) 결과 보고 (Release 발행은 별도 명령으로)
//
// 사용:
//   unset ELECTRON_RUN_AS_NODE
//   export INSERTYAI_SUPABASE_ANON_KEY=...
//   export INSERTYAI_UPDATE_TOKEN=...
//   node scripts/release-pipeline.cjs

const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')

const MIRROR_ROOT = path.resolve(__dirname, '..')
const OSS_ROOT = path.resolve(MIRROR_ROOT, '..', 'InsertyAI')
const PY_SRC_DIST = path.join(OSS_ROOT, 'python', 'dist')
const PY_DST_DIST = path.join(MIRROR_ROOT, 'python', 'dist')

const REQ_ENTRIES = ['inserty_python', 'inserty_agent', 'hwp_window_monitor', 'inserty_file_reader']

function sh(cmd, opts = {}) {
  console.log(`  $ ${cmd}`)
  return execSync(cmd, { stdio: 'inherit', cwd: MIRROR_ROOT, ...opts })
}

function step(n, name) {
  console.log('\n' + '='.repeat(70))
  console.log(`[${n}] ${name}`)
  console.log('='.repeat(70))
}

// ─── 1) Python dist 검증 ──────────────────────────────────────────────────────
step(1, 'Python dist 검증')
let invalid = false
for (const e of REQ_ENTRIES) {
  const exe = path.join(PY_SRC_DIST, e, `${e}.exe`)
  if (!fs.existsSync(exe)) { console.log(`  ✗ MISSING: ${exe}`); invalid = true; continue }
  const sz = fs.statSync(exe).size
  if (sz === 0) { console.log(`  ✗ EMPTY: ${exe}`); invalid = true; continue }
  console.log(`  ✓ ${e}.exe (${(sz / 1024 / 1024).toFixed(1)}MB)`)
}
if (invalid) {
  console.error('\nPython 빌드 산출물에 문제가 있어 파이프라인을 중단합니다.')
  process.exit(1)
}

// ─── 2) Python dist 복사 ──────────────────────────────────────────────────────
step(2, 'Python dist → 미러 레포 복사')
fs.mkdirSync(PY_DST_DIST, { recursive: true })
for (const e of REQ_ENTRIES) {
  const src = path.join(PY_SRC_DIST, e)
  const dst = path.join(PY_DST_DIST, e)
  if (fs.existsSync(dst)) {
    console.log(`  remove existing: ${dst}`)
    fs.rmSync(dst, { recursive: true, force: true })
  }
  console.log(`  copy ${e}/`)
  sh(`xcopy "${src}" "${dst}" /E /I /Q /Y >NUL`, { stdio: ['ignore', 'ignore', 'inherit'] })
}

// ─── 3) Electron 빌드 + NSIS ──────────────────────────────────────────────────
step(3, 'Electron 빌드 + NSIS')
if (!process.env.INSERTYAI_UPDATE_TOKEN) {
  console.error('  ✗ INSERTYAI_UPDATE_TOKEN env 가 설정되지 않았습니다. .env 또는 export 필요.')
  process.exit(2)
}
if (!process.env.INSERTYAI_SUPABASE_ANON_KEY) {
  console.error('  ✗ INSERTYAI_SUPABASE_ANON_KEY env 가 설정되지 않았습니다.')
  process.exit(2)
}
// 깨끗한 release 디렉터리에서 재빌드
const RELEASE_DIR = path.join(MIRROR_ROOT, 'release', '0.1.9')
if (fs.existsSync(RELEASE_DIR)) {
  console.log('  remove existing release/0.1.9')
  fs.rmSync(RELEASE_DIR, { recursive: true, force: true })
}
sh('pnpm vite build')
sh('npx electron-builder --win nsis --x64')

const SETUP = path.join(RELEASE_DIR, 'Inserty AI_0.1.9_Setup.exe')
const LATEST = path.join(RELEASE_DIR, 'latest.yml')
if (!fs.existsSync(SETUP) || !fs.existsSync(LATEST)) {
  console.error(`  ✗ NSIS 산출물 누락: ${SETUP}, ${LATEST}`)
  process.exit(3)
}
console.log(`  ✓ NSIS: ${(fs.statSync(SETUP).size / 1024 / 1024).toFixed(1)}MB`)
console.log(`  ✓ latest.yml present`)

// ─── 4) Playwright Electron E2E ───────────────────────────────────────────────
step(4, 'Playwright Electron E2E 3종')
const scripts = [
  'scripts/e2e-license-electron.cjs',
  'scripts/e2e-device-manager.cjs',
  'scripts/e2e-auto-update.cjs',
]
let failedAny = false
for (const s of scripts) {
  console.log(`\n  ▶ ${s}`)
  try {
    sh(`node "${s}"`)
  } catch (e) {
    console.error(`  ✗ ${s} FAILED`)
    failedAny = true
  }
}

step(5, '결과 요약')
console.log(`Python dist 복사: OK`)
console.log(`Electron NSIS 빌드: OK`)
console.log(`Playwright E2E: ${failedAny ? '일부 실패' : '전체 PASS'}`)
console.log('')
console.log(`산출물: ${SETUP}`)
console.log('')
console.log('다음 단계 (수동):')
console.log('  gh release create v0.1.9 \\')
console.log(`    "${SETUP}" "${LATEST}" \\`)
console.log('    --repo OpenScoutAI/insertyai \\')
console.log('    --title "v0.1.9 (commercial)" \\')
console.log('    --notes "First commercial release"')

process.exit(failedAny ? 4 : 0)

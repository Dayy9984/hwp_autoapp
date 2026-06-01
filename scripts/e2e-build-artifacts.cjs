// 빌드 산출물 정적 검증 E2E.
// 이번 세션에서 수정/추가한 모든 기능이 컴파일된 main/preload/renderer 번들에 실제로
// 반영되었는지 확인 (Python Nuitka 빌드 의존성 없음).

const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')

const ROOT = path.resolve(__dirname, '..')
const MAIN = path.join(ROOT, 'dist-electron', 'main', 'index.mjs')
const PRELOAD = path.join(ROOT, 'dist-electron', 'preload', 'index.js')
const RENDERER_DIR = path.join(ROOT, 'dist', 'assets')
const INSTALLER_NSH = path.join(ROOT, 'build', 'installer.nsh')
const EB_JSON = path.join(ROOT, 'electron-builder.json')

function loadFile(p) {
  if (!fs.existsSync(p)) throw new Error(`missing: ${p}`)
  return fs.readFileSync(p, 'utf8')
}

const results = []
function check(group, name, ok, detail = '') {
  results.push({ group, name, ok, detail })
  const tag = ok ? 'PASS' : 'FAIL'
  console.log(`  [${tag}] ${group} :: ${name}${detail ? ' — ' + detail : ''}`)
}

function findRendererBundle() {
  if (!fs.existsSync(RENDERER_DIR)) throw new Error('dist/assets 없음')
  const files = fs.readdirSync(RENDERER_DIR).filter((f) => f.endsWith('.js'))
  // 메인 chunk 가 가장 크므로 그것을 선택
  let largest = null
  let largestSize = 0
  for (const f of files) {
    const full = path.join(RENDERER_DIR, f)
    const sz = fs.statSync(full).size
    if (sz > largestSize) {
      largestSize = sz
      largest = full
    }
  }
  return largest
}

// =================================================================
// A. Main bundle 정적 검증 (electron/main/index.ts 변경사항)
// =================================================================
console.log('\n[A] electron/main bundle 검증')
const main = loadFile(MAIN)

// A1. auto-update GitHub private + token 흐름
check('main', 'auto-update: provider=github',
  /provider:\s*['"]github['"]|provider:['"]github['"]/.test(main),
  'GitHub provider 설정 inject 됨')
check('main', 'auto-update: OpenScoutAI/insertyai owner+repo',
  main.includes('OpenScoutAI') && main.includes('insertyai'),
  'release feed target 정확')
check('main', 'auto-update: private flag true',
  /private:\s*!0|private:\s*true/.test(main))
check('main', 'auto-update: INSERTYAI_UPDATE_TOKEN PAT inject',
  main.includes('github_pat_'),
  'fine-grained PAT 가 빌드에 inject 됨')
check('main', 'auto-update: 이전 UPDATE_FEED_URL 의존성 제거',
  !main.includes('UPDATE_FEED_URL not set, auto-update disabled'),
  '이전 disabled 경고문 사라짐')
check('main', 'auto-update: lastSignificantStatus error 도 캐싱',
  /lastSignificantStatus/.test(main) && /status\s*!==\s*['"]checking['"]/.test(main),
  '확장된 캐싱 정책 반영')
check('main', 'auto-update: setMainWindow 시 마지막 상태 push',
  /pushToWindow\(this\.lastSignificantStatus/.test(main) || /setMainWindow[\s\S]{0,300}lastSignificantStatus[\s\S]{0,200}send/.test(main),
  'window 재연결 시 복구 push')

// A2. License IPC 핸들러 — listDevices / removeDevice / openExternal / getInitialStatus
check('main', 'IPC: license:listDevices',
  main.includes('license:listDevices'))
check('main', 'IPC: license:removeDevice',
  main.includes('license:removeDevice'))
check('main', 'IPC: license:openExternal (KakaoSupportLink 용)',
  main.includes('license:openExternal'))
check('main', 'IPC: license:getInitialStatus',
  main.includes('license:getInitialStatus'))

// A3. LicenseBridge — Supabase URL, AES-256-GCM 캐시 상수, 디바이스 API endpoint
check('main', 'license-bridge: Supabase URL',
  main.includes('mpgnblfaiheovmzhdudj.supabase.co'))
check('main', 'license-bridge: AES-256-GCM 캐시 키 derivation',
  main.includes('insertyai-license-store-v1'))
check('main', 'license-bridge: devices-list endpoint',
  main.includes('/functions/v1/devices-list'))
check('main', 'license-bridge: devices-remove endpoint',
  main.includes('/functions/v1/devices-remove'))
check('main', 'license-bridge: cannot_remove_self 클라이언트 가드',
  main.includes('cannot_remove_self'),
  '자기 자신 삭제 클라이언트 측 차단도 컴파일됨')

// A4. mapReason 에 device_limit_reached 분기
check('main', 'mapReason: device_limit_reached',
  main.includes('device_limit_reached'))

// A5. initialLicenseCheck 는 BrowserWindow 생성 전 await
// 핸들러 등록 후 try { await xxx() } 패턴 (initialLicenseCheck 가 BrowserWindow 생성 전 await).
check('main', 'initialLicenseCheck await (창 생성 전 라이센스 결정)',
  /license:getInitialStatus[\s\S]{0,300}try\s*\{\s*await\s+[A-Za-z_$][\w$]*\(\)/.test(main))

// A6. node-machine-id 의존성 자체 제거 + Windows MachineGuid 를 reg.exe 로 inline 읽기
//     ("Cannot find module 'node-machine-id'" 다이얼로그를 근본적으로 차단)
check('main', 'license-device-id: Windows MachineGuid 직접 추출 (의존성 0)',
  main.includes('MachineGuid') && main.includes('reg.exe') &&
  !main.includes('require("node-machine-id")') &&
  !main.includes("require('node-machine-id')"),
  'node-machine-id 패키지 import 없이 reg query 로 직접 추출')

// =================================================================
// B. Preload bundle 검증
// =================================================================
console.log('\n[B] electron/preload bundle 검증')
const preload = loadFile(PRELOAD)

check('preload', 'license.activate', preload.includes('license:activate'))
check('preload', 'license.verify', preload.includes('license:verify'))
check('preload', 'license.getInitialStatus', preload.includes('license:getInitialStatus'))
check('preload', 'license.getCachedKey', preload.includes('license:getCachedKey'))
check('preload', 'license.tryPendingKey', preload.includes('license:tryPendingKey'))
check('preload', 'license.openExternal', preload.includes('license:openExternal'))
check('preload', 'license.listDevices', preload.includes('license:listDevices'))
check('preload', 'license.removeDevice', preload.includes('license:removeDevice'))
check('preload', 'update.check', preload.includes('update:check'))
check('preload', 'update.download', preload.includes('update:download'))
check('preload', 'update.install', preload.includes('update:install'))
check('preload', 'update.getCurrentVersion', preload.includes('update:getCurrentVersion'))
check('preload', 'update.getLastStatus', preload.includes('update:getLastStatus'))
check('preload', 'auto-update:status 이벤트 구독', preload.includes('auto-update:status'))

// =================================================================
// C. Renderer bundle 검증 (UI 컴포넌트가 컴파일됐는지)
// =================================================================
console.log('\n[C] renderer (React) bundle 검증')
const rendererPath = findRendererBundle()
const renderer = loadFile(rendererPath)
console.log(`  bundle: ${path.basename(rendererPath)} (${(renderer.length / 1024).toFixed(0)}KB)`)

// C1. LicenseGate UI 텍스트
check('renderer', 'LicenseGate: 라이센스 키 입력 화면 텍스트',
  renderer.includes('라이센스 키 입력'))
check('renderer', 'LicenseGate: INSRT 형식 placeholder',
  renderer.includes('INSRT-XXXX-XXXX-XXXX-XXXX'))
check('renderer', 'LicenseGate: 만료 화면',
  renderer.includes('라이센스가 만료'))
check('renderer', 'LicenseGate: 무효화 화면',
  renderer.includes('라이센스가 무효화'))
check('renderer', 'LicenseGate: 오프라인 7일 차단 화면',
  renderer.includes('7일 이상 오프라인'))

// C2. DeviceManagerScreen (이번 세션 신규)
check('renderer', 'DeviceManagerScreen: 헤더 문구',
  renderer.includes('기기 슬롯이 가득'))
check('renderer', 'DeviceManagerScreen: 현재 기기 라벨',
  renderer.includes('현재 기기'))
check('renderer', 'DeviceManagerScreen: 마지막 사용 라벨',
  renderer.includes('마지막 사용'))
check('renderer', 'DeviceManagerScreen: 다시 시도 버튼',
  renderer.includes('다시 시도'))
check('renderer', 'DeviceManagerScreen: 새로고침 버튼',
  renderer.includes('새로고침'))
check('renderer', 'reasonToMessage: cannot_remove_self 메시지',
  renderer.includes('현재 사용 중인 기기는 삭제할 수 없습니다'))

// C3. KakaoSupportLink (카카오 브랜드 컬러 + URL)
check('renderer', 'KakaoSupportLink: 오픈채팅 URL',
  renderer.includes('https://open.kakao.com/o/sSm9ZXei'))
check('renderer', 'KakaoSupportLink: 카카오 브랜드 컬러 #FEE500',
  renderer.includes('#FEE500'))

// C4. App.tsx mapLicenseStatus device_limit_reached 처리
check('renderer', 'App: mapLicenseStatus device_limit_reached 분기',
  renderer.includes('device_limit_reached'))

// =================================================================
// D. NSIS installer.nsh 정적 검증 (oneClick 정책)
// =================================================================
console.log('\n[D] build/installer.nsh 검증 (oneClick)')
const nsh = loadFile(INSTALLER_NSH)

check('nsis', 'customInit: silent 모드는 splash 표시 안 함',
  /customInit[\s\S]{0,200}\$\{If\}\s*\$\{Silent\}/.test(nsh))
check('nsis', 'customInit: NSIS 표준 splash plugin 으로 브랜드 BMP 표시',
  /splash::show\s+\d+\s+\$PLUGINSDIR\\installer-splash/.test(nsh))
check('nsis', 'customInit: InitPluginsDir + BMP 파일 plugin 디렉터리에 복사',
  /InitPluginsDir[\s\S]{0,200}File[\s\S]{0,200}installer-splash\.bmp/.test(nsh))
check('nsis', 'customUnInstall: 라이센스 파일 모두 제거',
  /customUnInstall[\s\S]{0,300}\.pending_license[\s\S]{0,100}\.license_cache/.test(nsh))
check('nsis', '키 입력 페이지 제거됨 (oneClick 모드)',
  !/Page\s+custom\s+LicenseKey/i.test(nsh) && !/INSRT-/i.test(nsh))

// =================================================================
// E. electron-builder.json publish 설정
// =================================================================
console.log('\n[E] electron-builder.json publish 설정')
const eb = JSON.parse(loadFile(EB_JSON))

check('builder', 'publish.provider == github', eb.publish?.provider === 'github')
check('builder', 'publish.owner == OpenScoutAI', eb.publish?.owner === 'OpenScoutAI')
check('builder', 'publish.repo == insertyai', eb.publish?.repo === 'insertyai')
check('builder', 'publish.private == true', eb.publish?.private === true)
check('builder', 'nsis.include == build/installer.nsh', eb.nsis?.include === 'build/installer.nsh')
check('builder', 'nsis.oneClick == true (즉시 설치 + 자동 실행)', eb.nsis?.oneClick === true)
check('builder', 'nsis.perMachine == false (UAC 동의창 회피, per-user 설치)',
  eb.nsis?.perMachine === false)
check('builder', 'nsis.runAfterFinish == true (설치 후 앱 자동 실행)',
  eb.nsis?.runAfterFinish === true)
check('builder', 'nsis.installerSidebar BMP 지정 (브랜드 자산)',
  typeof eb.nsis?.installerSidebar === 'string' && eb.nsis.installerSidebar.endsWith('.bmp'))
check('builder', 'nsis.installerHeader BMP 지정 (브랜드 자산)',
  typeof eb.nsis?.installerHeader === 'string' && eb.nsis.installerHeader.endsWith('.bmp'))
check('builder', 'extraResources: 4 Python entry 포함', (eb.extraResources || []).length === 4)

// 브랜드 BMP 자산 존재 검증
const SPLASH_BMP = path.join(ROOT, 'build', 'installer-splash.bmp')
const SIDEBAR_BMP = path.join(ROOT, 'build', 'installer-sidebar.bmp')
const HEADER_BMP = path.join(ROOT, 'build', 'installer-header.bmp')
check('builder', 'installer-splash.bmp 자산 존재', fs.existsSync(SPLASH_BMP),
  fs.existsSync(SPLASH_BMP) ? `${(fs.statSync(SPLASH_BMP).size / 1024).toFixed(0)}KB` : '')
check('builder', 'installer-sidebar.bmp 자산 존재', fs.existsSync(SIDEBAR_BMP),
  fs.existsSync(SIDEBAR_BMP) ? `${(fs.statSync(SIDEBAR_BMP).size / 1024).toFixed(0)}KB` : '')
check('builder', 'installer-header.bmp 자산 존재', fs.existsSync(HEADER_BMP),
  fs.existsSync(HEADER_BMP) ? `${(fs.statSync(HEADER_BMP).size / 1024).toFixed(0)}KB` : '')

// =================================================================
// F. Supabase 마이그레이션 + Edge Function source 검증 (source 단계)
// =================================================================
console.log('\n[F] Supabase source 검증')
const SUPA_BASE = path.resolve(ROOT, '..', 'InsertyAI', 'python', 'docs', 'supabase', 'supabase')
const mig002 = path.join(SUPA_BASE, 'migrations', '002_device_policy_v2.sql')
const actFn = path.join(SUPA_BASE, 'functions', 'activate', 'index.ts')
const listFn = path.join(SUPA_BASE, 'functions', 'devices-list', 'index.ts')
const removeFn = path.join(SUPA_BASE, 'functions', 'devices-remove', 'index.ts')

if (fs.existsSync(mig002)) {
  const sql = loadFile(mig002)
  check('supabase', 'migration 002: leaked → active 복구',
    /UPDATE\s+licenses[\s\S]{0,100}SET\s+status\s*=\s*'active'[\s\S]{0,80}WHERE\s+status\s*=\s*'leaked'/i.test(sql))
  check('supabase', 'migration 002: leaked 자동 전환 트리거 로직 제거',
    !/status\s*=\s*'leaked'[\s\S]{0,200}leaked_at\s*=\s*COALESCE/.test(sql) ||
    /leaked 전환 로직 제거/i.test(sql))
}

if (fs.existsSync(actFn)) {
  const t = loadFile(actFn)
  check('supabase', 'activate: MAX_DEVICES = 2 상수',
    /MAX_DEVICES\s*=\s*2/.test(t))
  check('supabase', 'activate: device_limit_reached 응답 분기',
    t.includes('device_limit_reached'))
  check('supabase', 'activate: 기존 디바이스 UPSERT 시 슬롯 카운트 미증가',
    /existingDevice/.test(t))
}

if (fs.existsSync(listFn)) {
  const t = loadFile(listFn)
  check('supabase', 'devices-list: JWT 검증',
    t.includes('jwtVerify'))
  check('supabase', 'devices-list: device_id 미스매치 차단',
    t.includes('device_mismatch'))
  check('supabase', 'devices-list: first_seen_at order',
    t.includes('first_seen_at'))
}

if (fs.existsSync(removeFn)) {
  const t = loadFile(removeFn)
  check('supabase', 'devices-remove: 자기 자신 삭제 거부 (cannot_remove_self)',
    t.includes('cannot_remove_self'))
  check('supabase', 'devices-remove: JWT device_id 일치 검증',
    t.includes('token_device_id') && t.includes('device_mismatch'))
  check('supabase', 'devices-remove: 다른 라이센스 침범 차단',
    t.includes('target_not_found'))
}

// =================================================================
// G. PAT 가이드 + 환경 변수
// =================================================================
console.log('\n[G] 환경 변수 / 가이드')
const ENV_FILE = path.join(ROOT, '.env')
if (fs.existsSync(ENV_FILE)) {
  const envText = loadFile(ENV_FILE)
  check('env', '.env: INSERTYAI_SUPABASE_ANON_KEY 정의',
    /INSERTYAI_SUPABASE_ANON_KEY=eyJ/.test(envText))
  check('env', '.env: INSERTYAI_UPDATE_TOKEN 정의 (github_pat_)',
    /INSERTYAI_UPDATE_TOKEN=github_pat_/.test(envText))
}
const guide = path.resolve(ROOT, '..', 'InsertyAI', 'python', 'docs', 'license_client', 'PAT_SETUP.md')
check('env', 'PAT 발급 가이드 문서 존재', fs.existsSync(guide))

// =================================================================
// H. GitHub API 라이브 검증 — PAT 가 실제로 OpenScoutAI/insertyai 에 접근 가능한지
// =================================================================
console.log('\n[H] GitHub API 라이브 검증 (PAT 권한)')
const token = (loadFile(ENV_FILE).match(/INSERTYAI_UPDATE_TOKEN=(\S+)/) || [])[1]
function ghProbe(pathPart) {
  return new Promise((resolve) => {
    const https = require('https')
    const req = https.request({
      method: 'GET',
      hostname: 'api.github.com',
      path: pathPart,
      headers: {
        Authorization: `Bearer ${token}`,
        'User-Agent': 'insertyai-e2e',
        Accept: 'application/vnd.github+json',
      },
    }, (res) => { resolve(res.statusCode); res.resume() })
    req.on('error', () => resolve(null))
    req.end()
  })
}
async function runGhChecks() {
  if (!token) return
  const repoCode = await ghProbe('/repos/OpenScoutAI/insertyai')
  check('github', 'repo metadata 200 (PAT 권한 OK)', repoCode === 200, `HTTP ${repoCode}`)
  const relCode = await ghProbe('/repos/OpenScoutAI/insertyai/releases')
  check('github', 'releases endpoint 200', relCode === 200, `HTTP ${relCode}`)
}

// =================================================================
// 최종 요약 (GitHub probe 비동기 완료 후)
// =================================================================
runGhChecks().then(() => {
  const passed = results.filter((r) => r.ok).length
  const total = results.length
  console.log('\n' + '='.repeat(70))
  console.log(`STATIC E2E SUMMARY: ${passed}/${total} PASS`)
  console.log('='.repeat(70))
  const failed = results.filter((r) => !r.ok)
  if (failed.length) {
    console.log('\n실패 항목:')
    for (const f of failed) {
      console.log(`  ✗ ${f.group} :: ${f.name}${f.detail ? ' — ' + f.detail : ''}`)
    }
  }
  process.exit(failed.length ? 1 : 0)
})

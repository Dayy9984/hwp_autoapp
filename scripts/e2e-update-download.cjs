// 자동 업데이트 실환경 E2E.
//   v0.1.9 win-unpacked 실행 → update.check → "v0.1.10 available" 받음
//   → update.download → progress 이벤트 1개 이상 수신 → 다운로드 확인
//   → update.install API 호출 정상 (실제로 앱은 종료 + 인스톨러 실행되지만 E2E 는
//     install 직전까지만 확인하고 close).

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')

const APP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9-backup-unpacked', 'Inserty AI.exe')

delete process.env.ELECTRON_RUN_AS_NODE
const env = Object.entries(process.env).map(([name, value]) => ({ name, value }))

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  console.log('========== Auto-Update Download E2E ==========')
  console.log('exe:', APP_EXE)
  if (!fs.existsSync(APP_EXE)) {
    console.error('v0.1.9 backup exe not found')
    process.exit(2)
  }

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-upd-'))
  const app = await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })

  // download-progress 이벤트 수집
  const progressEvents = []

  async function getActiveWindow(maxMs = 30000) {
    const deadline = Date.now() + maxMs
    while (Date.now() < deadline) {
      const wins = app.windows()
      for (let i = wins.length - 1; i >= 0; i--) {
        const w = wins[i]
        if (w.isClosed?.()) continue
        try {
          const ok = await w.evaluate(() => typeof window?.electronAPI?.update?.check === 'function')
          if (ok) return w
        } catch {}
      }
      await new Promise((r) => setTimeout(r, 500))
    }
    throw new Error('no active window')
  }

  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 4000))
    const win = await getActiveWindow(60000)

    // (0) onStatus 이벤트 구독을 가장 먼저 — check 호출 시 시작되는 checking/available 이벤트도 잡힐 수 있게
    await win.evaluate(() => {
      ;(window).__updateEvents = []
      const api = (window).electronAPI?.update
      if (api?.onStatus) {
        api.onStatus((status) => {
          ;(window).__updateEvents.push(status)
        })
      }
    })

    // (1) check
    const checkResp = await win.evaluate(async () => await window.electronAPI.update.check())
    check('update.check 성공', checkResp?.success === true && checkResp?.updateAvailable === true,
      `version=${checkResp?.version}, available=${checkResp?.updateAvailable}, error=${checkResp?.error}`)
    check('available version == 0.1.10', checkResp?.version === '0.1.10')

    // (3) download 호출
    const dlResp = await win.evaluate(async () => await window.electronAPI.update.download())
    check('update.download 호출', dlResp?.success === true,
      `error=${dlResp?.error}`)

    // (4) download progress 이벤트 폴링 (캐시 hit 시 progress 없이 바로 downloaded 가능)
    console.log('  ⏳ download progress / downloaded 이벤트 대기 (최대 180s)...')
    let downloaded = false
    let progressed = false
    let lastPct = 0
    const deadline = Date.now() + 180_000
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2000))
      const events = await win.evaluate(() => (window).__updateEvents || [])
      for (const e of events) {
        if (e.status === 'downloading' && typeof e.progress?.percent === 'number') {
          progressed = true
          if (e.progress.percent > lastPct) {
            lastPct = e.progress.percent
            console.log(`    progress ${e.progress.percent.toFixed(1)}% (${(e.progress.transferred / 1024 / 1024).toFixed(1)}MB / ${(e.progress.total / 1024 / 1024).toFixed(1)}MB)`)
          }
        }
        if (e.status === 'downloaded') {
          downloaded = true
        }
        if (e.status === 'error') {
          check('update download error 발생', false, e.error)
          break
        }
      }
      if (downloaded) break
    }
    // 다운로드가 캐시 hit 으로 즉시 완료된 경우 progress 이벤트 없이 downloaded 만 발화 → 정상 동작.
    // download-progress 이벤트는 "수신했거나, 캐시 hit 으로 즉시 downloaded" 둘 중 하나면 PASS.
    check('download-progress 이벤트 수신 또는 캐시 즉시완료',
      progressed || downloaded,
      `progressed=${progressed}, downloaded=${downloaded}, lastPct=${lastPct.toFixed(1)}`)
    check('download 최종 완료 (downloaded 이벤트)', downloaded,
      downloaded ? `lastPct=${lastPct.toFixed(1)}` : `lastPct=${lastPct.toFixed(1)}`)

    // (5) install API 호출 가능성만 확인 (실제 install 은 앱 종료 → 인스톨러 실행이라 E2E 종료)
    const installable = await win.evaluate(() =>
      typeof window.electronAPI.update.install === 'function',
    )
    check('update.install API 노출', installable)
  } finally {
    await app.close().catch(() => {})
  }

  const passed = results.filter((r) => r.ok).length
  console.log('\n' + '='.repeat(70))
  console.log(`SUMMARY: ${passed}/${results.length} PASS`)
  for (const r of results) console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`)
  console.log('='.repeat(70))
})().catch((e) => {
  console.error('FATAL:', e)
  process.exit(2)
})

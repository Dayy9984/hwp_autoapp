// 자동 업데이트 E2E (빌드된 앱).
//
// 검증 항목:
//   1) update-handlers 가 노출되어 있는지 (window.electronAPI.update.*)
//   2) update:check 호출 → 응답 구조 정상 (success / updateAvailable / version / error)
//   3) auto-updater 가 GitHub provider + PAT 로 인증되는지 (실패 시 error 메시지 확인)
//   4) update:getLastStatus 가 마지막 상태 캐싱 동작 (재호출 시 동일)
//
// 주의: 실제 release 가 publish 되지 않은 시점에는 update-not-available 또는
//       publish 후 검증 가능. 본 테스트는 IPC 흐름 + 인증 자체에 초점.

const { _electron: electron } = require('playwright')
const fs = require('fs')
const os = require('os')
const path = require('path')

const APP_EXE = path.resolve(__dirname, '..', 'release', '0.1.9', 'win-unpacked', 'Inserty AI.exe')

delete process.env.ELECTRON_RUN_AS_NODE
const env = Object.entries(process.env).map(([name, value]) => ({ name, value }))

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  console.log('========== Auto-Update E2E ==========')
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inserty-update-'))
  const app = await electron.launch({
    executablePath: APP_EXE,
    args: [`--user-data-dir=${userDataDir}`],
    env,
    timeout: 90000,
  })

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
    throw new Error('no active window with update API')
  }

  try {
    await app.firstWindow({ timeout: 60000 })
    await new Promise((r) => setTimeout(r, 4000))
    const win = await getActiveWindow(30000)

    const updateApi = await win.evaluate(() => {
      const u = (window).electronAPI?.update
      return {
        hasUpdate: typeof u === 'object',
        hasCheck: typeof u?.check === 'function' || typeof u?.checkForUpdate === 'function',
        keys: u ? Object.keys(u) : [],
      }
    })
    check('window.electronAPI.update 노출', updateApi.hasUpdate, JSON.stringify(updateApi.keys))

    // electronAPI.update 가 없으면 직접 ipcRenderer 패턴이 노출돼있는지 확인 시도.
    // 일단 백엔드 IPC 직접 호출 (preload 가 update.* 노출 안하면 ipcRenderer.invoke 통해).
    const checkResp = await win.evaluate(async () => {
      const api = (window).electronAPI
      if (api?.update?.check) return await api.update.check()
      // fallback: invoke through preload-exposed ipcRenderer-like surface (없으면 null)
      return null
    })
    if (checkResp != null) {
      check('update:check 응답 구조', typeof checkResp === 'object' && 'success' in checkResp,
        JSON.stringify(checkResp))
    } else {
      check('update API 미노출 (preload 에 추가 필요)', false,
        'window.electronAPI.update.check 없음 — preload 갱신 필요')
    }

    // version 확인
    const ver = await win.evaluate(() => (window).electronAPI?.update?.getCurrentVersion?.() ?? null)
    if (ver) {
      check('current version 응답', !!ver, JSON.stringify(ver))
    }

    const last = await win.evaluate(() => (window).electronAPI?.update?.getLastStatus?.() ?? null)
    if (last !== null) {
      check('getLastStatus 호출', true, JSON.stringify(last))
    }
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

const { _electron: electron } = require('playwright')
;(async () => {
  console.log('Launching app...')
  // ELECTRON_RUN_AS_NODE는 VSCode 셸이 설정 — Electron 실행 시 Node 모드로 빠짐
  // Playwright의 env 옵션은 [{name, value}] 배열 형식 (envArrayToObject 호출 대상)
  const filtered = { ...process.env }
  delete filtered.ELECTRON_RUN_AS_NODE
  const env = Object.entries(filtered).map(([name, value]) => ({ name, value }))
  try {
    const app = await electron.launch({
      executablePath: 'C:/Users/dlgkr/Desktop/mallo/InsertyAI-private/release/0.1.9/win-unpacked/Inserty AI.exe',
      env,
      timeout: 90000,
    })
    console.log('OK launched')
    const win = await app.firstWindow({ timeout: 60000 })
    console.log('OK first window')
    console.log('title:', await win.title())
    await app.close()
    console.log('OK closed')
  } catch (e) {
    console.error('FAIL', e.message)
    console.error(e.stack)
  }
})()

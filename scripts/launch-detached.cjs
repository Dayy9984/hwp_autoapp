const { spawn } = require('child_process')
const path = require('path')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env, ELECTRON_ENABLE_LOGGING: '1' }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'detach-' + Date.now())
console.log('userDataDir:', userDataDir)
const child = spawn(exe, [`--user-data-dir=${userDataDir}`], {
  env,
  detached: true,
  stdio: 'ignore',
})
child.unref()
console.log('detached pid:', child.pid)
console.log('app started, leaving it alive 30s')
setTimeout(()=>{
  try {
    require('child_process').execSync(`taskkill /pid ${child.pid} /f /t`)
    console.log('killed')
  } catch (e) { console.log('kill err', e.message) }
}, 30000)

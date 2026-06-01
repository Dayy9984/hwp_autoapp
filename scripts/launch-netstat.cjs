const { spawn, execSync } = require('child_process')
const path = require('path')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env, ELECTRON_ENABLE_LOGGING: '1' }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'netstat-' + Date.now())
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9911'], { env })
const pid = child.pid
console.log('pid:', pid)
;(async()=>{
  await new Promise(r=>setTimeout(r,8000))
  try {
    const out = execSync(`netstat -ano | findstr ${pid}`, { encoding: 'utf8' })
    console.log('NETSTAT:'); console.log(out)
  } catch (e) {
    console.log('netstat failed or no ports')
  }
  try {
    const out = execSync(`wmic process where "ParentProcessId=${pid}" get ProcessId,Name`, { encoding: 'utf8' })
    console.log('CHILDREN:'); console.log(out)
  } catch (e) {}
  child.kill('SIGKILL')
})()

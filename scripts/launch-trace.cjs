const { spawn } = require('child_process')
const path = require('path')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env, ELECTRON_ENABLE_LOGGING: '1' }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'trace-' + Date.now())
console.log('userDataDir:', userDataDir)
const child = spawn(exe, [`--user-data-dir=${userDataDir}`], { env })
console.log('pid:', child.pid)
child.stdout.on('data', b => console.log('OUT:', b.toString()))
child.stderr.on('data', b => console.log('ERR:', b.toString()))
child.on('spawn', () => console.log('spawn fired'))
child.on('error', e => console.log('error:', e.message))
child.on('close', (c, s) => console.log('close', { c, s }))
child.on('exit', (c, s) => console.log('exit', { c, s }))
setTimeout(()=>{
  console.log('sigkill')
  try{ child.kill('SIGKILL') }catch{}
}, 8000)

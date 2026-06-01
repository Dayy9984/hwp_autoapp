const { spawn } = require('child_process')
const path = require('path')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env, ELECTRON_ENABLE_LOGGING: '1', ELECTRON_ENABLE_STACK_DUMPING: '1' }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'verbose-' + Date.now())
console.log('userDataDir:', userDataDir)
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9911'], { env })
child.stdout.on('data', b => process.stdout.write('OUT: ' + b))
child.stderr.on('data', b => process.stdout.write('ERR: ' + b))
child.on('exit', (c,s)=>console.log('EXIT',{code:c,sig:s}))
setTimeout(()=>{ console.log('— SIGKILL'); child.kill('SIGKILL') }, 10000)

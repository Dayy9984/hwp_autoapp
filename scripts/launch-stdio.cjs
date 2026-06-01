const { spawn } = require('child_process')
const path = require('path')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = require('os').tmpdir() + '\inserty-fresh-' + Date.now()
console.log('userDataDir:', userDataDir)
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9876'], {
  env,
  stdio: 'inherit',  // 모든 출력을 부모로
})
child.on('exit', (code, sig) => console.log('EXIT', { code, sig }))
setTimeout(() => {
  console.log('— after 8s, sending SIGKILL')
  child.kill('SIGKILL')
}, 8000)

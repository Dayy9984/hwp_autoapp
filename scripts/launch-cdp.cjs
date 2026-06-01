const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'inserty-cdp-' + Date.now())
console.log('userDataDir:', userDataDir)
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9876'], {
  env,
  stdio: 'inherit',
})
function probe() {
  http.get('http://127.0.0.1:9876/json/version', (res) => {
    let buf=''; res.on('data', c=>buf+=c); res.on('end', ()=>console.log('CDP ✓', buf))
  }).on('error', e=>console.log('CDP ✗', e.message))
}
const start = Date.now()
const ti = setInterval(() => {
  probe()
  if (Date.now()-start > 12000) {
    clearInterval(ti); console.log('— done probing'); child.kill('SIGKILL')
  }
}, 2000)
child.on('exit', (code, sig) => console.log('EXIT', { code, sig }))

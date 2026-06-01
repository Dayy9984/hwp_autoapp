const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
// OSS 빌드된 win-unpacked 경로
const exe = '/c/Users/dlgkr/Desktop/mallo/InsertyAI/release/0.1.9/win-unpacked/Inserty AI.exe'
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'oss-test-' + Date.now())
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9988'], { env })
child.stdout.on('data', b => process.stdout.write('OUT: ' + b))
child.stderr.on('data', b => process.stdout.write('ERR: ' + b))
function probe() {
  return new Promise(resolve => {
    http.get('http://127.0.0.1:9988/json/version', (res) => {
      let buf=''; res.on('data', c=>buf+=c); res.on('end', ()=>resolve(buf))
    }).on('error', e=>resolve(null))
  })
}
;(async()=>{
  for (let i=1; i<=8; i++) {
    await new Promise(r=>setTimeout(r,2000))
    const v = await probe()
    if (v) { console.log(`OSS CDP ✓ ${i*2}s`); break }
    console.log(`OSS CDP ✗ ${i*2}s`)
  }
  child.kill('SIGKILL')
})()

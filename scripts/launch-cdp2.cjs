const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const exe = path.resolve(__dirname,'..','release','0.1.9','win-unpacked','Inserty AI.exe')
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const userDataDir = path.join(require('os').tmpdir(), 'inserty-cdp-' + Date.now())
console.log('userDataDir:', userDataDir)
// 인스톨러처럼 정상 모드로 실행: --user-data-dir + 디버그 포트
const child = spawn(exe, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=9876', '--no-sandbox'], {
  env,
})
child.stdout.on('data', b => process.stdout.write('OUT: ' + b))
child.stderr.on('data', b => process.stdout.write('ERR: ' + b))
function probe() {
  return new Promise(resolve => {
    http.get('http://127.0.0.1:9876/json/version', (res) => {
      let buf=''; res.on('data', c=>buf+=c); res.on('end', ()=>resolve(buf))
    }).on('error', e=>resolve(null))
  })
}
;(async()=>{
  for (let i=1; i<=20; i++) {
    await new Promise(r=>setTimeout(r,1500))
    const v = await probe()
    if (v) { console.log(`CDP ✓ after ${i*1.5}s:`, v.slice(0,150)); break }
    console.log(`CDP ✗ ${i*1.5}s`)
  }
  child.kill('SIGKILL')
})()

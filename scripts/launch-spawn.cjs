// 직접 spawn으로 Electron 앱 실행 + stdout/stderr 캡쳐
const { spawn } = require('child_process')
const path = require('path')

const exe = path.resolve(
  __dirname,
  '..',
  'release',
  '0.1.9',
  'win-unpacked',
  'Inserty AI.exe',
)
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE

console.log('spawning:', exe)
console.log('ELECTRON_RUN_AS_NODE:', env.ELECTRON_RUN_AS_NODE)

const child = spawn(exe, ['--remote-debugging-port=9876'], {
  env,
  detached: false,
})

child.stdout.on('data', (b) => console.log('STDOUT:', b.toString()))
child.stderr.on('data', (b) => console.log('STDERR:', b.toString()))
child.on('exit', (code, sig) => console.log('EXIT', { code, sig }))

setTimeout(() => {
  console.log('— after 5s, killing')
  child.kill()
}, 5000)

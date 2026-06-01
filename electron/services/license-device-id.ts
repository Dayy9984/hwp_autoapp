// Device ID 생성 — Windows MachineGuid 직접 추출 + SHA-256 해시.
//
// 외부 의존성을 두지 않는다 (이전 시도: node-machine-id 패키지는 CJS 모듈이고
// ESM main bundle + asar 환경에서 createRequire 해석 실패로 "Cannot find module"
// 다이얼로그가 떴음). 어차피 그 패키지가 하는 일은 Windows 의 경우
// HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid 를 reg query 로 읽는 것 뿐이라
// 같은 로직을 inline 으로 구현한다.

import * as crypto from 'crypto'
import * as os from 'os'
import { execFileSync } from 'child_process'

let cachedId: string | null = null

function readWindowsMachineGuid(): string | null {
  // 32-bit Node from 64-bit OS 호환: PROCESSOR_ARCHITEW6432 가 있으면 sysnative 사용.
  const usesSysnative =
    process.arch === 'ia32' &&
    Object.prototype.hasOwnProperty.call(process.env, 'PROCESSOR_ARCHITEW6432')
  const regExe = usesSysnative
    ? 'C:\\Windows\\sysnative\\reg.exe'
    : 'C:\\Windows\\System32\\reg.exe'
  try {
    const out = execFileSync(
      regExe,
      [
        'QUERY',
        'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography',
        '/v',
        'MachineGuid',
      ],
      { encoding: 'utf8' },
    )
    const m = out.match(/MachineGuid\s+REG_SZ\s+([^\s]+)/i)
    return m?.[1]?.trim() || null
  } catch {
    return null
  }
}

/**
 * 안정적인 디바이스 ID (해시) — raw MachineGuid 를 직접 노출하지 않고
 * SHA-256 의 앞 32자 (128 bit) 만 사용.
 */
export function getDeviceId(): string {
  if (cachedId) return cachedId
  let source: string | null = null
  if (process.platform === 'win32') {
    source = readWindowsMachineGuid()
  }
  if (!source) {
    // 폴백: hostname + arch + platform (가장 안정적이진 않지만 deterministic).
    source = `${os.hostname()}|${os.arch()}|${os.platform()}`
  }
  cachedId = crypto.createHash('sha256').update(source).digest('hex').slice(0, 32)
  return cachedId
}

export interface DeviceInfo {
  device_id: string
  device_name: string
  device_os: string
}

export function getDeviceInfo(): DeviceInfo {
  return {
    device_id: getDeviceId(),
    device_name: os.hostname(),
    device_os: `${os.platform()} ${os.release()}`,
  }
}

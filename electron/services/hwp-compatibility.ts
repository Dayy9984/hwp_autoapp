/**
 * HWP Compatibility Service
 *
 * 64-bit 프로세스에서 32-bit HWP COM 서버와 통신하기 위한
 * TypeLib 등록 및 호환성 검사 서비스
 */

import { exec, spawn } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import fs from 'node:fs'
import { app } from 'electron'

const execAsync = promisify(exec)

// HWP TypeLib GUID
const HWP_TYPELIB_GUID = '7D2B6F3C-1D95-4E0C-BF5A-5EE564186FBC'

// HWP 버전 매핑 (HOffice 폴더명 → 버전)
// 지원 버전: 2018, 2020, 2022, 2024
const HOFFICE_VERSION_MAP: Record<string, { version: string; year: number }> = {
  'HOffice130': { version: '13.0', year: 2024 },
  'HOffice120': { version: '12.0', year: 2022 },
  'HOffice110': { version: '11.0', year: 2020 },
  'HOffice100': { version: '10.0', year: 2018 },
}

// Fallback: 하드코딩된 경로 (레지스트리 조회 실패 시)
// 경로 패턴: Hnc/Office YYYY/HOfficeXXX/bin, Hancom/HOfficeXXX/Bin, NEO 버전
const HWP_FALLBACK_PATHS = [
  // HWP 2024 (64-bit)
  { version: '2024', path: 'C:\\Program Files\\Hnc\\Office 2024\\HOffice130\\Bin\\Hwp.exe', arch: 'x64' as const },
  { version: '2024', path: 'C:\\Program Files\\Hancom\\HOffice130\\Bin\\Hwp.exe', arch: 'x64' as const },
  { version: '2024', path: 'C:\\Program Files\\Hnc\\Hwp 2024\\Hwp.exe', arch: 'x64' as const },
  // HWP 2022 (64-bit)
  { version: '2022', path: 'C:\\Program Files\\Hnc\\Office 2022\\HOffice120\\Bin\\Hwp.exe', arch: 'x64' as const },
  { version: '2022', path: 'C:\\Program Files\\Hancom\\HOffice120\\Bin\\Hwp.exe', arch: 'x64' as const },
  // HWP 2020 (32-bit)
  { version: '2020', path: 'C:\\Program Files (x86)\\Hnc\\Office 2020\\HOffice110\\Bin\\Hwp.exe', arch: 'x86' as const },
  { version: '2020', path: 'C:\\Program Files (x86)\\Hancom\\HOffice110\\Bin\\Hwp.exe', arch: 'x86' as const },
  { version: '2020', path: 'C:\\Program Files (x86)\\Hnc\\HwpNeo\\Hwp.exe', arch: 'x86' as const },
  // HWP 2018 (32-bit)
  { version: '2018', path: 'C:\\Program Files (x86)\\Hnc\\Office 2018\\HOffice100\\Bin\\Hwp.exe', arch: 'x86' as const },
  { version: '2018', path: 'C:\\Program Files (x86)\\Hancom\\HOffice100\\Bin\\Hwp.exe', arch: 'x86' as const },
]

const HWP_SCAN_BASE_PATHS = [
  'C:\\Program Files\\Hnc',
  'C:\\Program Files\\Hancom',
  'C:\\Program Files (x86)\\Hnc',
  'C:\\Program Files (x86)\\Hancom',
]

const shouldDescendDir = (name: string, depth: number) => {
  if (depth === 0) return true
  return /HOffice|Office|Hwp|Hancom|Hnc/i.test(name)
}

export type CompatibilityStatus = 'NOT_CHECKED' | 'CHECK_PASSED' | 'CHECK_FAILED' | 'REGISTRATION_NEEDED'

interface HwpInstallInfo {
  version: string
  path: string
  arch: 'x86' | 'x64'
  exists: boolean
}

interface CompatibilityResult {
  status: CompatibilityStatus
  message: string
  hwpInfo?: HwpInstallInfo
  needsRegistration?: boolean
  needsAdmin?: boolean
}

/**
 * 레지스트리에서 HWP 설치 경로 조회
 */
async function findHwpFromRegistry(): Promise<HwpInstallInfo | null> {
  // 레지스트리 경로들 (64-bit 및 32-bit HWP)
  const registryPaths = [
    { key: 'HKLM\\SOFTWARE\\HNC\\Hwp', arch: 'x64' as const },
    { key: 'HKLM\\SOFTWARE\\WOW6432Node\\HNC\\Hwp', arch: 'x86' as const },
    { key: 'HKCU\\SOFTWARE\\HNC\\Hwp', arch: 'x64' as const },
  ]

  for (const { key, arch } of registryPaths) {
    try {
      const { stdout } = await execAsync(`reg query "${key}" /s 2>nul`, { encoding: 'utf-8' })

      // InstallDir 또는 Path 값 찾기
      const installDirMatch = stdout.match(/InstallDir\s+REG_SZ\s+(.+)/i)
      const pathMatch = stdout.match(/Path\s+REG_SZ\s+(.+)/i)

      const installDir = (installDirMatch?.[1] || pathMatch?.[1])?.trim()
      if (!installDir) continue

      // Hwp.exe 경로 구성
      let hwpExePath = installDir
      if (!hwpExePath.toLowerCase().endsWith('hwp.exe')) {
        hwpExePath = path.join(installDir, 'Bin', 'Hwp.exe')
      }

      if (fs.existsSync(hwpExePath)) {
        // 버전 감지 (폴더명에서)
        const versionInfo = detectVersionFromPath(hwpExePath)
        console.log(`[HwpCompatibility] Found HWP from registry: ${hwpExePath} (${arch})`)
        return {
          version: versionInfo?.year.toString() || 'Unknown',
          path: hwpExePath,
          arch,
          exists: true
        }
      }
    } catch {
      // 레지스트리 키가 없으면 무시
    }
  }

  return null
}

/**
 * COM 클래스 레지스트리에서 HWP 경로 찾기
 */
async function findHwpFromComRegistry(): Promise<HwpInstallInfo | null> {
  const comClsids = [
    'HWPFrame.HwpObject',
    'HWPFrame.HwpObject.1',
    'HWPFrame.HwpObject.2',
  ]

  for (const progId of comClsids) {
    try {
      // ProgID → CLSID 조회
      const { stdout: clsidOut } = await execAsync(
        `reg query "HKCR\\${progId}\\CLSID" /ve`,
        { encoding: 'utf-8' }
      )

      const clsidMatch = clsidOut.match(/\{[A-F0-9-]+\}/i)
      if (!clsidMatch) continue

      const clsid = clsidMatch[0]

      // 32-bit 및 64-bit LocalServer32 경로 모두 조회
      const clsidPaths = [
        `HKCR\\WOW6432Node\\CLSID\\${clsid}\\LocalServer32`,  // 32-bit HWP
        `HKCR\\CLSID\\${clsid}\\LocalServer32`,               // 64-bit HWP
        `HKLM\\SOFTWARE\\WOW6432Node\\Classes\\CLSID\\${clsid}\\LocalServer32`,  // 32-bit HKLM
        `HKLM\\SOFTWARE\\Classes\\CLSID\\${clsid}\\LocalServer32`,               // 64-bit HKLM
      ]

      for (const clsidPath of clsidPaths) {
        try {
          const { stdout: pathOut } = await execAsync(
            `reg query "${clsidPath}" /ve`,
            { encoding: 'utf-8' }
          )

          const pathMatch = pathOut.match(/REG_SZ\s+(.+)/i)
          if (!pathMatch) continue

          let hwpPath = pathMatch[1].trim().replace(/"/g, '')
          // 인자 제거 (-Automation 등)
          const spaceIdx = hwpPath.indexOf(' -')
          if (spaceIdx > 0) {
            hwpPath = hwpPath.substring(0, spaceIdx)
          }

          if (fs.existsSync(hwpPath)) {
            const arch = hwpPath.toLowerCase().includes('program files (x86)') ? 'x86' : 'x64'
            const versionInfo = detectVersionFromPath(hwpPath)
            console.log(`[HwpCompatibility] Found HWP from COM registry: ${hwpPath} (${arch})`)
            return {
              version: versionInfo?.year.toString() || 'Unknown',
              path: hwpPath,
              arch,
              exists: true
            }
          }
        } catch {
          // 이 경로에 없으면 다음 경로 시도
        }
      }
    } catch {
      // 레지스트리 키가 없으면 무시
    }
  }

  return null
}

/**
 * 경로에서 HWP 버전 감지
 */
function detectVersionFromPath(hwpPath: string): { version: string; year: number } | null {
  const officeYearMatch = hwpPath.match(/Office\\s(20\\d{2})/i)
  if (officeYearMatch) {
    const year = Number.parseInt(officeYearMatch[1], 10)
    if (Number.isFinite(year)) {
      return { version: officeYearMatch[1], year }
    }
  }
  for (const [folder, info] of Object.entries(HOFFICE_VERSION_MAP)) {
    if (hwpPath.includes(folder)) {
      return info
    }
  }
  return null
}

const findHwpFromFilesystem = (): HwpInstallInfo | null => {
  const candidates: HwpInstallInfo[] = []

  const scanDir = (dirPath: string, depth: number) => {
    if (depth > 4) return
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(dirPath, { withFileTypes: true })
    } catch {
      return
    }

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)
      if (entry.isFile() && entry.name.toLowerCase() === 'hwp.exe') {
        const arch = fullPath.toLowerCase().includes('program files (x86)') ? 'x86' : 'x64'
        const versionInfo = detectVersionFromPath(fullPath)
        candidates.push({
          version: versionInfo?.year ? versionInfo.year.toString() : 'Unknown',
          path: fullPath,
          arch,
          exists: true
        })
        continue
      }
      if (entry.isDirectory() && shouldDescendDir(entry.name, depth)) {
        scanDir(fullPath, depth + 1)
      }
    }
  }

  for (const basePath of HWP_SCAN_BASE_PATHS) {
    if (fs.existsSync(basePath)) {
      scanDir(basePath, 0)
    }
  }

  if (candidates.length === 0) return null
  const score = (candidate: HwpInstallInfo) => {
    const year = Number.parseInt(candidate.version, 10)
    return Number.isFinite(year) ? year : 0
  }
  candidates.sort((a, b) => score(b) - score(a))
  return candidates[0]
}

/**
 * 설치된 HWP 찾기 (레지스트리 우선, fallback으로 하드코딩 경로)
 */
export async function findInstalledHwpAsync(): Promise<HwpInstallInfo | null> {
  // 1. 레지스트리에서 찾기
  let hwpInfo = await findHwpFromRegistry()
  if (hwpInfo) return hwpInfo

  // 2. COM 레지스트리에서 찾기
  hwpInfo = await findHwpFromComRegistry()
  if (hwpInfo) return hwpInfo

  // 3. Fallback: 하드코딩된 경로 확인
  console.log('[HwpCompatibility] Registry lookup failed, trying fallback paths...')
  for (const hwp of HWP_FALLBACK_PATHS) {
    if (fs.existsSync(hwp.path)) {
      return {
        version: hwp.version,
        path: hwp.path,
        arch: hwp.arch,
        exists: true
      }
    }
  }

  // 4. Filesystem scan (fallback)
  const scanned = findHwpFromFilesystem()
  if (scanned) {
    console.log('[HwpCompatibility] Found HWP via filesystem scan:', scanned.path)
    return scanned
  }

  return null
}

/**
 * 설치된 HWP 찾기 (동기 버전 - 하드코딩 경로만)
 */
export function findInstalledHwp(): HwpInstallInfo | null {
  for (const hwp of HWP_FALLBACK_PATHS) {
    if (fs.existsSync(hwp.path)) {
      return {
        version: hwp.version,
        path: hwp.path,
        arch: hwp.arch,
        exists: true
      }
    }
  }
  return null
}

/**
 * TypeLib 파일 존재 여부 확인
 */
export function checkTypeLibFileExists(hwpPath: string): boolean {
  const hwpDir = path.dirname(hwpPath)
  const tlbPath = path.join(hwpDir, 'HwpObject.tlb')
  return fs.existsSync(tlbPath)
}

/**
 * 레지스트리에서 TypeLib 등록 확인
 */
export async function checkTypeLibRegistered(): Promise<boolean> {
  try {
    // HKEY_CLASSES_ROOT\TypeLib\{GUID} 확인
    const { stdout } = await execAsync(
      `reg query "HKCR\\TypeLib\\{${HWP_TYPELIB_GUID}}" /s`,
      { encoding: 'utf-8' }
    )
    return stdout.includes(HWP_TYPELIB_GUID)
  } catch {
    return false
  }
}

/**
 * HWP COM 서버가 정상적으로 작동하는지 테스트
 * 주의: COM 객체를 올바르게 정리해야 백그라운드 HWP 인스턴스가 남지 않음
 */
export async function testHwpComServer(): Promise<boolean> {
  try {
    // PowerShell로 COM 객체 생성 테스트 (올바른 cleanup 포함)
    const psScript = `
      try {
        $hwp = New-Object -ComObject HWPFrame.HwpObject
        $hwp.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($hwp) | Out-Null
        [System.GC]::Collect()
        [System.GC]::WaitForPendingFinalizers()
        Write-Output 'OK'
      } catch {
        Write-Output 'FAIL'
      }
    `
    const { stdout } = await execAsync(
      `powershell -Command "${psScript.replace(/"/g, '\\"').replace(/\n/g, ' ')}"`,
      { encoding: 'utf-8', timeout: 15000 }
    )
    return stdout.trim() === 'OK'
  } catch {
    return false
  }
}

/**
 * TypeLib 등록 (hwp.exe /regserver 사용)
 *
 * HWP는 Out-of-Process COM 서버이므로 TypeLib 등록은 선택사항입니다.
 * TypeLib 없이도 IDispatch 인터페이스로 통신 가능합니다.
 * 하지만 등록하면 더 나은 IntelliSense와 형식 정보를 제공합니다.
 *
 * 참고: regtlibv12는 Windows에 기본 설치되어 있지 않으므로
 * hwp.exe /regserver를 사용합니다.
 */
export async function registerTypeLib(hwpPath: string): Promise<{ success: boolean; needsAdmin: boolean }> {
  const hwpDir = path.dirname(hwpPath)
  const tlbPath = path.join(hwpDir, 'HwpObject.tlb')

  if (!fs.existsSync(tlbPath)) {
    console.log('[HwpCompatibility] TypeLib file not found:', tlbPath)
    // TypeLib 파일이 없어도 COM 서버는 동작할 수 있음
    return { success: false, needsAdmin: false }
  }

  // 방법 1: hwp.exe /regserver 실행 (일반 권한으로 시도)
  // HWP가 자체적으로 TypeLib과 COM 클래스를 등록합니다
  try {
    console.log('[HwpCompatibility] Trying hwp.exe /regserver...')
    await execAsync(`"${hwpPath}" /regserver`, {
      encoding: 'utf-8',
      timeout: 30000,
      cwd: hwpDir
    })
    console.log('[HwpCompatibility] TypeLib registered via hwp.exe /regserver')
    return { success: true, needsAdmin: false }
  } catch (err) {
    console.log('[HwpCompatibility] hwp.exe /regserver failed (may need admin):', err)
  }

  // 일반 권한으로 실패 시 관리자 권한 필요
  console.log('[HwpCompatibility] Needs admin privileges for registration')
  return { success: false, needsAdmin: true }
}

/**
 * 관리자 권한으로 TypeLib 등록
 */
export function registerTypeLibElevated(hwpPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const hwpDir = path.dirname(hwpPath)

    // PowerShell을 관리자 권한으로 실행하여 HWP /regserver 수행
    const psCommand = `
      Start-Process -FilePath "${hwpPath}" -ArgumentList "/regserver" -Verb RunAs -Wait
    `

    const child = spawn('powershell', ['-Command', psCommand], {
      stdio: 'ignore',
      shell: true
    })

    child.on('close', (code) => {
      resolve(code === 0)
    })

    child.on('error', () => {
      resolve(false)
    })

    // 30초 타임아웃
    setTimeout(() => {
      try { child.kill() } catch {}
      resolve(false)
    }, 30000)
  })
}

/**
 * HWP 호환성 검사 (메인 함수)
 */
export async function checkHwpCompatibility(): Promise<CompatibilityResult> {
  console.log('[HwpCompatibility] Starting compatibility check...')

  // 1. 설치된 HWP 찾기 (레지스트리 우선)
  const hwpInfo = await findInstalledHwpAsync()
  if (!hwpInfo) {
    return {
      status: 'CHECK_FAILED',
      message: 'HWP가 설치되어 있지 않습니다.',
    }
  }

  console.log(`[HwpCompatibility] Found HWP ${hwpInfo.version} (${hwpInfo.arch}) at ${hwpInfo.path}`)

  // 2. TypeLib 파일 존재 확인
  const tlbExists = checkTypeLibFileExists(hwpInfo.path)
  if (!tlbExists) {
    return {
      status: 'CHECK_FAILED',
      message: 'HWP TypeLib 파일을 찾을 수 없습니다.',
      hwpInfo
    }
  }

  // 3. TypeLib 레지스트리 등록 확인
  const isRegistered = await checkTypeLibRegistered()
  if (!isRegistered) {
    console.log('[HwpCompatibility] TypeLib not registered')
    return {
      status: 'REGISTRATION_NEEDED',
      message: 'HWP TypeLib 등록이 필요합니다.',
      hwpInfo,
      needsRegistration: true,
      needsAdmin: true
    }
  }

  // 4. COM 서버 테스트 (선택적)
  // const comWorks = await testHwpComServer()
  // if (!comWorks) {
  //   return {
  //     status: 'CHECK_FAILED',
  //     message: 'HWP COM 서버 연결에 실패했습니다.',
  //     hwpInfo
  //   }
  // }

  console.log('[HwpCompatibility] Compatibility check passed')
  return {
    status: 'CHECK_PASSED',
    message: 'HWP 호환성 검사 통과',
    hwpInfo
  }
}

/**
 * TypeLib 등록 시도 (필요시 관리자 권한 요청)
 */
export async function ensureTypeLibRegistered(): Promise<boolean> {
  const result = await checkHwpCompatibility()

  if (result.status === 'CHECK_PASSED') {
    return true
  }

  if (result.status === 'REGISTRATION_NEEDED' && result.hwpInfo) {
    console.log('[HwpCompatibility] Attempting TypeLib registration...')

    // 일반 권한으로 먼저 시도
    const regResult = await registerTypeLib(result.hwpInfo.path)
    if (regResult.success) {
      return true
    }

    // 관리자 권한 필요
    if (regResult.needsAdmin) {
      console.log('[HwpCompatibility] Requesting admin privileges...')
      return await registerTypeLibElevated(result.hwpInfo.path)
    }
  }

  return false
}

// 싱글톤 상태
let compatibilityStatus: CompatibilityStatus = 'NOT_CHECKED'
let lastCheckResult: CompatibilityResult | null = null

export function getCompatibilityStatus(): CompatibilityStatus {
  return compatibilityStatus
}

export function getLastCheckResult(): CompatibilityResult | null {
  return lastCheckResult
}

export async function runCompatibilityCheck(): Promise<CompatibilityResult> {
  lastCheckResult = await checkHwpCompatibility()
  compatibilityStatus = lastCheckResult.status
  return lastCheckResult
}

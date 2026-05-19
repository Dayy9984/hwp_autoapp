/**
 * HWP Version Detector
 *
 * 실행 중인 HWP 프로세스의 32-bit/64-bit 여부를 감지합니다.
 *
 * HWP 2020 이하: 32-bit
 * HWP 2022 이상: 64-bit
 */

import { exec } from 'node:child_process'
import { promisify } from 'node:util'

const execAsync = promisify(exec)

export interface HwpProcessInfo {
  pid: number
  name: string
  is64bit: boolean
}

/**
 * PowerShell로 실행 중인 HWP 프로세스의 아키텍처 감지
 */
export async function detectRunningHwpArchitecture(): Promise<HwpProcessInfo | null> {
  const psScript = `
    $targetNames = @('hwp.exe', 'hwpnt.exe', 'hanword.exe')
    $procs = Get-Process | Where-Object { $targetNames -contains $_.ProcessName.ToLower() }

    foreach ($proc in $procs) {
      try {
        $handle = [IntPtr]::Zero
        $PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        $kernel32 = Add-Type -MemberDefinition @'
          [DllImport("kernel32.dll", SetLastError = true)]
          public static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

          [DllImport("kernel32.dll", SetLastError = true)]
          public static extern bool IsWow64Process(IntPtr hProcess, out bool wow64Process);

          [DllImport("kernel32.dll", SetLastError = true)]
          public static extern bool CloseHandle(IntPtr hObject);
'@ -Name 'Kernel32' -Namespace 'Win32' -PassThru

        $handle = $kernel32::OpenProcess($PROCESS_QUERY_LIMITED_INFORMATION, $false, $proc.Id)
        if ($handle -ne [IntPtr]::Zero) {
          $isWow64 = $false
          if ($kernel32::IsWow64Process($handle, [ref]$isWow64)) {
            $is64bit = if ($isWow64) { $false } else { $true }
            $kernel32::CloseHandle($handle)

            # JSON 출력
            @{
              pid = $proc.Id
              name = $proc.ProcessName
              is64bit = $is64bit
            } | ConvertTo-Json -Compress
            break
          }
          $kernel32::CloseHandle($handle)
        }
      } catch {
        continue
      }
    }
  `

  try {
    const { stdout } = await execAsync(
      `powershell -NoProfile -ExecutionPolicy Bypass -Command "${psScript.replace(/"/g, '\\"')}"`,
      { windowsHide: true, timeout: 5000 }
    )

    const output = stdout.trim()
    if (!output) {
      console.log('[HwpVersionDetector] No running HWP process found')
      return null
    }

    const info = JSON.parse(output) as HwpProcessInfo
    console.log('[HwpVersionDetector] Detected running HWP:', info)
    return info
  } catch (error) {
    console.error('[HwpVersionDetector] Failed to detect HWP process:', error)
    return null
  }
}

/**
 * 감지된 HWP 프로세스에 맞는 Python 아키텍처 반환
 */
export function getPythonArchForHwp(hwpInfo: HwpProcessInfo | null): 'x86' | 'x64' {
  if (!hwpInfo) {
    // HWP 프로세스 미감지 시 기본값: 32-bit (하위 호환성)
    console.log('[HwpVersionDetector] No running HWP detected, defaulting to x86 Python')
    return 'x86'
  }

  const arch = hwpInfo.is64bit ? 'x64' : 'x86'
  console.log(`[HwpVersionDetector] HWP PID ${hwpInfo.pid} (${arch}-bit) → Python ${arch}`)
  return arch
}

/**
 * Process Monitor - 열린 문서 프로세스 감지
 *
 * Windows에서 실행 중인 한글/워드/엑셀 프로세스를 감지하고
 * 열려 있는 문서 파일 목록을 반환합니다.
 */

import { exec } from 'node:child_process'
import { promisify } from 'node:util'

const execAsync = promisify(exec)

export interface OpenDocument {
  id: string
  name: string
  path: string
  type: 'hwp' | 'word' | 'excel' | 'unknown'
  processName: string
  pid: number
}

// 프로세스명 → 문서 타입 매핑
const PROCESS_TYPE_MAP: Record<string, OpenDocument['type']> = {
  'hwp.exe': 'hwp',
  'hwordx.exe': 'hwp',
  'winword.exe': 'word',
  'excel.exe': 'excel',
}

/**
 * PowerShell로 열린 문서 파일 목록 조회
 * 모든 창(탭 포함)을 가져오기 위해 EnumWindows 사용
 */
async function getOpenDocumentsWindows(): Promise<OpenDocument[]> {
  const documents: OpenDocument[] = []

  try {
    // PowerShell: 모든 프로세스의 모든 창 제목 가져오기
    const psCommand = `
      Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        using System.Text;
        using System.Collections.Generic;

        public class WindowEnum {
          [DllImport("user32.dll")]
          private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

          [DllImport("user32.dll")]
          private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

          [DllImport("user32.dll")]
          private static extern int GetWindowTextLength(IntPtr hWnd);

          [DllImport("user32.dll")]
          private static extern bool IsWindowVisible(IntPtr hWnd);

          [DllImport("user32.dll")]
          private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

          private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

          public static List<object[]> GetAllWindows() {
            var windows = new List<object[]>();
            EnumWindows((hWnd, lParam) => {
              if (IsWindowVisible(hWnd)) {
                int length = GetWindowTextLength(hWnd);
                if (length > 0) {
                  StringBuilder sb = new StringBuilder(length + 1);
                  GetWindowText(hWnd, sb, sb.Capacity);
                  uint pid;
                  GetWindowThreadProcessId(hWnd, out pid);
                  windows.Add(new object[] { pid, sb.ToString() });
                }
              }
              return true;
            }, IntPtr.Zero);
            return windows;
          }
        }
"@

      $targetProcesses = @{
        'hwp' = 'hwp'
        'hwordx' = 'hwp'
        'winword' = 'word'
        'excel' = 'excel'
      }

      $results = @()
      $windows = [WindowEnum]::GetAllWindows()

      foreach ($window in $windows) {
        $pid = $window[0]
        $title = $window[1]

        try {
          $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
          if ($proc) {
            $procName = $proc.ProcessName.ToLower()
            if ($targetProcesses.ContainsKey($procName)) {
              $results += @{
                Pid = $pid
                Title = $title
                ProcessName = $proc.ProcessName + '.exe'
                Type = $targetProcesses[$procName]
              }
            }
          }
        } catch {}
      }

      $results | ConvertTo-Json -Compress
    `

    const { stdout } = await execAsync(
      `powershell -NoProfile -Command "${psCommand.replace(/"/g, '\\"').replace(/\n/g, ' ')}"`,
      { encoding: 'utf-8', maxBuffer: 1024 * 1024 }
    )

    if (!stdout.trim() || stdout.trim() === 'null') return documents

    // JSON 파싱
    let windows: Array<{ Pid: number; Title: string; ProcessName: string; Type: string }>
    const parsed = JSON.parse(stdout)
    windows = Array.isArray(parsed) ? parsed : [parsed]

    const seen = new Set<string>() // 중복 제거용

    for (const win of windows) {
      if (!win.Title) continue

      const docName = extractDocumentName(win.Title, win.ProcessName)
      if (!docName) continue

      // 중복 체크 (같은 문서명)
      const key = `${win.Type}-${docName}`
      if (seen.has(key)) continue
      seen.add(key)

      documents.push({
        id: `${win.ProcessName}-${win.Pid}-${docName}`,
        name: docName,
        path: win.Title,
        type: win.Type as OpenDocument['type'],
        processName: win.ProcessName,
        pid: win.Pid,
      })
    }
  } catch (err) {
    console.error('[ProcessMonitor] Error:', err)
  }

  return documents
}

/**
 * 창 제목에서 문서 이름 추출
 */
function extractDocumentName(windowTitle: string, processName: string): string | null {
  if (!windowTitle) return null

  const lowerProcess = processName.toLowerCase()

  if (lowerProcess.includes('hwp') || lowerProcess.includes('hword')) {
    // 한글: "문서.hwp - 한글 2022" 또는 "문서.hwpx - 한글"
    const match = windowTitle.match(/^(.+\.hwp[x]?)/i)
    if (match) return match[1]

    // 경로 포함: "C:\path\문서.hwp - 한글"
    const pathMatch = windowTitle.match(/([^\\/:*?"<>|]+\.hwp[x]?)/i)
    if (pathMatch) return pathMatch[1]

    // " - 한글" 앞부분
    const dashIdx = windowTitle.indexOf(' - ')
    if (dashIdx > 0) return windowTitle.substring(0, dashIdx)
  }

  if (lowerProcess.includes('winword')) {
    // Word: "Document.docx - Microsoft Word"
    const match = windowTitle.match(/^(.+\.docx?)/i)
    if (match) return match[1]

    const dashIdx = windowTitle.indexOf(' - ')
    if (dashIdx > 0) return windowTitle.substring(0, dashIdx)
  }

  if (lowerProcess.includes('excel')) {
    // Excel: "Book1.xlsx - Excel"
    const match = windowTitle.match(/^(.+\.xlsx?)/i)
    if (match) return match[1]

    const dashIdx = windowTitle.indexOf(' - ')
    if (dashIdx > 0) return windowTitle.substring(0, dashIdx)
  }

  return null
}

/**
 * 열린 문서 목록 조회 (크로스 플랫폼 대응)
 */
export async function getOpenDocuments(): Promise<OpenDocument[]> {
  if (process.platform === 'win32') {
    return getOpenDocumentsWindows()
  }

  // 다른 플랫폼은 빈 배열 반환
  return []
}

/**
 * 특정 프로세스에 포커스 주기
 */
export async function focusProcess(pid: number): Promise<boolean> {
  if (process.platform !== 'win32') return false

  try {
    const psCommand = `
      Add-Type -TypeDefinition @"
        using System;
        using System.Runtime.InteropServices;
        public class Win32 {
          [DllImport("user32.dll")]
          public static extern bool SetForegroundWindow(IntPtr hWnd);
        }
"@
      $proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
      if ($proc -and $proc.MainWindowHandle) {
        [Win32]::SetForegroundWindow($proc.MainWindowHandle)
      }
    `

    await execAsync(
      `powershell -NoProfile -Command "${psCommand.replace(/\n/g, ' ')}"`,
      { encoding: 'utf-8' }
    )
    return true
  } catch {
    return false
  }
}

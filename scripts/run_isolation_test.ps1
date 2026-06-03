# HWP parent isolation test launcher. ASCII-only.
# Launch K workers concurrently; mid-hold, snapshot hwp.exe process count.
# PASS if hwp.exe count >= K during hold (each worker = own instance) AND all workers EDIT_OK.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/run_isolation_test.ps1 [K]
param([int]$K = 3)
$ErrorActionPreference = "Continue"
$PYDIR  = "C:\Users\Public\projects\insertyai\python"
$SCRIPT = "C:\Users\Public\projects\insertyai\scripts\hwp_isolation_test.py"
$OUT    = "C:\Users\Public\projects\insertyai\eval-results"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

Write-Output "=== cleanup stray HWP ==="
Get-Process hwp -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep 2
Write-Output ("hwp before: " + (Get-Process hwp -EA SilentlyContinue | Measure-Object).Count)

Write-Output "=== launch $K workers ==="
$procs = @()
for ($i = 1; $i -le $K; $i++) {
  $log = "$OUT\iso_w${i}.log"
  $p = Start-Process uv -ArgumentList @("run","python",$SCRIPT,"$i") -WorkingDirectory $PYDIR -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  $procs += $p
  Start-Sleep -Milliseconds 800
}

# mid-hold snapshot (workers sleep 12s; snapshot a few times)
$maxHwp = 0
for ($s = 0; $s -lt 6; $s++) {
  Start-Sleep -Seconds 2
  $c = (Get-Process hwp -EA SilentlyContinue | Measure-Object).Count
  if ($c -gt $maxHwp) { $maxHwp = $c }
  Write-Output ("  snapshot t~" + (($s+1)*2) + "s: hwp.exe = " + $c)
}

Write-Output "=== wait for workers ==="
foreach ($p in $procs) { try { $p.WaitForExit(60000) | Out-Null } catch {} ; try { if (-not $p.HasExited) { $p.Kill($true) } } catch {} }
Start-Sleep 1
Get-Process hwp -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue

Write-Output "=== worker results ==="
$up = 0; $editok = 0; $fail = 0
for ($i = 1; $i -le $K; $i++) {
  $content = Get-Content "$OUT\iso_w${i}.log" -EA SilentlyContinue
  $u = ($content | Select-String "WORKER ${i} UP") | Select-Object -First 1
  $e = ($content | Select-String "EDIT_OK=True") | Select-Object -First 1
  $f = ($content | Select-String "FAIL|IMPORT_FAIL") | Select-Object -First 1
  if ($u) { $up++ }
  if ($e) { $editok++ }
  if ($f) { $fail++ }
  Write-Output ("  w" + $i + ": up=" + [bool]$u + " editok=" + [bool]$e + " " + [string]$f)
}
Write-Output "=== verdict ==="
Write-Output ("workers up: " + $up + "/" + $K + " | edit_ok: " + $editok + "/" + $K + " | max concurrent hwp.exe: " + $maxHwp)
if ($maxHwp -ge $K -and $editok -eq $K) { Write-Output "RESULT: PASS - process isolation gives $K independent concurrent HWP (edit isolated)" }
elseif ($maxHwp -ge 2) { Write-Output ("RESULT: PARTIAL - " + $maxHwp + " concurrent HWP, edit_ok " + $editok + "/" + $K + " (some instability)") }
else { Write-Output ("RESULT: FAIL - only " + $maxHwp + " hwp.exe (collapsed to one parent) -> need respawn logic or different approach") }

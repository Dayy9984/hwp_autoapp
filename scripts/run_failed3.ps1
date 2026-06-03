# Re-run ONLY the 3 forms that failed under gen_py contention, now with per-worker
# %TEMP% isolation (each worker gets its own win32com gen_py makepy cache -> no
# cross-process WinError 32 -> no cascade to "-2147023174 RPC 서버 사용 불가").
# Appends to the SAME tag (corpus_v3); Is-Clean reads the LAST record per form,
# so a clean re-run supersedes the earlier failure -> 9/9 once these pass.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/run_failed3.ps1 [K]
param([int]$K = 3)
$ErrorActionPreference = "Continue"
$PYDIR = "C:\Users\Public\projects\insertyai\python"
$OUT   = "C:\Users\Public\projects\insertyai\eval-results"
$TAG   = "corpus_v3"
$WLOG  = "$OUT\$TAG.failed3.wrapper.log"
$PERFORM_TIMEOUT_S = 420
$MAXRETRY = 2
New-Item -ItemType Directory -Force -Path $OUT | Out-Null
$WTMP = "$OUT\wtmp"
New-Item -ItemType Directory -Force -Path $WTMP | Out-Null
$ORIG_TMP = $env:TMP; $ORIG_TEMP = $env:TEMP

$hashes = @(
  "340a07da5affac8ba78724d60324032262d050824d98a5575f3c1de77c261591",
  "3c4a6ca7b221b9c63bf81cff2dfee3b9e76fcc166dc80507555dad5583dbc655",
  "f241fa3912ede5512d32b9a7b17fe2256ba178663c99431ac3bb1fb0e215eedc"
)

function Is-Clean($short) {
  $f = "$OUT\eval-$TAG.jsonl"
  if (-not (Test-Path $f)) { return $false }
  $rec = Get-Content $f -EA SilentlyContinue | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Where-Object { $_.name -like "*$short*" } | Select-Object -Last 1
  if (-not $rec) { return $false }
  if ($rec.error) { return $false }
  if (-not $rec.vision.overall) { return $false }
  return $true
}

"FAILED3+ISOLATION START $(Get-Date -Format o) K=$K total=$($hashes.Count) maxretry=$MAXRETRY" | Out-File $WLOG
Get-Process hwp -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep 2

$queue = New-Object System.Collections.Queue
foreach ($h in $hashes) { $queue.Enqueue($h) | Out-Null }
$running = @{}
$retries = @{}
$finalized = @{}
$total = $hashes.Count

while ($queue.Count -gt 0 -or $running.Count -gt 0) {
  while ($running.Count -lt $K -and $queue.Count -gt 0) {
    $h = [string]$queue.Dequeue()
    $short = $h.Substring(0, 12)
    $attempt = ($retries[$short]) + 1
    $log = "$OUT\f3_${short}_a${attempt}.log"
    # NOTE: PowerShell vars are case-insensitive, so the per-worker dir MUST NOT be
    # named $wtmp (collides with base $WTMP -> nests paths every iteration -> MAX_PATH).
    $wdir = "$WTMP\$short"
    New-Item -ItemType Directory -Force -Path $wdir | Out-Null
    $env:TMP = $wdir; $env:TEMP = $wdir
    $aa = @("run","python","-m","eval.auto_eval","--r2-keys","hwp/$h.hwp","--mode","full","--out","../eval-results","--tag",$TAG,"--max-apply-ops","400","--max-pages","1")
    $p = Start-Process uv -ArgumentList $aa -WorkingDirectory $PYDIR -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $running[$p.Id] = @{ proc = $p; hash = $short; full = $h; start = (Get-Date); attempt = $attempt }
    "LAUNCH $short attempt=$attempt pid=$($p.Id) tmp=$wdir running=$($running.Count)/$K queued=$($queue.Count) $(Get-Date -Format o)" | Out-File $WLOG -Append
    Start-Sleep -Milliseconds 900
  }
  Start-Sleep -Seconds 3
  foreach ($wpid in @($running.Keys)) {
    $info = $running[$wpid]
    $timedout = (((Get-Date) - $info.start).TotalSeconds -gt $PERFORM_TIMEOUT_S)
    if ($info.proc.HasExited -or $timedout) {
      if ($timedout -and -not $info.proc.HasExited) { try { $info.proc.Kill($true) } catch {} ; Get-Process hwp -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue }
      $running.Remove($wpid)
      $short = $info.hash
      Start-Sleep -Milliseconds 500
      if (Is-Clean $short) {
        $finalized[$short] = "clean"
        "RESULT $short CLEAN (attempt $($info.attempt)) $(Get-Date -Format o)" | Out-File $WLOG -Append
      } elseif ($retries[$short] -ge $MAXRETRY) {
        $finalized[$short] = "failed"
        "RESULT $short GAVEUP after $($info.attempt) attempts $(Get-Date -Format o)" | Out-File $WLOG -Append
      } else {
        $retries[$short] = ($retries[$short]) + 1
        $queue.Enqueue($info.full) | Out-Null
        "RETRY $short (now retry $($retries[$short])) timedout=$timedout $(Get-Date -Format o)" | Out-File $WLOG -Append
      }
    }
  }
}
Get-Process hwp -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
$env:TMP = $ORIG_TMP; $env:TEMP = $ORIG_TEMP
$clean = ($finalized.Values | Where-Object { $_ -eq "clean" } | Measure-Object).Count
"FAILED3+ISOLATION DONE clean=$clean/$total $(Get-Date -Format o)" | Out-File $WLOG -Append

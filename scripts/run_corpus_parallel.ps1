# Parallel corpus eval with RETRY — throttle <=K concurrent per-form processes (isolated HWP),
# re-queue any form that fails (error / no-verdict / timeout) up to MAXRETRY times.
# COM failures are non-deterministic, so a retry usually recovers them.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/run_corpus_parallel.ps1 [K]
param([int]$K = 5)
$ErrorActionPreference = "Continue"
$PYDIR = "C:\Users\Public\projects\insertyai\python"
$OUT   = "C:\Users\Public\projects\insertyai\eval-results"
$TAG   = "corpus_par2"
$WLOG  = "$OUT\$TAG.wrapper.log"
$PERFORM_TIMEOUT_S = 420
$MAXRETRY = 2
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

$hashes = @(
  "6ca0eb040bab984b9d371d0166947402caebe0771a19e9cf98d99d351701b6c1",
  "f241fa3912ede5512d32b9a7b17fe2256ba178663c99431ac3bb1fb0e215eedc",
  "3c4a6ca7b221b9c63bf81cff2dfee3b9e76fcc166dc80507555dad5583dbc655",
  "340a07da5affac8ba78724d60324032262d050824d98a5575f3c1de77c261591",
  "f07ef1e0db8fb9874140b060cd68e5133a38efb70e4db043d636ad96aefa2efb",
  "1f379b8f6cf814a49017d9f1e22e62a3a2e227ee89ff9bd31ce8373de554ca55",
  "7dc538e41c9fb6a4ef2046b239f162ead0e688cd277fb782cddd330325b55ec4",
  "dfffddfc371d51cf66b04ddef3a6920ca958348f1bc7a4094dc99d082d1c161c",
  "9f058351032b06988c78a766162a1dfe828502dabe618acd205cb030a7af1a92"
)
$byShort = @{}; foreach ($h in $hashes) { $byShort[$h.Substring(0,12)] = $h }

function Is-Clean($short) {
  $f = "$OUT\eval-$TAG.jsonl"
  if (-not (Test-Path $f)) { return $false }
  $rec = Get-Content $f -EA SilentlyContinue | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Where-Object { $_.name -like "*$short*" } | Select-Object -Last 1
  if (-not $rec) { return $false }
  if ($rec.error) { return $false }
  $ov = $rec.vision.overall
  if (-not $ov) { return $false }
  return $true
}

"PARALLEL+RETRY START $(Get-Date -Format o) K=$K total=$($hashes.Count) maxretry=$MAXRETRY" | Out-File $WLOG
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
    $log = "$OUT\par2_${short}_a${attempt}.log"
    $aa = @("run","python","-m","eval.auto_eval","--r2-keys","hwp/$h.hwp","--mode","full","--out","../eval-results","--tag",$TAG,"--max-apply-ops","400","--max-pages","1")
    $p = Start-Process uv -ArgumentList $aa -WorkingDirectory $PYDIR -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $running[$p.Id] = @{ proc = $p; hash = $short; full = $h; start = (Get-Date); attempt = $attempt }
    "LAUNCH $short attempt=$attempt pid=$($p.Id) running=$($running.Count)/$K queued=$($queue.Count) $(Get-Date -Format o)" | Out-File $WLOG -Append
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
$clean = ($finalized.Values | Where-Object { $_ -eq "clean" } | Measure-Object).Count
"PARALLEL+RETRY ALL DONE clean=$clean/$total $(Get-Date -Format o)" | Out-File $WLOG -Append

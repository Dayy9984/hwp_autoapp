# 코퍼스 전수 평가 — 폼당 독립 프로세스 + 하드 타임아웃(트리 kill). FIRST-PAGE only.
# 어떤 폼이 codex/HWP-COM서 행해도 7분 후 강제종료하고 다음으로 넘어감 → 전체 안 멈춤.
# detached 로 실행(tool 10분 cap 무관). 결과: eval-results/eval-corpus_fp1.jsonl (폼별 append).
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$env:CLOUDFLARE_ACCOUNT_ID = "a7651f53f22f1c6ec870c4c041a13235"
$PYDIR  = "C:\Users\Public\projects\insertyai\python"
$OUTDIR = "C:\Users\Public\projects\insertyai\eval-results"
$TAG    = "corpus_fp1"
$WLOG   = "$OUTDIR\$TAG.wrapper.log"
$PERFORM_TIMEOUT_S = 420   # 폼당 7분 하드캡 (first-page only → 빠름)
$MAX_PAGES = 1             # 채우기+검증 모두 1페이지로 스코프

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

function Kill-Hwp {
  Get-Process hwp -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*auto_eval*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}

"WRAPPER START $(Get-Date -Format o)" | Out-File $WLOG
$i = 0
foreach ($h in $hashes) {
  $i++; $short = $h.Substring(0, 12)
  "[$i/9] $short START $(Get-Date -Format o)" | Out-File $WLOG -Append
  Kill-Hwp
  $flog = "$OUTDIR\formlog_$short.txt"
  $args = @("run","python","-m","eval.auto_eval","--r2-keys","hwp/$h.hwp","--mode","full","--out","../eval-results","--tag",$TAG,"--max-apply-ops","400","--max-pages",$MAX_PAGES)
  $p = Start-Process uv -ArgumentList $args -WorkingDirectory $PYDIR -PassThru -NoNewWindow -RedirectStandardOutput $flog -RedirectStandardError "$flog.err"
  $exited = $p.WaitForExit($PERFORM_TIMEOUT_S * 1000)
  if (-not $exited) {
    try { $p.Kill($true) } catch {}
    "[$i/9] $short TIMEOUT (killed) $(Get-Date -Format o)" | Out-File $WLOG -Append
  } else {
    "[$i/9] $short EXIT=$($p.ExitCode) $(Get-Date -Format o)" | Out-File $WLOG -Append
  }
  Kill-Hwp
}
"WRAPPER ALL DONE $(Get-Date -Format o)" | Out-File $WLOG -Append
Kill-Hwp
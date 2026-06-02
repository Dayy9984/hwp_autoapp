# E2E staging 검증 assertion — 한 명령으로 캡처/조인/verify 적재를 PASS/FAIL 판정.
# 사용: 앱을 INSERTY_TRACE_BASE=staging 로 1회 구동(편집+동의+accept/reject) 후 실행.
#   powershell -File scripts/verify-e2e-staging.ps1
# 사전: wrangler 로그인(qudtnrh), Insertyai 계정 접근.

$ErrorActionPreference = "Continue"   # native(npx) stderr 경고가 중단시키지 않게
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$env:CLOUDFLARE_ACCOUNT_ID = "a7651f53f22f1c6ec870c4c041a13235"
$DB = "inserty-events-staging"
$pass = 0; $fail = 0

function Q($sql) {
  $raw = npx --yes wrangler d1 execute $DB --remote --json --command $sql 2>$null | Out-String
  $m = [regex]::Match($raw, '"n":\s*(\d+)')
  if ($m.Success) { return [int]$m.Groups[1].Value } else { return -1 }
}
function Check($name, $n, $needPositive = $true) {
  $ok = if ($needPositive) { $n -gt 0 } else { $n -ge 0 }
  if ($ok) { Write-Host ("  [PASS] {0} = {1}" -f $name, $n) -ForegroundColor Green; $script:pass++ }
  else     { Write-Host ("  [FAIL] {0} = {1} (데이터 없음 — 아직 E2E 미실행?)" -f $name, $n) -ForegroundColor Yellow; $script:fail++ }
}

Write-Host "=== E2E staging 검증 ($DB) ===" -ForegroundColor Cyan

# P2: 캡처
Check "block_traces (applied!=0)         " (Q "SELECT COUNT(*) n FROM block_traces WHERE applied != 0")
Check "block_traces (request_id 존재)     " (Q "SELECT COUNT(*) n FROM block_traces WHERE request_id IS NOT NULL")
Check "response_decisions (accept/reject) " (Q "SELECT COUNT(*) n FROM response_decisions")
Check "consents (동의)                    " (Q "SELECT COUNT(*) n FROM consents WHERE consented_at IS NOT NULL")

# P3: 검증 적재
Check "verify_results                     " (Q "SELECT COUNT(*) n FROM verify_results")
Check "verify_items                       " (Q "SELECT COUNT(*) n FROM verify_items")

# 조인키 무결성: verify_results.request_id 가 block_traces.request_id 와 매칭되나
Check "JOIN verify<->block (request_id)   " (Q "SELECT COUNT(*) n FROM verify_results v JOIN block_traces b ON v.request_id = b.request_id WHERE v.request_id IS NOT NULL")

# 렌더 이미지 prefix 기록(동의 시)
Check "verify_results.render_r2_prefix    " (Q "SELECT COUNT(*) n FROM verify_results WHERE render_r2_prefix IS NOT NULL") $false

Write-Host ""
Write-Host ("결과: PASS={0} FAIL={1}" -f $pass, $fail) -ForegroundColor Cyan
if ($fail -gt 0) {
  Write-Host "FAIL 있으면: (1) 앱을 INSERTY_TRACE_BASE=staging 로 구동했나 (2) 동의 ON (3) 실제 편집+accept/reject 했나 (4) 'wrangler tail --env staging' 로 verify 에러 확인" -ForegroundColor Yellow
  exit 1
}
Write-Host "ALL PASS — 캡처+검증+조인 정상." -ForegroundColor Green

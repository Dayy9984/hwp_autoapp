Get-Process electron -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process esbuild -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
$nodes = Get-Process node -ErrorAction SilentlyContinue
foreach ($p in $nodes) {
  try {
    $cmd = (Get-CimInstance Win32_Process -Filter ("ProcessId=" + $p.Id)).CommandLine
    if ($cmd -match 'vite|electron|esbuild') {
      Stop-Process -Id $p.Id -Force
    }
  } catch {}
}
Write-Host "killed"

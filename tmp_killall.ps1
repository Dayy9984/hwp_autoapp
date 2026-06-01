$names = @('electron', 'esbuild', 'Inserty AI', 'inserty_python', 'inserty_agent', 'hwp_window_monitor', 'inserty_file_reader')
foreach ($n in $names) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800
$nodes = Get-Process node -ErrorAction SilentlyContinue
foreach ($p in $nodes) {
  try {
    $cmd = (Get-CimInstance Win32_Process -Filter ("ProcessId=" + $p.Id)).CommandLine
    if ($cmd -match 'vite|electron|esbuild|InsertyAI-private') {
      Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
  } catch {}
}
Write-Host "killed all"

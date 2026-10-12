param([string]$what = "all")
# Stops only the alpha-test processes ("bix server", "bix device", the SMTP sink); never "bix dev" (the user's instance).
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine }
foreach ($p in $procs) {
  $cl = $p.CommandLine.Trim()
  $match = $false
  if ($what -in "all", "smtp" -and $cl -like "*smtp_sink.py*") { $match = $true }
  if ($what -in "all", "server" -and ($cl -like '*bix.exe" server' -or $cl -like '*bix.exe server')) { $match = $true }
  if ($what -in "all", "device" -and ($cl -like '*bix.exe" device' -or $cl -like '*bix.exe device')) { $match = $true }
  if ($match) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; "stopped $($p.ProcessId)" }
}

param([string]$what = "all")
# Starts the isolated alpha-test instance: server :8720, device panel :8721, SMTP sink :8025.
# State lives in .\work (see ..\PROJECT.md for the one-time setup).
$AT = Join-Path $PSScriptRoot "work"
$sw = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = Join-Path $sw ".venv\Scripts\python.exe"
$bix = Join-Path $sw ".venv\Scripts\bix.exe"
$env:PYTHONUTF8 = "1"
if ($what -in "all", "smtp") {
  Start-Process -FilePath $py -ArgumentList "$PSScriptRoot\smtp_sink.py", "$AT\mail" -WindowStyle Hidden -RedirectStandardError "$AT\smtp.err"
}
if ($what -in "all", "server") {
  $env:BIX_SERVER_CONFIG = "$AT\server\server.toml"
  Start-Process -FilePath $bix -ArgumentList "server" -WindowStyle Hidden -RedirectStandardError "$AT\server.err" -RedirectStandardOutput "$AT\server.out"
}
if ($what -in "all", "device") {
  $env:BIX_DEVICE_CONFIG = "$AT\device\device.toml"
  Start-Process -FilePath $bix -ArgumentList "device" -WindowStyle Hidden -RedirectStandardError "$AT\device.err" -RedirectStandardOutput "$AT\device.out"
}
if ($what -in "deviceB") {
  # second device (panel :8722) for two-room tests
  $env:BIX_DEVICE_CONFIG = "$AT\deviceB\device.toml"
  Start-Process -FilePath $bix -ArgumentList "device" -WindowStyle Hidden -RedirectStandardError "$AT\deviceB.err" -RedirectStandardOutput "$AT\deviceB.out"
}

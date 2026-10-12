# Bix on a Windows laptop: server + device (room panel) in one window.
#   powershell -ExecutionPolicy Bypass -File deploy\windows\start-bix.ps1
#   ... -Install    also adds a shortcut that starts Bix at logon
param([switch]$Install, [switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$venv = Join-Path $root ".venv"

if (-not (Test-Path (Join-Path $venv "Scripts\bix.exe"))) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "Installing uv (Python package manager)..."
        winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    }
    Push-Location $root
    uv venv --python 3.12 $venv
    uv pip install --python $venv -e ".[local-stt]"
    Pop-Location
}

if ($Install) {
    $startup = [Environment]::GetFolderPath("Startup")
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup "Bix.lnk"))
    $lnk.TargetPath = "powershell.exe"
    $lnk.Arguments = "-WindowStyle Minimized -ExecutionPolicy Bypass -File `"$PSCommandPath`" -NoBrowser"
    $lnk.WorkingDirectory = "$root"
    $lnk.Save()
    Write-Host "Bix will start at logon (shortcut in $startup)."
}

$args = @("dev")
if ($NoBrowser) { $args += "--no-browser" }
& (Join-Path $venv "Scripts\bix.exe") @args

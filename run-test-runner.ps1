# PowerShell — start Test Runner UI (Vite + API)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Error "pnpm not found. Install: npm install -g pnpm"
}

$VenvPython = Join-Path $Root "voice\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Voice venv missing. Run .\setup.ps1 first."
}

& $VenvPython -m pip install -r (Join-Path $Root "ui_test\requirements.txt") -q 2>$null
& $VenvPython -m playwright install chromium 2>$null

Push-Location $Root
try {
    if (-not (Test-Path "node_modules")) {
        pnpm install
    }
    pnpm dev
}
finally {
    Pop-Location
}

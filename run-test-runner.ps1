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

$PreloadScript = Join-Path $Root "scripts\preload-ollama.ps1"
if (Test-Path $PreloadScript) {
    & $PreloadScript
}

$StopPortsScript = Join-Path $Root "scripts\stop-test-runner-ports.ps1"
if (Test-Path $StopPortsScript) {
    & $StopPortsScript
}

Push-Location $Root
try {
    pnpm install
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pnpm install failed. Fix dependency errors above, then retry."
    }
    pnpm dev
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

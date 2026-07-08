# Start ComfyUI backend (full node UI on :8188)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ComfyDir = Join-Path $Root "ComfyUI"

if (-not (Test-Path $Python)) {
    Write-Error "Run .\setup.ps1 first."
}
if (-not (Test-Path (Join-Path $ComfyDir "main.py"))) {
    Write-Error "ComfyUI not found. Run .\setup.ps1 first."
}

Write-Host "ComfyUI: http://127.0.0.1:8188" -ForegroundColor Cyan
Push-Location $ComfyDir
try {
    & $Python main.py --listen 127.0.0.1 --port 8188
}
finally {
    Pop-Location
}

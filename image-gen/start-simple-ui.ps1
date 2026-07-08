# Start simple Gradio UI (:7860) — requires ComfyUI running

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Run .\setup.ps1 first."
}

Write-Host "Simple UI: http://127.0.0.1:7860" -ForegroundColor Cyan
Push-Location (Join-Path $Root "simple_ui")
try {
    & $Python app.py
}
finally {
    Pop-Location
}

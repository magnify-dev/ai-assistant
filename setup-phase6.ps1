# Phase 6: PC control + coding integrations
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$HelixDir = Join-Path $Root "helix-pilot"

Write-Host "=== Installing uv (Python runner for Helix-Pilot) ===" -ForegroundColor Cyan
python -m pip install uv

Write-Host "=== Cloning Helix-Pilot (desktop control MCP) ===" -ForegroundColor Cyan
if (-not (Test-Path $HelixDir)) {
    git clone https://github.com/tsunamayo7/helix-pilot.git $HelixDir
}

Set-Location $HelixDir
uv sync

Write-Host "=== Pull Ollama vision model for screen control ===" -ForegroundColor Cyan
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (Test-Path $OllamaExe) {
    & $OllamaExe pull gemma3:4b
}

Write-Host ""
Write-Host "Phase 6 setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "YOU still need to do these 3 things:" -ForegroundColor Yellow
Write-Host "1. Install Continue extension in Cursor (search 'Continue' in Extensions)"
Write-Host "2. Restart Cursor so MCP picks up helix-pilot (Settings -> MCP)"
Write-Host "3. Restart voice assistant: cd $Root; .\stop-all.ps1; .\start-all.ps1"
Write-Host ""
Write-Host "Then try voice commands like:" -ForegroundColor Cyan
Write-Host '  "Hey Jarvis, open Cursor"'
Write-Host '  "Hey Jarvis, list files in my Documents folder"'
Write-Host '  "Hey Jarvis, create a file test.txt that says hello"'

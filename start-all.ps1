# Start the full local AI stack (Ollama preload, Open WebUI, voice assistant).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OllamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$OllamaApp = Join-Path $OllamaDir "ollama app.exe"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"

function Test-OllamaApi {
    try {
        Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-OllamaIfNeeded {
    if (Test-OllamaApi) {
        Write-Host "Ollama is already running." -ForegroundColor Green
        return
    }

    Write-Host "Ollama is not running. Starting it..." -ForegroundColor Yellow

    if (-not (Test-Path $OllamaExe)) {
        Write-Error "Ollama not found. Install from https://ollama.com then run this again."
    }

    $ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if (-not $ollamaRunning) {
        # Hidden serve is the most reliable way to start from a script.
        Start-Process -WindowStyle Hidden -FilePath $OllamaExe -ArgumentList "serve"
        Start-Sleep -Seconds 2
    }

    if (Test-Path $OllamaApp) {
        $appRunning = Get-Process -Name "ollama app" -ErrorAction SilentlyContinue
        if (-not $appRunning) {
            Start-Process -FilePath $OllamaApp
        }
    }

    Write-Host "Waiting for Ollama API (up to 120 seconds)..." -ForegroundColor Cyan
    $startedAt = Get-Date
    $deadline = $startedAt.AddSeconds(120)
    $dots = 0
    $retriedServe = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-OllamaApi) {
            Write-Host ""
            Write-Host "Ollama is ready." -ForegroundColor Green
            return
        }

        if (-not $retriedServe -and ((Get-Date) - $startedAt).TotalSeconds -ge 20) {
            $serveRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
            if (-not $serveRunning) {
                Start-Process -WindowStyle Hidden -FilePath $OllamaExe -ArgumentList "serve"
            }
            $retriedServe = $true
        }

        $dots = ($dots + 1) % 4
        Write-Host ("`r  still waiting" + ("." * $dots).PadRight(3)) -NoNewline
        Start-Sleep -Seconds 3
    }

    Write-Host ""
    Write-Error @"
Ollama did not respond on http://127.0.0.1:11434 within 2 minutes.

Try this manually:
  1. Open Start menu -> search "Ollama" -> open the Ollama app
  2. Wait until the llama icon appears in the system tray (bottom-right)
  3. Run start-all again

If it still fails, reboot once (first GPU driver init can be slow).
"@
}

Start-OllamaIfNeeded

Write-Host "Preloading model into GPU memory..." -ForegroundColor Cyan
& (Join-Path $Root "scripts\preload-ollama.ps1")

Write-Host "Starting Open WebUI on http://localhost:8080 ..." -ForegroundColor Cyan
$webuiExe = Join-Path $Root "open-webui\.venv\Scripts\open-webui.exe"
if (Test-Path $webuiExe) {
    $existingWebUi = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if (-not $existingWebUi) {
        Start-Process -WindowStyle Minimized -FilePath $webuiExe -ArgumentList "serve","--host","127.0.0.1","--port","8080"
    } else {
        Write-Host "Open WebUI already listening on port 8080." -ForegroundColor Green
    }
} else {
    Write-Warning "Open WebUI not installed yet. Run setup.ps1 first."
}

Start-Sleep -Seconds 3

Write-Host "Starting voice assistant (wake word: hey jarvis)..." -ForegroundColor Cyan
$voicePython = Join-Path $Root "voice\.venv\Scripts\python.exe"
if (-not (Test-Path $voicePython)) {
    Write-Error "Voice assistant not installed. Run setup.ps1 first."
}

# Stop any old voice assistant first (new one uses a lock file too)
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "*ai-assistant\voice*"
} | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1
Start-Process -WindowStyle Minimized -FilePath $voicePython -ArgumentList (Join-Path $Root "voice\assistant.py") -WorkingDirectory (Join-Path $Root "voice")

Write-Host ""
Write-Host "Stack started." -ForegroundColor Green
Write-Host "- Chat UI:  http://localhost:8080" -ForegroundColor White
Write-Host "- Voice:    say 'hey jarvis' near your mic" -ForegroundColor White
Write-Host "- Logs:     $Root\logs\voice-assistant.log" -ForegroundColor White

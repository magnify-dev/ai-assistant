# One-time setup for the local AI assistant stack.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Voice = Join-Path $Root "voice"
$Tts = Join-Path $Root "tts"
$PiperDir = Join-Path $Tts "piper"
$VoiceDir = Join-Path $Tts "voices"
$Logs = Join-Path $Root "logs"
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"

New-Item -ItemType Directory -Force -Path $Voice, $Tts, $PiperDir, $VoiceDir, $Logs | Out-Null

Write-Host "=== Step 1: Python virtual environment ===" -ForegroundColor Cyan
$venv = Join-Path $Voice ".venv"
if (-not (Test-Path $venv)) {
    python -m venv $venv
}
& (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $venv "Scripts\pip.exe") install -r (Join-Path $Voice "requirements.txt")

Write-Host "=== Step 2: Download Piper TTS ===" -ForegroundColor Cyan
$piperZip = Join-Path $env:TEMP "piper_windows_amd64.zip"
$piperUrl = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
if (-not (Test-Path (Join-Path $PiperDir "piper.exe"))) {
    Invoke-WebRequest -Uri $piperUrl -OutFile $piperZip
    Expand-Archive -Path $piperZip -DestinationPath $PiperDir -Force
    Get-ChildItem -Path $PiperDir -Recurse -Filter "piper.exe" | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $PiperDir "piper.exe") -Force
    }
}

Write-Host "=== Step 3: Download Piper voice ===" -ForegroundColor Cyan
$voiceOnnx = Join-Path $VoiceDir "en_US-lessac-medium.onnx"
$voiceJson = Join-Path $VoiceDir "en_US-lessac-medium.onnx.json"
if (-not (Test-Path $voiceOnnx)) {
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
    Invoke-WebRequest -Uri "$base/en_US-lessac-medium.onnx" -OutFile $voiceOnnx
    Invoke-WebRequest -Uri "$base/en_US-lessac-medium.onnx.json" -OutFile $voiceJson
}

Write-Host "=== Step 4: Pull Ollama models ===" -ForegroundColor Cyan
if (-not (Test-Path $OllamaExe)) {
    Write-Warning "Ollama not found at $OllamaExe. Install from https://ollama.com first."
} else {
    & $OllamaExe pull qwen2.5-coder:14b
    & $OllamaExe pull qwen2.5:14b
    & $OllamaExe pull qwen3:14b
    & $OllamaExe pull qwen3:30b
}

Write-Host "=== Step 4b: Download wake word models ===" -ForegroundColor Cyan
& (Join-Path $venv "Scripts\python.exe") -c "import openwakeword; openwakeword.utils.download_models(); print('wake models ok')"

Write-Host "=== Step 4c: Pre-download Whisper speech model ===" -ForegroundColor Cyan
& (Join-Path $venv "Scripts\python.exe") -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('whisper ok')"

Write-Host "=== Step 5: Open WebUI (optional, may take several minutes) ===" -ForegroundColor Cyan
$webuiVenv = Join-Path $Root "open-webui\.venv"
if (-not (Test-Path $webuiVenv)) {
    python -m venv $webuiVenv
}
& (Join-Path $webuiVenv "Scripts\pip.exe") install --upgrade pip
& (Join-Path $webuiVenv "Scripts\pip.exe") install open-webui

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: run start-all.ps1" -ForegroundColor Yellow

# Preload the main Ollama model so it stays in VRAM.
$ErrorActionPreference = "SilentlyContinue"
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
$Model = "qwen2.5-coder:14b"

if (-not (Test-Path $OllamaExe)) { exit 1 }

$body = @{
    model = $Model
    messages = @(@{ role = "user"; content = "ready" })
    stream = $false
    keep_alive = -1
} | ConvertTo-Json -Depth 5

try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/chat" -Body $body -ContentType "application/json" -TimeoutSec 300 | Out-Null
} catch {
    # Model may load on first real request instead.
}

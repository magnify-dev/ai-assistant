# Preload the UI test Ollama model so it stays in VRAM.
param(
    [string]$Model = ""
)

$ErrorActionPreference = "SilentlyContinue"
$OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"

if (-not $Model) {
    $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    $CfgPath = Join-Path $Root "ui_test\config.yaml"
    if (Test-Path $CfgPath) {
        $line = Get-Content $CfgPath | Where-Object { $_ -match '^\s*model:\s*' } | Select-Object -First 1
        if ($line -match 'model:\s*["'']?([^"'']+)') {
            $Model = $Matches[1].Trim()
        }
    }
}
if (-not $Model) { $Model = "qwen2.5-coder:14b" }

if (-not (Test-Path $OllamaExe)) { exit 1 }

$body = @{
    model = $Model
    messages = @(@{ role = "user"; content = "ready" })
    stream = $false
    keep_alive = -1
} | ConvertTo-Json -Depth 5

try {
    Write-Host "Preloading Ollama model: $Model"
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/chat" -Body $body -ContentType "application/json" -TimeoutSec 600 | Out-Null
    Write-Host "Ollama model ready: $Model"
} catch {
    Write-Warning "Ollama preload skipped (model loads on first test run): $_"
}

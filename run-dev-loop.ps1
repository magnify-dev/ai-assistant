# PowerShell helper for dev_loop (Phase 1 — file-based handoff to Cursor).

param(
    [string]$Project = "",
    [string]$TestCmd = "",
    [string]$Note = "",
    [switch]$Demo,
    [switch]$SkipTests,
    [switch]$NoOllama
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root "voice\.venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Voice venv not found at $VenvPython. Run .\setup.ps1 first."
}

$DevLoopReqs = Join-Path $Root "dev_loop\requirements.txt"
if (Test-Path $DevLoopReqs) {
    & $VenvPython -m pip install -r $DevLoopReqs -q 2>$null
}

$Args = @("-m", "dev_loop")
if ($Demo) { $Args += "--demo" }
if ($SkipTests) { $Args += "--skip-tests" }
if ($NoOllama) { $Args += "--no-ollama" }
if ($Project) { $Args += @("--project", $Project) }
if ($TestCmd) { $Args += @("--test-cmd", $TestCmd) }
if ($Note) { $Args += @("--note", $Note) }

Push-Location $Root
try {
    & $VenvPython @Args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

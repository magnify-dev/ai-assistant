# PowerShell helper for ui_test loop (Railway → Playwright → REPORT.md).

param(
    [string]$Project = "",
    [string]$Task = "",
    [string]$TaskFile = "",
    [switch]$Push,
    [switch]$SkipDeploy,
    [switch]$SkipStructure,
    [switch]$SkipUi,
    [switch]$NoStructureBlock,
    [switch]$NoOllama,
    [switch]$Headed,
    [switch]$Serve,
    [switch]$InitProject
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root "voice\.venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Voice venv not found at $VenvPython. Run .\setup.ps1 first."
}

$UiTestReqs = Join-Path $Root "ui_test\requirements.txt"
if (Test-Path $UiTestReqs) {
    & $VenvPython -m pip install -r $UiTestReqs -q 2>$null
    & $VenvPython -m playwright install chromium 2>$null
}

$Args = @("-m", "ui_test")
if ($Serve) {
    Write-Host "Use .\run-test-runner.ps1 for the React UI instead of -Serve."
    $Serve = $false
}
if ($Push) { $Args += "--push" }
if ($SkipDeploy) { $Args += "--skip-deploy" }
if ($SkipStructure) { $Args += "--skip-structure" }
if ($SkipUi) { $Args += "--skip-ui" }
if ($NoStructureBlock) { $Args += "--no-structure-block" }
if ($NoOllama) { $Args += "--no-ollama" }
if ($Headed) { $Args += "--headed" }
if ($InitProject) { $Args += "--init-project" }
if ($Project) { $Args += @("--project", $Project) }
if ($Task) { $Args += @("--task", $Task) }
if ($TaskFile) { $Args += @("--task-file", $TaskFile) }

Push-Location $Root
try {
    & $VenvPython @Args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

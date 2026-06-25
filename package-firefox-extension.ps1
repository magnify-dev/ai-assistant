# Package the Jarvis Firefox extension as an XPI.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExtensionDir = Join-Path $Root "firefox-extension"
$OutputDir = Join-Path $Root "dist"
$Output = Join-Path $OutputDir "jarvis-page-bridge.xpi"

if (-not (Test-Path $ExtensionDir)) {
    Write-Error "Extension folder not found: $ExtensionDir"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
if (Test-Path $Output) {
    Remove-Item $Output -Force
}

$tmpZip = Join-Path $OutputDir "jarvis-page-bridge.zip"
if (Test-Path $tmpZip) {
    Remove-Item $tmpZip -Force
}

Compress-Archive -Path (Join-Path $ExtensionDir "*") -DestinationPath $tmpZip -Force
Rename-Item -Path $tmpZip -NewName "jarvis-page-bridge.xpi"

Write-Host "Packaged Firefox extension:" -ForegroundColor Green
Write-Host "  $Output"
Write-Host ""
Write-Host "Normal Firefox requires signed extensions for permanent install." -ForegroundColor Yellow
Write-Host "Use this XPI for AMO unlisted signing, or use Firefox Developer Edition/ESR with unsigned extensions enabled."

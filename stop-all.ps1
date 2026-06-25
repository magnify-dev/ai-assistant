# Stop background Jarvis services started by start-all.ps1.
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "*ai-assistant*"
} | Stop-Process -Force

Start-Sleep -Seconds 2
Write-Host "Stopped ai-assistant Python processes." -ForegroundColor Green

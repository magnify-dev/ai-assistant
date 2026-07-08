# Start both ComfyUI + Simple UI in separate windows

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start-comfyui.ps1")
Start-Sleep -Seconds 8
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start-simple-ui.ps1")
Write-Host "Started ComfyUI and Simple UI in new windows."

# Reinstall PyTorch with CUDA 12.8 for RTX 5080 (Blackwell sm_120)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pip = Join-Path $Root ".venv\Scripts\pip.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Pip)) {
    Write-Error "Run .\setup.ps1 first."
}

Write-Host "Reinstalling PyTorch cu128 for RTX 5080..."
& $Pip uninstall -y torch torchvision torchaudio 2>$null
& $Pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 -q
& $Python (Join-Path $Root "scripts\check_gpu.py")

# One-time setup: ComfyUI + Pony XL + Simple UI

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"
$ComfyDir = Join-Path $Root "ComfyUI"
$CheckpointDir = Join-Path $ComfyDir "models\checkpoints"

Write-Host "=== Image Gen Setup ===" -ForegroundColor Cyan

# 1. Python venv
if (-not (Test-Path $Python)) {
    Write-Host "Creating venv..."
    python -m venv $Venv
}

Write-Host "Upgrading pip..."
& $Python -m pip install --upgrade pip wheel -q

# 2. PyTorch with CUDA 12.8 (required for RTX 5080 / Blackwell sm_120)
Write-Host "Installing PyTorch (CUDA 12.8 for RTX 5080)..."
& $Pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 -q

# 3. Clone ComfyUI
if (-not (Test-Path (Join-Path $ComfyDir "main.py"))) {
    Write-Host "Cloning ComfyUI..."
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git $ComfyDir
}

Write-Host "Installing ComfyUI requirements..."
& $Pip install -r (Join-Path $ComfyDir "requirements.txt") -q

Write-Host "Installing simple UI requirements..."
& $Pip install -r (Join-Path $Root "requirements.txt") -q

# 4. Model dirs
New-Item -ItemType Directory -Force -Path $CheckpointDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "output") | Out-Null

$ModelFile = "ponyDiffusionV6XL_v6StartWithThisOne.safetensors"
$ModelPath = Join-Path $CheckpointDir $ModelFile

if (-not (Test-Path $ModelPath)) {
    Write-Host "Downloading Pony Diffusion V6 XL (~7 GB) - this takes a while..." -ForegroundColor Yellow
    & $Python (Join-Path $Root "scripts\download_model.py")
} else {
    Write-Host "Model already present: $ModelPath"
}

# Verify CUDA
Write-Host "Checking GPU..."
& $Python (Join-Path $Root "scripts\check_gpu.py")

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  1. .\start-comfyui.ps1"
Write-Host "  2. .\start-simple-ui.ps1"
Write-Host "  3. Open http://127.0.0.1:7860"

# Local Image Generation (ComfyUI + Pony XL)

Private local anime image generation on your RTX 5080. No cloud filters — runs entirely on your machine.

## What's included

| Piece | URL | Purpose |
|-------|-----|---------|
| **ComfyUI** | http://127.0.0.1:8188 | Full node-based UI (power user) |
| **Simple UI** | http://127.0.0.1:7860 | Prompt box + Generate (Gradio) |
| **Model** | Pony Diffusion V6 XL | Anime-focused SDXL checkpoint (~7 GB) |

## First-time setup (downloads everything)

```powershell
cd C:\Users\marce\Documents\Programming\ai-assistant\image-gen
.\setup.ps1
```

This will:
1. Clone ComfyUI
2. Create a Python venv with CUDA PyTorch
3. Download **Pony Diffusion V6 XL** from Hugging Face (~7 GB)
4. Install Gradio simple UI deps

**Takes a while** on first run (GPU drivers + model download).

If you have an **RTX 5080/5090** and see a `sm_120` PyTorch warning, run:

```powershell
.\fix-gpu.ps1
```

## Daily use

**Terminal 1** — ComfyUI backend (leave running):

```powershell
.\start-comfyui.ps1
```

**Terminal 2** — Simple UI:

```powershell
.\start-simple-ui.ps1
```

Open http://127.0.0.1:7860 — type a prompt and Generate.

Or use the full ComfyUI canvas at http://127.0.0.1:8188.

## Prompt tips (Pony XL)

With **quality tags** enabled (default), you only write the subject/tags:

```
1girl, solo, long silver hair, red eyes, school uniform, smiling, upper body
```

Common tags: `source_anime`, `1girl`, `solo`, character/scene tags from Danbooru-style tag lists.

## Output

Images are saved to `image-gen/output/` and ComfyUI's `ComfyUI/output/`.

## Add more models

Drop `.safetensors` files into:

```
image-gen/ComfyUI/models/checkpoints/
```

Then pick the checkpoint in ComfyUI or edit `config.yaml` → `model.checkpoint`.

## VRAM

RTX 5080 16 GB — 1024×1024 is comfortable. Try 1280×1280 if you want; reduce if OOM.

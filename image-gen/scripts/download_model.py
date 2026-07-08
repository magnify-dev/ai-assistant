"""Download Pony Diffusion V6 XL checkpoint into ComfyUI/models/checkpoints."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DIR = ROOT / "ComfyUI" / "models" / "checkpoints"
MODEL_FILE = "ponyDiffusionV6XL_v6StartWithThisOne.safetensors"
REPO = "LyliaEngine/Pony_Diffusion_V6_XL"


def main() -> int:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    dest = CHECKPOINT_DIR / MODEL_FILE
    if dest.is_file() and dest.stat().st_size > 1_000_000_000:
        print(f"Already downloaded: {dest}")
        return 0

    from huggingface_hub import hf_hub_download

    print(f"Downloading {MODEL_FILE} from {REPO} (~7 GB)...")
    path = hf_hub_download(
        repo_id=REPO,
        filename=MODEL_FILE,
        local_dir=str(CHECKPOINT_DIR),
    )
    print(f"Done: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

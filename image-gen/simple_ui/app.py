"""Simple Gradio UI — prompt box + generate (talks to ComfyUI API)."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import gradio as gr
import requests
import yaml

from comfy_client import build_txt2img_workflow

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def comfy_base(cfg: dict) -> str:
    c = cfg["comfyui"]
    return f"http://{c['host']}:{c['port']}"


def comfy_ready(cfg: dict) -> bool:
    try:
        r = requests.get(f"{comfy_base(cfg)}/system_stats", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def queue_prompt(cfg: dict, workflow: dict) -> str:
    client_id = str(uuid.uuid4())
    payload = {"prompt": workflow, "client_id": client_id}
    r = requests.post(f"{comfy_base(cfg)}/prompt", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_for_image(cfg: dict, prompt_id: str, timeout_sec: float = 600) -> tuple[str, str]:
    base = comfy_base(cfg)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = requests.get(f"{base}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        history = r.json()
        if prompt_id not in history:
            time.sleep(1)
            continue
        outputs = history[prompt_id].get("outputs") or {}
        for node_out in outputs.values():
            images = node_out.get("images") or []
            if not images:
                continue
            img = images[0]
            params = {
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            }
            view = requests.get(f"{base}/view", params=params, timeout=60)
            view.raise_for_status()
            out_dir = OUTPUT_DIR
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{prompt_id}_{img['filename']}"
            dest.write_bytes(view.content)
            return str(dest.resolve()), img["filename"]
        time.sleep(1)
    raise TimeoutError("ComfyUI did not finish in time")


def generate(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: int,
    use_prefix: bool,
) -> tuple[str | None, str]:
    cfg = load_config()
    if not comfy_ready(cfg):
        return None, (
            "ComfyUI is not running. Start it first:\n"
            "  .\\start-comfyui.ps1\n"
            "Then reload this page."
        )

    defaults = cfg.get("defaults") or {}
    model = (cfg.get("model") or {}).get("checkpoint", "")
    full_prompt = prompt.strip()
    if use_prefix:
        prefix = defaults.get("prompt_prefix", "")
        full_prompt = prefix + full_prompt

    neg = negative.strip() or defaults.get("negative_prompt", "")
    workflow = build_txt2img_workflow(
        checkpoint=model,
        positive=full_prompt,
        negative=neg,
        width=int(width),
        height=int(height),
        steps=int(steps),
        cfg=float(cfg_scale),
        seed=int(seed) if seed >= 0 else None,
        sampler=defaults.get("sampler", "euler_ancestral"),
        scheduler=defaults.get("scheduler", "normal"),
    )

    try:
        prompt_id = queue_prompt(cfg, workflow)
        path, name = wait_for_image(cfg, prompt_id)
        return path, f"Saved: {path}\nComfyUI file: {name}\nPrompt id: {prompt_id}"
    except Exception as exc:
        return None, f"Error: {exc}"


def main() -> None:
    cfg = load_config()
    defaults = cfg.get("defaults") or {}
    ui = cfg.get("simple_ui") or {}

    with gr.Blocks(title="Anime Image Gen") as demo:
        gr.Markdown(
            "# Local anime image gen\n"
            "**Backend:** ComfyUI + Pony Diffusion V6 XL (uncensored, local).\n\n"
            "1. Run `start-comfyui.ps1` (keep it open)\n"
            "2. Generate here, or use full ComfyUI at "
            f"http://{cfg['comfyui']['host']}:{cfg['comfyui']['port']}"
        )
        with gr.Row():
            with gr.Column():
                prompt = gr.Textbox(
                    label="Prompt",
                    lines=4,
                    placeholder="1girl, solo, long hair, ...",
                )
                negative = gr.Textbox(
                    label="Negative prompt (optional)",
                    lines=2,
                    value=defaults.get("negative_prompt", ""),
                )
                use_prefix = gr.Checkbox(
                    label="Add Pony quality tags (score_9, source_anime, …)",
                    value=True,
                )
                with gr.Row():
                    width = gr.Slider(512, 1536, value=defaults.get("width", 1024), step=64, label="Width")
                    height = gr.Slider(512, 1536, value=defaults.get("height", 1024), step=64, label="Height")
                with gr.Row():
                    steps = gr.Slider(10, 50, value=defaults.get("steps", 28), step=1, label="Steps")
                    cfg_scale = gr.Slider(1, 15, value=defaults.get("cfg", 7), step=0.5, label="CFG")
                seed = gr.Number(label="Seed (-1 = random)", value=-1, precision=0)
                btn = gr.Button("Generate", variant="primary")
            with gr.Column():
                image_out = gr.Image(label="Output", type="filepath")
                status = gr.Textbox(label="Status", lines=6)

        btn.click(
            generate,
            inputs=[prompt, negative, width, height, steps, cfg_scale, seed, use_prefix],
            outputs=[image_out, status],
        )

    demo.launch(
        server_name=ui.get("host", "127.0.0.1"),
        server_port=int(ui.get("port", 7860)),
        show_error=True,
        theme=gr.themes.Soft(),
        allowed_paths=[str(ROOT), str(OUTPUT_DIR)],
    )


if __name__ == "__main__":
    main()

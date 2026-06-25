#!/usr/bin/env python3
"""Local voice assistant: wake word -> STT -> Ollama (+ tools) -> TTS."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import requests
import yaml

from log_util import setup_logging, ui
from session import acquire_single_instance_lock, run_wake_word_loop
from speech import preload_tts, preload_whisper
from tools import start_browser_bridge, start_configured_browser_control

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def wait_for_ollama(url: str, timeout_sec: int = 120) -> None:
    import time

    deadline = time.time() + timeout_sec
    next_log = 0.0
    logging.info("Waiting for Ollama at %s", url)
    while time.time() < deadline:
        try:
            resp = requests.get(f"{url}/api/tags", timeout=3)
            if resp.ok:
                logging.info("Ollama is ready at %s", url)
                return
        except requests.RequestException:
            pass
        if time.time() >= next_log:
            logging.info("Still waiting for Ollama at %s", url)
            next_log = time.time() + 10
        time.sleep(2)
    raise RuntimeError(f"Ollama not reachable at {url} after {timeout_sec}s")


def preload_model(url: str, model: str) -> None:
    logging.info("Preloading model %s", model)
    try:
        requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ready"}],
                "stream": False,
                "keep_alive": -1,
            },
            timeout=300,
        ).raise_for_status()
        logging.info("Model %s loaded", model)
    except requests.RequestException as exc:
        logging.warning("Model preload failed (will load on first chat): %s", exc)


def main() -> None:
    acquire_single_instance_lock()
    cfg = load_config()
    setup_logging(cfg, ROOT)
    logging.info("Jarvis startup beginning")
    ui("Starting Jarvis...")
    workspace = cfg.get("tools", {}).get("workspace")
    if workspace:
        os.environ["JARVIS_WORKSPACE"] = workspace
    os.environ["JARVIS_OLLAMA_URL"] = cfg["ollama"]["url"]
    ollama_model = str(cfg["ollama"]["model"])
    os.environ["JARVIS_OLLAMA_MODEL"] = ollama_model
    if cfg.get("ollama", {}).get("resolve_targets", True):
        os.environ["JARVIS_LLM_RESOLVE_TARGETS"] = "1"
    else:
        os.environ["JARVIS_LLM_RESOLVE_TARGETS"] = "0"
    vision_cfg = cfg.get("vision", {})
    vision_model = str(vision_cfg.get("model") or "").strip()
    if vision_model and vision_model != ollama_model:
        os.environ["JARVIS_VISION_MODEL"] = vision_model
        logging.info("Optional vision model override: %s (main model: %s)", vision_model, ollama_model)
    else:
        os.environ.pop("JARVIS_VISION_MODEL", None)
        logging.info("Single Ollama model: %s", ollama_model)
    if vision_cfg.get("max_screenshot_width"):
        os.environ["JARVIS_VISION_MAX_WIDTH"] = str(vision_cfg["max_screenshot_width"])
    browser_cfg = cfg.get("browser", {})
    if browser_cfg.get("debug_port"):
        os.environ["JARVIS_BROWSER_DEBUG_PORT"] = str(browser_cfg["debug_port"])
    if browser_cfg.get("profile_dir"):
        profile_dir = resolve_path(str(browser_cfg["profile_dir"]))
        os.environ["JARVIS_BROWSER_PROFILE_DIR"] = str(profile_dir)
    os.environ["JARVIS_BROWSER_PROVIDER"] = str(browser_cfg.get("provider", "foxmcp"))
    if browser_cfg.get("foxmcp_server_dir"):
        os.environ["JARVIS_FOXMCP_SERVER_DIR"] = str(resolve_path(str(browser_cfg["foxmcp_server_dir"])))
    if browser_cfg.get("foxmcp_websocket_port"):
        os.environ["JARVIS_FOXMCP_WEBSOCKET_PORT"] = str(browser_cfg["foxmcp_websocket_port"])
    if browser_cfg.get("foxmcp_mcp_port"):
        os.environ["JARVIS_FOXMCP_MCP_PORT"] = str(browser_cfg["foxmcp_mcp_port"])
    if browser_cfg.get("auto_connect_firefox") is False:
        os.environ["JARVIS_FOXMCP_AUTO_CONNECT"] = "0"
    if browser_cfg.get("foxmcp_connect_timeout_sec"):
        os.environ["JARVIS_FOXMCP_CONNECT_TIMEOUT"] = str(browser_cfg["foxmcp_connect_timeout_sec"])
    if browser_cfg.get("foxmcp_startup_wait_sec"):
        os.environ["JARVIS_FOXMCP_STARTUP_WAIT"] = str(browser_cfg["foxmcp_startup_wait_sec"])
    if browser_cfg.get("provider") == "jarvis_extension":
        start_browser_bridge(int(browser_cfg.get("bridge_port", 8766)))
    browser_status = start_configured_browser_control(startup=True)
    logging.info("Browser control startup: %s", browser_status)
    if browser_status.startswith("FoxMCP connected"):
        ui(f"Browser: {browser_status}")
    elif "background" in browser_status:
        ui("Browser: connecting Firefox in background")
    elif browser_status == "OK":
        ui("Browser: connected")
    else:
        ui(f"Browser: {browser_status[:140]}")
    github_org = cfg.get("tools", {}).get("github_org")
    if github_org:
        os.environ["JARVIS_GITHUB_ORG"] = github_org
    git_roots = cfg.get("tools", {}).get("git_roots")
    if git_roots:
        os.environ["JARVIS_GIT_ROOTS"] = ";".join(git_roots)
    known_paths = cfg.get("tools", {}).get("known_paths") or {}
    if known_paths:
        import tools as tools_module

        tools_module.KNOWN_PATHS = dict(known_paths)
    ui("Loading AI and speech models...")
    wait_for_ollama(cfg["ollama"]["url"])
    preload_model(cfg["ollama"]["url"], cfg["ollama"]["model"])
    ui(f"Model: {cfg['ollama']['model']}")
    preload_whisper(cfg)
    preload_tts(cfg)
    ui("Jarvis ready.")
    run_wake_word_loop(cfg)


if __name__ == "__main__":
    main()

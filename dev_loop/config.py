from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config() -> dict[str, Any]:
    with _CONFIG_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def ollama_url(config: dict[str, Any]) -> str:
    ollama = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
    return str(
        os.environ.get("DEV_LOOP_OLLAMA_URL")
        or ollama.get("url")
        or "http://127.0.0.1:11434"
    ).rstrip("/")


def ollama_model(config: dict[str, Any]) -> str:
    ollama = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
    return str(
        os.environ.get("DEV_LOOP_OLLAMA_MODEL") or ollama.get("model") or "qwen2.5-coder:14b"
    ).strip()


def output_dir(config: dict[str, Any], project: Path) -> Path:
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    rel = str(output.get("dir") or "dev_loop/.runs/current")
    path = (project / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    return path


def history_dir(config: dict[str, Any], project: Path) -> Path:
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    rel = str(output.get("history_dir") or "dev_loop/.runs/history")
    path = (project / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    return path

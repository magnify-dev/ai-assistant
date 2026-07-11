from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "web_surf" / "config.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def default_config() -> dict[str, Any]:
    file_cfg = _load_yaml(DEFAULT_CONFIG_PATH)
    ollama = file_cfg.get("ollama") if isinstance(file_cfg.get("ollama"), dict) else {}
    research = file_cfg.get("research") if isinstance(file_cfg.get("research"), dict) else {}
    raw_form_values = os.environ.get("WEB_SURF_FORM_VALUES", "")
    # Optional seed values merged before AI form planning.
    try:
        form_values = json.loads(raw_form_values) if raw_form_values else {}
    except json.JSONDecodeError:
        form_values = {}
    if not isinstance(form_values, dict):
        form_values = {}
    return {
        "ollama_url": os.environ.get("WEB_SURF_OLLAMA_URL", ollama.get("url", "http://127.0.0.1:11434")),
        "ollama_model": os.environ.get("WEB_SURF_OLLAMA_MODEL", ollama.get("model", "qwen2.5:14b")),
        "ollama_timeout_sec": float(ollama.get("timeout_sec", 120)),
        "project": os.environ.get(
            "WEB_SURF_PROJECT",
            research.get("project", str(REPO_ROOT)),
        ),
        "max_search_results": int(research.get("max_search_results", 8)),
        "max_pages": int(research.get("max_pages", 5)),
        "fetch_timeout_sec": float(research.get("fetch_timeout_sec", 20)),
        "content_max_chars": int(research.get("content_max_chars", 12000)),
        "use_ollama": bool(research.get("use_ollama", True)),
        "form_values": {str(key): str(value) for key, value in form_values.items() if str(value)},
    }

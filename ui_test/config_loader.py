from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ui_test.project_paths import agent_config_path, agent_dir

_ENGINE_ROOT = Path(__file__).resolve().parents[1]


def engine_root() -> Path:
    return _ENGINE_ROOT


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_engine_config() -> dict[str, Any]:
    return load_yaml(_ENGINE_ROOT / "ui_test" / "config.yaml")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_project_config(project: Path) -> dict[str, Any]:
    return load_yaml(agent_config_path(project))


def merged_config(project: Path) -> dict[str, Any]:
    engine = load_engine_config()
    project_cfg = load_project_config(project)
    return _deep_merge(engine, project_cfg)


def agent_project_dir(project: Path) -> Path:
    return agent_dir(project)


def ui_test_dir(project: Path) -> Path:
    """Deprecated alias — use agent_project_dir()."""
    return agent_dir(project)


def output_dir(config: dict[str, Any], project: Path) -> Path:
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    rel = str(output.get("dir") or ".agent/current")
    return (project / rel).resolve()


def history_dir(config: dict[str, Any], project: Path) -> Path:
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    rel = str(output.get("history_dir") or ".agent/history")
    return (project / rel).resolve()


def artifacts_dir(config: dict[str, Any], project: Path) -> Path:
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    rel = str(output.get("artifacts_dir") or "ui-artifacts")
    return output_dir(config, project) / rel


def ollama_url(config: dict[str, Any]) -> str:
    ollama = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
    return str(os.environ.get("UI_TEST_OLLAMA_URL") or ollama.get("url") or "http://127.0.0.1:11434")


def ollama_model(config: dict[str, Any]) -> str:
    ollama = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
    return str(os.environ.get("UI_TEST_OLLAMA_MODEL") or ollama.get("model") or "qwen2.5-coder:14b")


def default_project(config: dict[str, Any]) -> Path | None:
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    raw = os.environ.get("UI_TEST_PROJECT") or defaults.get("project")
    if not raw or not str(raw).strip():
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = (_ENGINE_ROOT / path).resolve()
    return path

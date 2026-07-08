from __future__ import annotations

import os
from pathlib import Path

from ui_test.project_paths import agent_dir, ENV_FILE, ENV_LOCAL


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def load_project_env(project: Path) -> dict[str, str]:
    """Load `.agent/.env` and `.agent/.env.local` (later overrides earlier)."""
    base = agent_dir(project)
    merged: dict[str, str] = {}
    for name in (ENV_FILE, ENV_LOCAL):
        merged.update(_parse_env_file(base / name))
    return merged


def apply_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        if key not in os.environ:
            os.environ[key] = value


def require_keys(env: dict[str, str], keys: list[str]) -> list[str]:
    missing: list[str] = []
    for key in keys:
        if not str(env.get(key) or os.environ.get(key) or "").strip():
            missing.append(key)
    return missing


def expand_vars(text: str, env: dict[str, str]) -> str:
    combined = {**os.environ, **env}

    def replacer(match: str) -> str:
        key = match[2:-1] if match.startswith("${") and match.endswith("}") else match[1:]
        return combined.get(key, match)

    out = text
    for key, value in combined.items():
        out = out.replace(f"${{{key}}}", value)
    return out

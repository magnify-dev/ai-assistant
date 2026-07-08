from __future__ import annotations

from pathlib import Path
from typing import Any

from ui_test.env_loader import _parse_env_file, require_keys
from ui_test.project_profile import load_cheatsheet
from ui_test.project_paths import agent_dir, ENV_EXAMPLE, ENV_FILE, ENV_LOCAL


def cheatsheet_env_files(project: Path) -> tuple[str, ...]:
    sheet = load_cheatsheet(project)
    local = sheet.get("local") if isinstance(sheet.get("local"), dict) else {}
    files: list[str] = []
    raw = local.get("env_files")
    if isinstance(raw, list):
        files = [str(f).strip() for f in raw if str(f).strip()]
    env_file = str(local.get("env_file") or "").strip()
    if env_file:
        files.insert(0, env_file)
    if not files:
        files = [f"{agent_dir(project).name}/.env", f"{agent_dir(project).name}/.env.local"]
    return tuple(dict.fromkeys(files))


def cheatsheet_required_env(project: Path) -> tuple[str, ...]:
    sheet = load_cheatsheet(project)
    local = sheet.get("local") if isinstance(sheet.get("local"), dict) else {}
    raw = local.get("required_env")
    if isinstance(raw, list):
        keys = [str(k).strip() for k in raw if str(k).strip()]
        if keys:
            return tuple(dict.fromkeys(keys))
    return ("DATABASE_URL",)


def load_merged_local_env(project: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    for rel in cheatsheet_env_files(project):
        path = project / rel if not Path(rel).is_absolute() else Path(rel)
        merged.update(_parse_env_file(path))
    return merged


def local_env_status(project: Path) -> dict[str, Any]:
    base = agent_dir(project)
    env_files = cheatsheet_env_files(project)
    required = cheatsheet_required_env(project)
    merged = load_merged_local_env(project)
    missing = require_keys(merged, list(required))

    file_status: list[dict[str, Any]] = []
    for rel in env_files:
        path = project / rel if not Path(rel).is_absolute() else Path(rel)
        file_status.append({"path": rel, "exists": path.is_file()})

    example_path = base / ENV_EXAMPLE
    local_path = base / ENV_LOCAL
    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "required": list(required),
        "env_files": file_status,
        "has_env_local": local_path.is_file(),
        "has_env_example": example_path.is_file(),
        "env_example_path": f"{base.name}/{ENV_EXAMPLE}",
        "env_local_path": f"{base.name}/{ENV_LOCAL}",
    }


def format_local_env_hint(project: Path, missing: list[str]) -> str:
    status = local_env_status(project)
    keys = ", ".join(missing)
    if status["has_env_example"]:
        return (
            f"Missing {keys}. Copy {status['env_example_path']} -> {status['env_local_path']} "
            f"and set values (see cheatsheet notes)."
        )
    return f"Missing {keys} in {status['env_local_path']} or cheatsheet env_files."

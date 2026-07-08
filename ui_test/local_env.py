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
        files = ["micro-services/admin/.env", f"{agent_dir(project).name}/.env"]
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


def _env_example_path(env_path: str) -> str:
    if env_path.endswith(".env"):
        return f"{env_path[:-4]}.env.example"
    return f"{env_path}.example"


def _primary_env_file(env_files: tuple[str, ...]) -> str:
    normalized = [f.replace("\\", "/") for f in env_files]
    app_files = [f for f in normalized if ".agent/" not in f]
    if app_files:
        return app_files[-1]
    return normalized[-1] if normalized else f"{AGENT_DIR}/{ENV_FILE}"


def local_env_status(project: Path) -> dict[str, Any]:
    base = agent_dir(project)
    env_files = cheatsheet_env_files(project)
    required = cheatsheet_required_env(project)
    merged = load_merged_local_env(project)
    missing = require_keys(merged, list(required))
    primary_env = _primary_env_file(env_files)
    primary_example = _env_example_path(primary_env)

    file_status: list[dict[str, Any]] = []
    for rel in env_files:
        path = project / rel if not Path(rel).is_absolute() else Path(rel)
        file_status.append({"path": rel, "exists": path.is_file()})

    primary_path = project / primary_env if not Path(primary_env).is_absolute() else Path(primary_env)
    example_path = project / primary_example if not Path(primary_example).is_absolute() else Path(primary_example)
    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "required": list(required),
        "env_files": file_status,
        "has_env": primary_path.is_file(),
        "has_env_local": (base / ENV_LOCAL).is_file(),
        "has_env_example": example_path.is_file(),
        "env_path": primary_env,
        "env_example_path": primary_example,
        "env_local_path": primary_env,
    }


def format_local_env_hint(project: Path, missing: list[str]) -> str:
    status = local_env_status(project)
    keys = ", ".join(missing)
    if status["has_env_example"]:
        return (
            f"Missing {keys}. Copy {status['env_example_path']} -> {status['env_path']} "
            f"and set values (see cheatsheet notes)."
        )
    return f"Missing {keys} in {status['env_path']} or cheatsheet env_files."

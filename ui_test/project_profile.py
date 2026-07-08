from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ui_test.project_paths import (
    agent_cheatsheet_path,
    agent_dir,
    agent_profile_path,
    agent_specs_dir,
)

CHEATSHEET_VERSION = 1
DEFAULT_CHEATSHEET = """version: 1
# How to run this project locally before Railway deploy.
# The AI assistant reads this on every run — edit once, reuse forever.

local:
  base_url: http://localhost:3000
  start_command: pnpm dev
  start_cwd: .
  health_url: http://localhost:3000
  env_files:
    - micro-services/admin/.env
    - .agent/.env
  required_env:
    - DATABASE_URL
  auto_start: true
  keep_alive: true
  launch_in_terminal: true
  startup_timeout_sec: 120

deploy:
  platform: railway
  push_before_deploy: false
  skip_deploy_wait: true
  fallback_to_deployed: true

spec:
  default: mini-admin.yaml

notes:
  - Copy micro-services/admin/.env.example to micro-services/admin/.env and set DATABASE_URL.
  - Optional: UI test login + Railway token in .agent/.env (UI_TEST_EMAIL, UI_TEST_PASSWORD, RAILWAY_TOKEN).
  - Local server is started automatically from start_commands when skip deploy is enabled.
  - Set UI_TEST_EMAIL and UI_TEST_PASSWORD in .agent/.env
"""

DEFAULT_PROFILE: dict[str, Any] = {
    "version": 1,
    "name": "",
    "repo_url": "",
    "default_task": "",
    "settings": {
        "push": False,
        "skip_deploy": True,
        "skip_cursor": False,
        "cursor_runtime": "cloud",
    },
}


def profile_path(project: Path) -> Path:
    return agent_profile_path(project)


def cheatsheet_path(project: Path) -> Path:
    return agent_cheatsheet_path(project)


def specs_dir(project: Path) -> Path:
    return agent_specs_dir(project)


def load_profile(project: Path) -> dict[str, Any]:
    path = profile_path(project)
    if not path.is_file():
        return dict(DEFAULT_PROFILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = dict(DEFAULT_PROFILE)
            merged.update(data)
            if isinstance(data.get("settings"), dict):
                merged["settings"] = {**DEFAULT_PROFILE["settings"], **data["settings"]}
            return merged
    except (json.JSONDecodeError, OSError):
        pass
    return dict(DEFAULT_PROFILE)


def save_profile(project: Path, profile: dict[str, Any]) -> Path:
    path = profile_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(profile)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_cheatsheet(project: Path) -> dict[str, Any]:
    path = cheatsheet_path(project)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


def save_cheatsheet(project: Path, content: str) -> Path:
    path = cheatsheet_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def cheatsheet_raw(project: Path) -> str:
    path = cheatsheet_path(project)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return DEFAULT_CHEATSHEET


def local_base_url(project: Path) -> str:
    sheet = load_cheatsheet(project)
    local = sheet.get("local") if isinstance(sheet.get("local"), dict) else {}
    url = str(local.get("base_url") or "").strip().rstrip("/")
    if not url:
        return ""
    if "localhost" in url:
        url = url.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
    return url


def list_specs(project: Path) -> list[dict[str, str]]:
    directory = specs_dir(project)
    if not directory.is_dir():
        return []
    items: list[dict[str, str]] = []
    for path in sorted(directory.glob("*.yaml")):
        items.append({"name": path.name, "path": str(path)})
    return items


def read_spec(project: Path, name: str) -> str:
    path = specs_dir(project) / name
    if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
        raise FileNotFoundError(f"Spec not found: {name}")
    return path.read_text(encoding="utf-8")


def _guess_start_commands(project: Path) -> list[str]:
    pkg = project / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
            pm = "pnpm" if (project / "pnpm-lock.yaml").is_file() else "npm run"
            if scripts.get("admin:dev"):
                cmds = []
                if scripts.get("server:dev"):
                    cmds.append(f"{pm} server:dev")
                cmds.append(f"{pm} admin:dev")
                return cmds
            for key in ("dev", "start", "serve"):
                if scripts.get(key):
                    return [f"{pm} {key}" if pm == "pnpm" else f"npm run {key}"]
        except (json.JSONDecodeError, OSError):
            pass
    if (project / "manage.py").is_file():
        return ["python manage.py runserver"]
    return ["pnpm dev"]


def ensure_cheatsheet(project: Path) -> bool:
    path = cheatsheet_path(project)
    if path.is_file():
        return False
    start_cmds = _guess_start_commands(project)
    if len(start_cmds) == 1:
        content = DEFAULT_CHEATSHEET.replace("pnpm dev", start_cmds[0])
    else:
        content = DEFAULT_CHEATSHEET.replace(
            "  start_command: pnpm dev",
            "  start_commands:\n" + "\n".join(f"    - {c}" for c in start_cmds),
        )
    save_cheatsheet(project, content)
    return True


def ensure_profile(project: Path) -> bool:
    path = profile_path(project)
    if path.is_file():
        return False
    profile = dict(DEFAULT_PROFILE)
    profile["name"] = project.name
    save_profile(project, profile)
    return True

from __future__ import annotations

import shutil
from pathlib import Path

AGENT_DIR = ".agent"

# Legacy layout (pre-consolidation). Migrated into `.agent/` on setup.
_LEGACY_UI_TEST = "ui-test"

# Config/spec paths relative to `.agent/`
CONFIG_FILE = "config.yaml"
CHEATSHEET_FILE = "cheatsheet.yaml"
PROFILE_FILE = "profile.json"
RAILWAY_FILE = "railway.yaml"
SPECS_DIR = "specs"
TASKS_DIR = "tasks"
EXPLORATION_FILE = "exploration.yaml"
ENV_EXAMPLE = ".env.example"
ENV_FILE = ".env"
ENV_LOCAL = ".env.local"


def agent_dir(project: Path) -> Path:
    return project / AGENT_DIR


def agent_config_path(project: Path) -> Path:
    return agent_dir(project) / CONFIG_FILE


def agent_cheatsheet_path(project: Path) -> Path:
    return agent_dir(project) / CHEATSHEET_FILE


def agent_profile_path(project: Path) -> Path:
    return agent_dir(project) / PROFILE_FILE


def agent_railway_path(project: Path) -> Path:
    return agent_dir(project) / RAILWAY_FILE


def agent_specs_dir(project: Path) -> Path:
    return agent_dir(project) / SPECS_DIR


def agent_tasks_dir(project: Path) -> Path:
    return agent_dir(project) / TASKS_DIR


def agent_env_dir(project: Path) -> Path:
    return agent_dir(project)


def migrate_legacy_ui_test(project: Path) -> list[str]:
    """Move `ui-test/` into `.agent/` once. Returns relative paths migrated."""
    legacy = project / _LEGACY_UI_TEST
    target = agent_dir(project)
    if not legacy.is_dir():
        return []

    migrated: list[str] = []
    target.mkdir(parents=True, exist_ok=True)

    for item in legacy.iterdir():
        dest = target / item.name
        if dest.exists():
            if item.is_dir():
                for child in item.rglob("*"):
                    if child.is_file():
                        rel = child.relative_to(item)
                        child_dest = dest / rel
                        if not child_dest.exists():
                            child_dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(child, child_dest)
                            migrated.append(str(child_dest.relative_to(project)))
            continue
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
            migrated.append(str(dest.relative_to(project)))
        else:
            shutil.copy2(item, dest)
            migrated.append(str(dest.relative_to(project)))

    try:
        shutil.rmtree(legacy)
        migrated.append(f"(removed {_LEGACY_UI_TEST}/)")
    except OSError:
        pass

    return migrated

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ui_test.project_paths import agent_dir

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def state_path(project: Path) -> Path:
    return agent_dir(project) / "current" / "local-dev-state.json"


def read_state(project: Path) -> dict[str, Any] | None:
    path = state_path(project)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def write_state(project: Path, payload: dict[str, Any]) -> None:
    path = state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def clear_state(project: Path) -> None:
    path = state_path(project)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def launch_command_in_terminal(
    *,
    command: str,
    cwd: Path,
    title: str,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> None:
    """Start a long-lived dev command in a new terminal (Windows) or detached shell."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.is_file():
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# relaunch {datetime.now(timezone.utc).isoformat()}\n")
    else:
        log_path.write_text(f"# {command}\n", encoding="utf-8")

    merged_env = {**os.environ, **(env or {})}
    cwd_str = str(cwd.resolve())
    log_str = str(log_path.resolve())

    if os.name == "nt":
        inner = f'cd /d "{cwd_str}" && {command} >> "{log_str}" 2>&1'
        launch = f'start "{title}" cmd /k "{inner}"'
        subprocess.Popen(
            launch,
            shell=True,
            env=merged_env,
            creationflags=CREATE_NO_WINDOW,
        )
        return

    inner = f'cd "{cwd_str}" && {command} >> "{log_str}" 2>&1'
    subprocess.Popen(
        ["bash", "-lc", inner],
        env=merged_env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def record_launch(
    project: Path,
    *,
    commands: list[str],
    log_dir: Path,
    urls: dict[str, str],
    keep_alive: bool,
    launch_in_terminal: bool,
) -> None:
    write_state(
        project,
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "project": str(project.resolve()),
            "commands": commands,
            "log_dir": str(log_dir),
            "urls": urls,
            "keep_alive": keep_alive,
            "launch_in_terminal": launch_in_terminal,
        },
    )

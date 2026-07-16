from __future__ import annotations

from pathlib import Path

_active_project: Path | None = None


def set_active_project(project: Path | None) -> None:
    global _active_project
    _active_project = project.resolve() if project else None


def get_active_project() -> Path | None:
    return _active_project

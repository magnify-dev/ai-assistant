"""Jarvis tools - paths.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from jarvis_tools.constants import GIT_ROOTS, KNOWN_PATHS, WORKSPACE

def _voice_root() -> Path:
    return Path(__file__).resolve().parent.parent

def _repo_root() -> Path:
    return _voice_root().parent

def _python_exe() -> str:
    venv_python = _voice_root() / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable

def _log_dir() -> Path:
    path = _repo_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _safe_path(relative: str) -> Path:
    rel = relative.strip().lstrip("/\\")
    target = (WORKSPACE / rel).resolve()
    if not str(target).startswith(str(WORKSPACE)):
        raise PermissionError("Path must stay inside Documents")
    return target

def _resolve_git_project(path_str: str) -> Path:
    raw = path_str.strip().strip('"').strip("'")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (WORKSPACE / raw).resolve()
    else:
        candidate = candidate.resolve()

    for root in GIT_ROOTS:
        root_str = str(root)
        if str(candidate) == root_str or str(candidate).startswith(root_str + os.sep):
            return candidate

    allowed = ", ".join(str(r) for r in GIT_ROOTS)
    raise PermissionError(f"Project path must be under: {allowed}")

def _resolve_folder_path(path_str: str) -> Path:
    raw = path_str.strip().strip('"').strip("'")
    key = raw.lower().replace("\\", "/").rstrip("/")
    key_slug = key.replace(" ", "-").split("/")[-1]

    if key in {k.lower() for k in KNOWN_PATHS}:
        for name, value in KNOWN_PATHS.items():
            if name.lower() == key:
                return Path(value).resolve()

    if key_slug in {k.lower() for k in KNOWN_PATHS}:
        for name, value in KNOWN_PATHS.items():
            if name.lower() == key_slug:
                return Path(value).resolve()

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (WORKSPACE / raw).resolve()
    else:
        candidate = candidate.resolve()
    return candidate

def _first_existing(candidates: list[Path]) -> str | None:
    for candidate in candidates:
        raw = str(candidate)
        if not raw or raw == ".":
            continue
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None

def _browser_profile_dir() -> Path:
    raw = os.environ.get("JARVIS_BROWSER_PROFILE_DIR")
    if raw:
        return Path(raw).resolve()
    return (_repo_root() / "logs" / "jarvis-browser-profile").resolve()


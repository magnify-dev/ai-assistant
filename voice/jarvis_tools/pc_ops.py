"""Jarvis tools - pc_ops.py"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from jarvis_tools.definitions import BLOCKED_COMMAND_FRAGMENTS
from jarvis_tools.constants import WORKSPACE
from jarvis_tools.git_ops import _shell_env
from jarvis_tools.paths import _resolve_folder_path

def _cursor_exe() -> Path | None:
    candidates = [
        Path.home() / "AppData/Local/Programs/cursor/Cursor.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/cursor/Cursor.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def _open_folder_in_cursor(folder_path: str) -> str:
    path = _resolve_folder_path(folder_path)
    if not path.is_dir():
        return f"Folder not found: {path}"

    cursor_exe = _cursor_exe()
    if cursor_exe:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f'Start-Process "{cursor_exe}" -ArgumentList "{path}"',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_shell_env(),
        )
        if proc.returncode == 0:
            return f"Opened {path} in Cursor"
        err = (proc.stderr or proc.stdout or "").strip()
        return f"Could not open Cursor for {path}: {err or 'unknown error'}"

    proc = subprocess.run(
        ["cursor", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        shell=True,
        env=_shell_env(),
    )
    if proc.returncode == 0:
        return f"Opened {path} in Cursor"
    return f"Could not open Cursor for {path}"

def _run_powershell(command: str, timeout: int = 120) -> str:
    lowered = command.lower()
    for blocked in BLOCKED_COMMAND_FRAGMENTS:
        if blocked in lowered:
            return f"Blocked dangerous command: {blocked.strip()}"
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(WORKSPACE),
        env=_shell_env(),
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"Exit {proc.returncode}\n{err or out or 'command failed'}"
    return out or "OK"

def _validate_url(url: str) -> str:
    raw = url.strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    return raw

def _open_url(url: str) -> str:
    safe_url = _validate_url(url)
    return _run_powershell(f"Start-Process '{safe_url}'")

def _web_search(query: str) -> str:
    query = query.strip()
    if not query:
        return "Search query was empty"
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    result = _open_url(url)
    return f"Opened web search for: {query}" if result == "OK" else result

def _fetch_url(url: str, max_chars: int = 6000) -> str:
    safe_url = _validate_url(url)
    req = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "JarvisVoiceAssistant/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content_type = resp.headers.get("content-type", "")
        if "text" not in content_type and "html" not in content_type and "json" not in content_type:
            return f"Unsupported content type: {content_type or 'unknown'}"
        raw = resp.read(min(max_chars * 4, 200_000)).decode("utf-8", errors="replace")

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] or "(empty page)"


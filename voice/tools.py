"""Safe local tools for the voice assistant agent."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", Path.home() / "Documents")).resolve()

# Snapshot taken when the wake word fires (before Jarvis records or runs tools).
_context_window: dict | None = None

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_active_window",
            "description": (
                "Get the title of the foreground (active) window. "
                "Use when the user asks what app or file they are looking at. "
                "Cursor window titles usually contain the open file name."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "Run a PowerShell command on this Windows PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open a Windows application, file, or URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "App name, path, or URL",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file under the user's Documents folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 8000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file under Documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files under Documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": ""},
                },
            },
        },
    },
]

BLOCKED_COMMAND_FRAGMENTS = [
    "format ",
    "remove-item -recurse -force c:\\",
    "rm -rf /",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "reg delete",
    "stop-process -name 'cursor'",
    'stop-process -name "cursor"',
    "stop-process cursor",
]

def _pid_to_exe(pid: int) -> str:
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd) + 1
    if length <= 1:
        return ""
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value.strip()


def _window_info(hwnd: int) -> tuple[str, str]:
    title = _window_title(hwnd)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return title, _pid_to_exe(pid.value)


def _is_assistant_window(exe: str, title: str) -> bool:
    exe_lower = exe.lower().replace("/", "\\")
    if "ai-assistant" in exe_lower and exe_lower.endswith("python.exe"):
        return True
    if title.lower().endswith("python.exe") and "ai-assistant" in exe_lower:
        return True
    return False


def _format_window(title: str, exe: str) -> str:
    app = Path(exe).name if exe else "unknown"
    if title:
        return f"{title} ({app})"
    return app or "No active window title found"


def _enumerate_visible_windows() -> list[tuple[str, str, int]]:
    windows: list[tuple[str, str, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title, exe = _window_info(hwnd)
        if not title:
            return True
        windows.append((title, exe, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def capture_active_window_context() -> None:
    """Remember what the user was looking at when they said the wake word."""
    global _context_window
    hwnd = user32.GetForegroundWindow()
    title, exe = _window_info(hwnd)
    if title and not _is_assistant_window(exe, title):
        _context_window = {"title": title, "exe": exe, "captured_at": time.time()}
        return

    for title, exe, _hwnd in _enumerate_visible_windows():
        if "cursor" in exe.lower() or " - cursor" in title.lower():
            _context_window = {"title": title, "exe": exe, "captured_at": time.time()}
            return

    _context_window = {"title": title, "exe": exe, "captured_at": time.time()}


def _find_cursor_window() -> tuple[str, str]:
    for title, exe, _hwnd in _enumerate_visible_windows():
        if exe.lower().endswith("cursor.exe"):
            return title, exe
    for title, exe, _hwnd in _enumerate_visible_windows():
        if " - cursor" in title.lower():
            return title, exe
    return "", ""


def get_active_window_title() -> str:
    global _context_window

    if _context_window:
        age = time.time() - float(_context_window.get("captured_at", 0))
        if age <= 120:
            title = str(_context_window.get("title", ""))
            exe = str(_context_window.get("exe", ""))
            if title and not _is_assistant_window(exe, title):
                return _format_window(title, exe)

    title, exe = _find_cursor_window()
    if title:
        return _format_window(title, exe)

    hwnd = user32.GetForegroundWindow()
    title, exe = _window_info(hwnd)
    if title and not _is_assistant_window(exe, title):
        return _format_window(title, exe)

    return "No active window title found"


def _safe_path(relative: str) -> Path:
    rel = relative.strip().lstrip("/\\")
    target = (WORKSPACE / rel).resolve()
    if not str(target).startswith(str(WORKSPACE)):
        raise PermissionError("Path must stay inside Documents")
    return target


def _run_powershell(command: str, timeout: int = 30) -> str:
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
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"Exit {proc.returncode}\n{err or out or 'command failed'}"
    return out or "OK"


def execute_tool(name: str, arguments: dict) -> str:
    try:
        if name == "get_active_window":
            return get_active_window_title()
        if name == "run_powershell":
            return _run_powershell(arguments["command"])
        if name == "open_application":
            target = arguments["target"]
            if target.lower() in {"cursor", "cursor ide"}:
                return _run_powershell("Start-Process cursor")
            if target.startswith("http://") or target.startswith("https://"):
                return _run_powershell(f"Start-Process '{target}'")
            return _run_powershell(f"Start-Process '{target}'")
        if name == "read_file":
            path = _safe_path(arguments["path"])
            if not path.is_file():
                return f"File not found: {path}"
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[: int(arguments.get("max_chars", 8000))]
        if name == "write_file":
            path = _safe_path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            return f"Wrote {path}"
        if name == "list_directory":
            rel = arguments.get("path", "")
            path = _safe_path(rel) if rel else WORKSPACE
            if not path.is_dir():
                return f"Not a directory: {path}"
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = [f"{'[dir]' if e.is_dir() else '[file]'} {e.name}" for e in entries[:100]]
            return "\n".join(lines) or "(empty)"
        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Tool error: {exc}"


def tools_enabled(cfg: dict) -> bool:
    return bool(cfg.get("tools", {}).get("enabled", False))

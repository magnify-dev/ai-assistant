"""Shared constants and module-level state for Jarvis tools."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", Path.home() / "Documents")).resolve()
GITHUB_ORG = os.environ.get("JARVIS_GITHUB_ORG", "magnify-dev")
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("JARVIS_OLLAMA_MODEL", "qwen2.5:14b")
VISION_MODEL = os.environ.get("JARVIS_VISION_MODEL", "")

KNOWN_PATHS: dict[str, str] = {}
GIT_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get(
        "JARVIS_GIT_ROOTS",
        f"{Path.home() / 'Documents'};{Path.home() / 'Documents' / 'Programming' / 'ai-assistant'}",
    ).split(";")
    if p.strip()
]
GIT_EXE_CANDIDATES = [
    Path(os.environ.get("JARVIS_GIT_EXE", "")),
    Path(r"C:\Program Files\Git\cmd\git.exe"),
    Path(r"C:\Program Files\Git\bin\git.exe"),
    Path.home() / "AppData/Local/Programs/Git/cmd/git.exe",
]
GH_EXE_CANDIDATES = [
    Path(os.environ.get("JARVIS_GH_EXE", "")),
    Path(r"C:\Program Files\GitHub CLI\gh.exe"),
    Path.home() / "AppData/Local/GitHubCLI/gh.exe",
]
BROWSER_EXE_CANDIDATES = [
    Path(os.environ.get("JARVIS_BROWSER_EXE", "")),
    Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]
FIREFOX_EXE_CANDIDATES = [
    Path(os.environ.get("JARVIS_FIREFOX_EXE", "")),
    Path(os.environ.get("PROGRAMFILES", "")) / "Mozilla Firefox/firefox.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Mozilla Firefox/firefox.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla Firefox/firefox.exe",
]

# Snapshot taken when the wake word fires (before Jarvis records or runs tools).
_context_window: dict | None = None
_browser_context: dict = {}
_browser_command: dict | None = None
_browser_command_result: dict | None = None
_browser_bridge_server: ThreadingHTTPServer | None = None
_firefox_bridge_process: subprocess.Popen | None = None
_foxmcp_process: subprocess.Popen | None = None
_foxmcp_work_tab_id: int | None = None
_browser_context_lock = threading.Lock()
_browser_command_lock = threading.Lock()
_firefox_bridge_lock = threading.Lock()
_foxmcp_lock = threading.Lock()
_foxmcp_connect_thread: threading.Thread | None = None

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

ACTION_STOPWORDS = {
    "a",
    "an",
    "and",
    "can",
    "could",
    "go",
    "i",
    "in",
    "into",
    "me",
    "my",
    "on",
    "open",
    "please",
    "press",
    "select",
    "switch",
    "take",
    "the",
    "to",
    "you",
}
ACTION_SYNONYMS = {
    "song": {"song", "track", "music", "video", "media"},
    "songs": {"song", "track", "music", "video", "media"},
    "track": {"song", "track", "music", "video", "media"},
    "video": {"song", "track", "music", "video", "media"},
    "play": {"play", "start", "resume"},
    "click": {"click", "press", "select", "choose"},
    "open": {"open", "go", "navigate", "show", "take"},
}

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_KEY_K = 0x4B
SW_RESTORE = 9


def _browser_provider() -> str:
    return os.environ.get("JARVIS_BROWSER_PROVIDER", "foxmcp").strip().lower()

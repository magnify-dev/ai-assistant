"""Jarvis tools - executor.py"""

from __future__ import annotations

from pathlib import Path

from jarvis_tools.actions import _act_on_context
from jarvis_tools.browser_api import (
    _click_browser_context,
    _navigate_browser_context,
    _open_jarvis_browser,
    _read_browser_context,
    _read_jarvis_browser,
    _setup_browser_control,
)
from jarvis_tools.browser_bridge import _find_browser_link
from jarvis_tools.constants import WORKSPACE
from jarvis_tools.git_ops import _git_command, _git_status, _github_publish_project
from jarvis_tools.paths import _safe_path
from jarvis_tools.pc_ops import (
    _fetch_url,
    _open_folder_in_cursor,
    _open_url,
    _run_powershell,
    _web_search,
)
from jarvis_tools.vision import _describe_screen
from jarvis_tools.windows import get_active_window_title

def execute_tool(name: str, arguments: dict) -> str:
    try:
        if name == "get_active_window":
            return get_active_window_title()
        if name == "describe_screen":
            return _describe_screen(arguments.get("question", ""))
        if name == "open_jarvis_browser":
            return _open_jarvis_browser(arguments["url"])
        if name == "read_jarvis_browser":
            return _read_jarvis_browser(arguments.get("question", ""))
        if name == "read_browser_context":
            return _read_browser_context(arguments.get("question", ""))
        if name == "open_browser_context_link":
            utterance = str(arguments.get("utterance") or arguments["query"])
            result = _click_browser_context(arguments["query"], utterance=utterance)
            if result == "OK":
                return f"Clicked {arguments['query']}"
            href = _find_browser_link(arguments["query"])
            if href:
                nav = _navigate_browser_context(href)
                return f"Opened {href}" if nav == "OK" else nav
            return result
        if name == "navigate_browser_context":
            return _navigate_browser_context(arguments["url"])
        if name == "click_browser_context":
            return _click_browser_context(
                arguments["query"],
                utterance=str(arguments.get("utterance") or ""),
            )
        if name == "act_on_context":
            return _act_on_context(arguments["command"])
        if name == "setup_firefox_bridge":
            return _setup_browser_control()
        if name == "run_powershell":
            return _run_powershell(arguments["command"])
        if name == "open_folder_in_cursor":
            return _open_folder_in_cursor(arguments["folder_path"])
        if name == "open_application":
            target = arguments["target"]
            if target.lower() in {"cursor", "cursor ide"}:
                return _run_powershell("Start-Process cursor")
            path = Path(target.strip().strip('"')).expanduser()
            if path.is_dir():
                return _open_folder_in_cursor(str(path))
            if target.startswith("http://") or target.startswith("https://"):
                return _open_url(target)
            return _run_powershell(f"Start-Process '{target}'")
        if name == "web_search":
            return _web_search(arguments["query"])
        if name == "open_url":
            return _open_url(arguments["url"])
        if name == "fetch_url":
            return _fetch_url(arguments["url"], int(arguments.get("max_chars", 6000)))
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
        if name == "git_status":
            return _git_status(arguments["project_path"])
        if name == "git_command":
            return _git_command(arguments["project_path"], arguments["args"])
        if name == "github_publish_project":
            return _github_publish_project(
                arguments["project_path"],
                arguments["repo_name"],
                arguments["commit_message"],
                arguments.get("visibility", "public"),
                arguments.get("org"),
            )
        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Tool error: {exc}"

def tools_enabled(cfg: dict) -> bool:
    return bool(cfg.get("tools", {}).get("enabled", False))


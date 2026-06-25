"""Safe local tools for the voice assistant agent."""

from __future__ import annotations

import asyncio
import ctypes
import base64
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", Path.home() / "Documents")).resolve()
GITHUB_ORG = os.environ.get("JARVIS_GITHUB_ORG", "magnify-dev")
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.environ.get("JARVIS_VISION_MODEL", "gemma3:4b")
KNOWN_PATHS: dict[str, str] = {}
GIT_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get(
        "JARVIS_GIT_ROOTS",
        f"{Path.home() / 'Documents'};{Path.home() / 'ai-assistant'}",
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
            "name": "describe_screen",
            "description": (
                "Read the visible desktop/browser screen using the local vision model. "
                "Use this on authenticated/personalized pages like YouTube playlists, "
                "where fetch_url cannot see the user's logged-in page. Returns visible text and likely page elements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What to look for on screen, e.g. visible YouTube playlist names",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_jarvis_browser",
            "description": (
                "Open a URL in the dedicated Jarvis-controlled browser profile. "
                "Use for personalized/authenticated sites Jarvis needs to inspect later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to open"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_jarvis_browser",
            "description": (
                "Read real DOM text from the dedicated Jarvis-controlled browser tab. "
                "Use instead of screenshots for personalized pages like YouTube playlists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to extract from the current page"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_browser_context",
            "description": (
                "Read the latest DOM/text/link snapshot sent by the Jarvis Firefox extension. "
                "Use this for the user's real Firefox session and authenticated pages like YouTube playlists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to extract, e.g. YouTube playlist names"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_browser_context_link",
            "description": (
                "Open a link from the latest Firefox page context by matching link text. "
                "Use after read_browser_context when the user names a playlist/link to open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Playlist/link name to match"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_browser_context",
            "description": "Navigate the Firefox tab connected to the Jarvis extension to a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_browser_context",
            "description": (
                "Click a visible Firefox page link, button, or control by matching its text or aria label."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Visible text to click"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "act_on_context",
            "description": (
                "Observe currently available browser, window, app, file, and tool actions, then map the user's "
                "spoken command to the most likely action and execute it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The user's spoken command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_firefox_bridge",
            "description": (
                "Open Firefox's temporary extension page and the Jarvis extension folder so the user can load "
                "the extension into their normal logged-in Firefox session."
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
            "name": "open_folder_in_cursor",
            "description": (
                "Open a folder in the Cursor code editor. "
                "Use when the user asks to open a project or folder in Cursor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_path": {
                        "type": "string",
                        "description": (
                            "Folder path, e.g. C:/Users/marce/ai-assistant or ai-assistant"
                        ),
                    },
                },
                "required": ["folder_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open a Windows application, file, or URL (not folders — use open_folder_in_cursor for those).",
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
            "name": "web_search",
            "description": "Search the web in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to open in the browser",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to open",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public web page and return readable text for summarizing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to read",
                    },
                    "max_chars": {"type": "integer", "default": 6000},
                },
                "required": ["url"],
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
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": (
                "Check git status for a project folder: branch, uncommitted changes, remote URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Project folder path (e.g. C:/Users/marce/ai-assistant)",
                    },
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_command",
            "description": (
                "Run a safe git command in an allowed project folder. "
                "Use for status, diff, log, add, commit, pull, push, branch, and remote checks. "
                "Dangerous args like force push, hard reset, clean -fdx, and branch delete are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Project folder path",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Git arguments without the leading 'git', e.g. ['status', '--short']",
                    },
                },
                "required": ["project_path", "args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_publish_project",
            "description": (
                "Create a GitHub repo under magnify-dev (if it does not exist), "
                "commit all changes, and push to GitHub. Use when the user asks to "
                "put a project on GitHub or push code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Local project folder to publish",
                    },
                    "repo_name": {
                        "type": "string",
                        "description": "GitHub repository name (e.g. ai-assistant)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Commit message for any uncommitted changes",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["public", "private"],
                        "description": "Repo visibility when creating (default public)",
                    },
                    "org": {
                        "type": "string",
                        "description": "GitHub organization (default magnify-dev)",
                    },
                },
                "required": ["project_path", "repo_name", "commit_message"],
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

BLOCKED_GIT_ARGS = [
    "--force",
    "push --force",
    "reset --hard",
    "clean -fdx",
    "filter-branch",
    "branch -D",
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


class _BrowserBridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path == "/context":
            with _browser_context_lock:
                context = dict(_browser_context)
            self._send_json(200, {"ok": True, "context": context})
            return
        if path == "/command":
            global _browser_command
            with _browser_command_lock:
                command = dict(_browser_command) if _browser_command else None
                _browser_command = None
            self._send_json(200, {"ok": True, "command": command})
            return
        if path == "/command-result":
            with _browser_command_lock:
                result = dict(_browser_command_result) if _browser_command_result else None
            self._send_json(200, {"ok": True, "result": result})
            return
        else:
            self._send_json(404, {"error": "not found"})
            return

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path not in {"/context", "/command-result"}:
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(min(length, 2_000_000))
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            if path == "/context":
                payload["received_at"] = time.time()
                with _browser_context_lock:
                    _browser_context.clear()
                    _browser_context.update(payload)
            else:
                global _browser_command_result
                payload["received_at"] = time.time()
                with _browser_command_lock:
                    _browser_command_result = payload
            self._send_json(200, {"ok": True})
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_browser_bridge(port: int = 8765) -> None:
    global _browser_bridge_server
    if _browser_bridge_server is not None:
        return
    server = ThreadingHTTPServer(("127.0.0.1", port), _BrowserBridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _browser_bridge_server = server
    logging.info("Firefox browser bridge ready at http://127.0.0.1:%s/context", port)


def _latest_browser_context() -> dict:
    with _browser_context_lock:
        return dict(_browser_context)


def _browser_context_is_fresh(max_age_sec: float = 30.0) -> bool:
    context = _latest_browser_context()
    if not context:
        return False
    return time.time() - float(context.get("received_at", 0)) <= max_age_sec


def _voice_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    return _voice_root().parent


def _python_exe() -> str:
    venv_python = _voice_root() / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _firefox_exe() -> str | None:
    return _first_existing(FIREFOX_EXE_CANDIDATES)


def _open_firefox_url(url: str) -> str:
    safe_url = _validate_url(url)
    firefox = _firefox_exe()
    if firefox:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([firefox, safe_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            return "OK"
        except Exception as exc:
            return f"Could not open Firefox: {exc}"
    return _open_url(safe_url)


def _open_firefox_extension_setup() -> str:
    firefox = _firefox_exe()
    setup_url = "about:debugging#/runtime/this-firefox"
    if firefox:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([firefox, setup_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    else:
        _run_powershell(f"Start-Process '{setup_url}'")
    subprocess.Popen(["explorer.exe", str(_repo_root() / "firefox-extension")], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "Opened Firefox extension setup."


def _setup_browser_control() -> str:
    if _browser_provider() == "foxmcp":
        ready = _ensure_foxmcp_running()
        if ready != "OK":
            return ready
        status = _foxmcp_call_tool("debug_websocket_status", {})
        if "connected" in status.lower():
            return "FoxMCP browser control is connected."
        return "FoxMCP server is running. Open Firefox and check the FoxMCP extension connection status."
    return _open_firefox_extension_setup()


def _browser_provider() -> str:
    return os.environ.get("JARVIS_BROWSER_PROVIDER", "foxmcp").strip().lower()


def _foxmcp_server_dir() -> Path:
    raw = os.environ.get("JARVIS_FOXMCP_SERVER_DIR")
    if raw:
        return Path(raw).resolve()
    return (_repo_root() / "vendor" / "foxmcp").resolve()


def _foxmcp_ws_port() -> int:
    return int(os.environ.get("JARVIS_FOXMCP_WEBSOCKET_PORT", "8765"))


def _foxmcp_mcp_port() -> int:
    return int(os.environ.get("JARVIS_FOXMCP_MCP_PORT", "3000"))


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:
        return False


def _foxmcp_mcp_url() -> str:
    return f"http://localhost:{_foxmcp_mcp_port()}/mcp/"


def _foxmcp_ready() -> bool:
    return _http_ok(_foxmcp_mcp_url())


def start_configured_browser_control() -> str:
    """Start the configured browser-control backend during Jarvis startup."""
    if _browser_provider() == "foxmcp":
        return _ensure_foxmcp_running()
    return "OK"


def _ensure_foxmcp_running() -> str:
    global _foxmcp_process
    if _foxmcp_ready():
        return "OK"

    with _foxmcp_lock:
        if _foxmcp_process and _foxmcp_process.poll() is None:
            deadline = time.time() + 10
            while time.time() < deadline:
                if _foxmcp_ready():
                    return "OK"
                time.sleep(0.25)
            return "FoxMCP server is starting. Ask again in a moment."

        server_dir = _foxmcp_server_dir()
        server_script = server_dir / "server" / "server.py"
        if not server_script.exists():
            return f"FoxMCP server not found at {server_script}."

        log_dir = _repo_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "foxmcp.log"
        try:
            log_file = log_path.open("a", encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _foxmcp_process = subprocess.Popen(
                [
                    _python_exe(),
                    str(server_script),
                    "--port",
                    str(_foxmcp_ws_port()),
                    "--mcp-port",
                    str(_foxmcp_mcp_port()),
                ],
                cwd=str(server_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=flags,
            )
            logging.info("Started FoxMCP server for Firefox browser control")
        except Exception as exc:
            _foxmcp_process = None
            return f"Could not start FoxMCP server: {exc}"

    deadline = time.time() + 15
    while time.time() < deadline:
        if _foxmcp_ready():
            return "OK"
        if _foxmcp_process and _foxmcp_process.poll() is not None:
            _foxmcp_process = None
            return "FoxMCP server failed to start. Check logs/foxmcp.log."
        time.sleep(0.25)
    return "FoxMCP server is starting. Ask again in a moment."


async def _foxmcp_call_tool_async(name: str, arguments: dict | None = None) -> str:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(_foxmcp_mcp_url(), timeout=20) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments or {})

    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(str(item))
    return "\n".join(parts).strip() or str(result)


def _foxmcp_call_tool(name: str, arguments: dict | None = None) -> str:
    ready = _ensure_foxmcp_running()
    if ready != "OK":
        return ready
    try:
        return asyncio.run(_foxmcp_call_tool_async(name, arguments))
    except Exception as exc:
        return f"FoxMCP tool error: {exc}"


def _foxmcp_active_tab_id() -> int | None:
    tabs = _foxmcp_call_tool("tabs_list", {})
    match = re.search(r"- ID (\d+): .*\(active\)", tabs)
    if not match:
        match = re.search(r"- ID (\d+):", tabs)
    if not match:
        return None
    return int(match.group(1))


def _foxmcp_work_tab_exists(tabs: str, tab_id: int) -> bool:
    return bool(re.search(rf"- ID {re.escape(str(tab_id))}:", tabs))


def _foxmcp_target_tab_id() -> int | None:
    global _foxmcp_work_tab_id
    tabs = _foxmcp_call_tool("tabs_list", {})
    if _foxmcp_work_tab_id and _foxmcp_work_tab_exists(tabs, _foxmcp_work_tab_id):
        return _foxmcp_work_tab_id
    _foxmcp_work_tab_id = None
    return _foxmcp_active_tab_id()


def _foxmcp_existing_work_tab_id() -> int | None:
    global _foxmcp_work_tab_id
    if not _foxmcp_work_tab_id:
        return None
    tabs = _foxmcp_call_tool("tabs_list", {})
    if _foxmcp_work_tab_exists(tabs, _foxmcp_work_tab_id):
        return _foxmcp_work_tab_id
    _foxmcp_work_tab_id = None
    return None


def _strip_foxmcp_text_header(text: str) -> tuple[str, str, list[str]]:
    match = re.match(r"Text content from (.*?) \((.*?)\):\s*\n\n(.*)", text, flags=re.DOTALL)
    if match:
        title, url, body = match.groups()
    else:
        title, url, body = "Firefox page", "", text
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return title, url, lines


def _visible_playlist_names(lines: list[str]) -> list[str]:
    visibility_words = {
        "javen",
        "zaseben",
        "public",
        "private",
        "unlisted",
        "nenaveden",
    }
    ignored = {
        "seznami predvajanja",
        "playlist",
        "playlists",
        "seznam predvajanja",
        "ogled celotnega seznama",
        "view full playlist",
        "nedavno dodano",
        "recently added",
        "glasba",
        "music",
        "miksi",
        "mixes",
        "v lasti",
        "owned",
        "shranjeno",
        "saved",
    }
    names: list[str] = []
    for idx, line in enumerate(lines):
        normalized = line.casefold()
        if normalized in ignored or normalized in visibility_words:
            continue
        if re.search(r"\b(videoposnetkov|videos?)\b", normalized):
            continue
        next_one = lines[idx + 1].casefold() if idx + 1 < len(lines) else ""
        next_two = lines[idx + 2].casefold() if idx + 2 < len(lines) else ""
        if next_one in visibility_words or "seznam predvajanja" in next_one or "playlist" in next_one:
            names.append(line)
        elif next_two in visibility_words or "seznam predvajanja" in next_two or "playlist" in next_two:
            names.append(line)
    return list(dict.fromkeys(names))[:20]


def _summarize_foxmcp_page_text(question: str, text: str) -> str:
    title, url, lines = _strip_foxmcp_text_header(text)
    question_lower = question.lower()
    playlist_names = _visible_playlist_names(lines)
    if playlist_names and (
        "playlist" in question_lower
        or "playlists" in question_lower
        or "what do you see" in question_lower
        or "see" in question_lower
    ):
        return "Visible playlists: " + ", ".join(playlist_names)
    if "what do you see" in question_lower or "see" in question_lower or "read" in question_lower:
        preview = [line for line in lines if len(line) <= 120][:25]
        return "\n".join([f"Title: {title}", f"URL: {url}", "Visible text:", *preview])
    return text[:4000]


def _foxmcp_video_state(tab_id: int) -> str:
    script = r"""
(() => {
  const video = document.querySelector("video");
  if (!video) {
    return JSON.stringify({ hasVideo: false, title: document.title, url: location.href });
  }
  const title =
    document.querySelector("h1 yt-formatted-string")?.innerText ||
    document.querySelector("h1")?.innerText ||
    document.title ||
    "";
  return JSON.stringify({
    hasVideo: true,
    paused: video.paused,
    ended: video.ended,
    muted: video.muted,
    currentTime: Math.round(video.currentTime || 0),
    duration: Math.round(video.duration || 0),
    title,
    url: location.href
  });
})()
"""
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    match = re.search(r"Script result .*?:\s*(\{.*\})\s*$", result, flags=re.DOTALL)
    if not match:
        return ""
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    if not state.get("hasVideo"):
        return "I don't see a video player on the current page."
    if state.get("ended"):
        status = "The video has ended."
    elif state.get("paused"):
        status = "The video is paused."
    else:
        status = "The video is playing."
    title = str(state.get("title") or "").strip()
    if title:
        return f"{status} Current video: {title}"
    return status


def _foxmcp_read_browser_context(question: str = "") -> str:
    tab_id = _foxmcp_target_tab_id()
    if tab_id is None:
        return "I can't see Firefox through FoxMCP yet. Make sure the FoxMCP extension is enabled."
    if re.search(r"\b(playing|paused|video|song|music|audio)\b", question.lower()):
        playback = _foxmcp_video_state(tab_id)
        if playback:
            return playback
    text = _foxmcp_call_tool("content_get_text", {"tab_id": tab_id, "max_length": 12000})
    return _summarize_foxmcp_page_text(question, text)


def _foxmcp_navigate_browser_context(url: str) -> str:
    global _foxmcp_work_tab_id
    safe_url = _validate_url(url)
    tab_id = _foxmcp_existing_work_tab_id()
    if tab_id is not None:
        result = _foxmcp_call_tool("navigation_go_to_url", {"tab_id": tab_id, "url": safe_url})
        return "OK" if "successfully navigated" in result.lower() else result

    result = _foxmcp_call_tool("tabs_create", {"url": safe_url, "active": True})
    match = re.search(r"Created tab: ID (\d+)", result)
    if match:
        _foxmcp_work_tab_id = int(match.group(1))
        return "OK"
    return "OK" if "created" in result.lower() or "success" in result.lower() else result


def _browser_match_key(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def _browser_match_score(query: str, candidate: str) -> float:
    q_key = _browser_match_key(query)
    c_key = _browser_match_key(candidate)
    if not q_key or not c_key:
        return 0.0
    if q_key == c_key:
        return 1.0
    if q_key in c_key or c_key in q_key:
        return 0.92
    ratio = SequenceMatcher(None, q_key, c_key).ratio()
    q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    c_words = set(re.findall(r"[a-z0-9]+", candidate.lower()))
    overlap = len(q_words & c_words) / max(1, len(q_words | c_words))
    return max(ratio, overlap)


def _foxmcp_script_json(result: str) -> object | None:
    match = re.search(r"Script result .*?:\s*([\[{].*)\s*$", result, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _foxmcp_clickable_candidates(tab_id: int) -> list[dict[str, object]]:
    script = r"""
(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ") : "");
  const attr = (el, name) => (el.getAttribute(name) || "").trim().replace(/\s+/g, " ");
  const label = (el) => {
    const labelledBy = attr(el, "aria-labelledby")
      .split(/\s+/)
      .map((id) => textOf(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");
    const closestTitle = textOf(el.closest("ytd-playlist-video-renderer, ytd-video-renderer, ytd-rich-item-renderer")?.querySelector("#video-title, a#video-title, h3"));
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll("img[alt]") : [])
      .map((img) => attr(img, "alt"))
      .find(Boolean) || "";
    return (
      textOf(el) || attr(el, "aria-label") || attr(el, "title") || attr(el, "placeholder") ||
      String(el.value || "").trim() || labelledBy || closestTitle || imageAlt
    ).trim().replace(/\s+/g, " ");
  };
  const describe = (el, index, videoOrdinal) => {
    const tag = el.tagName.toLowerCase();
    const href = el.href || "";
    const aria = attr(el, "aria-label");
    const title = attr(el, "title");
    const role = attr(el, "role");
    const classes = String(el.className || "");
    const itemText = label(el);
    const haystack = `${itemText} ${aria} ${title} ${role} ${href} ${classes}`.toLowerCase();
    let kind = role || tag;
    if (href.includes("/watch") || href.includes("watch?v=")) kind = "video-link";
    else if (href.includes("playlist") || href.includes("list=")) kind = "playlist-link";
    else if (tag === "button" || role === "button") kind = "button";
    let action = "";
    if (haystack.includes("pause")) action = "pause";
    else if (haystack.includes("play")) action = "play";
    return {
      index,
      kind,
      action,
      text: itemText,
      href,
      aria,
      title,
      role,
      ordinal: kind === "video-link" ? videoOrdinal : 0
    };
  };

  const raw = Array.from(document.querySelectorAll(
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='option'], [tabindex]:not([tabindex='-1'])"
  )).filter(visible);
  const seen = new Set();
  const items = [];
  let videoOrdinal = 0;
  for (const el of raw) {
    const data = describe(el, items.length, 0);
    if (data.kind === "video-link") videoOrdinal += 1;
    data.ordinal = data.kind === "video-link" ? videoOrdinal : 0;
    const key = `${data.kind}|${data.text}|${data.aria}|${data.title}|${data.href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (data.text || data.aria || data.title || data.href || data.action) items.push(data);
  }

  const video = document.querySelector("video");
  if (video) {
    items.unshift({
      index: -1,
      kind: "video-player",
      action: video.paused ? "play" : "pause",
      text: video.paused ? "Video player paused" : "Video player playing",
      href: location.href,
      aria: "video player",
      title: document.title || "",
      role: "",
      ordinal: 0
    });
  }
  return JSON.stringify(items.slice(0, 300));
})()
"""
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    data = _foxmcp_script_json(result)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _query_ordinal(query: str) -> int | None:
    lowered = query.lower()
    ordinals = {
        "first": 1,
        "1st": 1,
        "second": 2,
        "2nd": 2,
        "third": 3,
        "3rd": 3,
        "fourth": 4,
        "4th": 4,
        "fifth": 5,
        "5th": 5,
    }
    for word, value in ordinals.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return value
    return None


def _foxmcp_candidate_haystack(item: dict[str, object]) -> str:
    fields = [
        item.get("text", ""),
        item.get("aria", ""),
        item.get("title", ""),
        item.get("href", ""),
        item.get("kind", ""),
        item.get("action", ""),
        item.get("role", ""),
    ]
    return " ".join(str(field) for field in fields if field).strip()


def _best_foxmcp_candidate(query: str, candidates: list[dict[str, object]]) -> dict[str, object] | None:
    best: dict[str, object] | None = None
    best_score = 0.0
    query_lower = query.lower().strip(" .!?")
    query_words = set(re.findall(r"[a-z0-9]+", query_lower))
    play_intent = bool(re.search(r"\b(play|start|resume)\b", query_lower))
    video_intent = bool(re.search(r"\b(video|song|track|music)\b", query_lower))
    wanted_ordinal = _query_ordinal(query_lower)

    for item in candidates:
        haystack = _foxmcp_candidate_haystack(item)
        if not haystack:
            continue
        fields = [str(item.get(key, "")) for key in ("text", "aria", "title", "href", "kind", "action") if item.get(key)]
        score = max(_browser_match_score(query, field) for field in fields)
        candidate_words = set(re.findall(r"[a-z0-9]+", haystack.lower()))
        if query_words and candidate_words:
            score += 0.35 * (len(query_words & candidate_words) / len(query_words | candidate_words))

        kind = str(item.get("kind", ""))
        action = str(item.get("action", ""))
        ordinal = int(item.get("ordinal") or 0)

        if play_intent:
            if action == "play":
                score += 1.5
            if "play" in haystack.lower():
                score += 0.5
            if kind == "video-player":
                score += 0.7
            if kind == "video-link":
                score += 0.35
        if video_intent and kind in {"video-link", "video-player"}:
            score += 0.7
        if wanted_ordinal and ordinal:
            score += 2.0 if ordinal == wanted_ordinal else -0.25

        if score > best_score:
            best = item
            best_score = score

    if best and best_score >= 0.72:
        return best
    if best and (play_intent or wanted_ordinal) and best_score >= 0.6:
        return best
    return None


def _foxmcp_click_interactable(tab_id: int, candidate: dict[str, object]) -> str:
    target = {
        "index": candidate.get("index"),
        "kind": candidate.get("kind"),
        "text": candidate.get("text"),
        "aria": candidate.get("aria"),
        "title": candidate.get("title"),
        "href": candidate.get("href"),
    }
    script = r"""
(async () => {
  const target = __TARGET__;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const activate = (el) => {
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "center" });
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const verifyPlaying = async (video) => {
    await sleep(600);
    return Boolean(video && !video.paused && !video.ended);
  };
  const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ") : "");
  const attr = (el, name) => (el.getAttribute(name) || "").trim().replace(/\s+/g, " ");
  const label = (el) => {
    const closestTitle = textOf(el.closest("ytd-playlist-video-renderer, ytd-video-renderer, ytd-rich-item-renderer")?.querySelector("#video-title, a#video-title, h3"));
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll("img[alt]") : [])
      .map((img) => attr(img, "alt"))
      .find(Boolean) || "";
    return (
      textOf(el) || attr(el, "aria-label") || attr(el, "title") || attr(el, "placeholder") ||
      String(el.value || "").trim() || closestTitle || imageAlt
    ).trim().replace(/\s+/g, " ");
  };
  const describe = (el, index, videoOrdinal) => {
    const tag = el.tagName.toLowerCase();
    const href = el.href || "";
    const aria = attr(el, "aria-label");
    const title = attr(el, "title");
    const role = attr(el, "role");
    const classes = String(el.className || "");
    const itemText = label(el);
    const haystack = `${itemText} ${aria} ${title} ${role} ${href} ${classes}`.toLowerCase();
    let kind = role || tag;
    if (href.includes("/watch") || href.includes("watch?v=")) kind = "video-link";
    else if (href.includes("playlist") || href.includes("list=")) kind = "playlist-link";
    else if (tag === "button" || role === "button") kind = "button";
    let action = "";
    if (haystack.includes("pause")) action = "pause";
    else if (haystack.includes("play")) action = "play";
    return { index, kind, action, text: itemText, href, aria, title, role, ordinal: kind === "video-link" ? videoOrdinal : 0 };
  };
  const raw = Array.from(document.querySelectorAll(
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='option'], [tabindex]:not([tabindex='-1'])"
  )).filter(visible);
  const items = [];
  const seen = new Set();
  let videoOrdinal = 0;
  for (const el of raw) {
    const data = describe(el, items.length, 0);
    if (data.kind === "video-link") videoOrdinal += 1;
    data.ordinal = data.kind === "video-link" ? videoOrdinal : 0;
    const key = `${data.kind}|${data.text}|${data.aria}|${data.title}|${data.href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (data.text || data.aria || data.title || data.href || data.action) items.push({ el, data });
  }

  let item = null;
  if (target.kind === "video-player") {
    item = { el: document.querySelector("video"), data: target };
  }
  if (!item && Number.isInteger(target.index)) {
    item = items.find((entry) => entry.data.index === target.index);
  }
  if (!item && target.href) {
    item = items.find((entry) => entry.data.href === target.href);
  }
  if (!item) {
    const targetText = `${target.text || ""} ${target.aria || ""} ${target.title || ""}`.toLowerCase();
    item = items.find((entry) => targetText && `${entry.data.text} ${entry.data.aria} ${entry.data.title}`.toLowerCase().includes(targetText.trim()));
  }
  if (!item || !item.el) return `No visible element matched: ${target.text || target.aria || target.href || "selected element"}`;

  if (item.el.tagName && item.el.tagName.toLowerCase() === "video") {
    const video = item.el;
    const playButtons = [
      ".ytp-large-play-button",
      ".ytp-play-button",
      "button[aria-label*='Play']",
      "button[title*='Play']"
    ];
    for (const selector of playButtons) {
      const button = Array.from(document.querySelectorAll(selector))
        .find((el) => visible(el) && !`${attr(el, "aria-label")} ${attr(el, "title")}`.toLowerCase().includes("pause"));
      if (button) {
        activate(button);
        if (await verifyPlaying(video)) return `OK: ${selector}`;
      }
    }

    activate(video);
    if (await verifyPlaying(video)) return "OK: video click";

    video.muted = false;
    try {
      const playResult = video.play();
      if (playResult && typeof playResult.then === "function") {
        await playResult;
      }
      if (await verifyPlaying(video)) return "OK: video.play()";
      return "Play did not start: video is still paused";
    } catch (err) {
      return `Play failed: ${err && err.message ? err.message : err}`;
    }
  }
  item.el.scrollIntoView({ block: "center", inline: "center" });
  item.el.click();
  const clicked = item.data.text || item.data.aria || item.data.title || item.data.href || item.data.kind;
  return `OK: ${clicked}`;
})()
""".replace("__TARGET__", json.dumps(target))
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    return "OK" if "OK:" in result else result


def _foxmcp_click_browser_context(query: str) -> str:
    tab_id = _foxmcp_target_tab_id()
    if tab_id is None:
        return "I can't click Firefox through FoxMCP yet. Make sure the FoxMCP extension is enabled."
    query_lower = query.lower().strip(" .!?")
    candidates = _foxmcp_clickable_candidates(tab_id)
    best = _best_foxmcp_candidate(query, candidates)
    if best:
        logging.info("FoxMCP click selected interactable for %r: %r", query, best)
        result = _foxmcp_click_interactable(tab_id, best)
        if result == "OK":
            return "OK"
        logging.info("FoxMCP selected interactable failed, trying fallback: %s", result[:300])

    if query_lower in {"play", "press play", "click play", "hit play"}:
        return _foxmcp_press_play(tab_id)
    if (
        re.search(r"\b(first|1st)\b", query_lower) and re.search(r"\b(video|song|track)\b", query_lower)
    ) or query_lower in {"first", "first one", "1st"}:
        result = _foxmcp_click_first_video(tab_id)
        if result != "OK":
            return result
        time.sleep(2)
        play_result = _foxmcp_press_play(tab_id)
        return "OK" if play_result == "OK" else play_result

    target_query = query
    script = r"""
(() => {
  const query = __QUERY__;
  const words = String(query || "").toLowerCase().match(/[a-z0-9]+/g) || [];
  const normalizedQuery = words.join(" ");
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const label = (el) => (
    el.innerText || el.textContent || el.getAttribute("aria-label") ||
    el.getAttribute("title") || el.getAttribute("placeholder") || el.value || ""
  ).trim().replace(/\s+/g, " ");
  const candidates = Array.from(document.querySelectorAll(
    "a, button, [role='button'], [role='link'], input[type='button'], input[type='submit']"
  )).filter(visible);
  let best = null;
  let bestScore = 0;
  for (const el of candidates) {
    const itemLabel = label(el);
    const haystack = `${itemLabel} ${el.href || ""}`.toLowerCase();
    const normalizedLabel = (itemLabel.toLowerCase().match(/[a-z0-9]+/g) || []).join(" ");
    let score = words.reduce((sum, word) => sum + (haystack.includes(word) ? 1 : 0), 0);
    if (normalizedQuery && normalizedLabel.includes(normalizedQuery)) score += 10;
    if (normalizedQuery && haystack.includes(normalizedQuery)) score += 5;
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  }
  if (!best || bestScore < Math.max(1, Math.min(words.length, 2))) return `No visible element matched: ${query}`;
  const text = label(best);
  best.scrollIntoView({ block: "center", inline: "center" });
  best.click();
  return `OK: ${text}`;
})()
""".replace("__QUERY__", json.dumps(target_query))
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    return "OK" if "OK:" in result else result


def _foxmcp_click_first_video(tab_id: int) -> str:
    script = r"""
(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    "ytd-playlist-video-renderer a#video-title",
    "ytd-playlist-video-renderer a.yt-simple-endpoint",
    "ytd-video-renderer a#video-title",
    "ytd-rich-item-renderer a#video-title-link",
    "a#video-title"
  ];
  for (const selector of selectors) {
    const target = Array.from(document.querySelectorAll(selector)).find(visible);
    if (target) {
      const text = (target.innerText || target.textContent || target.getAttribute("title") || "").trim();
      target.scrollIntoView({ block: "center", inline: "center" });
      target.click();
      return `OK: ${text || target.href || selector}`;
    }
  }

  const anchors = Array.from(document.querySelectorAll("a[href*='/watch'], a[href*='watch?v=']"))
    .filter(visible)
    .filter((a) => !String(a.href || "").includes("start_radio=1"));
  if (anchors[0]) {
    const target = anchors[0];
    const text = (target.innerText || target.textContent || target.getAttribute("title") || "").trim();
    target.scrollIntoView({ block: "center", inline: "center" });
    target.click();
    return `OK: ${text || target.href}`;
  }

  return "No visible video found";
})()
"""
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    if "OK:" in result:
        logging.info("FoxMCP clicked first video")
        return "OK"
    return result


def _foxmcp_press_play(tab_id: int) -> str:
    script = r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const attr = (el, name) => (el.getAttribute(name) || "").trim();
  const activate = (el) => {
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "center" });
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const verifyPlaying = async (video) => {
    await sleep(600);
    return Boolean(video && !video.paused && !video.ended);
  };

  const video = document.querySelector("video");
  const playerButtons = [
    ".ytp-large-play-button",
    ".ytp-play-button",
    "button[aria-label*='Play']",
    "button[title*='Play']"
  ];
  for (const selector of playerButtons) {
    const target = Array.from(document.querySelectorAll(selector))
      .find((el) => visible(el) && !`${attr(el, "aria-label")} ${attr(el, "title")}`.toLowerCase().includes("pause"));
    if (target) {
      activate(target);
      if (!video || await verifyPlaying(video)) return `OK: ${selector}`;
    }
  }

  if (video) {
    activate(video);
    if (await verifyPlaying(video)) return "OK: video click";

    video.muted = false;
    const playResult = video.play();
    if (playResult && typeof playResult.then === "function") {
      try {
        await playResult;
      } catch (err) {
        return `Play failed: ${err && err.message ? err.message : err}`;
      }
    }
    if (await verifyPlaying(video)) return "OK: video.play()";
    return "Play did not start: video is still paused";
  }

  const videoLinks = Array.from(document.querySelectorAll(
    "ytd-playlist-video-renderer a#video-title, ytd-playlist-video-renderer a.yt-simple-endpoint, a[href*='/watch'], a[href*='watch?v=']"
  ))
    .filter(visible)
    .filter((a) => !String(a.href || "").includes("start_radio=1"));
  if (videoLinks[0]) {
    const target = videoLinks[0];
    const text = (target.innerText || target.textContent || target.getAttribute("title") || "").trim();
    target.scrollIntoView({ block: "center", inline: "center" });
    target.click();
    return `OK: ${text || target.href}`;
  }

  return "No visible play button or video found";
})()
"""
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    if "OK:" in result:
        logging.info("FoxMCP pressed play")
        return "OK"
    return result


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


def _log_dir() -> Path:
    path = _repo_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _action_words(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", text.lower())
    expanded: list[str] = []
    for word in raw:
        if word in ACTION_STOPWORDS:
            continue
        expanded.append(word)
        expanded.extend(sorted(ACTION_SYNONYMS.get(word, set())))
    return list(dict.fromkeys(expanded))


def _truncate_value(value: object, max_len: int = 240) -> object:
    if isinstance(value, str):
        return value if len(value) <= max_len else value[: max_len - 3] + "..."
    if isinstance(value, dict):
        return {str(k): _truncate_value(v, max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_value(item, max_len) for item in value[:80]]
    return value


def _make_action(
    *,
    action_id: str,
    source: str,
    action: str,
    label: str,
    type_: str,
    aliases: list[str] | None = None,
    ordinal: int = 0,
    group: str = "",
    state: dict | None = None,
    payload: dict | None = None,
) -> dict[str, object]:
    return {
        "id": action_id,
        "source": source,
        "action": action,
        "label": label.strip() or action,
        "type": type_,
        "aliases": [alias for alias in (aliases or []) if alias],
        "ordinal": ordinal,
        "group": group,
        "state": state or {},
        "payload": payload or {},
    }


def _browser_page_title_url() -> tuple[str, str]:
    if _browser_provider() == "foxmcp":
        tab_id = _foxmcp_target_tab_id()
        if tab_id is None:
            return "", ""
        script = "(() => JSON.stringify({title: document.title || '', url: location.href || ''}))()"
        result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
        data = _foxmcp_script_json(result)
        if isinstance(data, dict):
            return str(data.get("title") or ""), str(data.get("url") or "")
        return "", ""
    context = _latest_browser_context()
    return str(context.get("title") or ""), str(context.get("url") or "")


def _browser_action_candidates() -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if _browser_provider() == "foxmcp":
        tab_id = _foxmcp_target_tab_id()
        if tab_id is None:
            return []
        title, url = _browser_page_title_url()
        for item in _foxmcp_clickable_candidates(tab_id):
            label = str(item.get("text") or item.get("aria") or item.get("title") or item.get("href") or "").strip()
            kind = str(item.get("kind") or "element")
            semantic_action = str(item.get("action") or "")
            href = str(item.get("href") or "")
            action_name = semantic_action or ("open" if href else "click")
            ordinal = int(item.get("ordinal") or 0)
            aliases = [
                str(item.get("text") or ""),
                str(item.get("aria") or ""),
                str(item.get("title") or ""),
                href,
                kind,
                semantic_action,
                "song" if kind in {"video-link", "video-player"} else "",
                "track" if kind in {"video-link", "video-player"} else "",
                "video" if kind in {"video-link", "video-player"} else "",
            ]
            actions.append(
                _make_action(
                    action_id=f"browser:{item.get('index', len(actions))}:{len(actions)}",
                    source="browser",
                    action=action_name,
                    label=label or kind,
                    type_=kind,
                    aliases=aliases,
                    ordinal=ordinal,
                    group=title or url,
                    state={"pageTitle": title, "url": url},
                    payload={"provider": "foxmcp", "tab_id": tab_id, "target": item},
                )
            )
        return actions

    if not _browser_context_is_fresh():
        return []
    context = _latest_browser_context()
    title = str(context.get("title") or "")
    url = str(context.get("url") or "")
    interactables = context.get("interactables") if isinstance(context.get("interactables"), list) else []
    for idx, item in enumerate(interactables):
        if not isinstance(item, dict):
            continue
        label = str(item.get("text") or item.get("aria") or item.get("title") or item.get("href") or "").strip()
        kind = str(item.get("kind") or "element")
        semantic_action = str(item.get("action") or "")
        href = str(item.get("href") or "")
        actions.append(
            _make_action(
                action_id=f"browser-extension:{idx}",
                source="browser",
                action=semantic_action or ("open" if href else "click"),
                label=label or kind,
                type_=kind,
                aliases=[label, href, kind, semantic_action],
                ordinal=int(item.get("ordinal") or 0),
                group=title or url,
                state={"pageTitle": title, "url": url},
                payload={"provider": "extension", "query": label or href or kind},
            )
        )
    return actions


def _pc_action_candidates() -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for idx, (title, exe, hwnd) in enumerate(_enumerate_visible_windows()[:40]):
        app = Path(exe).name if exe else "window"
        actions.append(
            _make_action(
                action_id=f"window:{hwnd}",
                source="pc",
                action="switch",
                label=title,
                type_="window",
                aliases=[title, app, title.split(" - ")[0]],
                ordinal=idx + 1,
                group="visible windows",
                state={"app": app, "exe": exe},
                payload={"hwnd": hwnd},
            )
        )

    app_targets = {
        "cursor": "cursor",
        "firefox": "firefox",
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "chatgpt": "https://chatgpt.com",
    }
    for name, target in app_targets.items():
        action = "navigate" if target.startswith("http") else "open"
        actions.append(
            _make_action(
                action_id=f"app:{name}",
                source="pc",
                action=action,
                label=name,
                type_="app" if action == "open" else "website",
                aliases=[name, target],
                payload={"target": target},
            )
        )

    for name, path in KNOWN_PATHS.items():
        actions.append(
            _make_action(
                action_id=f"path:{name}",
                source="pc",
                action="open",
                label=name,
                type_="path",
                aliases=[name, path, Path(path).name],
                payload={"path": path},
            )
        )

    for root in GIT_ROOTS:
        actions.append(
            _make_action(
                action_id=f"tool:git-status:{root}",
                source="tool",
                action="check",
                label=f"git status {root.name}",
                type_="git",
                aliases=["git", "status", "changes", root.name, str(root)],
                payload={"tool": "git_status", "project_path": str(root)},
            )
        )
    return actions


def _action_snapshot(command: str = "") -> dict[str, object]:
    snapshot_id = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    actions = [*_browser_action_candidates(), *_pc_action_candidates()]
    return {
        "id": snapshot_id,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "actions": [_truncate_value(action) for action in actions],
    }


def _write_action_snapshot(snapshot: dict[str, object]) -> None:
    log_dir = _log_dir()
    actions = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    (log_dir / "current-actions.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Current Actions",
        "",
        f"Snapshot: {snapshot.get('id')}",
        f"Created: {snapshot.get('createdAt')}",
        f"Command: {snapshot.get('command')}",
        "",
    ]
    grouped: dict[str, list[dict[str, object]]] = {}
    for action in actions:
        if isinstance(action, dict):
            grouped.setdefault(str(action.get("source") or "unknown"), []).append(action)
    for source, items in grouped.items():
        lines.append(f"## {source}")
        for item in items[:80]:
            bits = [
                str(item.get("action") or ""),
                str(item.get("type") or ""),
                str(item.get("label") or ""),
            ]
            ordinal = item.get("ordinal")
            if ordinal:
                bits.append(f"ordinal={ordinal}")
            lines.append("- " + " | ".join(bit for bit in bits if bit))
        lines.append("")
    (log_dir / "current-actions.md").write_text("\n".join(lines), encoding="utf-8")

    snap_dir = log_dir / "action-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{snapshot.get('id')}.json"
    snap_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_action_decision(decision: dict[str, object]) -> None:
    path = _log_dir() / "action-decisions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_truncate_value(decision), ensure_ascii=False) + "\n")


def _action_query_intent(command: str) -> tuple[str, int | None]:
    lowered = command.lower()
    action = ""
    if re.search(r"\b(play|start|resume)\b", lowered):
        action = "play"
    elif re.search(r"\b(switch|focus|activate)\b", lowered):
        action = "switch"
    elif re.search(r"\b(open|go|navigate|show|take)\b", lowered):
        action = "open"
    elif re.search(r"\b(click|press|select|choose)\b", lowered):
        action = "click"
    elif re.search(r"\b(check|status|changes)\b", lowered):
        action = "check"
    return action, _query_ordinal(lowered)


def _score_action(command: str, candidate: dict[str, object]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    words = set(_action_words(command))
    action_intent, wanted_ordinal = _action_query_intent(command)
    fields = [
        str(candidate.get("label") or ""),
        str(candidate.get("action") or ""),
        str(candidate.get("type") or ""),
        str(candidate.get("source") or ""),
        str(candidate.get("group") or ""),
        *[str(alias) for alias in candidate.get("aliases", []) if alias],
    ]
    haystack = " ".join(fields)
    hay_words = set(_action_words(haystack))
    score = max((_browser_match_score(command, field) for field in fields if field), default=0.0)
    if score:
        reasons.append(f"text={score:.2f}")
    if words and hay_words:
        overlap = len(words & hay_words) / max(1, len(words | hay_words))
        score += overlap * 0.8
        if overlap:
            reasons.append(f"word-overlap={overlap:.2f}")
    candidate_action = str(candidate.get("action") or "")
    candidate_type = str(candidate.get("type") or "")
    if action_intent:
        if action_intent == candidate_action:
            score += 1.2
            reasons.append("action-exact")
        elif action_intent == "open" and candidate_action in {"open", "navigate"}:
            score += 1.0
            reasons.append("open-compatible")
        elif action_intent == "click" and candidate_action in {"click", "open", "play"}:
            score += 0.5
            reasons.append("click-compatible")
    if any(word in words for word in {"song", "track", "music", "video", "media"}) and candidate_type in {
        "video-link",
        "video-player",
        "media",
    }:
        score += 1.0
        reasons.append("media-type")
    ordinal = int(candidate.get("ordinal") or 0)
    if wanted_ordinal:
        media_query = any(word in words for word in {"song", "track", "music", "video", "media"})
        browser_item_query = any(word in words for word in {"result", "item", "link", "button"})
        window_query = any(word in words for word in {"window", "app", "application"})
        ordinal_compatible = True
        if media_query:
            ordinal_compatible = candidate_type in {"video-link", "video-player", "media"}
        elif browser_item_query:
            ordinal_compatible = str(candidate.get("source") or "") == "browser"
        elif window_query:
            ordinal_compatible = candidate_type in {"window", "app"}
        if ordinal_compatible and ordinal == wanted_ordinal:
            score += 2.5
            reasons.append(f"ordinal={wanted_ordinal}")
        elif ordinal_compatible and ordinal:
            score -= 0.5
    source = str(candidate.get("source") or "")
    if source == "browser" and re.search(r"\b(page|site|youtube|song|playlist|video|play)\b", command.lower()):
        score += 0.25
        reasons.append("browser-context")
    return score, reasons


def _resolve_action(command: str, snapshot: dict[str, object]) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    actions = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    ranked: list[dict[str, object]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        score, reasons = _score_action(command, action)
        ranked.append({"score": round(score, 4), "reasons": reasons, "action": action})
    ranked.sort(key=lambda item: float(item["score"]), reverse=True)
    if not ranked or float(ranked[0]["score"]) < 0.65:
        return None, ranked[:8]
    if len(ranked) > 1 and float(ranked[0]["score"]) - float(ranked[1]["score"]) < 0.15:
        return None, ranked[:8]
    return ranked[0]["action"], ranked[:8]


def _execute_action_candidate(candidate: dict[str, object]) -> str:
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    source = str(candidate.get("source") or "")
    action = str(candidate.get("action") or "")
    if source == "browser":
        provider = str(payload.get("provider") or "")
        if provider == "foxmcp":
            tab_id = int(payload.get("tab_id") or 0)
            target = payload.get("target")
            if tab_id and isinstance(target, dict):
                return _foxmcp_click_interactable(tab_id, target)
        query = str(payload.get("query") or candidate.get("label") or action)
        return _click_browser_context(query)

    if source == "pc" and candidate.get("type") == "window":
        hwnd = int(payload.get("hwnd") or 0)
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return "OK"
    if source == "pc" and candidate.get("type") == "website":
        target = str(payload.get("target") or "")
        if target:
            return _navigate_browser_context(target)
    if source == "pc" and candidate.get("type") == "app":
        target = str(payload.get("target") or candidate.get("label") or "")
        return _run_powershell(f"Start-Process '{target}'")
    if source == "pc" and candidate.get("type") == "path":
        path = str(payload.get("path") or "")
        if path:
            resolved = Path(path)
            return _open_folder_in_cursor(str(resolved)) if resolved.is_dir() else _run_powershell(f"Start-Process '{path}'")
    if source == "tool" and payload.get("tool") == "git_status":
        return _git_status(str(payload.get("project_path") or ""))
    return f"No executor for action: {candidate.get('id')}"


def _act_on_context(command: str) -> str:
    command = command.strip()
    if not command:
        return "No action command provided."
    snapshot = _action_snapshot(command)
    _write_action_snapshot(snapshot)
    selected, top = _resolve_action(command, snapshot)
    decision: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "snapshotId": snapshot.get("id"),
        "topMatches": top,
        "selected": selected,
    }
    if not selected:
        decision["result"] = "no-confident-action"
        _append_action_decision(decision)
        return "I couldn't confidently map that to an available action."
    result = _execute_action_candidate(selected)
    decision["executorResult"] = result
    ok = result == "OK" or result.startswith(("OK:", "Opened ", "Created ", "Pushed ", "Project:"))
    decision["verified"] = ok
    if ok:
        action_name = str(selected.get("action") or "")
        source = str(selected.get("source") or "")
        if source == "browser" or action_name in {"open", "navigate", "switch"}:
            time.sleep(1.5)
            post_snapshot = _action_snapshot(f"after: {command}")
            _write_action_snapshot(post_snapshot)
            decision["postSnapshotId"] = post_snapshot.get("id")
    _append_action_decision(decision)
    if ok:
        return "OK"
    return result


def _ensure_firefox_bridge_running(initial_url: str | None = None) -> str:
    """Verify the user's normal Firefox extension is reporting page context."""
    if _browser_provider() == "foxmcp":
        ready = _ensure_foxmcp_running()
        if ready != "OK":
            return ready
        return "OK"

    if _browser_context_is_fresh():
        return "OK"
    if initial_url:
        opened = _open_firefox_url(initial_url)
        return "OPENED_URL" if opened == "OK" else opened
    return "Jarvis needs the Firefox extension loaded in your normal Firefox first."


def _wait_for_browser_context(timeout_sec: float = 15.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _browser_context_is_fresh(max_age_sec=timeout_sec + 5):
            return True
        time.sleep(0.25)
    return False


def _read_browser_context(question: str = "") -> str:
    if _browser_provider() == "foxmcp":
        return _foxmcp_read_browser_context(question)

    bridge_status = _ensure_firefox_bridge_running()
    if bridge_status != "OK":
        return bridge_status
    if not _browser_context_is_fresh() and not _wait_for_browser_context():
        return "Firefox bridge is starting. Use Firefox normally, then ask again once the page loads."

    context = _latest_browser_context()
    if not context:
        return "No Firefox page context received yet. Use Firefox normally, then ask again once the page loads."

    age = time.time() - float(context.get("received_at", 0))
    title = str(context.get("title", ""))
    url = str(context.get("url", ""))
    question_lower = (question or "").lower()
    links = context.get("links") if isinstance(context.get("links"), list) else []
    visible_text = context.get("visibleText") if isinstance(context.get("visibleText"), list) else []

    if "playlist" in question_lower:
        names: list[str] = []
        for item in links:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            href = str(item.get("href", ""))
            if text and ("playlist" in href or "list=" in href):
                names.append(text)
        if not names:
            for line in visible_text:
                text = str(line).strip()
                if text and len(text) <= 100:
                    names.append(text)
        unique = list(dict.fromkeys(names))[:30]
        if unique:
            return "\n".join(unique)
        return f"I can read Firefox, but I do not see playlist names on this page. Page: {title}"

    lines = [f"Title: {title}", f"URL: {url}", f"Age: {age:.1f}s", "Visible text:"]
    lines.extend(str(line) for line in visible_text[:80])
    return "\n".join(lines)


def _find_browser_link(query: str) -> str | None:
    query_words = [word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) > 1]
    if not query_words:
        return None
    context = _latest_browser_context()
    links = context.get("links") if isinstance(context.get("links"), list) else []
    best_score = 0
    best_href: str | None = None
    for item in links:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        href = str(item.get("href", ""))
        haystack = f"{text} {href}".lower()
        score = sum(1 for word in query_words if word in haystack)
        if score > best_score and href.startswith(("http://", "https://")):
            best_score = score
            best_href = href
    return best_href


def _send_browser_command(action: str, **payload: object) -> str:
    global _browser_command, _browser_command_result
    initial_url = str(payload.get("url", "")) if action == "navigate" else None
    bridge_status = _ensure_firefox_bridge_running(initial_url=initial_url)
    if bridge_status == "OPENED_URL":
        return "OK"
    if bridge_status != "OK":
        return bridge_status
    if not _browser_context_is_fresh() and not _wait_for_browser_context():
        return "Firefox bridge is starting. Use Firefox normally, then ask again once the page loads."
    if action == "navigate" and initial_url:
        context = _latest_browser_context()
        current_url = str(context.get("url", ""))
        if current_url.startswith(initial_url.rstrip("/")):
            return "OK"

    command_id = str(time.time_ns())
    command = {"id": command_id, "action": action, **payload}
    with _browser_command_lock:
        _browser_command = command
        _browser_command_result = None

    deadline = time.time() + 8
    while time.time() < deadline:
        with _browser_command_lock:
            result = dict(_browser_command_result) if _browser_command_result else None
        if result and result.get("id") == command_id:
            if result.get("ok"):
                return str(result.get("message") or "OK")
            return f"Browser command failed: {result.get('error') or result.get('message') or 'unknown error'}"
        time.sleep(0.1)
    return "Browser command timed out. Is the Firefox bridge extension loaded?"


def _navigate_browser_context(url: str) -> str:
    if _browser_provider() == "foxmcp":
        return _foxmcp_navigate_browser_context(url)
    return _send_browser_command("navigate", url=_validate_url(url))


def _click_browser_context(query: str) -> str:
    query = query.strip()
    if not query:
        return "No click target provided."
    if _browser_provider() == "foxmcp":
        return _foxmcp_click_browser_context(query)
    return _send_browser_command("click", query=query)


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


def _screenshot_base64() -> str:
    from PIL import ImageGrab

    image = ImageGrab.grab()
    max_width = int(os.environ.get("JARVIS_VISION_MAX_WIDTH", "1280"))
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)))

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _describe_screen(question: str = "") -> str:
    question = (question or "Describe the visible screen and extract important text.").strip()
    prompt = (
        "You are helping a voice assistant understand the user's visible Windows screen. "
        "Extract visible text, page/app names, buttons, links, and list items. "
        "If this is YouTube playlists, list the visible playlist names. "
        "Be concise and do not invent items that are not visible.\n\n"
        f"User question: {question}"
    )
    payload = {
        "model": os.environ.get("JARVIS_VISION_MODEL", VISION_MODEL),
        "prompt": prompt,
        "images": [_screenshot_base64()],
        "stream": False,
        "keep_alive": -1,
    }
    req = urllib.request.Request(
        f"{os.environ.get('JARVIS_OLLAMA_URL', OLLAMA_URL)}/api/generate",
        data=__import__("json").dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return (data.get("response") or "").strip() or "I couldn't read the screen."


def _browser_port() -> int:
    return int(os.environ.get("JARVIS_BROWSER_DEBUG_PORT", "9222"))


def _browser_profile_dir() -> Path:
    raw = os.environ.get("JARVIS_BROWSER_PROFILE_DIR")
    if raw:
        return Path(raw).resolve()
    return (Path(__file__).resolve().parents[1] / "logs" / "jarvis-browser-profile").resolve()


def _browser_exe() -> str | None:
    return _first_existing(BROWSER_EXE_CANDIDATES)


def _cdp_url(path: str) -> str:
    return f"http://127.0.0.1:{_browser_port()}{path}"


def _cdp_json(path: str, timeout: int = 5) -> object:
    with urllib.request.urlopen(_cdp_url(path), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _cdp_ready() -> bool:
    try:
        _cdp_json("/json/version", timeout=2)
        return True
    except Exception:
        return False


def _start_jarvis_browser(url: str | None = None) -> str:
    exe = _browser_exe()
    if not exe:
        return "No supported browser found. Install Microsoft Edge or Google Chrome."

    profile = _browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={_browser_port()}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--new-window",
    ]
    if url:
        args.append(url)

    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        if _cdp_ready():
            return "OK"
        time.sleep(0.25)
    return "Browser started, but remote control did not become ready."


def _open_jarvis_browser(url: str) -> str:
    safe_url = _validate_url(url)
    if not _cdp_ready():
        started = _start_jarvis_browser(safe_url)
        return started

    req = urllib.request.Request(_cdp_url("/json/new?" + urllib.parse.quote(safe_url, safe=":/?&=%")), method="PUT")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()
    return "OK"


def _browser_tabs() -> list[dict]:
    tabs = _cdp_json("/json", timeout=5)
    return tabs if isinstance(tabs, list) else []


def _best_browser_tab() -> dict | None:
    tabs = [tab for tab in _browser_tabs() if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")]
    if not tabs:
        return None
    for tab in tabs:
        if "youtube.com" in str(tab.get("url", "")).lower():
            return tab
    return tabs[0]


def _cdp_evaluate(tab: dict, expression: str) -> object:
    import websocket

    ws = websocket.create_connection(str(tab["webSocketDebuggerUrl"]), timeout=10)
    try:
        payload = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }
        ws.send(json.dumps(payload))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                if "exceptionDetails" in data:
                    raise RuntimeError(str(data["exceptionDetails"])[:500])
                return data.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def _read_jarvis_browser(question: str = "") -> str:
    if not _cdp_ready():
        return "Jarvis browser is not running. Open YouTube with Jarvis first."

    tab = _best_browser_tab()
    if not tab:
        return "I could not find a readable Jarvis browser tab."

    expression = r"""
(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const lines = document.body.innerText
    .split(/\n+/)
    .map(s => s.trim())
    .filter(Boolean)
    .filter(s => s.length <= 120);

  const links = Array.from(document.querySelectorAll('a'))
    .filter(visible)
    .map(a => ({
      text: (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' '),
      href: a.href || ''
    }))
    .filter(x => x.text && x.text.length <= 120);

  return JSON.stringify({
    title: document.title,
    url: location.href,
    visibleText: Array.from(new Set(lines)).slice(0, 120),
    links: links.slice(0, 80)
  });
})()
"""
    raw = _cdp_evaluate(tab, expression)
    if not raw:
        return "I could not read text from the Jarvis browser."

    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return str(raw)[:4000]

    question_lower = (question or "").lower()
    if "playlist" in question_lower:
        candidates: list[str] = []
        for item in data.get("links", []):
            text = str(item.get("text", "")).strip()
            href = str(item.get("href", ""))
            if text and ("playlist" in href or "/playlist" in href or "list=" in href):
                candidates.append(text)
        if not candidates:
            for line in data.get("visibleText", []):
                if line and line not in candidates:
                    candidates.append(str(line))
        names = list(dict.fromkeys(candidates))[:20]
        return "\n".join(names) if names else "I do not see playlist names in the Jarvis browser."

    lines = [f"Title: {data.get('title', '')}", f"URL: {data.get('url', '')}", "Visible text:"]
    lines.extend(str(line) for line in data.get("visibleText", [])[:60])
    return "\n".join(lines)


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


def _shell_env() -> dict[str, str]:
    env = os.environ.copy()
    extra = r"C:\Program Files\Git\cmd;C:\Program Files\GitHub CLI"
    path_key = "Path" if "Path" in env else "PATH"
    path = env.get(path_key, "")
    if "Git\\cmd" not in path:
        env[path_key] = path + ";" + extra
    return env


def _first_existing(candidates: list[Path]) -> str | None:
    for candidate in candidates:
        raw = str(candidate)
        if not raw or raw == ".":
            continue
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _resolve_command(args: list[str]) -> list[str]:
    if not args:
        return args
    if args[0].lower() == "git":
        git_exe = _first_existing(GIT_EXE_CANDIDATES)
        if git_exe:
            return [git_exe, *args[1:]]
    if args[0].lower() == "gh":
        gh_exe = _first_existing(GH_EXE_CANDIDATES)
        if gh_exe:
            return [gh_exe, *args[1:]]
    return args


def _run_cmd(
    args: list[str],
    cwd: Path,
    timeout: int = 120,
) -> tuple[int, str]:
    joined = " ".join(args).lower()
    for blocked in BLOCKED_GIT_ARGS:
        if blocked in joined:
            return 1, f"Blocked dangerous git command: {blocked}"

    proc = subprocess.run(
        _resolve_command(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
        env=_shell_env(),
    )
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return proc.returncode, out or "(no output)"


def _git_status(project_path: str) -> str:
    root = _resolve_git_project(project_path)
    if not (root / ".git").exists():
        return f"Not a git repo yet: {root}"

    parts: list[str] = [f"Project: {root}"]
    code, branch = _run_cmd(["git", "branch", "--show-current"], root, timeout=30)
    if code == 0:
        parts.append(f"Branch: {branch}")
    else:
        parts.append(f"Branch check failed: {branch}")

    code, remote = _run_cmd(["git", "remote", "get-url", "origin"], root, timeout=30)
    if code == 0:
        parts.append(f"Remote: {remote}")
    else:
        parts.append(f"Remote check failed: {remote}")

    code, status = _run_cmd(["git", "status", "--short"], root, timeout=30)
    if code == 0:
        parts.append("Changes:\n" + (status if status else "(clean working tree)"))
    else:
        parts.append(f"Status check failed: {status}")

    return "\n".join(parts)


def _github_publish_project(
    project_path: str,
    repo_name: str,
    commit_message: str,
    visibility: str = "public",
    org: str | None = None,
) -> str:
    root = _resolve_git_project(project_path)
    if not root.is_dir():
        return f"Not a directory: {root}"

    org_name = (org or GITHUB_ORG).strip()
    repo_name = repo_name.strip().lower().replace(" ", "-")
    vis_flag = "--private" if visibility == "private" else "--public"
    full_name = f"{org_name}/{repo_name}"
    repo_url = f"https://github.com/{full_name}"

    if not (root / ".git").exists():
        code, out = _run_cmd(["git", "init", "-b", "main"], root)
        if code != 0:
            return f"git init failed:\n{out}"

    code, out = _run_cmd(["git", "add", "-A"], root)
    if code != 0:
        return f"git add failed:\n{out}"

    code, status = _run_cmd(["git", "status", "--porcelain"], root)
    if code != 0:
        return f"git status failed:\n{status}"

    if status.strip():
        code, out = _run_cmd(["git", "commit", "-m", commit_message], root)
        if code != 0:
            return f"git commit failed:\n{out}"

    code, remote = _run_cmd(["git", "remote", "get-url", "origin"], root)
    if code != 0:
        code, out = _run_cmd(
            [
                "gh",
                "repo",
                "create",
                full_name,
                vis_flag,
                "--source=.",
                "--remote=origin",
                "--push",
            ],
            root,
            timeout=180,
        )
        if code != 0:
            return f"github create/push failed:\n{out}"
        return f"Created and pushed {repo_url}"

    code, out = _run_cmd(["git", "push", "-u", "origin", "HEAD"], root, timeout=180)
    if code != 0:
        return f"git push failed:\n{out}"

    return f"Pushed latest code to {repo_url}"


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


def _git_command(project_path: str, args: list[str]) -> str:
    if not args:
        return "No git arguments provided"
    if any(not isinstance(arg, str) or not arg.strip() for arg in args):
        return "Git args must be non-empty strings"
    root = _resolve_git_project(project_path)
    code, out = _run_cmd(["git", *args], root, timeout=180)
    if code != 0:
        return f"Exit {code}\n{out}"
    return out


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
            result = _click_browser_context(arguments["query"])
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
            return _click_browser_context(arguments["query"])
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

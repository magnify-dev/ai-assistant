"""Safe local tools for the voice assistant agent."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", Path.home() / "Documents")).resolve()
GITHUB_ORG = os.environ.get("JARVIS_GITHUB_ORG", "magnify-dev")
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

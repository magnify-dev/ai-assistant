"""Jarvis tools - foxmcp.connection.py"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from jarvis_tools.constants import (
    FIREFOX_EXE_CANDIDATES,
    _browser_provider,
    _foxmcp_connect_thread,
    _foxmcp_lock,
    _foxmcp_process,
)
from jarvis_tools.paths import _first_existing, _python_exe, _repo_root


def _firefox_exe() -> str | None:
    return _first_existing(FIREFOX_EXE_CANDIDATES)

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

def _foxmcp_ws_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", _foxmcp_ws_port()), timeout=1.0):
            return True
    except OSError:
        return False

def _foxmcp_servers_ready() -> bool:
    return _foxmcp_ready() and _foxmcp_ws_listening()

def _foxmcp_probe_extension_connection() -> tuple[bool, str]:
    """Check whether the FoxMCP Firefox extension can answer tab requests."""
    if _ensure_foxmcp_running() != "OK":
        return False, "FoxMCP server not ready"
    from jarvis_tools.foxmcp.client import _foxmcp_call_tool_async

    try:
        tabs = asyncio.run(_foxmcp_call_tool_async("tabs_list", {}))
    except Exception as exc:
        logging.info("FoxMCP extension probe failed: %s", exc)
        return False, str(exc)
    lowered = tabs.lower()
    if "no extension connection" in lowered:
        return False, tabs
    if "open tabs" in lowered or "no tabs found" in lowered:
        return True, tabs
    if re.search(r"- ID \d+:", tabs):
        return True, tabs
    logging.info("FoxMCP extension probe inconclusive: %s", tabs[:200])
    return False, tabs

def _foxmcp_extension_client_count() -> int:
    connected, _detail = _foxmcp_probe_extension_connection()
    return 1 if connected else 0

def _foxmcp_connect_timeout_sec() -> float:
    return float(os.environ.get("JARVIS_FOXMCP_CONNECT_TIMEOUT", "45"))

def _foxmcp_startup_wait_sec() -> float:
    return float(os.environ.get("JARVIS_FOXMCP_STARTUP_WAIT", "8"))

def _foxmcp_auto_connect_firefox() -> bool:
    raw = os.environ.get("JARVIS_FOXMCP_AUTO_CONNECT", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}

def _firefox_is_running() -> bool:
    proc = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq firefox.exe", "/NH"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "firefox.exe" in proc.stdout.lower()

def _launch_firefox(url: str = "") -> str:
    exe = _firefox_exe()
    if not exe:
        return "Firefox executable not found"
    args = [exe]
    if url.strip():
        raw = url.strip()
        if raw.lower().startswith("about:"):
            args.append(raw)
        else:
            from jarvis_tools.pc_ops import _validate_url

            args.append(_validate_url(raw))
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return "OK"
    except Exception as exc:
        return f"Could not launch Firefox: {exc}"

def _wait_for_foxmcp_extension(timeout_sec: float) -> int:
    deadline = time.time() + timeout_sec
    next_log = 0.0
    while time.time() < deadline:
        count = _foxmcp_extension_client_count()
        if count > 0:
            return count
        if time.time() >= next_log:
            remaining = max(0, int(deadline - time.time()))
            logging.info("Waiting for FoxMCP Firefox extension (%ss remaining)...", remaining)
            next_log = time.time() + 5
        time.sleep(0.5)
    return _foxmcp_extension_client_count()

def _foxmcp_connected_message(count: int) -> str:
    label = "client" if count == 1 else "clients"
    return f"FoxMCP connected ({count} extension {label})"

def _foxmcp_prepare_firefox_for_connect() -> None:
    if not _foxmcp_auto_connect_firefox():
        return
    if _firefox_is_running():
        logging.info("Firefox already running; waiting for FoxMCP extension to connect")
        return
    logging.info("FoxMCP extension not connected — opening Firefox")
    launch = _launch_firefox("about:blank")
    if launch != "OK":
        logging.warning("%s", launch)

def _foxmcp_connect_background_worker() -> None:
    count = _wait_for_foxmcp_extension(_foxmcp_connect_timeout_sec())
    if count > 0:
        logging.info("FoxMCP Firefox extension connected in background (%s client(s))", count)
        try:
            from log_util import ui

            ui(f"Browser: {_foxmcp_connected_message(count)}")
        except ImportError:
            pass
        return
    logging.warning(
        "FoxMCP Firefox extension did not connect. "
        "Install the FoxMCP add-on in Firefox (addons.mozilla.org) and keep Firefox open."
    )

def _start_foxmcp_connect_background() -> None:
    global _foxmcp_connect_thread
    with _foxmcp_lock:
        if _foxmcp_connect_thread and _foxmcp_connect_thread.is_alive():
            return
        _foxmcp_connect_thread = threading.Thread(
            target=_foxmcp_connect_background_worker,
            name="foxmcp-connect",
            daemon=True,
        )
        _foxmcp_connect_thread.start()

def _ensure_foxmcp_extension_connected(*, max_wait_sec: float | None = None) -> str:
    ready = _ensure_foxmcp_running()
    if ready != "OK":
        return ready

    count = _foxmcp_extension_client_count()
    if count > 0:
        logging.info("FoxMCP browser extension connected (%s client(s))", count)
        return "OK"

    if not _foxmcp_auto_connect_firefox():
        msg = "FoxMCP server running; Firefox extension not connected"
        logging.warning(msg)
        return msg

    _foxmcp_prepare_firefox_for_connect()
    wait = _foxmcp_connect_timeout_sec() if max_wait_sec is None else max(0.0, max_wait_sec)
    if wait > 0:
        count = _wait_for_foxmcp_extension(wait)
        if count > 0:
            logging.info("FoxMCP Firefox extension connected (%s client(s))", count)
            return "OK"

    msg = (
        "FoxMCP server running; Firefox extension not connected. "
        "Install the FoxMCP add-on in Firefox (addons.mozilla.org) and keep Firefox open."
    )
    logging.warning(msg)
    return msg

def start_configured_browser_control(*, startup: bool = False) -> str:
    """Start browser control and connect the FoxMCP Firefox extension when needed."""
    if _browser_provider() != "foxmcp":
        return "OK"

    ready = _ensure_foxmcp_running()
    if ready != "OK":
        return ready

    count = _foxmcp_extension_client_count()
    if count > 0:
        return _foxmcp_connected_message(count)

    if startup:
        _foxmcp_prepare_firefox_for_connect()
        count = _wait_for_foxmcp_extension(_foxmcp_startup_wait_sec())
        if count > 0:
            return _foxmcp_connected_message(count)
        _start_foxmcp_connect_background()
        return "FoxMCP server started; connecting Firefox in background"

    result = _ensure_foxmcp_extension_connected()
    if result != "OK":
        return result
    count = _foxmcp_extension_client_count()
    return _foxmcp_connected_message(count)

def _ensure_foxmcp_running() -> str:
    global _foxmcp_process
    if _foxmcp_servers_ready():
        return "OK"

    with _foxmcp_lock:
        if _foxmcp_process and _foxmcp_process.poll() is None:
            deadline = time.time() + 10
            while time.time() < deadline:
                if _foxmcp_servers_ready():
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
        if _foxmcp_servers_ready():
            return "OK"
        if _foxmcp_process and _foxmcp_process.poll() is not None:
            _foxmcp_process = None
            return "FoxMCP server failed to start. Check logs/foxmcp.log."
        time.sleep(0.25)
    return "FoxMCP server is starting. Ask again in a moment."


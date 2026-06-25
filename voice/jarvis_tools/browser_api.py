"""Jarvis tools - browser_api.py"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from jarvis_tools.browser_bridge import (
    _browser_context_is_fresh,
    _ensure_firefox_bridge_running,
    _latest_browser_context,
    _send_browser_command,
    _wait_for_browser_context,
)
from jarvis_tools.constants import BROWSER_EXE_CANDIDATES, FIREFOX_EXE_CANDIDATES, _browser_provider
from jarvis_tools.foxmcp.client import (
    _foxmcp_call_tool,
    _foxmcp_navigate_browser_context,
    _foxmcp_read_browser_context,
    _foxmcp_script_json,
    _foxmcp_target_tab_id,
)
from jarvis_tools.foxmcp.clicks import _foxmcp_click_browser_context
from jarvis_tools.foxmcp.scripts import _load_script
from jarvis_tools.paths import _browser_profile_dir, _first_existing, _repo_root
from jarvis_tools.pc_ops import _open_url, _run_powershell, _validate_url

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
        from jarvis_tools.foxmcp.connection import _ensure_foxmcp_running

        ready = _ensure_foxmcp_running()
        if ready != "OK":
            return ready
        status = _foxmcp_call_tool("debug_websocket_status", {})
        if "connected" in status.lower():
            return "FoxMCP browser control is connected."
        return "FoxMCP server is running. Open Firefox and check the FoxMCP extension connection status."
    return _open_firefox_extension_setup()

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

def _navigate_browser_context(url: str) -> str:
    if _browser_provider() == "foxmcp":
        return _foxmcp_navigate_browser_context(url)
    return _send_browser_command("navigate", url=_validate_url(url))

def _click_browser_context(query: str, *, utterance: str = "") -> str:
    query = query.strip()
    if not query:
        return "No click target provided."
    if _browser_provider() == "foxmcp":
        return _foxmcp_click_browser_context(query, utterance=utterance)
    return _send_browser_command("click", query=query)

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

def _browser_port() -> int:
    return int(os.environ.get("JARVIS_BROWSER_DEBUG_PORT", "9222"))

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

    expression = _load_script("read_jarvis_browser.js")
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


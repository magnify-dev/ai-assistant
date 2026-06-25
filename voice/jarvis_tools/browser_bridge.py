"""Jarvis tools - browser_bridge.py"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis_tools.constants import (
    _browser_command,
    _browser_command_lock,
    _browser_command_result,
    _browser_context,
    _browser_context_lock,
    _browser_bridge_server,
    _browser_provider,
)

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

def _ensure_firefox_bridge_running(initial_url: str | None = None) -> str:
    """Verify the user's normal Firefox extension is reporting page context."""
    if _browser_provider() == "foxmcp":
        from jarvis_tools.foxmcp.connection import _ensure_foxmcp_running

        ready = _ensure_foxmcp_running()
        if ready != "OK":
            return ready
        return "OK"

    if _browser_context_is_fresh():
        return "OK"
    if initial_url:
        from jarvis_tools.browser_api import _open_firefox_url

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


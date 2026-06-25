"""Jarvis tools - foxmcp.client.py"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from jarvis_tools.foxmcp.connection import (
    _ensure_foxmcp_extension_connected,
    _ensure_foxmcp_running,
    _foxmcp_mcp_url,
)
from jarvis_tools.constants import _foxmcp_work_tab_id
from jarvis_tools.pc_ops import _validate_url

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
        result = asyncio.run(_foxmcp_call_tool_async(name, arguments))
    except Exception as exc:
        return f"FoxMCP tool error: {exc}"
    if "no extension connection" in result.lower():
        logging.info("FoxMCP extension disconnected during %s — reconnecting", name)
        connect = _ensure_foxmcp_extension_connected()
        if connect != "OK":
            return result
        try:
            result = asyncio.run(_foxmcp_call_tool_async(name, arguments))
        except Exception as exc:
            return f"FoxMCP tool error: {exc}"
    return result

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

def _foxmcp_script_json(result: str) -> object | None:
    match = re.search(r"Script result .*?:\s*([\[{].*)\s*$", result, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

def _foxmcp_page_url(tab_id: int) -> str:
    result = _foxmcp_call_tool(
        "content_execute_script",
        {"tab_id": tab_id, "code": "(() => location.href)()"},
    )
    match = re.search(r"Script result .*?:\s*(https?://\S+)", result)
    return match.group(1).strip() if match else ""

def _foxmcp_tab_title(tab_id: int) -> str:
    tabs = _foxmcp_call_tool("tabs_list", {})
    match = re.search(rf"- ID {re.escape(str(tab_id))}: (.+?)(?:\s+\(active\))?$", tabs, re.MULTILINE)
    return match.group(1).strip() if match else ""

def _foxmcp_activate_tab(tab_id: int) -> None:
    for tool in ("tabs_switch", "tabs_activate", "tabs_focus"):
        result = _foxmcp_call_tool(tool, {"tab_id": tab_id})
        lowered = result.lower()
        if result and "error" not in lowered and "unknown tool" not in lowered and "not found" not in lowered:
            return

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

def _foxmcp_read_browser_context(question: str = "") -> str:
    tab_id = _foxmcp_target_tab_id()
    if tab_id is None:
        return "I can't see Firefox through FoxMCP yet. Make sure the FoxMCP extension is enabled."
    if re.search(r"\b(playing|paused|video|song|music|audio)\b", question.lower()):
        from jarvis_tools.foxmcp.playback import _foxmcp_video_state

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


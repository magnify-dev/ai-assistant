"""Jarvis tools - foxmcp.playback.py"""

from __future__ import annotations

import json
import logging
import re
import time

from jarvis_tools.constants import KEYEVENTF_KEYUP, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, VK_KEY_K, user32
from jarvis_tools.foxmcp.client import (
    _foxmcp_activate_tab,
    _foxmcp_call_tool,
    _foxmcp_script_json,
    _foxmcp_tab_title,
)
from jarvis_tools.foxmcp.scripts import _load_script
from jarvis_tools.windows import (
    _find_firefox_hwnd,
    _focus_hwnd,
    _os_click_screen,
    _os_press_key,
    _restore_foreground,
)

def _foxmcp_video_state(tab_id: int) -> str:
    script = _load_script("video_state.js")
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

def _foxmcp_video_is_playing(tab_id: int) -> bool:
    script = _load_script("video_is_playing.js")
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    data = _foxmcp_script_json(result)
    return isinstance(data, dict) and bool(data.get("playing"))

def _foxmcp_os_play_fallback(tab_id: int) -> str:
    if _foxmcp_video_is_playing(tab_id):
        logging.info("FoxMCP OS play fallback: already playing")
        return "OK"

    previous_hwnd = user32.GetForegroundWindow()
    firefox_hwnd = 0
    try:
        title = _foxmcp_tab_title(tab_id)
        _foxmcp_activate_tab(tab_id)
        time.sleep(0.25)

        firefox_hwnd = _find_firefox_hwnd(title)
        if not firefox_hwnd:
            return "Play blocked: Firefox window not found"

        focused = _focus_hwnd(firefox_hwnd)
        logging.info("FoxMCP OS play fallback: hwnd=%s focused=%s title=%r", firefox_hwnd, focused, title)
        time.sleep(0.2)

        _os_press_key(VK_KEY_K)
        time.sleep(0.9)
        if _foxmcp_video_is_playing(tab_id):
            logging.info("FoxMCP OS keyboard play succeeded")
            return "OK"

        _os_press_key(0x20)  # Space — YouTube play/pause
        time.sleep(0.9)
        if _foxmcp_video_is_playing(tab_id):
            logging.info("FoxMCP OS spacebar play succeeded")
            return "OK"

        raw = _foxmcp_call_tool(
            "content_execute_script",
            {"tab_id": tab_id, "code": _load_script("play_screen_target.js")},
        )
        data = _foxmcp_script_json(raw)
        if isinstance(data, dict) and data.get("screenX") is not None and data.get("screenY") is not None:
            _focus_hwnd(firefox_hwnd)
            time.sleep(0.15)
            _os_click_screen(int(data["screenX"]), int(data["screenY"]))
            time.sleep(0.9)
            if _foxmcp_video_is_playing(tab_id):
                logging.info("FoxMCP OS click play succeeded via %s", data.get("selector"))
                return "OK"

        return "Play blocked: allow autoplay for YouTube in Firefox, or click play once manually"
    finally:
        _restore_foreground(previous_hwnd, firefox_hwnd)


def _foxmcp_youtube_play(tab_id: int) -> str:
    result = _foxmcp_call_tool(
        "content_execute_script",
        {"tab_id": tab_id, "code": _load_script("youtube_play.js")},
    )
    if "OK:" in result or "already playing" in result.lower():
        logging.info("FoxMCP YouTube play succeeded")
        return "OK"
    logging.info("FoxMCP script play failed (%s), trying OS input fallback", result[:160])
    os_result = _foxmcp_os_play_fallback(tab_id)
    if os_result == "OK":
        return "OK"
    return os_result

def _foxmcp_press_play(tab_id: int) -> str:
    return _foxmcp_youtube_play(tab_id)

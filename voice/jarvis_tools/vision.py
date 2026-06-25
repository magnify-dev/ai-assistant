"""Jarvis tools - vision.py"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import urllib.request

from jarvis_tools.browser_api import _read_browser_context
from jarvis_tools.constants import OLLAMA_URL
from jarvis_tools.models import jarvis_ollama_model, jarvis_vision_model
from jarvis_tools.windows import get_active_window_title

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
    vision_model = jarvis_vision_model()
    if not vision_model:
        browser = _read_browser_context(question)
        if browser and not browser.lower().startswith(("i can't", "firefox bridge", "no firefox")):
            return browser
        return get_active_window_title()

    prompt = (
        "You are helping a voice assistant understand the user's visible Windows screen. "
        "Extract visible text, page/app names, buttons, links, and list items. "
        "If this is YouTube playlists, list the visible playlist names. "
        "Be concise and do not invent items that are not visible.\n\n"
        f"User question: {question}"
    )
    payload = {
        "model": vision_model,
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


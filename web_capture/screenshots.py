from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from web_capture.maps import site_key, _slug

SCREENSHOT_REL_DIR = "screenshots"


def screenshots_dir(project: Path) -> Path:
    return project / ".agent" / "web-capture" / SCREENSHOT_REL_DIR


def screenshot_rel_for_url(url: str, *, capture_id: str = "") -> str:
    base = _slug(site_key(url))
    suffix = f"-{capture_id}" if str(capture_id or "").strip() else ""
    return f"{SCREENSHOT_REL_DIR}/{base}{suffix}.jpg"


def screenshot_file_for_url(project: Path, url: str, *, capture_id: str = "") -> Path:
    rel = screenshot_rel_for_url(url, capture_id=capture_id)
    return project / ".agent" / "web-capture" / rel


def _decode_screenshot(screenshot_b64: str) -> bytes | None:
    raw = str(screenshot_b64 or "").strip()
    if not raw:
        return None
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    return data if len(data) >= 100 else None


def persist_screenshot(
    project: Path | None,
    url: str,
    screenshot_b64: str,
    *,
    capture_id: str = "",
) -> str | None:
    """Save viewport JPEG for a URL; returns relative path under web-capture/."""
    if not project or not url:
        return None
    data = _decode_screenshot(screenshot_b64)
    if not data:
        return None
    rel = screenshot_rel_for_url(url, capture_id=capture_id)
    path = project / ".agent" / "web-capture" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jpg.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return rel


def load_screenshot_b64(project: Path | None, url: str) -> str | None:
    if not project or not url:
        return None
    path = screenshot_file_for_url(project, url)
    if not path.is_file():
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def attach_screenshot_to_capture(capture: dict[str, Any], project: Path | None) -> dict[str, Any]:
    url = str(capture.get("url") or "")
    if not url:
        return capture
    rel = str(capture.get("screenshot") or "").strip() or screenshot_rel_for_url(url)
    if project and screenshot_file_for_url(project, url).is_file():
        capture["screenshot"] = rel
    elif not capture.get("screenshot"):
        return capture
    else:
        capture["screenshot"] = rel
    return capture

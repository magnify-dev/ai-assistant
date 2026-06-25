"""Jarvis tools - foxmcp.candidates.py"""

from __future__ import annotations

import json
import logging
import re
import time

from jarvis_tools.foxmcp.client import (
    _foxmcp_call_tool,
    _foxmcp_navigate_browser_context,
    _foxmcp_page_url,
    _foxmcp_script_json,
)
from jarvis_tools.foxmcp.scripts import _load_script
from jarvis_tools.paths import _log_dir
from jarvis_tools.text_match import extract_playlist_query

def _foxmcp_candidate_label(item: dict[str, object]) -> str:
    return str(item.get("text") or item.get("aria") or item.get("title") or "").strip()

_PLAYLIST_LABEL_NOISE = frozenset(
    {
        "ogled celotnega seznama",
        "view full playlist",
        "poznejsi ogled",
        "poznejši ogled",
        "recently watched",
        "vseckani videoposnetki",
        "všečkani videoposnetki",
        "liked videos",
        "seznami predvajanja",
        "playlists",
        "playlist",
    }
)

def _playlist_list_id(href: str) -> str:
    match = re.search(r"list=([^&]+)", href)
    return match.group(1) if match else href

def _is_playlist_label_noise(label: str) -> bool:
    normalized = label.casefold().strip()
    if not normalized or normalized in _PLAYLIST_LABEL_NOISE:
        return True
    if re.search(r"^\d+\s+(videoposnetkov|videos?)\b", normalized):
        return True
    if re.search(r"\b(videoposnetkov|videos?)\b", normalized) and len(normalized.split()) <= 3:
        return True
    return False

def _is_user_playlist_candidate(item: dict[str, object]) -> bool:
    """A specific user playlist tile/link, not the library index nav."""
    href = str(item.get("href") or "")
    label = _foxmcp_candidate_label(item)
    if not label or _is_playlist_label_noise(label):
        return False
    if href.rstrip("/").endswith("/feed/playlists") or href.rstrip("/").endswith("/feed/playlists/"):
        return False
    if "list=" in href:
        return True
    kind = str(item.get("kind") or "")
    return kind == "playlist-link" and "/playlist" in href

def _playlist_title_pool(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    """One entry per playlist, preferring the title link over view-full / count links."""
    by_list_id: dict[str, dict[str, object]] = {}
    for item in candidates:
        if not _is_user_playlist_candidate(item):
            continue
        href = str(item.get("href") or "")
        list_id = _playlist_list_id(href) or _foxmcp_candidate_label(item).casefold()
        label = _foxmcp_candidate_label(item)
        existing = by_list_id.get(list_id)
        if not existing:
            by_list_id[list_id] = item
            continue
        existing_label = _foxmcp_candidate_label(existing)
        if _is_playlist_label_noise(existing_label) and not _is_playlist_label_noise(label):
            by_list_id[list_id] = item
        elif len(label) < len(existing_label) and not _is_playlist_label_noise(label):
            by_list_id[list_id] = item
    return list(by_list_id.values())

def _named_playlist_intent(utterance: str, query: str) -> bool:
    lowered = f"{utterance} {query}".lower()
    if re.search(r"\b(play|video|song|track|button|pause)\b", query.lower()):
        return False
    name = extract_playlist_query(utterance) or query.strip()
    if not name or name.lower() in {"playlist", "playlists"}:
        return bool(re.search(r"\bplaylists?\b", lowered) and name)
    return bool(re.search(r"\bplaylists?\b", lowered) or len(name.split()) <= 6)

def _opened_user_playlist(url: str) -> bool:
    """True when URL is a specific playlist, not the library index."""
    return "list=" in url and "/feed/playlists" not in url.split("?", 1)[0]

def _foxmcp_ensure_playlists_library(tab_id: int) -> None:
    url = _foxmcp_page_url(tab_id)
    if "/feed/playlists" in url:
        return
    logging.info("Navigating to YouTube library before playlist selection")
    _foxmcp_navigate_browser_context("https://www.youtube.com/feed/playlists")
    time.sleep(1.2)

def _log_playlist_candidates(utterance: str, query: str, pool: list[dict[str, object]]) -> None:
    labels = [_foxmcp_candidate_label(item) for item in pool[:40]]
    logging.info(
        "Playlist candidates for %r / %r (%d): %s",
        utterance,
        query,
        len(pool),
        labels,
    )
    path = _log_dir() / "target-resolution.jsonl"
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "utterance": utterance,
        "query": query,
        "count": len(pool),
        "labels": labels,
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logging.debug("Could not write target-resolution log: %s", exc)

def _foxmcp_clickable_candidates(tab_id: int) -> list[dict[str, object]]:
    script = _load_script("clickable_candidates.js")
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    data = _foxmcp_script_json(result)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


"""Jarvis tools - foxmcp.clicks.py"""

from __future__ import annotations

import json
import logging
import re
import time

from jarvis_tools.foxmcp.candidates import (
    _foxmcp_candidate_label,
    _foxmcp_clickable_candidates,
    _foxmcp_ensure_playlists_library,
    _is_user_playlist_candidate,
    _log_playlist_candidates,
    _named_playlist_intent,
    _opened_user_playlist,
    _playlist_title_pool,
)
from jarvis_tools.foxmcp.client import (
    _foxmcp_call_tool,
    _foxmcp_page_url,
    _foxmcp_tab_title,
    _foxmcp_target_tab_id,
)
from jarvis_tools.foxmcp.playback import _foxmcp_press_play, _foxmcp_youtube_play
from jarvis_tools.foxmcp.scripts import _load_script
from jarvis_tools.llm_resolve import _llm_resolve_enabled, _resolve_foxmcp_target_with_llm
from jarvis_tools.text_match import (
    _browser_match_score,
    _rank_foxmcp_candidates,
    extract_playlist_query,
)

def _prefer_strong_fuzzy_match(
    query: str,
    llm_match: dict[str, object],
    pool: list[dict[str, object]],
) -> dict[str, object]:
    """Override a weak LLM pick when fuzzy text match is much stronger."""
    if not pool:
        return llm_match
    llm_label = _foxmcp_candidate_label(llm_match)
    llm_score = _browser_match_score(query, llm_label)
    best = max(pool, key=lambda item: _browser_match_score(query, _foxmcp_candidate_label(item)))
    best_label = _foxmcp_candidate_label(best)
    best_score = _browser_match_score(query, best_label)
    if best_score >= 0.75 and best_score - llm_score >= 0.25:
        logging.info(
            "Overriding LLM pick %r (%.2f) with stronger fuzzy match %r (%.2f)",
            llm_label,
            llm_score,
            best_label,
            best_score,
        )
        return best
    return llm_match

def _foxmcp_click_interactable(tab_id: int, candidate: dict[str, object]) -> str:
    kind = str(candidate.get("kind") or "")
    if kind in {"video-player", "play-button"}:
        return _foxmcp_youtube_play(tab_id)

    target = {
        "index": candidate.get("index"),
        "kind": candidate.get("kind"),
        "text": candidate.get("text"),
        "aria": candidate.get("aria"),
        "title": candidate.get("title"),
        "href": candidate.get("href"),
    }
    script = _load_script("click_interactable.js").replace("__TARGET__", json.dumps(target))
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    return "OK" if "OK:" in result else result

def _foxmcp_try_select_candidate(
    tab_id: int,
    query: str,
    resolve_text: str,
    candidates: list[dict[str, object]],
    *,
    page_hint: str,
    pool: list[dict[str, object]] | None,
    verify_playlist: bool,
) -> str | None:
    active_pool = pool if pool is not None else candidates

    if _llm_resolve_enabled():
        llm_match = _resolve_foxmcp_target_with_llm(
            resolve_text,
            candidates,
            page_hint=page_hint,
            pool=active_pool,
        )
        if llm_match:
            if pool is not None:
                llm_match = _prefer_strong_fuzzy_match(query, llm_match, active_pool)
            label = _foxmcp_candidate_label(llm_match)
            logging.info("FoxMCP LLM selected %r for %r", label, resolve_text)
            result = _foxmcp_click_interactable(tab_id, llm_match)
            if result == "OK":
                if verify_playlist:
                    time.sleep(0.6)
                    url = _foxmcp_page_url(tab_id)
                    if _opened_user_playlist(url):
                        logging.info("FoxMCP opened playlist URL: %s", url)
                        return "OK"
                    logging.info("FoxMCP LLM click did not open a playlist (url=%s)", url)
                else:
                    return "OK"

    search_pool = active_pool if pool is not None else candidates
    ranked = _rank_foxmcp_candidates(query, search_pool, utterance=resolve_text)

    min_score = 0.65 if verify_playlist else 0.72
    for score, best in ranked[:8]:
        if score < min_score:
            break
        label = _foxmcp_candidate_label(best)
        logging.info(
            "FoxMCP click trying %r for %r (score=%.2f)",
            label,
            query,
            score,
        )
        result = _foxmcp_click_interactable(tab_id, best)
        if result != "OK":
            logging.info("FoxMCP click failed for %r: %s", label, result[:300])
            continue
        if verify_playlist:
            time.sleep(0.6)
            url = _foxmcp_page_url(tab_id)
            if _opened_user_playlist(url):
                logging.info("FoxMCP opened playlist URL: %s", url)
                return "OK"
            logging.info("FoxMCP candidate %r did not open a playlist (url=%s)", label, url)
            continue
        return "OK"
    return None

def _foxmcp_click_browser_context(query: str, *, utterance: str = "") -> str:
    tab_id = _foxmcp_target_tab_id()
    if tab_id is None:
        return "I can't click Firefox through FoxMCP yet. Make sure the FoxMCP extension is enabled."
    query_lower = query.lower().strip(" .!?")
    resolve_text = (utterance or query).strip()
    named_playlist = _named_playlist_intent(resolve_text, query)
    candidates = _foxmcp_clickable_candidates(tab_id)
    page_hint = _foxmcp_tab_title(tab_id)

    if named_playlist:
        pool = _playlist_title_pool(candidates)
        _log_playlist_candidates(resolve_text, query, pool)
        best_fuzzy = max(
            (_browser_match_score(query, _foxmcp_candidate_label(item)) for item in pool),
            default=0.0,
        )
        if not pool or best_fuzzy < 0.55:
            _foxmcp_ensure_playlists_library(tab_id)
            candidates = _foxmcp_clickable_candidates(tab_id)
            pool = _playlist_title_pool(candidates)
            _log_playlist_candidates(resolve_text, query, pool)

        picked = _foxmcp_try_select_candidate(
            tab_id,
            query,
            resolve_text,
            candidates,
            page_hint=page_hint,
            pool=pool,
            verify_playlist=True,
        )
        if picked == "OK":
            return "OK"
        if pool:
            labels = ", ".join(_foxmcp_candidate_label(item) for item in pool[:12])
            return f"No playlist matched {query!r}. Visible: {labels}"
        return f"No playlists visible on the page for {query!r}."

    picked = _foxmcp_try_select_candidate(
        tab_id,
        query,
        resolve_text,
        candidates,
        page_hint=page_hint,
        pool=None,
        verify_playlist=False,
    )
    if picked == "OK":
        return "OK"

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
    script = _load_script("click_browser_fuzzy.js").replace("__QUERY__", json.dumps(target_query))
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    return "OK" if "OK:" in result else result

def _foxmcp_click_first_video(tab_id: int) -> str:
    script = _load_script("click_first_video.js")
    result = _foxmcp_call_tool("content_execute_script", {"tab_id": tab_id, "code": script})
    if "OK:" in result:
        logging.info("FoxMCP clicked first video")
        return "OK"
    return result


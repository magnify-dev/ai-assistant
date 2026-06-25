"""Jarvis tools - text_match.py"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from jarvis_tools.constants import ACTION_STOPWORDS, ACTION_SYNONYMS

def _browser_match_key(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))

def _browser_match_score(query: str, candidate: str) -> float:
    """Fallback text similarity when the local model is unavailable."""
    q_key = _browser_match_key(query)
    c_key = _browser_match_key(candidate)
    if not q_key or not c_key:
        return 0.0
    if q_key == c_key:
        return 1.0
    if q_key in c_key or c_key in q_key:
        return 0.94
    ratio = SequenceMatcher(None, q_key, c_key).ratio()
    q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    c_words = set(re.findall(r"[a-z0-9]+", candidate.lower()))
    overlap = len(q_words & c_words) / max(1, len(q_words | c_words))
    return max(ratio, overlap)

def _action_words(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", text.lower())
    expanded: list[str] = []
    for word in raw:
        if word in ACTION_STOPWORDS:
            continue
        expanded.append(word)
        expanded.extend(sorted(ACTION_SYNONYMS.get(word, set())))
    return list(dict.fromkeys(expanded))

def _query_ordinal(query: str) -> int | None:
    lowered = query.lower()
    ordinals = {
        "first": 1,
        "1st": 1,
        "second": 2,
        "2nd": 2,
        "third": 3,
        "3rd": 3,
        "fourth": 4,
        "4th": 4,
        "fifth": 5,
        "5th": 5,
    }
    for word, value in ordinals.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return value
    return None

def _looks_like_playlist_name_query(query: str) -> bool:
    lowered = query.lower().strip(" .!?")
    if not lowered or re.search(r"\b(play|video|song|track|button|pause)\b", lowered):
        return False
    words = re.findall(r"[a-z0-9]+", lowered)
    return 1 <= len(words) <= 8

def extract_playlist_query(text: str) -> str:
    """Pull a playlist title out of a spoken command."""
    cleaned = re.sub(r"\bplease\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(select|choose|open|click|press|go to|navigate to|show|take|pick)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(the|a|an|my)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bplaylists?\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" .!?")

def _foxmcp_candidate_haystack(item: dict[str, object]) -> str:
    fields = [
        item.get("text", ""),
        item.get("aria", ""),
        item.get("title", ""),
        item.get("href", ""),
        item.get("kind", ""),
        item.get("action", ""),
        item.get("role", ""),
    ]
    return " ".join(str(field) for field in fields if field).strip()

def _score_foxmcp_candidate(query: str, item: dict[str, object]) -> float:
    haystack = _foxmcp_candidate_haystack(item)
    if not haystack:
        return 0.0
    query_lower = query.lower().strip(" .!?")
    query_words = set(re.findall(r"[a-z0-9]+", query_lower))
    play_intent = bool(re.search(r"\b(play|start|resume)\b", query_lower))
    video_intent = bool(re.search(r"\b(video|song|track|music)\b", query_lower))
    playlist_query = _looks_like_playlist_name_query(query)
    wanted_ordinal = _query_ordinal(query_lower)

    fields = [str(item.get(key, "")) for key in ("text", "aria", "title", "href", "kind", "action") if item.get(key)]
    score = max(_browser_match_score(query, field) for field in fields)
    candidate_words = set(re.findall(r"[a-z0-9]+", haystack.lower()))
    if query_words and candidate_words:
        score += 0.35 * (len(query_words & candidate_words) / len(query_words | candidate_words))

    kind = str(item.get("kind", ""))
    action = str(item.get("action", ""))
    ordinal = int(item.get("ordinal") or 0)

    if playlist_query:
        from jarvis_tools.foxmcp.candidates import _is_user_playlist_candidate

        if _is_user_playlist_candidate(item):
            score += 2.5
        else:
            href = str(item.get("href") or "")
            if "/feed/playlists" in href:
                score -= 4.0
    if play_intent:
        if kind == "play-button":
            score += 2.5
        if action == "play":
            score += 1.5
        if "play" in haystack.lower():
            score += 0.5
        if kind == "video-player":
            score += 0.35
        if kind == "video-link":
            score += 0.35
    if video_intent and kind in {"video-link", "video-player"}:
        score += 0.7
    if wanted_ordinal and ordinal:
        score += 2.0 if ordinal == wanted_ordinal else -0.25
    return score

def _rank_foxmcp_candidates(
    query: str,
    candidates: list[dict[str, object]],
    *,
    utterance: str = "",
) -> list[tuple[float, dict[str, object]]]:
    from jarvis_tools.foxmcp.candidates import _named_playlist_intent, _playlist_title_pool

    playlist_query = _looks_like_playlist_name_query(query) or _named_playlist_intent(utterance, query)
    pool = candidates
    if playlist_query:
        named = _playlist_title_pool(candidates)
        if named:
            pool = named
    ranked = [(_score_foxmcp_candidate(query, item), item) for item in pool]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked

def _action_query_intent(command: str) -> tuple[str, int | None]:
    lowered = command.lower()
    action = ""
    if re.search(r"\b(play|start|resume)\b", lowered):
        action = "play"
    elif re.search(r"\b(switch|focus|activate)\b", lowered):
        action = "switch"
    elif re.search(r"\b(open|go|navigate|show|take)\b", lowered):
        action = "open"
    elif re.search(r"\b(click|press|select|choose)\b", lowered):
        action = "click"
    elif re.search(r"\b(check|status|changes)\b", lowered):
        action = "check"
    return action, _query_ordinal(lowered)

def _score_action(command: str, candidate: dict[str, object]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    words = set(_action_words(command))
    action_intent, wanted_ordinal = _action_query_intent(command)
    fields = [
        str(candidate.get("label") or ""),
        str(candidate.get("action") or ""),
        str(candidate.get("type") or ""),
        str(candidate.get("source") or ""),
        str(candidate.get("group") or ""),
        *[str(alias) for alias in candidate.get("aliases", []) if alias],
    ]
    haystack = " ".join(fields)
    hay_words = set(_action_words(haystack))
    score = max((_browser_match_score(command, field) for field in fields if field), default=0.0)
    if score:
        reasons.append(f"text={score:.2f}")
    if words and hay_words:
        overlap = len(words & hay_words) / max(1, len(words | hay_words))
        score += overlap * 0.8
        if overlap:
            reasons.append(f"word-overlap={overlap:.2f}")
    candidate_action = str(candidate.get("action") or "")
    candidate_type = str(candidate.get("type") or "")
    play_intent = action_intent == "play"
    if play_intent and candidate_type == "playlist-link":
        score -= 2.0
        reasons.append("playlist-not-play")
    if action_intent:
        if action_intent == candidate_action:
            score += 1.2
            reasons.append("action-exact")
        elif action_intent == "open" and candidate_action in {"open", "navigate"}:
            score += 1.0
            reasons.append("open-compatible")
        elif action_intent == "click" and candidate_action in {"click", "open", "play"}:
            score += 0.5
            reasons.append("click-compatible")
    if play_intent:
        if candidate_type == "play-button":
            score += 2.5
            reasons.append("play-button")
        if candidate_action == "play":
            score += 0.5
        if "play" in haystack.lower():
            score += 0.5
        if candidate_type == "video-player":
            score += 0.35
    if re.search(r"\bplaylist", command.lower()) and candidate_type == "playlist-link":
        score += 2.0
        reasons.append("playlist-link")
    if _looks_like_playlist_name_query(command) and candidate_type == "playlist-link":
        score += 1.5
        reasons.append("named-playlist")
    if any(word in words for word in {"song", "track", "music", "video", "media"}) and candidate_type in {
        "video-link",
        "video-player",
        "play-button",
        "media",
    }:
        score += 1.0
        reasons.append("media-type")
    ordinal = int(candidate.get("ordinal") or 0)
    if wanted_ordinal:
        media_query = any(word in words for word in {"song", "track", "music", "video", "media"})
        browser_item_query = any(word in words for word in {"result", "item", "link", "button"})
        window_query = any(word in words for word in {"window", "app", "application"})
        ordinal_compatible = True
        if media_query:
            ordinal_compatible = candidate_type in {"video-link", "video-player", "play-button", "media"}
        elif browser_item_query:
            ordinal_compatible = str(candidate.get("source") or "") == "browser"
        elif window_query:
            ordinal_compatible = candidate_type in {"window", "app"}
        if ordinal_compatible and ordinal == wanted_ordinal:
            score += 2.5
            reasons.append(f"ordinal={wanted_ordinal}")
        elif ordinal_compatible and ordinal:
            score -= 0.5
    source = str(candidate.get("source") or "")
    if source == "browser" and re.search(r"\b(page|site|youtube|song|playlist|video|play)\b", command.lower()):
        score += 0.25
        reasons.append("browser-context")
    return score, reasons


"""Jarvis tools - llm_resolve.py"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request

from jarvis_tools.constants import ACTION_STOPWORDS, OLLAMA_URL
from jarvis_tools.foxmcp.candidates import _foxmcp_candidate_label
from jarvis_tools.models import jarvis_ollama_model

def _llm_options_limit() -> int:
    try:
        return max(20, int(os.environ.get("JARVIS_LLM_OPTIONS_LIMIT", "120")))
    except ValueError:
        return 120

def _utterance_content_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - ACTION_STOPWORDS

def _limit_llm_options(utterance: str, options: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep option list within model context; prefer items sharing words with the utterance."""
    cap = _llm_options_limit()
    if len(options) <= cap:
        return options
    words = _utterance_content_words(utterance)
    if not words:
        return options[:cap]

    def sort_key(index_item: tuple[int, dict[str, object]]) -> tuple[int, int]:
        index, option = index_item
        label_words = set(re.findall(r"[a-z0-9]+", str(option.get("label") or "").lower()))
        return (-len(words & label_words), index)

    ordered = [option for _, option in sorted(enumerate(options), key=sort_key)]
    return ordered[:cap]

def _llm_resolve_enabled() -> bool:
    return os.environ.get("JARVIS_LLM_RESOLVE_TARGETS", "1") == "1"

def _parse_llm_json(content: str) -> dict | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

def _ollama_json_chat(system: str, user: str, *, timeout: float = 45.0) -> dict | None:
    payload = {
        "model": jarvis_ollama_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "keep_alive": -1,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{os.environ.get('JARVIS_OLLAMA_URL', OLLAMA_URL)}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        return _parse_llm_json(str(message.get("content") or ""))
    except Exception as exc:
        logging.warning("Local model target resolution failed: %s", exc)
        return None

def _use_llm_action_resolver(text: str) -> bool:
    if not _llm_resolve_enabled():
        return False
    lowered = text.lower().strip(" .!?")
    if len(lowered) < 2:
        return False
    if re.match(
        r"^(what|which|where|who|when|how|why|is|are|am|do|does|did|has|have|can|could|would|will|should)\b",
        lowered,
    ):
        return False
    return True

def _llm_pick_from_options(
    utterance: str,
    options: list[dict[str, object]],
    *,
    page_hint: str = "",
) -> int | None:
    """Ask the local model which listed action best matches the voice transcript."""
    if not options:
        return None
    visible = [
        {
            "index": idx,
            "label": str(item.get("label") or ""),
            "kind": str(item.get("kind") or item.get("type") or ""),
            "action": str(item.get("semantic_action") or item.get("action") or ""),
        }
        for idx, item in enumerate(options)
        if str(item.get("label") or "").strip()
    ]
    if not visible:
        return None

    hint = f"\nPage context: {page_hint}" if page_hint else ""
    prompt = (
        "Map a voice transcript to one available action from the list below.\n"
        "Speech-to-text is unreliable: words may be wrong, split, merged, or include filler.\n"
        "Match by meaning and by how the transcript would sound when spoken aloud, "
        "not by exact spelling. Spoken names are often split or merged "
        '(e.g. "dope as" often means a playlist titled "DopeAss").\n'
        "Use kind/action to disambiguate (e.g. play vs open vs click).\n"
        "Pick the single best match. Use null only when nothing in the list plausibly fits.\n"
        f"Transcript: {utterance!r}{hint}\n\n"
        "Available actions (ONLY pick from this list):\n"
        f"{json.dumps(visible, ensure_ascii=False)}\n\n"
        'Reply with JSON only: {"match_index": <number or null>, "reason": "<brief>"}'
    )
    parsed = _ollama_json_chat(
        "You map imperfect voice transcripts to one item from a provided action list. "
        "The transcript may mis-hear names and commands. Pick the closest listed item. "
        "Output only valid JSON.",
        prompt,
    )
    if not parsed:
        return None
    match_index = parsed.get("match_index")
    if match_index is None:
        logging.info("LLM found no target for %r: %s", utterance, parsed.get("reason", ""))
        return None
    try:
        idx = int(match_index)
    except (TypeError, ValueError):
        return None
    if 0 <= idx < len(options):
        logging.info(
            "LLM mapped %r -> %r (%s)",
            utterance,
            options[idx].get("label"),
            parsed.get("reason", ""),
        )
        return idx
    return None

def _foxmcp_candidates_to_llm_options(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in candidates:
        label = _foxmcp_candidate_label(item)
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "label": label,
                "kind": str(item.get("kind") or ""),
                "semantic_action": str(item.get("action") or ""),
                "item": item,
            }
        )
    return options

def _actions_to_llm_options(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    seen: set[str] = set()
    for action in actions:
        label = str(action.get("label") or "").strip()
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "label": label,
                "kind": str(action.get("type") or ""),
                "semantic_action": str(action.get("action") or ""),
                "action": action,
            }
        )
    return options

def _page_hint_from_actions(actions: list[dict[str, object]]) -> str:
    for action in actions:
        state = action.get("state")
        if isinstance(state, dict):
            title = str(state.get("pageTitle") or "").strip()
            url = str(state.get("url") or "").strip()
            if title or url:
                return f"{title} ({url})".strip(" ()") if title and url else title or url
        group = str(action.get("group") or "").strip()
        if group:
            return group
    return ""

def _resolve_foxmcp_target_with_llm(
    utterance: str,
    candidates: list[dict[str, object]],
    *,
    page_hint: str = "",
    pool: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    active_pool = pool if pool is not None else candidates
    options = _limit_llm_options(utterance, _foxmcp_candidates_to_llm_options(active_pool))
    idx = _llm_pick_from_options(utterance, options, page_hint=page_hint)
    if idx is None:
        return None
    item = options[idx].get("item")
    return item if isinstance(item, dict) else None

def _resolve_actions_with_llm(
    utterance: str,
    actions: list[dict[str, object]],
    *,
    page_hint: str = "",
) -> dict[str, object] | None:
    options = _limit_llm_options(utterance, _actions_to_llm_options(actions))
    if not options:
        return None
    idx = _llm_pick_from_options(utterance, options, page_hint=page_hint)
    if idx is None:
        return None
    picked = options[idx].get("action")
    return picked if isinstance(picked, dict) else None


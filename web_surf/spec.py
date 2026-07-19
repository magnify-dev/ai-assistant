from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from web_surf.llm import ollama_chat_json
from web_surf.page_match import focus_query, is_secondary_host, parse_user_preferred_domains

logger = logging.getLogger(__name__)

PROMPTS_PATH = Path(__file__).resolve().parents[1] / "prompts.yaml"


def _get_prompt(key: str) -> str:
    import yaml

    data = yaml.safe_load(PROMPTS_PATH.read_text(encoding="utf-8"))
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"Prompt '{key}' not found in {PROMPTS_PATH}")
        node = node[part]
    if not isinstance(node, str):
        raise KeyError(f"Prompt '{key}' is not a string")
    return node.strip()


def fallback_research_spec(query: str) -> dict[str, Any]:
    from web_surf.plan import fallback_accomplishment_steps

    text = query.strip()
    return {
        "summary": text[:200],
        "data_needed": [text] if text else [],
        "search_queries": [text] if text else [],
        "success_criteria": [f"Find reliable information about: {text}"] if text else [],
        "max_pages": 5,
        "official_sources": [],
        "accomplishment_steps": fallback_accomplishment_steps(text),
        "notes": [],
    }


def structure_research_spec(
    *,
    query: str,
    ollama_url: str,
    model: str,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    text = query.strip()
    if not text:
        return fallback_research_spec("")

    user = f"task: {text}"
    parsed = ollama_chat_json(
        prompt_key="web_research.spec",
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
        system=_get_prompt("web_research.spec"),
        user=user,
    )
    if not parsed:
        return fallback_research_spec(text)
    from web_surf.plan import normalize_accomplishment_steps

    parsed["source_query"] = text
    queries = parsed.get("search_queries")
    if not isinstance(queries, list) or not queries:
        parsed["search_queries"] = [text]
    parsed["max_pages"] = int(parsed.get("max_pages") or 5)
    publishers = parsed.get("official_sources")
    if isinstance(publishers, list):
        parsed["official_sources"] = [
            str(item).strip() for item in publishers if str(item).strip()
        ]
    else:
        parsed["official_sources"] = []
    parsed["accomplishment_steps"] = normalize_accomplishment_steps(
        parsed.get("accomplishment_steps"),
        query=text,
    )
    # Keep the original prompt attached so later stages never lose it.
    parsed.setdefault("summary", text[:200])
    if not str(parsed.get("summary") or "").strip():
        parsed["summary"] = text[:200]
    return parsed


def wants_verbatim_copy(query: str) -> bool:
    text = query.lower()
    return any(
        phrase in text
        for phrase in (
            "copy the",
            "copy latest",
            "copy verbatim",
            "paste the",
            "quote the",
            "exact text",
            "word for word",
        )
    )


def _fallback_source_tiers(results: list[Any], query: str) -> tuple[list[Any], list[Any]]:
    """When the model is unavailable, only demote obvious social/forum hosts."""
    goal = focus_query(query)
    official: list[Any] = []
    secondary: list[Any] = []
    for row in results:
        url = str(getattr(row, "url", "") or "")
        if is_secondary_host(url):
            secondary.append(row)
        else:
            official.append(row)
    if not official and secondary:
        return secondary, []
    return official, secondary


def classify_search_sources(
    *,
    query: str,
    spec: dict[str, Any],
    results: list[Any],
    ollama_url: str,
    model: str,
    timeout_sec: float = 120.0,
) -> tuple[list[Any], list[Any], set[str]]:
    """Split search results into publisher-primary vs secondary tiers via the model."""
    if not results:
        return [], [], set()

    publishers = [
        str(item).strip()
        for item in (spec.get("official_sources") or [])
        if str(item).strip()
    ]
    lines: list[str] = []
    for index, row in enumerate(results, start=1):
        title = str(getattr(row, "title", "") or getattr(row, "url", "")).strip()
        url = str(getattr(row, "url", "") or "").strip()
        snippet = str(getattr(row, "snippet", "") or "").strip()[:240]
        lines.append(f"{index}. {title}\n   {url}\n   {snippet}")

    user = (
        f"task: {focus_query(query)}\n"
        f"publishers: {json.dumps(publishers, ensure_ascii=False)}\n"
        f"preferred_sources: {json.dumps(sorted(parse_user_preferred_domains(query)), ensure_ascii=False)}\n\n"
        f"results:\n" + "\n".join(lines)
    )
    parsed = ollama_chat_json(
        prompt_key="web_research.classify_sources",
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
        system=_get_prompt("web_research.classify_sources"),
        user=user,
    )
    if not parsed:
        official, secondary = _fallback_source_tiers(results, query)
        from web_surf.page_match import official_registrable_domains

        official_urls = [str(getattr(row, "url", "") or "") for row in official]
        return official, secondary, official_registrable_domains(official_urls)

    tier_by_index: dict[int, str] = {}
    for item in parsed.get("sources") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        tier = str(item.get("tier") or "").lower().strip()
        if tier in {"official", "secondary"}:
            tier_by_index[index] = tier

    official: list[Any] = []
    secondary: list[Any] = []
    for index, row in enumerate(results, start=1):
        tier = tier_by_index.get(index, "secondary")
        if tier == "official":
            official.append(row)
        else:
            secondary.append(row)

    if not official and secondary:
        official, secondary = _fallback_source_tiers(results, query)

    from web_surf.page_match import official_registrable_domains

    official_urls = [str(getattr(row, "url", "") or "") for row in official]
    return official, secondary, official_registrable_domains(official_urls)

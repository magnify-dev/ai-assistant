from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from web_surf.llm import ollama_chat_json

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
    text = query.strip()
    return {
        "summary": text[:200],
        "data_needed": [text] if text else [],
        "search_queries": [text] if text else [],
        "success_criteria": [f"Find reliable information about: {text}"] if text else [],
        "max_pages": 5,
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
    parsed["source_query"] = text
    queries = parsed.get("search_queries")
    if not isinstance(queries, list) or not queries:
        parsed["search_queries"] = [text]
    parsed["max_pages"] = int(parsed.get("max_pages") or 5)
    return parsed

from __future__ import annotations

import logging
import re
from typing import Any

from web_surf import events
from web_surf.context_curate import curate_extract_context
from web_surf.llm import ollama_chat_json
from web_surf.spec import _get_prompt
from web_surf.store import content_hash, normalize_url

logger = logging.getLogger(__name__)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def quote_supported(quote: str, page_text: str) -> bool:
    quote_norm = _normalize_whitespace(quote)
    text_norm = _normalize_whitespace(page_text)
    if not quote_norm or not text_norm:
        return False
    if quote_norm in text_norm:
        return True
    if len(quote_norm) > 24 and quote_norm[:48] in text_norm:
        return True
    return False


def _ollama_json(
    *,
    ollama_url: str,
    model: str,
    timeout_sec: float,
    system: str,
    user: str,
    source_session_id: str = "",
    source_step_id: str = "",
    source_snapshot_id: str = "",
    page_url: str = "",
) -> dict[str, Any] | None:
    return ollama_chat_json(
        prompt_key="web_research.extract",
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
        system=system,
        user=user,
        session_id=source_session_id,
        step_id=source_step_id,
        snapshot_id=source_snapshot_id,
        url=page_url,
    )


def extract_facts_from_page(
    *,
    page_text: str,
    page_url: str,
    page_title: str,
    research_spec: dict[str, Any],
    ollama_url: str,
    model: str,
    timeout_sec: float = 120.0,
    max_content_chars: int = 8000,
    source_session_id: str = "",
    source_step_id: str = "",
    source_snapshot_id: str = "",
) -> tuple[list[dict[str, Any]], str]:
    clipped = page_text[:max_content_chars]
    user = curate_extract_context(
        page_text=clipped,
        page_url=page_url,
        page_title=page_title,
        research_spec=research_spec,
        max_chars=min(max_content_chars, 7000),
    )
    parsed = _ollama_json(
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
        system=_get_prompt("web_research.extract"),
        user=user,
        source_session_id=source_session_id,
        source_step_id=source_step_id,
        source_snapshot_id=source_snapshot_id,
        page_url=page_url,
    )
    if not parsed:
        events.extract_preview(
            {
                "phase": "llm_extract",
                "url": page_url,
                "step_id": source_step_id,
                "snapshot_id": source_snapshot_id,
                "input_chars": len(clipped),
                "curated_chars": len(user),
                "text_preview": clipped[:1500],
                "raw_fact_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "error": "llm returned no parseable json",
            }
        )
        return [], ""

    summary = str(parsed.get("page_summary") or "").strip()
    raw_facts = parsed.get("facts")
    if not isinstance(raw_facts, list):
        events.extract_preview(
            {
                "phase": "llm_extract",
                "url": page_url,
                "step_id": source_step_id,
                "snapshot_id": source_snapshot_id,
                "input_chars": len(clipped),
                "curated_chars": len(user),
                "text_preview": clipped[:1500],
                "page_summary": summary,
                "raw_fact_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "error": "llm response missing facts array",
            }
        )
        return [], summary

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        value = str(item.get("value") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not field or not value or not quote:
            rejected.append(
                {
                    "field": field,
                    "value": value,
                    "quote": quote[:240],
                    "reason": "missing field, value, or quote",
                }
            )
            continue
        if not quote_supported(quote, clipped):
            rejected.append(
                {
                    "field": field,
                    "value": value,
                    "quote": quote[:240],
                    "reason": "quote not found in page text",
                }
            )
            continue
        accepted.append(
            {
                "id": content_hash(f"{field}|{value}|{normalize_url(page_url)}"),
                "field": field,
                "value": value,
                "source_url": normalize_url(page_url),
                "quote": quote,
                "source_session_id": source_session_id,
                "source_step_id": source_step_id,
                "source_snapshot_id": source_snapshot_id,
            }
        )
    events.extract_preview(
        {
            "phase": "llm_extract",
            "url": page_url,
            "step_id": source_step_id,
            "snapshot_id": source_snapshot_id,
            "input_chars": len(clipped),
            "curated_chars": len(user),
            "text_preview": clipped[:1500],
            "page_summary": summary,
            "raw_fact_count": len(raw_facts),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "facts": accepted[:20],
            "rejected": rejected[:20],
        }
    )
    return accepted, summary

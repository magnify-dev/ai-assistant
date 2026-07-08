from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def normalize_href(href: str) -> str:
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return ""
    if href.startswith("/"):
        return href.rstrip("/") or "/"
    parsed = urlparse(href)
    if parsed.scheme and parsed.netloc:
        return (parsed.path or "/").rstrip("/") or "/"
    return href.rstrip("/") or "/"


def element_key(el: dict[str, Any]) -> str:
    href = normalize_href(str(el.get("href") or ""))
    parts = [
        str(el.get("kind") or ""),
        str(el.get("test_id") or ""),
        str(el.get("text") or "").strip().lower()[:80],
        str(el.get("aria") or "").strip().lower()[:80],
        href,
        str(el.get("placeholder") or ""),
    ]
    return "|".join(parts)


def should_store_interactable(el: dict[str, Any]) -> bool:
    text = str(el.get("text") or "").strip()
    aria = str(el.get("aria") or "").strip()
    if len(text) > 120:
        return False
    if len(text) <= 4 and aria.lower().startswith("preview "):
        return False
    return bool(text or aria or el.get("test_id") or el.get("href"))


def suggest_selector(el: dict[str, Any]) -> str | None:
    if el.get("test_id"):
        return f'[data-testid="{el["test_id"]}"]'
    if el.get("kind") == "input" and el.get("placeholder"):
        return f'[placeholder="{el["placeholder"]}"]'
    if el.get("kind") == "input" and el.get("text"):
        return f'input[type="{el.get("input_type") or "text"}"]'
    return None


def normalize_interactable(el: dict[str, Any]) -> dict[str, Any]:
    href = str(el.get("href") or "")
    norm_href = normalize_href(href) if href else ""
    entry: dict[str, Any] = {
        "kind": el.get("kind"),
        "text": el.get("text"),
        "aria": el.get("aria"),
        "test_id": el.get("test_id"),
        "placeholder": el.get("placeholder"),
        "role": el.get("role"),
        "input_type": el.get("input_type"),
        "selector": suggest_selector(el),
    }
    if norm_href:
        entry["href"] = norm_href
    return entry

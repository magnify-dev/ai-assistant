from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from threading import Lock
from typing import Any

import httpx

from ui_test.config_loader import load_engine_config, ollama_model, ollama_url
from ui_test.prompts import get_prompt

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = Lock()
_MAX_CACHE = 40


def _compact_element(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "role": item.get("role"),
        "tag": item.get("tag"),
        "text": item.get("text"),
        "aria": item.get("aria"),
        "label": item.get("label"),
        "name": item.get("name"),
        "input_type": item.get("input_type"),
        "disabled": bool(item.get("disabled")),
        "rect": item.get("rect"),
        "cursor": item.get("cursor"),
        "locator_status": item.get("locator_status"),
        "frame_url": item.get("frame_url"),
        "shadow_host": item.get("shadow_host"),
    }


def _apply_results(capture: dict[str, Any], result: dict[str, Any]) -> None:
    decisions = result.get("elements") if isinstance(result.get("elements"), list) else []
    by_id = {
        str(row.get("id")): row
        for row in decisions
        if isinstance(row, dict) and row.get("id")
    }
    kept = 0
    rejected = 0
    for item in capture.get("elements") or []:
        if not isinstance(item, dict):
            continue
        issues = set(item.get("deterministic_issues") or [])
        row = by_id.get(str(item.get("id"))) or {}
        deterministic_reject = bool({"disabled", "duplicate_id"} & issues)
        interactive = bool(row.get("interactive")) and not deterministic_reject
        item["ai_interactive"] = interactive
        try:
            item["ai_confidence"] = max(0.0, min(1.0, float(row.get("confidence", 0))))
        except (TypeError, ValueError):
            item["ai_confidence"] = 0.0
        item["ai_control_type"] = str(row.get("control_type") or item.get("kind") or "unknown")[:40]
        item["ai_reason"] = (
            "Rejected by deterministic checks."
            if deterministic_reject
            else str(row.get("reason") or "AI returned no decision for this element.")[:240]
        )
        if interactive:
            kept += 1
        else:
            rejected += 1
    capture.setdefault("summary", {}).update({"ai_kept": kept, "ai_rejected": rejected})


def analyze_capture(capture: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    fingerprint = str(capture.get("fingerprint") or "")
    with _CACHE_LOCK:
        cached = deepcopy(_CACHE.get(fingerprint))
    if cached:
        _apply_results(capture, cached)
        capture["ai"] = {
            "status": "ready",
            "cached": True,
            "model": cached.get("_model"),
            "duration_ms": 0,
        }
        return capture

    if os.environ.get("WEB_CAPTURE_AI", "1").strip().lower() in {"0", "false", "off"}:
        capture["ai"] = {"status": "disabled", "duration_ms": 0}
        return capture

    config = load_engine_config()
    url = ollama_url(config)
    model = ollama_model(config)
    payload = {
        "url": capture.get("url"),
        "viewport": capture.get("viewport"),
        "elements": [
            _compact_element(item)
            for item in (capture.get("elements") or [])[:200]
            if isinstance(item, dict)
        ],
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": get_prompt("web_capture.classify")},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
        content = (response.json().get("message") or {}).get("content") or ""
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("classification response was not an object")
        parsed["_model"] = model
        _apply_results(capture, parsed)
        with _CACHE_LOCK:
            if len(_CACHE) >= _MAX_CACHE:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[fingerprint] = deepcopy(parsed)
        capture["ai"] = {
            "status": "ready",
            "cached": False,
            "model": model,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        capture["ai"] = {
            "status": "unavailable",
            "model": model,
            "error": str(exc)[:300],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    return capture

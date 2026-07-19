from __future__ import annotations

from typing import Any

_PHASE_MESSAGES = {
    "geometry": "Scanning controls and page content…",
    "locators": "Validating Playwright locators…",
    "analyzing": "Classifying controls and content blocks…",
    "visual": "Building pixel map…",
    "complete": "Map ready — inspect below",
    "error": "Could not build page map",
}


def capture_progress_event(
    *,
    phase: str,
    url: str = "",
    message: str = "",
    capture: dict[str, Any] | None = None,
    error: str | None = None,
    element_count: int | None = None,
    screenshot_b64: str | None = None,
    title: str = "",
    interactables: list[dict[str, Any]] | None = None,
) -> None:
    payload = {
        "type": "web_capture_progress",
        "phase": phase,
        "url": url,
        "message": message or _PHASE_MESSAGES.get(phase, phase),
        "capture": capture,
        "error": error,
        "element_count": element_count,
        "screenshot_b64": screenshot_b64,
        "title": title,
        "interactables": interactables,
    }
    for module in ("ui_test.events", "web_surf.events"):
        try:
            import importlib

            events = importlib.import_module(module)
            dispatch = getattr(events, "_dispatch", None)
            if callable(dispatch):
                dispatch(payload)
                return
            fn = getattr(events, "capture_progress", None)
            if callable(fn):
                fn(
                    phase=phase,
                    url=url,
                    message=payload["message"],
                    capture=capture,
                    error=error,
                    element_count=element_count,
                    screenshot_b64=screenshot_b64,
                    title=title,
                    interactables=interactables,
                )
                return
        except Exception:
            continue

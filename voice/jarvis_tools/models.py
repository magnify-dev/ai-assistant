"""Small shared data helpers and Ollama model accessors."""

from __future__ import annotations

from jarvis_tools.constants import OLLAMA_MODEL, VISION_MODEL


def jarvis_ollama_model() -> str:
    import os

    return os.environ.get("JARVIS_OLLAMA_MODEL", OLLAMA_MODEL).strip() or OLLAMA_MODEL


def jarvis_vision_model() -> str | None:
    """Separate vision model only when explicitly configured and different from the main model."""
    import os

    explicit = os.environ.get("JARVIS_VISION_MODEL", VISION_MODEL).strip()
    if not explicit:
        return None
    if explicit == jarvis_ollama_model():
        return None
    return explicit


def make_action(
    *,
    action_id: str,
    source: str,
    action: str,
    label: str,
    type_: str,
    aliases: list[str] | None = None,
    ordinal: int = 0,
    group: str = "",
    state: dict | None = None,
    payload: dict | None = None,
) -> dict[str, object]:
    return {
        "id": action_id,
        "source": source,
        "action": action,
        "label": label.strip() or action,
        "type": type_,
        "aliases": [alias for alias in (aliases or []) if alias],
        "ordinal": ordinal,
        "group": group,
        "state": state or {},
        "payload": payload or {},
    }


def truncate_value(value: object, max_len: int = 240) -> object:
    if isinstance(value, str):
        return value if len(value) <= max_len else value[: max_len - 3] + "..."
    if isinstance(value, dict):
        return {str(k): truncate_value(v, max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_value(item, max_len) for item in value[:80]]
    return value


# Backward-compatible aliases used inside the package.
_make_action = make_action
_truncate_value = truncate_value

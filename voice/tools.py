"""Backward-compatible shim - import from jarvis_tools."""

from jarvis_tools import (  # noqa: F401
    KNOWN_PATHS,
    TOOL_DEFINITIONS,
    capture_active_window_context,
    execute_tool,
    extract_playlist_query,
    get_active_window_title,
    jarvis_ollama_model,
    jarvis_vision_model,
    start_browser_bridge,
    start_configured_browser_control,
    tools_enabled,
)

__all__ = [
    "TOOL_DEFINITIONS",
    "execute_tool",
    "tools_enabled",
    "capture_active_window_context",
    "extract_playlist_query",
    "get_active_window_title",
    "jarvis_ollama_model",
    "jarvis_vision_model",
    "KNOWN_PATHS",
    "start_browser_bridge",
    "start_configured_browser_control",
]

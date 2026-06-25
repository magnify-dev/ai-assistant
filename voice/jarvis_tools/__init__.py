"""Public API for Jarvis voice assistant tools."""

from jarvis_tools.browser_bridge import start_browser_bridge
from jarvis_tools.foxmcp.connection import start_configured_browser_control
from jarvis_tools.constants import KNOWN_PATHS
from jarvis_tools.models import jarvis_ollama_model, jarvis_vision_model
from jarvis_tools.definitions import TOOL_DEFINITIONS
from jarvis_tools.executor import execute_tool, tools_enabled
from jarvis_tools.text_match import extract_playlist_query
from jarvis_tools.windows import capture_active_window_context, get_active_window_title

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

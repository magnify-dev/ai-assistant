"""Jarvis tool wrapper for web_surf phase-1 research."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from web_surf.runner import run_web_research


def _web_research(
    query: str,
    *,
    project_path: str = "",
    max_pages: int = 5,
) -> str:
    query = query.strip()
    if not query:
        return "Research query was empty"

    kwargs: dict = {"max_pages": max_pages}
    if project_path.strip():
        kwargs["project"] = project_path.strip()

    result = run_web_research(query, **kwargs)
    return result.to_tool_text()

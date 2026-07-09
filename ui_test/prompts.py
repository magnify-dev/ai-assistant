"""Central prompt registry loader.

All LLM prompts live in prompts.yaml at the repo root. Code fetches them by
dotted key and substitutes {{name}} placeholders — never hardcode prompt text.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_PATH = Path(__file__).resolve().parents[1] / "prompts.yaml"

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    data = yaml.safe_load(PROMPTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"prompts.yaml did not parse to a mapping: {PROMPTS_PATH}")
    return data


def get_prompt(key: str) -> str:
    """Fetch a prompt by dotted key, e.g. 'exploration.decide'."""
    node: Any = _registry()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"Prompt '{key}' not found in {PROMPTS_PATH}")
        node = node[part]
    if not isinstance(node, str):
        raise KeyError(f"Prompt '{key}' is not a string in {PROMPTS_PATH}")
    return node.strip()


def render_prompt(key: str, **variables: str) -> str:
    """Fetch a prompt and substitute {{name}} placeholders."""
    template = get_prompt(key)

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"Prompt '{key}' placeholder '{{{{{name}}}}}' has no value")
        return str(variables[name])

    return _PLACEHOLDER.sub(repl, template)

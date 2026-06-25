from __future__ import annotations

from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


def _load_script(name: str) -> str:
    return (_SCRIPTS_DIR / name).read_text(encoding="utf-8")

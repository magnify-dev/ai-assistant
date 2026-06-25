"""Logging helpers: verbose file logs, minimal stdout for the control panel."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_UI_LOGGER = "jarvis.ui"


class _ConsoleFilter(logging.Filter):
    """Stdout only shows user-facing lines and warnings/errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return record.name == _UI_LOGGER


def setup_logging(cfg: dict, root: Path) -> None:
    log_cfg = cfg.get("logging", {})
    log_file = (root / log_cfg.get("file", "../logs/voice-assistant.log")).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(_ConsoleFilter())
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def ui(message: str) -> None:
    """One-line message for the control panel live output."""
    logging.getLogger(_UI_LOGGER).info(message)

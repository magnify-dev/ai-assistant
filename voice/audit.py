from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_audit_lock = threading.Lock()

def _audit_path(cfg: dict) -> Path:
    raw_path = cfg.get("logging", {}).get("command_audit_file", "../logs/voice-commands.jsonl")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _audit_value(value: object, max_chars: int = 4000) -> object:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): _audit_value(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_audit_value(v, max_chars=max_chars) for v in value]
    return value


def audit_event(cfg: dict, event_type: str, **payload: object) -> None:
    """Append structured voice/debug events for later inspection."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event_type,
        **{key: _audit_value(value) for key, value in payload.items()},
    }
    try:
        path = _audit_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _audit_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:
        logging.debug("Could not write command audit event: %s", exc)

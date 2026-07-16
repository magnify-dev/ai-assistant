from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


def _ollama_exe() -> Path | None:
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if not localappdata:
        return None
    exe = Path(localappdata) / "Programs" / "Ollama" / "ollama.exe"
    return exe if exe.is_file() else None


def _model_names(tags_body: dict) -> set[str]:
    names: set[str] = set()
    for entry in tags_body.get("models") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("name", "model"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
    return names


def _normalize_model_ref(name: str) -> str:
    trimmed = str(name or "").strip().lower()
    if not trimmed:
        return ""
    if ":" not in trimmed:
        return f"{trimmed}:latest"
    base, tag = trimmed.split(":", 1)
    digest = re.search(r"-[a-f0-9]{8,}$", tag, flags=re.I)
    if digest and digest.start() > 0:
        tag = tag[: digest.start()]
    return f"{base}:{tag or 'latest'}"


def _models_equivalent(requested: str, actual: str) -> bool:
    return _normalize_model_ref(requested) == _normalize_model_ref(actual)


def _model_loaded(ps_body: dict, model: str) -> bool:
    for entry in ps_body.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "")
        if _models_equivalent(model, name):
            return True
    return False


def wait_for_ollama(url: str, *, timeout_sec: float = 120, on_log: LogFn | None = None) -> None:
    deadline = time.time() + timeout_sec
    next_log = 0.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3.0) as client:
                if client.get(f"{url.rstrip('/')}/api/tags").is_success:
                    return
        except httpx.HTTPError:
            pass
        if on_log and time.time() >= next_log:
            on_log("Waiting for Ollama at http://127.0.0.1:11434…")
            next_log = time.time() + 10
        time.sleep(2)
    raise RuntimeError(f"Ollama not reachable at {url} after {int(timeout_sec)}s")


def pull_model(model: str, *, on_log: LogFn | None = None) -> None:
    exe = _ollama_exe()
    if not exe:
        raise RuntimeError("Ollama CLI not found — install from https://ollama.com")
    if on_log:
        on_log(f"Pulling model {model} (first time only)…")
    proc = subprocess.run(
        [str(exe), "pull", model],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ollama pull {model} failed: {detail or proc.returncode}")


def preload_model(
    url: str,
    model: str,
    *,
    timeout_sec: float = 600,
    on_log: LogFn | None = None,
) -> None:
    if on_log:
        on_log(f"Loading {model} into VRAM (first load can take 30–90s)…")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ready"}],
        "stream": False,
        "keep_alive": -1,
    }
    with httpx.Client(timeout=timeout_sec) as client:
        client.post(f"{url.rstrip('/')}/api/chat", json=payload).raise_for_status()
    if on_log:
        on_log(f"Model {model} is loaded and ready")


def unload_model(url: str, model: str, *, timeout_sec: float = 120) -> None:
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": 0,
    }
    with httpx.Client(timeout=timeout_sec) as client:
        response = client.post(f"{url.rstrip('/')}/api/generate", json=payload)
        if response.status_code not in {200, 404}:
            response.raise_for_status()


def ensure_ollama_ready(
    *,
    url: str,
    model: str,
    wait_timeout_sec: float = 120,
    preload_timeout_sec: float = 600,
    on_log: LogFn | None = None,
) -> None:
    wait_for_ollama(url, timeout_sec=wait_timeout_sec, on_log=on_log)

    with httpx.Client(timeout=30.0) as client:
        tags = client.get(f"{url.rstrip('/')}/api/tags").json()
        ps = client.get(f"{url.rstrip('/')}/api/ps").json()

    available = _model_names(tags if isinstance(tags, dict) else {})
    if not any(_models_equivalent(model, name) for name in available):
        pull_model(model, on_log=on_log)
    elif on_log:
        on_log(f"Model {model} is available")

    if _model_loaded(ps if isinstance(ps, dict) else {}, model):
        if on_log:
            on_log(f"Model {model} already loaded in VRAM")
        return

    loaded_names = [
        str(entry.get("name") or entry.get("model") or "")
        for entry in (ps.get("models") if isinstance(ps, dict) else []) or []
        if isinstance(entry, dict)
    ]
    to_unload = [name for name in loaded_names if name and not _models_equivalent(model, name)]
    for name in to_unload:
        if on_log:
            on_log(f"Unloading {name} from VRAM…")
        unload_model(url, name)

    preload_model(url, model, timeout_sec=preload_timeout_sec, on_log=on_log)

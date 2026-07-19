"""Analyze the latest web research run for failures."""
from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT = Path(r"C:\Users\marce\Documents\Programming\content-manager")
SESSION = PROJECT / ".agent" / "current" / "web-artifacts" / "playwright-session" / "session.json"
LOG = Path(r"C:\Users\marce\Documents\Programming\ai-assistant\logs\test-runner-last-run.log")
HISTORY = PROJECT / ".agent" / "history"
CURRENT = PROJECT / ".agent" / "current"


def summarize_session() -> None:
    data = json.loads(SESSION.read_text(encoding="utf-8"))
    print("=== SESSION ===")
    print("recorded_at", data.get("recorded_at"), "frames", data.get("frame_count"))
    for i, frame in enumerate(data.get("frames") or []):
        if not isinstance(frame, dict):
            continue
        url = frame.get("url") or (frame.get("snapshot") or {}).get("url")
        title = frame.get("title") or (frame.get("snapshot") or {}).get("title")
        action = frame.get("action") or frame.get("decision") or {}
        print(f"\n-- frame {i} --")
        print("url:", url)
        print("title:", (title or "")[:120])
        if isinstance(action, dict):
            print(
                "action:",
                action.get("action"),
                "target:",
                (action.get("target_id") or action.get("reason") or "")[:100],
            )
            reason = str(action.get("reason") or "")[:200]
            if reason:
                print("reason:", reason)
        # common keys
        for key in ("step", "error", "status", "decision", "events"):
            if key in frame:
                val = frame[key]
                print(f"{key}:", str(val)[:200])


def find_history_runs() -> None:
    print("\n=== HISTORY / CURRENT ===")
    if HISTORY.exists():
        runs = sorted(HISTORY.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for run in runs[:8]:
            print(run.name, run.is_dir(), run.stat().st_mtime)
    for path in sorted(CURRENT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        print("current:", path.name)


def scan_log() -> None:
    print("\n=== LOG TAIL ===")
    text = LOG.read_text(encoding="utf-8", errors="replace")
    print("log bytes", len(text))
    ids = list(dict.fromkeys(re.findall(r"web_[a-f0-9]{20,}", text[-800_000:])))
    print("recent run ids:", ids[-8:])

    # Prefer NDJSON-ish lines with web_ types near the end
    lines = text.splitlines()
    interesting: list[str] = []
    keys = (
        "web_decision",
        "web_step",
        "web_research",
        "error",
        "failed",
        "intercept",
        "section_hub",
        "force_click",
        "overlay",
        "keep watching",
        "stall",
        "recover",
        "navigate",
        "collect",
        "Reusing stored",
        "page_understanding",
        "classic+",
        "mop-classic",
        "goal",
    )
    for line in lines[-8000:]:
        low = line.lower()
        if any(k in low for k in keys):
            interesting.append(line[:350])
    print(f"interesting lines: {len(interesting)}")
    for line in interesting[-80:]:
        print(line)


def main() -> None:
    summarize_session()
    find_history_runs()
    scan_log()


if __name__ == "__main__":
    main()

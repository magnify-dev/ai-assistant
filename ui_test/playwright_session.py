from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page


class PlaywrightSessionRecorder:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir.resolve()
        self.screenshots_dir = self.session_dir / "screenshots"
        self.video_dir = self.session_dir / "video"
        self.frames: list[dict[str, Any]] = []
        self._step = 0
        self._context: BrowserContext | None = None

    def prepare(self) -> None:
        if self.session_dir.exists():
            shutil.rmtree(self.session_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def context_options(self) -> dict[str, Any]:
        return {
            "record_video_dir": str(self.video_dir),
            "record_video_size": {"width": 960, "height": 640},
        }

    def attach(self, context: BrowserContext) -> None:
        self._context = context
        context.tracing.start(screenshots=True, snapshots=True, sources=False)

    def record_frame(self, page: Page, *, label: str, url: str = "", context: str = "") -> None:
        self._step += 1
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", label or "frame").strip("_")[:40] or "frame"
        filename = f"{self._step:03d}_{safe}.jpg"
        screenshot_rel: str | None = None
        path = self.screenshots_dir / filename
        try:
            page.screenshot(path=str(path), type="jpeg", quality=72, full_page=False, timeout=8000)
            if path.is_file():
                screenshot_rel = f"screenshots/{filename}"
        except Exception:
            screenshot_rel = None
        self.frames.append(
            {
                "step": self._step,
                "label": label,
                "url": url or page.url,
                "context": context,
                "screenshot": screenshot_rel,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )

    def stop_tracing(self) -> None:
        if not self._context:
            return
        trace_path = self.session_dir / "trace.zip"
        try:
            self._context.tracing.stop(path=str(trace_path))
        except Exception:
            pass
        self._context = None

    def finalize(self) -> dict[str, Any]:
        trace_rel = "trace.zip" if (self.session_dir / "trace.zip").is_file() else None
        video_rel: str | None = None
        if self.video_dir.is_dir():
            for webm in sorted(self.video_dir.glob("*.webm")):
                video_rel = f"video/{webm.name}"
                break

        manifest = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "frames": self.frames,
            "trace": trace_rel,
            "video": video_rel,
            "frame_count": len(self.frames),
        }
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "session.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest


_active_recorder: PlaywrightSessionRecorder | None = None


def set_active_recorder(recorder: PlaywrightSessionRecorder | None) -> None:
    global _active_recorder
    _active_recorder = recorder


def get_active_recorder() -> PlaywrightSessionRecorder | None:
    return _active_recorder


def session_manifest_paths(manifest: dict[str, Any], *, base: str = "ui-artifacts/playwright-session") -> dict[str, Any]:
    out = dict(manifest)
    if out.get("video"):
        out["video"] = f"{base}/{out['video']}"
    if out.get("trace"):
        out["trace"] = f"{base}/{out['trace']}"
    frames = []
    for frame in out.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        item = dict(frame)
        if item.get("screenshot"):
            item["screenshot"] = f"{base}/{item['screenshot']}"
        frames.append(item)
    out["frames"] = frames
    return out
def notify_page_state(page: Page, *, context: str = "") -> None:
    recorder = get_active_recorder()
    if recorder:
        recorder.record_frame(page, label=context or "state", context=context)

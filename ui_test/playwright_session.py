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
        self.frames: list[dict[str, Any]] = []
        self._step = 0

    def prepare(self) -> None:
        if self.session_dir.exists():
            shutil.rmtree(self.session_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def context_options(self) -> dict[str, Any]:
        return {}

    def attach(self, context: BrowserContext) -> None:
        # Session replay uses explicit screenshots and semantic snapshots only.
        # Video and Playwright tracing duplicate that evidence at high storage cost.
        return

    def record_frame(
        self,
        page: Page,
        *,
        label: str,
        url: str = "",
        context: str = "",
        snapshot: dict[str, Any] | None = None,
    ) -> None:
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
                "interactables": list((snapshot or {}).get("interactables") or []),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._persist_manifest()

    def record_decision(self, decision: dict[str, Any]) -> None:
        """Attach the next AI action to the screenshot and controls it used."""
        if not self.frames:
            return
        frame = self.frames[-1]
        target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
        target_id = str(decision.get("target_id") or target.get("id") or "").strip()
        selected: dict[str, Any] | None = None
        index = target.get("index")
        for item in frame.get("interactables") or []:
            if not isinstance(item, dict):
                continue
            if target_id and str(item.get("id") or "") == target_id:
                selected = item
                break
            if index is not None and item.get("index") == index:
                selected = item
                break
            if target.get("id") and item.get("id") == target["id"]:
                selected = item
                break
            if target.get("text") and item.get("text") == target["text"]:
                selected = item
                break
        frame["decision"] = dict(decision)
        if selected:
            frame["selected_interactable_id"] = selected.get("id")
            frame["selected_interactable"] = selected
        elif target_id:
            frame["selected_interactable_id"] = target_id
        self._persist_manifest()

    def _persist_manifest(self) -> dict[str, Any]:
        manifest = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "frames": self.frames,
            "frame_count": len(self.frames),
        }
        self.session_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.session_dir / "session.json"
        tmp_path = manifest_path.with_suffix(".json.tmp")
        payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(manifest_path)
        return manifest

    def finalize(self) -> dict[str, Any]:
        return self._persist_manifest()


_active_recorder: PlaywrightSessionRecorder | None = None


def set_active_recorder(recorder: PlaywrightSessionRecorder | None) -> None:
    global _active_recorder
    _active_recorder = recorder


def get_active_recorder() -> PlaywrightSessionRecorder | None:
    return _active_recorder


def session_manifest_paths(manifest: dict[str, Any], *, base: str = "ui-artifacts/playwright-session") -> dict[str, Any]:
    out = dict(manifest)
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
def notify_page_state(page: Page, *, context: str = "", snapshot: dict[str, Any] | None = None) -> None:
    recorder = get_active_recorder()
    if recorder:
        recorder.record_frame(page, label=context or "state", context=context, snapshot=snapshot)

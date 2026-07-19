from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web_capture.context import get_active_project
from web_capture.maps import append_training_record


def capture_dir_for_session(session_dir: Path) -> Path:
    resolved = session_dir.resolve()
    run_root = resolved.parents[1] if len(resolved.parents) > 1 else resolved.parent
    return run_root / "web-capture"


def project_capture_dir(project: Path) -> Path:
    return project / ".agent" / "web-capture"


def _copy_one_screenshot(project: Path, target_dir: Path, rel: str) -> None:
    rel = str(rel or "").strip()
    if not rel:
        return
    source = project / ".agent" / "web-capture" / rel
    if not source.is_file():
        return
    target = target_dir / source.name
    try:
        target.write_bytes(source.read_bytes())
    except OSError:
        pass


def _copy_screenshot_to_run(project: Path, session_dir: Path, capture: dict[str, Any]) -> None:
    """Copy capture screenshot and every scroll-slice image into the run folder."""
    target_dir = capture_dir_for_session(session_dir) / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    _copy_one_screenshot(project, target_dir, str(capture.get("screenshot") or ""))
    scroll_map = capture.get("scroll_map") if isinstance(capture.get("scroll_map"), dict) else {}
    for slice_row in scroll_map.get("slices") or []:
        if isinstance(slice_row, dict):
            _copy_one_screenshot(project, target_dir, str(slice_row.get("screenshot") or ""))


def persist_capture(session_dir: Path, capture: dict[str, Any]) -> Path:
    target_dir = capture_dir_for_session(session_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    capture_id = str(capture.get("capture_id") or "capture")
    raw_dir = target_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(capture, ensure_ascii=False, indent=2) + "\n"
    for dest in (raw_dir / f"{capture_id}.json", target_dir / f"{capture_id}.json"):
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(dest)
    latest = target_dir / "latest.json"
    latest_tmp = latest.with_suffix(".json.tmp")
    latest_tmp.write_text(payload, encoding="utf-8")
    latest_tmp.replace(latest)

    project = get_active_project()
    if project:
        project_raw = project_capture_dir(project) / "raw"
        project_raw.mkdir(parents=True, exist_ok=True)
        project_latest = project_capture_dir(project) / "latest.json"
        project_tmp = project_latest.with_suffix(".json.tmp")
        project_tmp.write_text(payload, encoding="utf-8")
        project_tmp.replace(project_latest)
        project_raw_tmp = project_raw / f"{capture_id}.json.tmp"
        project_raw_tmp.write_text(payload, encoding="utf-8")
        project_raw_tmp.replace(project_raw / f"{capture_id}.json")
        _copy_screenshot_to_run(project, session_dir, capture)
        append_training_record(
            project,
            {
                "kind": "capture",
                "capture_id": capture_id,
                "url": capture.get("url"),
                "fingerprint": capture.get("fingerprint"),
                "summary": capture.get("summary"),
                "map": capture.get("map"),
                "ai": capture.get("ai"),
                "screenshot": capture.get("screenshot"),
            },
        )
    return target_dir / f"{capture_id}.json"

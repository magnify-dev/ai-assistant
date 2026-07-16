from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web_capture.maps import site_key, _slug


def visual_dir(project: Path) -> Path:
    return project / ".agent" / "web-capture" / "visual"


def visual_file_for_url(project: Path, url: str) -> Path:
    return visual_dir(project) / f"{_slug(site_key(url))}.json"


DEFAULT_COLS = 48
DEFAULT_ROWS = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_visual_map(project, url: str) -> dict[str, Any] | None:
    path = visual_file_for_url(project, url)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def encode_cell(cell: dict[str, Any]) -> str:
    color = str(cell.get("color") or "#e5e7eb").strip()
    kind = str(cell.get("kind") or "chrome").strip().lower()
    if not color.startswith("#"):
        color = "#e5e7eb"
    return f"{color}|{kind}"


def decode_cell(raw: str) -> dict[str, str]:
    if "|" in raw:
        color, kind = raw.split("|", 1)
        return {"color": color or "#e5e7eb", "kind": kind or "chrome"}
    return {"color": raw or "#e5e7eb", "kind": "chrome"}


def normalize_tiles(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    cols = int(raw.get("cols") or DEFAULT_COLS)
    rows = int(raw.get("rows") or DEFAULT_ROWS)
    cells_raw = raw.get("cells")
    if not isinstance(cells_raw, list) or not cells_raw:
        return None
    cells: list[str] = []
    for item in cells_raw[: cols * rows]:
        if isinstance(item, str):
            cells.append(item if "|" in item else encode_cell({"color": item}))
        elif isinstance(item, dict):
            cells.append(encode_cell(item))
        else:
            cells.append(encode_cell({}))
    while len(cells) < cols * rows:
        cells.append(encode_cell({}))
    return {"cols": cols, "rows": rows, "cells": cells[: cols * rows]}


def collect_visual_tiles(page) -> dict[str, Any] | None:
    try:
        from ui_test.browser_state import VISUAL_TILE_JS

        raw = page.evaluate(VISUAL_TILE_JS)
    except Exception:
        return None
    return normalize_tiles(raw)


def _rect_cells(
    rect: dict[str, Any],
    viewport: dict[str, Any],
    cols: int,
    rows: int,
) -> list[int]:
    if not isinstance(rect, dict):
        return []
    width = float(viewport.get("width") or 1)
    height = float(viewport.get("height") or 1)
    left = max(0.0, float(rect.get("x") or 0))
    top = max(0.0, float(rect.get("y") or 0))
    right = min(width, left + max(0.0, float(rect.get("width") or 0)))
    bottom = min(height, top + max(0.0, float(rect.get("height") or 0)))
    if right <= left or bottom <= top:
        return []
    cell_w = width / cols
    cell_h = height / rows
    col_start = max(0, int(left // cell_w))
    col_end = min(cols - 1, int((right - 1) // cell_w))
    row_start = max(0, int(top // cell_h))
    row_end = min(rows - 1, int((bottom - 1) // cell_h))
    indices: list[int] = []
    for row in range(row_start, row_end + 1):
        for col in range(col_start, col_end + 1):
            indices.append(row * cols + col)
    return indices


def overlay_from_elements(
    elements: list[dict[str, Any]],
    *,
    cols: int,
    rows: int,
    viewport: dict[str, Any],
    prior: list[str | None] | None = None,
) -> list[str | None]:
    overlay: list[str | None] = list(prior or [None] * (cols * rows))
    if len(overlay) < cols * rows:
        overlay.extend([None] * (cols * rows - len(overlay)))
    for item in elements:
        if not isinstance(item, dict):
            continue
        if item.get("user_interactive") is None:
            continue
        mark = "+" if item.get("user_interactive") else "-"
        for index in _rect_cells(item.get("rect") or {}, viewport, cols, rows):
            overlay[index] = mark
    return overlay[: cols * rows]


def merge_display_cells(base_cells: list[str], overlay: list[str | None]) -> list[str]:
    merged: list[str] = []
    for index, cell in enumerate(base_cells):
        mark = overlay[index] if index < len(overlay) else None
        if mark == "+":
            merged.append(re.sub(r"\|[^|]+$", "|kept", cell))
        elif mark == "-":
            merged.append(re.sub(r"\|[^|]+$", "|rejected", cell))
        else:
            merged.append(cell)
    return merged


def _active_source(built_at: str, corrected_at: str) -> str:
    if corrected_at and corrected_at >= built_at:
        return "corrected"
    return "built"


def resolve_visual_map(
    project,
    *,
    url: str,
    capture_id: str,
    viewport: dict[str, Any],
    elements: list[dict[str, Any]],
    fresh_tiles: dict[str, Any] | None,
) -> dict[str, Any]:
    stored = load_visual_map(project, url) if project and url else None
    built_at = _now()

    if fresh_tiles:
        cols = int(fresh_tiles["cols"])
        rows = int(fresh_tiles["rows"])
        base_cells = list(fresh_tiles["cells"])
        status = "built"
    elif stored and isinstance(stored.get("cells"), list):
        cols = int(stored.get("cols") or DEFAULT_COLS)
        rows = int(stored.get("rows") or DEFAULT_ROWS)
        base_cells = [str(item) for item in stored["cells"]]
        built_at = str(stored.get("built_at") or built_at)
        status = "reused"
    else:
        return {
            "status": "missing",
            "site_key": site_key(url),
            "cols": DEFAULT_COLS,
            "rows": DEFAULT_ROWS,
            "cells": [],
            "overlay": [],
            "display_cells": [],
            "active_source": "none",
        }

    prior_overlay = stored.get("overlay") if stored and isinstance(stored.get("overlay"), list) else None
    overlay = overlay_from_elements(
        elements,
        cols=cols,
        rows=rows,
        viewport=viewport,
        prior=[str(item) if item in {"+", "-"} else None for item in (prior_overlay or [])],
    )
    corrected_at = str(stored.get("corrected_at") or "") if stored else ""
    if any(item in {"+", "-"} for item in overlay):
        corrected_at = max(corrected_at, built_at) if corrected_at else built_at

    display_cells = merge_display_cells(base_cells, overlay)
    payload = {
        "version": 1,
        "site_key": site_key(url),
        "url": url,
        "viewport": viewport,
        "cols": cols,
        "rows": rows,
        "cells": base_cells,
        "overlay": overlay,
        "display_cells": display_cells,
        "built_at": built_at,
        "built_capture_id": capture_id,
        "corrected_at": corrected_at or None,
        "updated_at": _now(),
    }
    if project and url:
        _write_json(visual_file_for_url(project, url), payload)

    active = _active_source(built_at, corrected_at or "")
    return {
        "status": status,
        "site_key": site_key(url),
        "cols": cols,
        "rows": rows,
        "cells": base_cells,
        "overlay": overlay,
        "display_cells": display_cells,
        "built_at": built_at,
        "corrected_at": corrected_at or None,
        "active_source": active,
    }


def stamp_element_correction_on_visual(
    project,
    url: str,
    element: dict[str, Any],
    *,
    interactive: bool,
) -> dict[str, Any] | None:
    if not project or not url:
        return None
    stored = load_visual_map(project, url)
    if not stored:
        return None
    cols = int(stored.get("cols") or DEFAULT_COLS)
    rows = int(stored.get("rows") or DEFAULT_ROWS)
    viewport = stored.get("viewport") if isinstance(stored.get("viewport"), dict) else {}
    if not viewport:
        viewport = {"width": float(cols), "height": float(rows)}
    rect = element.get("rect")
    overlay = stored.get("overlay") if isinstance(stored.get("overlay"), list) else [None] * (cols * rows)
    overlay = [str(item) if item in {"+", "-"} else None for item in overlay]
    while len(overlay) < cols * rows:
        overlay.append(None)
    mark = "+" if interactive else "-"
    for index in _rect_cells(rect or {}, viewport, cols, rows):
        overlay[index] = mark
    base_cells = [str(item) for item in (stored.get("cells") or [])]
    stored["overlay"] = overlay
    stored["display_cells"] = merge_display_cells(base_cells, overlay)
    stored["corrected_at"] = _now()
    stored["updated_at"] = stored["corrected_at"]
    _write_json(visual_file_for_url(project, url), stored)
    return stored

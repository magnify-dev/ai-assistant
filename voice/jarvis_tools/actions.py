"""Jarvis tools - actions.py"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from jarvis_tools.browser_api import (
    _browser_page_title_url,
    _click_browser_context,
    _navigate_browser_context,
)
from jarvis_tools.browser_bridge import _browser_context_is_fresh, _latest_browser_context
from jarvis_tools.constants import GIT_ROOTS, KNOWN_PATHS, _browser_provider
from jarvis_tools.foxmcp.candidates import _foxmcp_clickable_candidates
from jarvis_tools.foxmcp.client import _foxmcp_target_tab_id
from jarvis_tools.foxmcp.clicks import _foxmcp_click_interactable
from jarvis_tools.git_ops import _git_status
from jarvis_tools.llm_resolve import (
    _page_hint_from_actions,
    _resolve_actions_with_llm,
    _use_llm_action_resolver,
)
from jarvis_tools.models import _make_action, _truncate_value
from jarvis_tools.paths import _log_dir
from jarvis_tools.pc_ops import _open_folder_in_cursor, _run_powershell
from jarvis_tools.text_match import _score_action
from jarvis_tools.windows import _enumerate_visible_windows, _focus_hwnd

def _browser_action_candidates() -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if _browser_provider() == "foxmcp":
        tab_id = _foxmcp_target_tab_id()
        if tab_id is None:
            return []
        title, url = _browser_page_title_url()
        for item in _foxmcp_clickable_candidates(tab_id):
            label = str(item.get("text") or item.get("aria") or item.get("title") or item.get("href") or "").strip()
            kind = str(item.get("kind") or "element")
            semantic_action = str(item.get("action") or "")
            href = str(item.get("href") or "")
            action_name = semantic_action or ("open" if href else "click")
            ordinal = int(item.get("ordinal") or 0)
            aliases = [
                str(item.get("text") or ""),
                str(item.get("aria") or ""),
                str(item.get("title") or ""),
                href,
                kind,
                semantic_action,
                "song" if kind in {"video-link", "video-player", "play-button"} else "",
                "track" if kind in {"video-link", "video-player", "play-button"} else "",
                "video" if kind in {"video-link", "video-player", "play-button"} else "",
                "play" if kind in {"play-button", "video-player"} else "",
            ]
            actions.append(
                _make_action(
                    action_id=f"browser:{item.get('index', len(actions))}:{len(actions)}",
                    source="browser",
                    action=action_name,
                    label=label or kind,
                    type_=kind,
                    aliases=aliases,
                    ordinal=ordinal,
                    group=title or url,
                    state={"pageTitle": title, "url": url},
                    payload={"provider": "foxmcp", "tab_id": tab_id, "target": item},
                )
            )
        return actions

    if not _browser_context_is_fresh():
        return []
    context = _latest_browser_context()
    title = str(context.get("title") or "")
    url = str(context.get("url") or "")
    interactables = context.get("interactables") if isinstance(context.get("interactables"), list) else []
    for idx, item in enumerate(interactables):
        if not isinstance(item, dict):
            continue
        label = str(item.get("text") or item.get("aria") or item.get("title") or item.get("href") or "").strip()
        kind = str(item.get("kind") or "element")
        semantic_action = str(item.get("action") or "")
        href = str(item.get("href") or "")
        actions.append(
            _make_action(
                action_id=f"browser-extension:{idx}",
                source="browser",
                action=semantic_action or ("open" if href else "click"),
                label=label or kind,
                type_=kind,
                aliases=[label, href, kind, semantic_action],
                ordinal=int(item.get("ordinal") or 0),
                group=title or url,
                state={"pageTitle": title, "url": url},
                payload={"provider": "extension", "query": label or href or kind},
            )
        )
    return actions

def _pc_action_candidates() -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for idx, (title, exe, hwnd) in enumerate(_enumerate_visible_windows()[:40]):
        app = Path(exe).name if exe else "window"
        actions.append(
            _make_action(
                action_id=f"window:{hwnd}",
                source="pc",
                action="switch",
                label=title,
                type_="window",
                aliases=[title, app, title.split(" - ")[0]],
                ordinal=idx + 1,
                group="visible windows",
                state={"app": app, "exe": exe},
                payload={"hwnd": hwnd},
            )
        )

    app_targets = {
        "cursor": "cursor",
        "firefox": "firefox",
        "youtube": "https://www.youtube.com",
        "my playlists": "https://www.youtube.com/feed/playlists",
        "playlists": "https://www.youtube.com/feed/playlists",
        "google": "https://www.google.com",
        "chatgpt": "https://chatgpt.com",
    }
    for name, target in app_targets.items():
        action = "navigate" if target.startswith("http") else "open"
        actions.append(
            _make_action(
                action_id=f"app:{name}",
                source="pc",
                action=action,
                label=name,
                type_="app" if action == "open" else "website",
                aliases=[name, target],
                payload={"target": target},
            )
        )

    for name, path in KNOWN_PATHS.items():
        actions.append(
            _make_action(
                action_id=f"path:{name}",
                source="pc",
                action="open",
                label=name,
                type_="path",
                aliases=[name, path, Path(path).name],
                payload={"path": path},
            )
        )

    for root in GIT_ROOTS:
        actions.append(
            _make_action(
                action_id=f"tool:git-status:{root}",
                source="tool",
                action="check",
                label=f"git status {root.name}",
                type_="git",
                aliases=["git", "status", "changes", root.name, str(root)],
                payload={"tool": "git_status", "project_path": str(root)},
            )
        )
    return actions

def _action_snapshot(command: str = "") -> dict[str, object]:
    snapshot_id = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    actions = [*_browser_action_candidates(), *_pc_action_candidates()]
    return {
        "id": snapshot_id,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "actions": [_truncate_value(action) for action in actions],
    }

def _write_action_snapshot(snapshot: dict[str, object]) -> None:
    log_dir = _log_dir()
    actions = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    (log_dir / "current-actions.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Current Actions",
        "",
        f"Snapshot: {snapshot.get('id')}",
        f"Created: {snapshot.get('createdAt')}",
        f"Command: {snapshot.get('command')}",
        "",
    ]
    grouped: dict[str, list[dict[str, object]]] = {}
    for action in actions:
        if isinstance(action, dict):
            grouped.setdefault(str(action.get("source") or "unknown"), []).append(action)
    for source, items in grouped.items():
        lines.append(f"## {source}")
        for item in items[:80]:
            bits = [
                str(item.get("action") or ""),
                str(item.get("type") or ""),
                str(item.get("label") or ""),
            ]
            ordinal = item.get("ordinal")
            if ordinal:
                bits.append(f"ordinal={ordinal}")
            lines.append("- " + " | ".join(bit for bit in bits if bit))
        lines.append("")
    (log_dir / "current-actions.md").write_text("\n".join(lines), encoding="utf-8")

    snap_dir = log_dir / "action-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{snapshot.get('id')}.json"
    snap_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

def _append_action_decision(decision: dict[str, object]) -> None:
    path = _log_dir() / "action-decisions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_truncate_value(decision), ensure_ascii=False) + "\n")

def _resolve_action(command: str, snapshot: dict[str, object]) -> tuple[dict[str, object] | None, list[dict[str, object]], dict[str, object] | None]:
    actions = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    ranked: list[dict[str, object]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        score, reasons = _score_action(command, action)
        ranked.append({"score": round(score, 4), "reasons": reasons, "action": action})
    ranked.sort(key=lambda item: float(item["score"]), reverse=True)

    llm_meta: dict[str, object] | None = None
    if _use_llm_action_resolver(command) and actions:
        page_hint = _page_hint_from_actions(actions)
        llm_pick = _resolve_actions_with_llm(command, actions, page_hint=page_hint)
        if llm_pick:
            llm_meta = {"label": llm_pick.get("label"), "id": llm_pick.get("id"), "source": "llm"}
            return llm_pick, ranked[:8], llm_meta

    if not ranked or float(ranked[0]["score"]) < 0.65:
        return None, ranked[:8], None
    if len(ranked) > 1 and float(ranked[0]["score"]) - float(ranked[1]["score"]) < 0.15:
        return None, ranked[:8], None
    return ranked[0]["action"], ranked[:8], None

def _execute_action_candidate(candidate: dict[str, object], *, utterance: str = "") -> str:
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    source = str(candidate.get("source") or "")
    action = str(candidate.get("action") or "")
    if source == "browser":
        provider = str(payload.get("provider") or "")
        if provider == "foxmcp":
            tab_id = int(payload.get("tab_id") or 0)
            target = payload.get("target")
            if tab_id and isinstance(target, dict):
                return _foxmcp_click_interactable(tab_id, target)
        query = str(payload.get("query") or candidate.get("label") or action)
        return _click_browser_context(query, utterance=utterance or query)

    if source == "pc" and candidate.get("type") == "window":
        hwnd = int(payload.get("hwnd") or 0)
        if hwnd:
            _focus_hwnd(hwnd)
            return "OK"
    if source == "pc" and candidate.get("type") == "website":
        target = str(payload.get("target") or "")
        if target:
            return _navigate_browser_context(target)
    if source == "pc" and candidate.get("type") == "app":
        target = str(payload.get("target") or candidate.get("label") or "")
        return _run_powershell(f"Start-Process '{target}'")
    if source == "pc" and candidate.get("type") == "path":
        path = str(payload.get("path") or "")
        if path:
            resolved = Path(path)
            return _open_folder_in_cursor(str(resolved)) if resolved.is_dir() else _run_powershell(f"Start-Process '{path}'")
    if source == "tool" and payload.get("tool") == "git_status":
        return _git_status(str(payload.get("project_path") or ""))
    return f"No executor for action: {candidate.get('id')}"

def _act_on_context(command: str) -> str:
    command = command.strip()
    if not command:
        return "No action command provided."
    snapshot = _action_snapshot(command)
    _write_action_snapshot(snapshot)
    selected, top, llm_meta = _resolve_action(command, snapshot)
    decision: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "snapshotId": snapshot.get("id"),
        "topMatches": top,
        "selected": selected,
    }
    if llm_meta:
        decision["llmResolved"] = llm_meta
    if not selected:
        decision["result"] = "no-confident-action"
        _append_action_decision(decision)
        return "I couldn't confidently map that to an available action."
    result = _execute_action_candidate(selected, utterance=command)
    decision["executorResult"] = result
    ok = result == "OK" or result.startswith(("OK:", "Opened ", "Created ", "Pushed ", "Project:"))
    decision["verified"] = ok
    if ok:
        action_name = str(selected.get("action") or "")
        source = str(selected.get("source") or "")
        if source == "browser" or action_name in {"open", "navigate", "switch"}:
            snapshot_command = f"after: {command}"

            def _write_post_snapshot() -> None:
                post_snapshot = _action_snapshot(snapshot_command)
                _write_action_snapshot(post_snapshot)
                decision["postSnapshotId"] = post_snapshot.get("id")

            threading.Thread(target=_write_post_snapshot, daemon=True).start()
    _append_action_decision(decision)
    if ok:
        return "OK"
    return result


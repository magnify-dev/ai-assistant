from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from web_surf.store import normalize_url


def _candidate_label(row: Any, url: str) -> str:
    title = str(getattr(row, "title", "") or "").strip()
    if title:
        return title[:80]
    host = urlparse(url).netloc.replace("www.", "")
    return host or url[:80]


def _safe_norm(url: str) -> str:
    try:
        return normalize_url(str(url or ""))
    except (TypeError, ValueError):
        return str(url or "").strip()


def match_seed_url(url: str, seed_urls: list[str]) -> str:
    """Return the canonical normalized seed URL matching url, or empty string."""
    norm = _safe_norm(url)
    if not norm:
        return ""
    seed_norms = {_safe_norm(seed): seed for seed in seed_urls}
    if norm in seed_norms:
        return _safe_norm(seed_norms[norm])
    return ""


def resolve_active_branch_url(
    *,
    page_url: str,
    seed_urls: list[str],
    active_branch_url: str,
) -> str:
    """Keep branch identity in sync when the browser lands on a search-result seed."""
    matched = match_seed_url(page_url, seed_urls)
    if matched:
        return matched
    active = _safe_norm(active_branch_url)
    return active or matched


def _visited_urls(history: list[dict[str, Any]]) -> set[str]:
    visited: set[str] = set()
    for item in history:
        if not item.get("ok"):
            continue
        for key in ("url", "target_href"):
            raw = str(item.get(key) or "").strip()
            if raw:
                norm = _safe_norm(raw)
                if norm:
                    visited.add(norm)
        if str(item.get("action") or "") in {"navigate", "swap_branch"}:
            raw = str(item.get("url") or item.get("target_href") or "").strip()
            if raw:
                norm = _safe_norm(raw)
                if norm:
                    visited.add(norm)
    return visited


def _explored_branch_urls(
    history: list[dict[str, Any]],
    *,
    seed_urls: list[str] | None = None,
) -> set[str]:
    explored: set[str] = set()
    seed_norms = {_safe_norm(seed) for seed in (seed_urls or []) if _safe_norm(seed)}
    for item in history:
        if not item.get("ok"):
            continue
        branch = _safe_norm(str(item.get("branch_url") or ""))
        if branch:
            explored.add(branch)
        action = str(item.get("action") or "")
        navigated = _safe_norm(str(item.get("url") or ""))
        if navigated and action in {"navigate", "swap_branch", "origin", "click"}:
            if not seed_norms or navigated in seed_norms:
                explored.add(navigated)
            matched = match_seed_url(navigated, list(seed_urls or []))
            if matched:
                explored.add(matched)
    return explored


def unexplored_seed_urls(
    seed_urls: list[str],
    history: list[dict[str, Any]],
    *,
    active_branch_url: str = "",
    current_page_url: str = "",
) -> list[str]:
    """Seed URLs from search that have not yet been opened as an exploration branch."""
    explored = _explored_branch_urls(history, seed_urls=seed_urls)
    active = _safe_norm(active_branch_url)
    if active:
        explored.add(active)
    on_seed = match_seed_url(current_page_url, seed_urls)
    if on_seed:
        explored.add(on_seed)
    pending: list[str] = []
    for url in seed_urls:
        norm = _safe_norm(url)
        if norm and norm not in explored:
            pending.append(norm)
    return pending


def _branch_history(
    history: list[dict[str, Any]],
    *,
    active_branch_url: str,
) -> list[dict[str, Any]]:
    branch_key = _safe_norm(active_branch_url)
    if not branch_key:
        return list(history)
    scoped: list[dict[str, Any]] = []
    for item in history:
        tagged = str(item.get("branch_url") or "").strip()
        if not tagged or _safe_norm(tagged) == branch_key:
            scoped.append(item)
    return scoped or list(history)


def _stall_count(history: list[dict[str, Any]], *, window: int = 8) -> int:
    return sum(
        1
        for item in history[-window:]
        if item.get("progress") is False
        or (
            not item.get("ok")
            and (
                "no progress" in str(item.get("error") or "").lower()
                or "already tried" in str(item.get("error") or "").lower()
            )
        )
    )


def summarize_exploration_branches(
    *,
    current_url: str,
    seed_urls: list[str],
    candidates: list[Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    active_branch_url: str | None = None,
    branch_steps: int = 0,
    max_steps_per_branch: int = 20,
    stall_window: int = 8,
    stall_threshold: int = 5,
    min_branch_steps_before_swap: int = 4,
) -> dict[str, Any]:
    """Summarize search-result branches so the model can continue or swap."""
    history = list(history or [])
    visited = _visited_urls(history)
    current = _safe_norm(current_url)
    active_branch = _safe_norm(active_branch_url or current_url or (seed_urls[0] if seed_urls else ""))

    title_by_url: dict[str, str] = {}
    rows = list(candidates or [])
    seed_norms = [_safe_norm(url) for url in seed_urls if _safe_norm(url)]
    for index, url in enumerate(seed_urls):
        norm = _safe_norm(url)
        if not norm:
            continue
        row = rows[index] if index < len(rows) else None
        title_by_url[norm] = _candidate_label(row, norm)

    branch_history = _branch_history(history, active_branch_url=active_branch)
    stall_count = _stall_count(branch_history, window=stall_window)
    steps_on_branch = branch_steps or sum(
        1 for item in branch_history if item.get("action") not in {None, "pending"}
    )
    on_redirect = bool(current and active_branch and current != active_branch)
    branch_stalled = (
        steps_on_branch >= min_branch_steps_before_swap and stall_count >= stall_threshold
    ) or steps_on_branch >= max_steps_per_branch

    branches: list[dict[str, Any]] = []
    for norm in seed_norms:
        if norm == active_branch:
            status = "stalled" if branch_stalled else "active"
        elif norm in visited:
            status = "visited"
        else:
            status = "unexplored"
        branches.append(
            {
                "url": norm,
                "label": title_by_url.get(norm, norm)[:80],
                "status": status,
            }
        )

    active_label = title_by_url.get(active_branch, active_branch)[:80]
    current_branch: dict[str, Any] = {
        "url": active_branch,
        "label": active_label or active_branch[:80],
        "status": "stalled" if branch_stalled else "active",
        "steps": steps_on_branch,
        "max_steps": max_steps_per_branch,
    }
    if on_redirect and current:
        current_branch["current_page"] = current
        host = urlparse(current).netloc.replace("www.", "")
        current_branch["redirect_note"] = (
            f"Redirected within this branch — continue exploring {host or 'this page'} "
            "before swap_branch."
        )

    alternatives = [branch for branch in branches if branch["status"] == "unexplored"]
    can_back = any(
        item.get("ok")
        and str(item.get("action") or "") in {"navigate", "swap_branch", "click", "origin", "back"}
        for item in branch_history
    )

    advice = ""
    if branch_stalled and alternatives:
        advice = (
            "This branch looks sufficiently explored and stalled — use swap_branch to an "
            "unexplored alternative, or back to leave this branch."
        )
    elif branch_stalled and not alternatives:
        advice = "This branch looks stalled and no unexplored alternatives remain — try back or report."
    elif on_redirect:
        advice = current_branch.get("redirect_note", "")

    return {
        "current": current_branch,
        "all": branches[:10],
        "alternatives": alternatives[:6],
        "stall_count": stall_count,
        "branch_steps": steps_on_branch,
        "max_steps_per_branch": max_steps_per_branch,
        "can_back": can_back,
        "advice": advice,
    }


def build_exploration_menu(
    *,
    controls: list[dict[str, Any]] | None,
    overlay_actions: list[dict[str, Any]] | None,
    branch_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compact numbered menu of possible next moves for the local model."""
    menu: list[dict[str, Any]] = []
    number = 1

    advice = str(branch_info.get("advice") or "").strip()
    if advice:
        menu.append({"n": number, "action": "note", "label": advice})
        number += 1

    current = branch_info.get("current") if isinstance(branch_info.get("current"), dict) else {}
    branch_stalled = str(current.get("status") or "") == "stalled"

    for alt in branch_info.get("alternatives") or []:
        if not isinstance(alt, dict) or not alt.get("url"):
            continue
        menu.append(
            {
                "n": number,
                "action": "swap_branch",
                "url": str(alt["url"]),
                "label": f"Swap branch → {alt.get('label') or alt['url']}"[:100],
            }
        )
        number += 1

    if branch_info.get("can_back"):
        menu.append({"n": number, "action": "back", "label": "Return — leave this branch"})
        number += 1

    if not branch_stalled:
        for overlay in overlay_actions or []:
            if not isinstance(overlay, dict):
                continue
            for act in overlay.get("actions") or []:
                if not isinstance(act, dict) or not act.get("id"):
                    continue
                intent = str(act.get("intent") or "click")
                action = intent if intent in {"fill", "select"} else "click"
                menu.append(
                    {
                        "n": number,
                        "action": action,
                        "target_id": str(act["id"]),
                        "label": f"{overlay.get('kind', 'overlay')}: {act.get('label', act['id'])}"[:100],
                    }
                )
                number += 1
                if number > 14:
                    break
            if number > 14:
                break

        for control in controls or []:
            if not isinstance(control, dict) or not control.get("id"):
                continue
            row: dict[str, Any] = {
                "n": number,
                "action": str(control.get("action") or "click"),
                "target_id": str(control["id"]),
                "label": str(control.get("label") or control["id"])[:100],
            }
            href = str(control.get("href") or "").strip()
            if href:
                row["href"] = href
            menu.append(row)
            number += 1
            if number > 18:
                break

    menu.append({"n": number, "action": "extract", "label": "Extract facts from this page"})
    menu.append({"n": number + 1, "action": "report", "label": "Goal met on this page — report answer"})
    return menu[:20]

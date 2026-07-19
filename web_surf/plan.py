"""Accomplishment-step plans derived from the user prompt and tracked through browse."""

from __future__ import annotations

import re
from typing import Any

from web_surf.page_match import focus_query, parse_user_preferred_domains, query_implies_recency

_TERMINAL_RE = re.compile(r"\b(report|answer|copy|deliver|return|paste)\b", re.I)
_EXTRACT_RE = re.compile(r"\b(extract|collect|copy|capture|read|gather)\b", re.I)
_OPEN_RE = re.compile(r"\b(open|visit|go to|navigate|reach|find site|load)\b", re.I)
_LATEST_RE = re.compile(r"\b(latest|newest|most recent|current|recency)\b", re.I)


def _slug_id(index: int, description: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-").split("-")
    stem = "-".join(words[:4]) or "step"
    return f"s{index}_{stem}"[:40]


_HOME_PAGE_RE = re.compile(r"\b(home\s*page|homepage|front page|main page)\b", re.I)
_FLUFF_RE = re.compile(
    r"\b(verify|credibility|accuracy|double[- ]?check|validate source|confirm authenticity)\b",
    re.I,
)


def _sanitize_step_text(text: str) -> str:
    """Avoid plan wording that forces useless homepage navigations or fluff checks."""
    cleaned = str(text or "").strip()
    cleaned = _HOME_PAGE_RE.sub("site", cleaned)
    return cleaned[:240]


def _is_fluff_step(description: str, done_when: str) -> bool:
    blob = f"{description} {done_when}"
    if _FLUFF_RE.search(blob) and not _EXTRACT_RE.search(blob) and not _TERMINAL_RE.search(blob):
        return True
    return False


def normalize_accomplishment_steps(
    raw: Any,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    """Normalize model/fallback steps into a stable list of plan items."""
    steps: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw, start=1):
            if isinstance(item, str) and item.strip():
                description = _sanitize_step_text(item)
                if not description or _is_fluff_step(description, description):
                    continue
                steps.append(
                    {
                        "id": _slug_id(index, description),
                        "description": description,
                        "done_when": description,
                        "status": "pending",
                    }
                )
                continue
            if not isinstance(item, dict):
                continue
            description = _sanitize_step_text(
                str(item.get("description") or item.get("step") or "")
            )
            if not description:
                continue
            done_when = _sanitize_step_text(
                str(item.get("done_when") or item.get("done") or description)
            )
            if _is_fluff_step(description, done_when):
                continue
            step_id = str(item.get("id") or "").strip() or _slug_id(index, description)
            status = str(item.get("status") or "pending").strip().lower()
            if status not in {"pending", "done", "skipped"}:
                status = "pending"
            steps.append(
                {
                    "id": step_id[:40],
                    "description": description,
                    "done_when": done_when,
                    "status": status,
                }
            )
    if steps:
        # Ensure a terminal report step exists.
        if not any(is_terminal_step(step) for step in steps):
            steps.append(
                {
                    "id": "s_report",
                    "description": "Report the final answer once the prior steps are done",
                    "done_when": "Answer is ready to return to the user",
                    "status": "pending",
                }
            )
        return steps[:8]
    return fallback_accomplishment_steps(query)


def fallback_accomplishment_steps(query: str) -> list[dict[str, Any]]:
    """Deterministic plan when the model does not return accomplishment_steps."""
    goal = focus_query(query).strip() or str(query or "").strip()
    if not goal:
        return []
    preferred = sorted(parse_user_preferred_domains(goal))
    steps: list[dict[str, str]] = []
    if preferred:
        site = preferred[0]
        if query_implies_recency(goal) or re.search(r"\bnews\b", goal, re.I):
            steps.append(
                {
                    "id": "s1_open_listing",
                    "description": f"Open the {site} news/listing page for this topic",
                    "done_when": f"Browser is on a {site} news or updates listing (not a random article)",
                }
            )
            steps.append(
                {
                    "id": "s2_pick_latest",
                    "description": "From the listing, open the newest dated item that matches the request",
                    "done_when": "On the newest matching article chosen from dates/titles on the listing",
                }
            )
        else:
            steps.append(
                {
                    "id": "s1_open_site",
                    "description": f"Open {site} and navigate to the relevant section",
                    "done_when": f"Browser is on {site} content that matches the request",
                }
            )
            steps.append(
                {
                    "id": "s2_reach_content",
                    "description": f"Navigate to the page that matches: {goal[:120]}",
                    "done_when": "Visible page content is clearly about the requested topic",
                }
            )
    else:
        steps.append(
            {
                "id": "s1_open_source",
                "description": "Open a relevant search result for the user's request",
                "done_when": "Landed on a page that can answer the request",
            }
        )
        if query_implies_recency(goal):
            steps.append(
                {
                    "id": "s2_pick_latest",
                    "description": "Identify and open the newest / most recent matching item",
                    "done_when": "On the newest dated article or section that matches the request",
                }
            )
        else:
            steps.append(
                {
                    "id": "s2_reach_content",
                    "description": f"Navigate to the page that matches: {goal[:120]}",
                    "done_when": "Visible page content is clearly about the requested topic",
                }
            )
    steps.append(
        {
            "id": "s4_extract",
            "description": "Extract or copy the facts/text the user asked for",
            "done_when": "Required content has been collected from the page",
        }
    )
    steps.append(
        {
            "id": "s5_report",
            "description": "Report the final answer once the prior steps are done",
            "done_when": "Answer is ready to return to the user",
        }
    )
    return [{**step, "status": "pending"} for step in steps]


def is_terminal_step(step: dict[str, Any]) -> bool:
    blob = f"{step.get('description') or ''} {step.get('done_when') or ''}"
    return bool(_TERMINAL_RE.search(blob))


def is_extract_step(step: dict[str, Any]) -> bool:
    blob = f"{step.get('description') or ''} {step.get('done_when') or ''}"
    return bool(_EXTRACT_RE.search(blob)) and not is_terminal_step(step)


def plan_progress(steps: list[dict[str, Any]]) -> dict[str, Any]:
    pending = [step for step in steps if step.get("status") != "done"]
    done = [step for step in steps if step.get("status") == "done"]
    current = pending[0] if pending else None
    blocking = [step for step in pending if not is_terminal_step(step)]
    return {
        "steps": steps,
        "current": current,
        "remaining": pending,
        "done": done,
        "blocking": blocking,
        "all_done": not pending,
        "ready_to_report": not blocking,
    }


def mark_step_done(steps: list[dict[str, Any]], step_id: str) -> bool:
    target = str(step_id or "").strip()
    if not target:
        return False
    for step in steps:
        if str(step.get("id") or "") == target and step.get("status") != "done":
            step["status"] = "done"
            return True
    return False


def mark_current_done(steps: list[dict[str, Any]]) -> str | None:
    progress = plan_progress(steps)
    current = progress.get("current")
    if not isinstance(current, dict):
        return None
    step_id = str(current.get("id") or "")
    if mark_step_done(steps, step_id):
        return step_id
    return None


def infer_step_completion(
    steps: list[dict[str, Any]],
    *,
    action: str,
    page_relevant: bool = False,
    evidence_collected: bool = False,
    reported: bool = False,
    on_preferred_source: bool = False,
) -> list[str]:
    """Heuristic completion marks from successful browser outcomes."""
    marked: list[str] = []
    action = str(action or "").lower().strip()

    def _mark_matching(predicate) -> None:
        for step in steps:
            if step.get("status") == "done":
                continue
            if predicate(step):
                step["status"] = "done"
                marked.append(str(step.get("id") or ""))

    if action in {"navigate", "swap_branch", "click"} and (on_preferred_source or page_relevant):
        _mark_matching(lambda step: bool(_OPEN_RE.search(str(step.get("description") or ""))))
    if page_relevant:
        _mark_matching(
            lambda step: "navigate to the page" in str(step.get("description") or "").lower()
            or "reach" in str(step.get("description") or "").lower()
            or "matches:" in str(step.get("description") or "").lower()
        )
    if page_relevant and action in {"click", "navigate", "swap_branch", "extract", "filter"}:
        # Mark "pick latest" style steps once we are on relevant content.
        _mark_matching(lambda step: bool(_LATEST_RE.search(str(step.get("description") or ""))))
    if evidence_collected or action in {"extract", "filter"}:
        _mark_matching(is_extract_step)
    if reported:
        _mark_matching(is_terminal_step)
    return marked


def compact_plan_for_prompt(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Payload fragment for browse/extract/answer prompts."""
    progress = plan_progress(steps)
    current = progress.get("current")
    return {
        "user_goal_steps": [
            {
                "id": step.get("id"),
                "description": step.get("description"),
                "done_when": step.get("done_when"),
                "status": step.get("status"),
            }
            for step in steps
        ],
        "current_step": (
            {
                "id": current.get("id"),
                "description": current.get("description"),
                "done_when": current.get("done_when"),
            }
            if isinstance(current, dict)
            else None
        ),
        "remaining_steps": [
            {"id": step.get("id"), "description": step.get("description")}
            for step in progress["remaining"]
        ],
        "plan_note": (
            "Follow user_goal_steps in order. Advance the current_step with your next action. "
            "Do not report until remaining non-report steps are done (or clearly impossible). "
            "When a step is finished, set completed_step_id to that step's id."
        ),
        "ready_to_report": progress["ready_to_report"],
    }

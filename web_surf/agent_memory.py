from __future__ import annotations

import re
from typing import Any

_DECISION_KEYS = ("action", "target_id", "url", "value_key", "reason", "question", "note")
_OVERLAY_DEFERRED_RE = re.compile(
    r"clear blocking overlay|dismiss consent|complete age verification",
    re.I,
)
_TARGET_NOT_IN_SNAPSHOT_RE = re.compile(
    r"(fill|select|click) target_id is not in the current snapshot",
    re.I,
)


def compact_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(decision[key])[:200]
        for key in _DECISION_KEYS
        if decision.get(key)
    }


def outcome_status(outcome: dict[str, Any]) -> str:
    if outcome.get("ok") is True:
        return "ok"
    if outcome.get("progress") is False:
        return "no_change"
    if outcome.get("ok") is False:
        return "fail"
    return "unknown"


def summarize_step(
    *,
    step_id: str,
    decision: dict[str, Any],
    outcome: dict[str, Any],
    page_url: str = "",
) -> str:
    action = str(decision.get("action") or outcome.get("action") or "?")
    target = str(decision.get("target_id") or outcome.get("target_id") or "").strip()
    reason = str(decision.get("reason") or outcome.get("reason") or "").strip()
    label = str(outcome.get("target_label") or "").strip()
    url = str(
        outcome.get("url") or outcome.get("target_href") or decision.get("url") or ""
    ).strip()
    status = outcome_status(outcome)
    error = str(outcome.get("error") or "").strip()

    parts = [f"{step_id}: {action}"]
    if target:
        parts.append(f"@{target}")
    if label:
        parts.append(f'"{label[:40]}"')
    if url:
        parts.append(f"->{url[:80]}")
    if reason:
        parts.append(f'why:{reason[:80]}')
    parts.append(status)
    if error:
        parts.append(f"({error[:100]})")
    hint = str(outcome.get("hint") or "").strip()
    if hint and hint not in error:
        parts.append(f"hint:{hint[:80]}")
    if page_url:
        parts.append(f"on {page_url[:80]}")
    return " ".join(parts)


def commit_agent_memory(
    *,
    step_id: str,
    decision: dict[str, Any],
    outcome: dict[str, Any],
    page_url: str = "",
    hint: str = "",
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one memory entry from a model decision and its executed outcome."""
    failure_hint = str(hint or outcome.get("hint") or "").strip()
    if not failure_hint and outcome.get("ok") is False:
        failure_hint = explain_failure(
            decision,
            str(outcome.get("error") or ""),
            snapshot=snapshot,
        )
    outcome_payload = {
        "ok": outcome.get("ok"),
        "progress": outcome.get("progress"),
        "status": outcome_status(outcome),
        "error": str(outcome.get("error") or "")[:200],
        "url": str(outcome.get("url") or "")[:200],
    }
    if failure_hint:
        outcome_payload["hint"] = failure_hint[:160]
    return {
        "step_id": step_id,
        "page_url": page_url[:200] if page_url else "",
        "decision": compact_decision(decision),
        "outcome": outcome_payload,
        "summary": summarize_step(
            step_id=step_id,
            decision=decision,
            outcome={**outcome, "hint": failure_hint} if failure_hint else outcome,
            page_url=page_url,
        ),
    }


def explain_failure(
    decision: dict[str, Any] | None,
    error: str,
    *,
    snapshot: dict[str, Any] | None = None,
) -> str:
    """Turn a validation/runtime error into a short, actionable hint for the model."""
    err = str(error or "").strip()
    if not err:
        return ""
    action = str((decision or {}).get("action") or "").lower()
    target = str((decision or {}).get("target_id") or "").strip()
    url = str((decision or {}).get("url") or "").strip()
    value_key = str((decision or {}).get("value_key") or "").strip()

    if _TARGET_NOT_IN_SNAPSHOT_RE.search(err) and target:
        overlay_ids = {
            str(item.get("id") or "")
            for item in (snapshot or {}).get("blocking_overlays") or []
            if isinstance(item, dict) and item.get("id")
        }
        if target in overlay_ids:
            return f"{target} is the overlay container, not a field — use menu[] or form_fields[]"
        field_ids = {
            str(item.get("id") or "")
            for item in (snapshot or {}).get("interactables") or []
            if isinstance(item, dict) and item.get("id")
        }
        if not field_ids:
            return f"no form controls on page — cannot {action or 'act on'} {target}"
        return f"{target} not on page — copy id from menu[] or form_fields[]"

    if _OVERLAY_DEFERRED_RE.search(err):
        if action in {"extract", "filter", "report"}:
            return f"{action} blocked until overlay is cleared"
        if action in {"navigate", "swap_branch"} or url:
            return "navigation blocked while overlay is up — clear overlay first or swap_branch from menu[]"
        return "overlay must be cleared before this action"

    if "already tried" in err.lower():
        parts = [action or "action"]
        if target:
            parts.append(f"@{target}")
        if value_key:
            parts.append(f"[{value_key}]")
        return f"{' '.join(parts)} — no page change, pick something else"

    if "already collected" in err.lower():
        return "page content already collected — use action=report"

    if "no progress" in err.lower():
        return "same page state after action — try a different control or route"

    if "value_key is not available" in err.lower():
        return f"need provide_values for {value_key or 'missing key'} before {action}"

    return err[:120]


def compact_failed_steps(
    entries: list[dict[str, Any]] | None,
    *,
    limit: int = 8,
) -> list[str]:
    """Short lines for steps that failed or made no progress."""
    lines: list[str] = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
        status = str(outcome.get("status") or outcome_status(outcome))
        if status not in {"fail", "no_change"}:
            continue
        step = str(item.get("step_id") or "")
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        action = str(decision.get("action") or "?")
        target = str(decision.get("target_id") or "").strip()
        url = str(decision.get("url") or "").strip()
        hint = str(outcome.get("hint") or "").strip()
        error = str(outcome.get("error") or "").strip()
        line = f"{step} {action}"
        if target:
            line += f" @{target}"
        if url:
            line += f" -> {url[:60]}"
        if hint:
            line += f": {hint[:90]}"
        elif error:
            line += f": {error[:90]}"
        lines.append(line)
    return lines[-limit:]


def compact_avoid(
    *,
    blocked_signatures: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    agent_memory: list[dict[str, Any]] | None = None,
    snapshot: dict[str, Any] | None = None,
    limit: int = 10,
) -> list[str]:
    """Human-readable do-not-repeat list from signatures, history, and memory."""
    seen: set[str] = set()
    avoid: list[str] = []

    def _add(line: str) -> None:
        text = " ".join(str(line or "").split()).strip()
        if not text or text in seen:
            return
        seen.add(text)
        avoid.append(text[:140])

    for sig in blocked_signatures or []:
        parts = str(sig).split("|")
        action = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        url = parts[2] if len(parts) > 2 else ""
        value_key = parts[3] if len(parts) > 3 else ""
        hint = explain_failure(
            {"action": action, "target_id": target, "url": url, "value_key": value_key},
            "action already tried without progress",
            snapshot=snapshot,
        )
        _add(hint or sig)

    for item in agent_memory or []:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
        if outcome.get("status") not in {"fail", "no_change"}:
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        hint = str(outcome.get("hint") or "").strip()
        if not hint:
            hint = explain_failure(decision, str(outcome.get("error") or ""), snapshot=snapshot)
        action = str(decision.get("action") or "?")
        target = str(decision.get("target_id") or "").strip()
        url = str(decision.get("url") or "").strip()
        line = action
        if target:
            line += f" @{target}"
        if url:
            line += f" -> {url[:50]}"
        if hint:
            line += f" — {hint}"
        _add(line)

    for item in history or []:
        if not isinstance(item, dict) or item.get("ok"):
            continue
        decision = {
            "action": item.get("action"),
            "target_id": item.get("target_id"),
            "url": item.get("url") or item.get("target_href"),
            "value_key": item.get("value_key"),
        }
        hint = str(item.get("hint") or "").strip()
        if not hint:
            hint = explain_failure(decision, str(item.get("error") or ""), snapshot=snapshot)
        action = str(item.get("action") or "?")
        target = str(item.get("target_id") or "").strip()
        line = action
        if target:
            line += f" @{target}"
        if hint:
            line += f" — {hint}"
        _add(line)

    return avoid[:limit]


def compact_branch_note(
    branch_info: dict[str, Any] | None,
    agent_memory: list[dict[str, Any]] | None = None,
) -> str:
    """One line: active branch, redirect, and last branch-level choice."""
    info = branch_info if isinstance(branch_info, dict) else {}
    current = info.get("current") if isinstance(info.get("current"), dict) else {}
    label = str(current.get("label") or current.get("url") or "")[:60]
    page = str(current.get("current_page") or "").strip()
    steps = int(info.get("branch_steps") or current.get("steps") or 0)
    stalls = int(info.get("stall_count") or 0)

    last_swap = ""
    for item in reversed(agent_memory or []):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        if str(decision.get("action") or "") != "swap_branch":
            continue
        url = str(decision.get("url") or "").strip()
        reason = str(decision.get("reason") or "").strip()
        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
        status = str(outcome.get("status") or "")
        if url:
            last_swap = f"last swap -> {url[:50]}"
            if status in {"fail", "no_change"}:
                last_swap += " (failed)"
            elif reason:
                last_swap += f" ({reason[:40]})"
        break

    parts = [f"branch: {label}", f"{steps} steps"]
    if stalls:
        parts.append(f"{stalls} stalls")
    if page:
        parts.append(f"now on {page[:50]}")
    if last_swap:
        parts.append(last_swap)
    advice = str(info.get("advice") or "").strip()
    if advice:
        parts.append(advice[:80])
    return " · ".join(parts)[:220]


def stuck_reason(
    *,
    snapshot: dict[str, Any],
    branch_info: dict[str, Any] | None = None,
    failed_steps: list[str] | None = None,
) -> str:
    """One-line explanation of why exploration is stuck on this page."""
    overlays = [
        item
        for item in (snapshot.get("blocking_overlays") or [])
        if isinstance(item, dict)
    ]
    interactables = [
        item
        for item in (snapshot.get("interactables") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    stalls = int((branch_info or {}).get("stall_count") or 0)

    if overlays and not interactables:
        kind = str(overlays[0].get("label") or overlays[0].get("text") or "overlay")[:40]
        return f"{kind} blocks page but no controls captured — use menu[] or swap_branch"

    if overlays:
        return "overlay present — clear it via menu[] before extract/report"

    if stalls >= 3:
        return f"branch stalled ({stalls} no-progress steps) — swap_branch or back"

    if failed_steps and len(failed_steps) >= 3:
        return "repeated failures on this page — try a different action from menu[]"

    return ""


def compact_agent_memory_for_prompt(
    entries: list[dict[str, Any]] | None,
    *,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    """Return prompt-safe memory rows and an optional truncation note."""
    rows = [item for item in (entries or []) if isinstance(item, dict) and item.get("step_id")]
    total = len(rows)
    note = ""
    if total > limit:
        note = f"Last {limit} of {total} steps."
        rows = rows[-limit:]
    compact = [
        {
            "step": str(item.get("step_id") or ""),
            "summary": str(item.get("summary") or "")[:180],
        }
        for item in rows
    ]
    return compact, note

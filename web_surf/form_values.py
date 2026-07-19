from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from web_surf.context_curate import curate_form_plan_context
from web_surf.llm import ollama_chat_json
from web_surf.spec import _get_prompt

logger = logging.getLogger(__name__)

FormValuesProvider = Callable[[dict[str, Any]], dict[str, Any]]

VERIFICATION_FIELD_RE = re.compile(
    r"\b(age|birth|dob|date.of.birth|country|identity|verify|consent|email|postal|zip|phone|region|state)\b",
    re.I,
)
AGE_GATE_RE = re.compile(
    r"\b(age.?verif\w*|confirm your age|verify your age|date of birth|birth.?year|"
    r"too young|enter your (date|age)|dob)\b",
    re.I,
)
FORM_KINDS = {"textbox", "combobox", "input", "select", "textarea", "spinbutton"}
MAX_FORM_KEYS = 24
MAX_VALUE_LEN = 2000
MIN_ADULT_AGE_YEARS = 25
DEFAULT_BIRTH_YEAR = "1990"
DEFAULT_BIRTH_DATE = "1990-06-15"
AGE_GATE_AGENT_NOTE = (
    "Age-limited content: gates reject recent birth years as too young to access. "
    f"Use value_keys with birth years at least {MIN_ADULT_AGE_YEARS} years ago "
    f"(e.g. birth_year={DEFAULT_BIRTH_YEAR}). Never pick the current year or other recent years."
)
GATE_FIELD_RE = re.compile(
    r"\b(year|month|day|birth|dob|date of birth|country|region|nation)\b",
    re.I,
)
NON_GATE_FIELD_RE = re.compile(
    r"\b(difficulty|email|newsletter|subscribe|search|password|comment|username)\b",
    re.I,
)
GATE_CONFIRM_RE = re.compile(
    r"\b(continue|confirm|submit|enter|verify|proceed)\b",
    re.I,
)
COOKIE_OVERLAY_RE = re.compile(
    r"\b(cookies?|consent|tracking|privacy preferences|gdpr|ccpa)\b",
    re.I,
)
LEGAL_LINK_RE = re.compile(
    r"\b(cookie policy|privacy policy|privacy notice|terms of (use|service)|"
    r"terms and conditions|legal notice|more information about your privacy)\b",
    re.I,
)
OVERLAY_REJECT_RE = re.compile(
    r"\b(reject all|decline all|reject|decline|deny all|deny|only necessary|essential cookies only)\b",
    re.I,
)
OVERLAY_ACCEPT_RE = re.compile(
    r"\b(accept all cookies|accept cookies|accept all|agree and continue|allow all|i agree|i accept|got it)\b",
    re.I,
)
CONSENT_REGION_RE = re.compile(
    r"\b(cookie|consent|privacy|tracking|gdpr|ccpa|your privacy)\b",
    re.I,
)
OVERLAY_DISMISS_RE = re.compile(
    r"\b(accept all cookies|accept cookies|agree and continue|accept all|reject all|"
    r"got it|allow all|keep watching|skip(?:\s+ad)?|close video|not interested)\b",
    re.I,
)
OVERLAY_BUTTON_RE = re.compile(
    r"\b(accept|agree|allow|ok|okay|close|continue|confirm|dismiss|got it|understood|"
    r"reject|decline|save|keep watching|skip)\b",
    re.I,
)
VIDEO_OVERLAY_RE = re.compile(
    r"\b(keep watching|watch next|play video|video player|trailer|skip ad)\b",
    re.I,
)
# Timestamped player chrome like "0:16 / 2:55 Keep Watching" — unstable for locators.
_VIDEO_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?\b")
_VIDEO_DISMISS_LABEL_RE = re.compile(
    r"\b(keep watching|skip(?:\s+ad)?|close(?:\s+video)?|not interested|watch next)\b",
    re.I,
)
_OVERLAY_KIND_ORDER = {"cookie": 0, "video": 1, "generic": 2, "age_gate": 3}
NEGATIVE_REPORT_RE = re.compile(
    r"\b("
    r"not available|not found|unable to find|cannot find|could not find|"
    r"no .{0,40}found|does not contain|does not display|aren't available|are not available|"
    r"no official|not official|without official|community feedback|"
    r"unavailable|no relevant|without finding|not present|doesn't contain"
    r")\b",
    re.I,
)
_GATE_FIELD_ORDER = ("year", "month", "day", "birth", "country")
_MONTH_ABBR = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _field_description(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("text", "aria", "label", "placeholder", "name", "nearby_text")
    ).strip()


def _primary_label(item: dict[str, Any]) -> str:
    """Element label only — excludes nearby_text to avoid false overlay matches."""
    return " ".join(
        str(item.get(key) or "")
        for key in ("text", "aria", "label", "placeholder", "name")
    ).strip()


def _is_legal_info_link(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or item.get("role") or "").lower()
    if kind != "link":
        return False
    primary = _primary_label(item).lower()
    href = str(item.get("href") or "").lower()
    if LEGAL_LINK_RE.search(primary):
        return True
    if re.search(r"/cookies?|/privacy|/terms|/legal", href):
        return True
    return False


def _is_overlay_button(item: dict[str, Any]) -> bool:
    if item.get("disabled") or not item.get("id") or _is_legal_info_link(item):
        return False
    kind = str(item.get("kind") or item.get("role") or "").lower()
    if kind in {"button", "blz-button"}:
        return True
    # Video/promo dismiss controls are often links or clickable divs.
    primary = _primary_label(item)
    if kind in {"link", "div"} and (
        OVERLAY_DISMISS_RE.search(primary) or VIDEO_OVERLAY_RE.search(primary)
    ):
        return True
    return False


def _in_consent_region(item: dict[str, Any]) -> bool:
    blob = " ".join(
        str(item.get(key) or "")
        for key in ("landmark", "nearest_heading", "nearby_text")
    )
    return bool(CONSENT_REGION_RE.search(blob))


def _snapshot_blockers(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    from ui_test.browser_state import filter_blocking_overlays

    return filter_blocking_overlays(snapshot.get("blocking_overlays") or [])


def classify_overlay(overlay: dict[str, Any]) -> str:
    blob = f"{overlay.get('text') or ''} {overlay.get('label') or ''} {overlay.get('tag') or ''}"
    if COOKIE_OVERLAY_RE.search(blob):
        return "cookie"
    if AGE_GATE_RE.search(blob):
        return "age_gate"
    if VIDEO_OVERLAY_RE.search(blob):
        return "video"
    return "generic"


def _prioritized_overlays(overlays: list[Any], snapshot: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    classified: list[tuple[str, dict[str, Any]]] = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        classified.append((classify_overlay(overlay), overlay))
    classified.sort(key=lambda pair: _OVERLAY_KIND_ORDER.get(pair[0], 9))
    return classified


def _overlay_blob(overlay: dict[str, Any]) -> str:
    return " ".join(
        str(overlay.get(key) or "")
        for key in ("text", "label", "id", "tag", "role")
    ).lower()


def _control_matches_overlay(item: dict[str, Any], overlay: dict[str, Any], overlay_kind: str) -> bool:
    blob = _overlay_blob(overlay)
    landmark = str(item.get("landmark") or "").lower()
    heading = str(item.get("nearest_heading") or "").lower()
    primary = _primary_label(item).lower()
    if overlay_kind == "cookie":
        hints = ("cookie", "consent", "privacy", "tracking")
        if any(hint in blob for hint in hints) and any(hint in landmark or hint in heading for hint in hints):
            return True
        return bool(
            _is_overlay_button(item)
            and (OVERLAY_REJECT_RE.search(primary) or OVERLAY_ACCEPT_RE.search(primary))
        )
    if overlay_kind == "age_gate":
        name = str(item.get("name") or "").lower()
        if name in {"year", "month", "day"} or re.search(r"\b(year|month|day|birth)\b", primary):
            return True
        return bool(_is_overlay_button(item) and GATE_CONFIRM_RE.search(primary))
    if landmark and landmark in blob:
        return True
    if heading and any(token in blob for token in heading.split() if len(token) > 3):
        return True
    return _is_overlay_button(item) and OVERLAY_BUTTON_RE.search(primary)


def _controls_for_overlay(
    interactables: list[Any],
    overlay: dict[str, Any],
    overlay_kind: str,
    *,
    all_overlays: list[Any],
) -> list[dict[str, Any]]:
    scoped: list[dict[str, Any]] = []
    multi = len(all_overlays) > 1
    for raw in interactables:
        if not isinstance(raw, dict) or raw.get("disabled") or not raw.get("id"):
            continue
        if _is_legal_info_link(raw):
            continue
        if multi:
            if _control_matches_overlay(raw, overlay, overlay_kind):
                scoped.append(raw)
            continue
        if overlay_kind == "cookie":
            if _is_cookie_overlay_control(raw):
                scoped.append(raw)
        elif overlay_kind == "age_gate":
            scoped.append(raw)
        elif _is_overlay_button(raw) and OVERLAY_BUTTON_RE.search(_primary_label(raw)):
            scoped.append(raw)
    return scoped


def _consent_action_score(item: dict[str, Any], *, reject: bool) -> int:
    primary = _primary_label(item).lower()
    if reject:
        if re.search(r"\breject all\b", primary):
            return 100
        if re.search(r"\bdecline all\b", primary):
            return 95
        if re.search(r"\breject\b", primary):
            return 80
        if re.search(r"\bdecline\b", primary):
            return 75
        if re.search(r"\bdeny\b", primary):
            return 70
        if re.search(r"\b(only necessary|essential)\b", primary):
            return 65
        return -1
    if re.search(r"\baccept all cookies\b", primary):
        return 90
    if re.search(r"\baccept all\b", primary):
        return 85
    if re.search(r"\baccept cookies\b", primary):
        return 80
    if OVERLAY_ACCEPT_RE.search(primary):
        return 70
    if re.search(r"\b(agree and continue|allow all|got it)\b", primary):
        return 60
    if OVERLAY_BUTTON_RE.search(primary):
        return 40
    return -1


def _is_cookie_overlay_control(item: dict[str, Any]) -> bool:
    if not _is_overlay_button(item):
        return False
    primary = _primary_label(item)
    # Video player chrome must not be treated as a cookie/consent button.
    if VIDEO_OVERLAY_RE.search(primary) and not COOKIE_OVERLAY_RE.search(primary):
        return False
    if (
        OVERLAY_REJECT_RE.search(primary)
        or OVERLAY_ACCEPT_RE.search(primary)
        or OVERLAY_DISMISS_RE.search(primary)
    ):
        return True
    if _consent_action_score(item, reject=True) >= 0 or _consent_action_score(item, reject=False) >= 0:
        return True
    return bool(_in_consent_region(item) and primary and OVERLAY_BUTTON_RE.search(primary))


def _action_signature(action: dict[str, Any]) -> str:
    from ui_test.state_diff import action_signature

    return action_signature(action)


def _blocked_action_signatures(
    recent_history: list[dict[str, Any]] | None,
    blocked_attempts: list[str] | None = None,
) -> set[str]:
    """Signatures of actions that already failed or made no progress on this page."""
    blocked = {str(sig).strip() for sig in (blocked_attempts or []) if str(sig).strip()}
    for row in recent_history or []:
        if not isinstance(row, dict):
            continue
        if row.get("ok") is True:
            continue
        if row.get("progress") is not False and row.get("ok") is not False:
            continue
        sig = _action_signature(
            {
                "action": row.get("action"),
                "target_id": row.get("target_id"),
                "url": row.get("url") or row.get("target_href"),
                "value_key": row.get("value_key"),
                "value": row.get("value"),
            }
        )
        if sig.strip("|"):
            blocked.add(sig)
    return blocked


def _action_is_blocked(action: dict[str, Any] | None, blocked: set[str]) -> bool:
    return bool(action and _action_signature(action) in blocked)


def _cookie_dismiss_failed(recent_history: list[dict[str, Any]] | None) -> bool:
    for row in recent_history or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "") != "click":
            continue
        if row.get("ok") is True or row.get("progress") is True:
            continue
        reason = str(row.get("reason") or "").lower()
        if "consent" in reason or "cookie" in reason or "overlay" in reason:
            return True
    return False


def _pick_consent_action(
    controls: list[dict[str, Any]],
    *,
    prefer_reject: bool = True,
    reason: str = "Reject consent/cookie overlay",
    blocked: set[str] | None = None,
) -> dict[str, Any] | None:
    blocked = blocked or set()
    buttons = [item for item in controls if _is_overlay_button(item)]
    if prefer_reject:
        ranked = sorted(
            ((item, _consent_action_score(item, reject=True)) for item in buttons),
            key=lambda pair: pair[1],
            reverse=True,
        )
        for item, score in ranked:
            if score >= 0:
                label = _primary_label(item) or item.get("id")
                action = {
                    "action": "click",
                    "target_id": str(item["id"]),
                    "reason": f"{reason} ({label})",
                }
                if not _action_is_blocked(action, blocked):
                    return action
    ranked = sorted(
        ((item, _consent_action_score(item, reject=False)) for item in buttons),
        key=lambda pair: pair[1],
        reverse=True,
    )
    for item, score in ranked:
        if score >= 0:
            label = _primary_label(item) or item.get("id")
            action = {
                "action": "click",
                "target_id": str(item["id"]),
                "reason": f"Dismiss consent/cookie overlay ({label})",
            }
            if not _action_is_blocked(action, blocked):
                return action
    return None


def _pick_age_gate_confirm(
    interactables: list[Any],
    *,
    blocked: set[str] | None = None,
) -> dict[str, Any] | None:
    blocked = blocked or set()
    for raw in interactables:
        if not isinstance(raw, dict) or not _is_overlay_button(raw):
            continue
        primary = _primary_label(raw)
        if GATE_CONFIRM_RE.search(primary):
            action = {
                "action": "click",
                "target_id": str(raw["id"]),
                "reason": "Confirm age verification after filling gate fields",
            }
            if not _action_is_blocked(action, blocked):
                return action
    return None


def _suggest_gate_field_action(
    snapshot: dict[str, Any],
    form_values: dict[str, str],
    field_mapping: dict[str, str],
    *,
    recent_history: list[dict[str, Any]] | None = None,
    blocked: set[str] | None = None,
) -> dict[str, Any] | None:
    blocked = blocked or set()
    interactables = snapshot.get("interactables") or []
    filled_ids = {
        str(row.get("target_id") or "")
        for row in (recent_history or [])
        if row.get("ok") and row.get("action") in {"fill", "select"} and row.get("target_id")
    }
    gate_fields = sorted(collect_gate_fields(snapshot), key=_gate_field_rank)
    for field in gate_fields:
        if not _field_is_unfilled(field, filled_ids=filled_ids, require_explicit_fill=True):
            continue
        semantic_key = field_mapping.get(field["id"])
        if not semantic_key:
            continue
        value = (form_values or {}).get(semantic_key)
        if not value:
            continue
        raw_field = next(
            (
                item
                for item in interactables
                if isinstance(item, dict) and str(item.get("id") or "") == field["id"]
            ),
            field,
        )
        value = normalize_gate_select_value(str(value), raw_field if isinstance(raw_field, dict) else field)
        action_name = "select" if str(field.get("kind") or "").lower() in {"select", "combobox"} else "fill"
        action = {
            "action": action_name,
            "target_id": field["id"],
            "value_key": semantic_key,
            "value": value,
            "reason": f"Fill {semantic_key} to clear age verification overlay",
        }
        if not _action_is_blocked(action, blocked):
            return action

    if gate_fields and looks_like_age_gate(snapshot):
        unfilled = [
            field
            for field in gate_fields
            if _field_is_unfilled(field, filled_ids=filled_ids, require_explicit_fill=True)
        ]
        if not unfilled:
            return _pick_age_gate_confirm(interactables, blocked=blocked)
    return None


def summarize_overlay_actions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Map each blocking overlay to the actions available on it (for model context)."""
    overlays = _snapshot_blockers(snapshot)
    if not overlays:
        return []
    interactables = snapshot.get("interactables") or []
    summaries: list[dict[str, Any]] = []
    for overlay_kind, overlay in _prioritized_overlays(overlays, snapshot):
        controls = _controls_for_overlay(interactables, overlay, overlay_kind, all_overlays=overlays)
        actions: list[dict[str, str]] = []
        for item in controls:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            primary = _primary_label(item) or str(item.get("id"))
            kind = str(item.get("kind") or item.get("role") or "").lower()
            widget = str(item.get("widget") or "").lower()
            if kind in {"select", "combobox", "input", "textbox", "textarea"} or widget in {"select", "combobox", "text", "number"}:
                field_action = "select" if kind in {"select", "combobox"} or widget in {"select", "combobox"} else "fill"
                row: dict[str, Any] = {"id": str(item["id"]), "label": primary[:80], "intent": field_action}
                name = str(item.get("name") or "").lower()
                if name:
                    row["name"] = name[:40]
                options = item.get("options")
                if isinstance(options, list) and options:
                    row["options"] = [str(opt)[:40] for opt in options[:8]]
                actions.append(row)
                continue
            if not _is_overlay_button(item):
                continue
            reject_score = _consent_action_score(item, reject=True)
            accept_score = _consent_action_score(item, reject=False)
            if reject_score >= 0:
                intent = "reject"
            elif accept_score >= 0:
                intent = "accept"
            elif GATE_CONFIRM_RE.search(primary):
                intent = "confirm"
            elif OVERLAY_BUTTON_RE.search(primary):
                intent = "dismiss"
            else:
                continue
            row = {"id": str(item["id"]), "label": primary[:80], "intent": intent}
            landmark = str(item.get("landmark") or "").strip()
            if landmark:
                row["landmark"] = landmark[:60]
            heading = str(item.get("nearest_heading") or "").strip()
            if heading:
                row["heading"] = heading[:60]
            rect = item.get("rect") if isinstance(item.get("rect"), dict) else {}
            if rect:
                row["rect"] = {
                    "x": round(float(rect.get("x") or 0), 1),
                    "y": round(float(rect.get("y") or 0), 1),
                    "w": round(float(rect.get("width") or 0), 1),
                    "h": round(float(rect.get("height") or 0), 1),
                }
            actions.append(row)
        if actions:
            summaries.append(
                {
                    "kind": overlay_kind,
                    "label": (_overlay_blob(overlay) or overlay_kind)[:120],
                    "actions": actions[:12],
                }
            )
    return summaries


def overlay_target_ids(snapshot: dict[str, Any]) -> set[str]:
    """Ids the overlay-dismiss model may click/fill/select."""
    ids: set[str] = set()
    for summary in summarize_overlay_actions(snapshot):
        for act in summary.get("actions") or []:
            if isinstance(act, dict) and act.get("id"):
                ids.add(str(act["id"]))
    return ids


def build_overlay_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Rich overlay interaction map for the dismiss model — ids, labels, intents, layout hints."""
    summaries = summarize_overlay_actions(snapshot)
    interactables = {
        str(item.get("id") or ""): item
        for item in (snapshot.get("interactables") or [])
        if isinstance(item, dict) and item.get("id")
    }
    elements: list[dict[str, Any]] = []
    menu: list[dict[str, Any]] = []
    number = 1
    for summary in summaries:
        overlay_kind = str(summary.get("kind") or "generic")
        for act in summary.get("actions") or []:
            if not isinstance(act, dict) or not act.get("id"):
                continue
            target_id = str(act["id"])
            intent = str(act.get("intent") or "click")
            action = intent if intent in {"fill", "select"} else "click"
            row: dict[str, Any] = {
                "id": target_id,
                "label": str(act.get("label") or target_id)[:80],
                "intent": intent,
                "action": action,
                "overlay_kind": overlay_kind,
            }
            for key in ("landmark", "heading", "name", "options", "rect"):
                if act.get(key) is not None:
                    row[key] = act[key]
            item = interactables.get(target_id)
            if item and "widget" not in row:
                widget = str(item.get("widget") or item.get("kind") or "").strip()
                if widget:
                    row["widget"] = widget[:40]
            elements.append(row)
            menu.append(
                {
                    "n": number,
                    "action": action,
                    "target_id": target_id,
                    "label": f"{overlay_kind}/{intent}: {row['label']}"[:100],
                }
            )
            number += 1
    return {
        "overlays": summaries,
        "elements": elements[:16],
        "menu": menu[:16],
    }


def _latest_adult_birth_year(*, min_age_years: int = MIN_ADULT_AGE_YEARS) -> int:
    from datetime import date

    return date.today().year - min_age_years


def _parse_year(value: str) -> int | None:
    match = re.match(r"^(\d{4})$", str(value or "").strip())
    return int(match.group(1)) if match else None


def _pick_adult_year(options: list[Any] | None, *, min_age_years: int = MIN_ADULT_AGE_YEARS) -> str:
    """Pick a birth year old enough to pass typical age gates (not too young to access)."""
    cutoff = _latest_adult_birth_year(min_age_years=min_age_years)
    preferred = int(DEFAULT_BIRTH_YEAR)
    parsed = [_parse_year(str(opt)) for opt in (options or [])]
    years = [year for year in parsed if year is not None]
    if not years:
        return DEFAULT_BIRTH_YEAR
    passing = [year for year in years if year <= cutoff]
    if not passing:
        return DEFAULT_BIRTH_YEAR
    if preferred in passing:
        return DEFAULT_BIRTH_YEAR
    return str(min(passing))


def report_is_negative(reason: str = "", note: str = "") -> bool:
    """True when the model's report reason indicates failure rather than an answer."""
    blob = f"{reason} {note}".strip()
    return bool(blob and NEGATIVE_REPORT_RE.search(blob))


def normalize_gate_select_value(value: str, field: dict[str, Any]) -> str:
    """Map semantic values like birth_month=7 to the option label the page expects (e.g. Jul)."""
    text = str(value or "").strip()
    if not text:
        return text
    name = str(field.get("name") or "").lower()
    label = f"{field.get('label') or ''} {field.get('text') or ''} {field.get('aria') or ''}".lower()
    options = [str(opt).strip() for opt in (field.get("options") or []) if str(opt).strip()]
    option_values = [str(opt).strip() for opt in (field.get("option_values") or []) if str(opt).strip()]
    candidates = [*options, *option_values]
    if text in candidates:
        return text
    if name == "month" or re.search(r"\bmonth\b", label):
        try:
            month_num = int(text)
            if 1 <= month_num <= 12:
                abbr = _MONTH_ABBR[month_num]
                for opt in candidates:
                    if opt.lower() == abbr.lower() or opt.lower().startswith(abbr.lower()):
                        return opt
        except ValueError:
            pass
        lowered = text.lower()
        for opt in candidates:
            if lowered in opt.lower() or opt.lower().startswith(lowered[:3]):
                return opt
    if name == "day" or re.search(r"\bday\b", label):
        try:
            day_num = int(text)
            for candidate in (str(day_num), f"{day_num:02d}"):
                if candidate in candidates:
                    return candidate
        except ValueError:
            pass
    return text


def _gate_field_rank(field: dict[str, Any]) -> int:
    label = f"{field.get('label') or ''} {field.get('name') or ''}".lower()
    for index, hint in enumerate(_GATE_FIELD_ORDER):
        if hint in label:
            return index
    return len(_GATE_FIELD_ORDER)


def _field_is_unfilled(
    field: dict[str, Any],
    *,
    filled_ids: set[str],
    require_explicit_fill: bool = False,
) -> bool:
    field_id = str(field.get("id") or "")
    if not field_id or field_id in filled_ids:
        return False
    if require_explicit_fill:
        return True
    return not str(field.get("value") or "").strip()


def _infer_cookie_overlay(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Detect cookie banners that are not classified as blocking overlays."""
    visible = str(snapshot.get("visible_text") or "")
    if not COOKIE_OVERLAY_RE.search(visible):
        for raw in snapshot.get("interactables") or []:
            if not isinstance(raw, dict):
                continue
            blob = " ".join(
                str(raw.get(key) or "")
                for key in ("aria", "landmark", "nearest_heading", "nearby_text", "text")
            )
            if COOKIE_OVERLAY_RE.search(blob):
                visible = blob
                break
        else:
            return None
    buttons = [
        raw
        for raw in snapshot.get("interactables") or []
        if isinstance(raw, dict)
        and _is_overlay_button(raw)
        and (
            OVERLAY_REJECT_RE.search(_primary_label(raw))
            or OVERLAY_ACCEPT_RE.search(_primary_label(raw))
        )
    ]
    if not buttons:
        return None
    return {
        "id": "inferred-cookie-banner",
        "tag": "div",
        "text": visible[:300],
        "label": "Cookie banner",
    }


def _cookie_buttons_from_interactables(interactables: list[Any]) -> list[dict[str, Any]]:
    return [
        raw
        for raw in interactables
        if isinstance(raw, dict) and _is_cookie_overlay_control(raw)
    ]


def stable_video_dismiss_label(label: str) -> str:
    """Strip ticking timestamps so Keep Watching locators stay matchable."""
    cleaned = _VIDEO_TIME_RE.sub(" ", str(label or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    match = _VIDEO_DISMISS_LABEL_RE.search(cleaned)
    if match:
        # Prefer the stable phrase over the full noisy chrome string.
        phrase = match.group(0)
        if phrase.lower() == "keep watching":
            return "Keep Watching"
        return phrase
    return cleaned


def is_video_chrome_label(label: str) -> bool:
    return bool(VIDEO_OVERLAY_RE.search(str(label or "")))


def video_dismiss_failures(recent_history: list[dict[str, Any]] | None) -> int:
    """How many recent failed attempts were video/promo dismiss clicks."""
    count = 0
    for row in recent_history or []:
        if not isinstance(row, dict):
            continue
        if row.get("ok") is True or row.get("progress") is True:
            continue
        reason = str(row.get("reason") or "").lower()
        target = str(row.get("target_id") or "").lower()
        if "video" in reason or "keep watching" in reason or "keep-watching" in target:
            count += 1
    return count


def strip_video_blockers(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop video/trailer blockers so browsing can continue after dismiss fails."""
    overlays = snapshot.get("blocking_overlays") or []
    kept = [
        raw
        for raw in overlays
        if not (
            isinstance(raw, dict)
            and classify_overlay(raw) == "video"
        )
    ]
    if len(kept) == len(overlays):
        return snapshot
    next_snap = dict(snapshot)
    next_snap["blocking_overlays"] = kept
    return next_snap


def snapshot_needs_overlay_action(snapshot: dict[str, Any]) -> bool:
    """True when deterministic overlay handling should run before the LLM decides.

    Embedded video chrome ("Keep Watching" / "Video Player" on the page) is NOT a
    consent overlay — only real blockers (cookie/age/dialog/pointer-block) count.
    """
    if _snapshot_blockers(snapshot):
        return True
    if looks_like_age_gate(snapshot):
        return True
    if _infer_cookie_overlay(snapshot) is not None:
        return True
    return False


def suggest_overlay_action(
    snapshot: dict[str, Any],
    form_values: dict[str, str],
    field_mapping: dict[str, str],
    *,
    recent_history: list[dict[str, Any]] | None = None,
    blocked_attempts: list[str] | None = None,
) -> dict[str, Any] | None:
    """Deterministic next step to clear a blocking overlay (cookie consent, age gate, etc.)."""
    blocked = _blocked_action_signatures(recent_history, blocked_attempts)
    overlays = list(_snapshot_blockers(snapshot))
    inferred_cookie = _infer_cookie_overlay(snapshot)
    if inferred_cookie and not any(classify_overlay(overlay) == "cookie" for overlay in overlays):
        overlays.append(inferred_cookie)
    interactables = snapshot.get("interactables") or []
    gate_fields = sorted(collect_gate_fields(snapshot), key=_gate_field_rank)
    has_gate_work = bool(
        gate_fields
        and any(field_mapping.get(str(field.get("id") or "")) for field in gate_fields)
        and any((form_values or {}).get(field_mapping[str(field["id"])]) for field in gate_fields if field_mapping.get(str(field.get("id") or "")))
    )

    if not overlays:
        if looks_like_age_gate(snapshot):
            overlays.append({"id": "inferred-age-gate", "text": "Age Verification"})
        else:
            cookie_buttons = _cookie_buttons_from_interactables(interactables)
            if cookie_buttons:
                action = _pick_consent_action(cookie_buttons, blocked=blocked)
                if action:
                    return action
            return _suggest_gate_field_action(
                snapshot,
                form_values,
                field_mapping,
                recent_history=recent_history,
                blocked=blocked,
            )

    # After repeated failed Keep Watching clicks, stop retrying — let browse continue.
    if video_dismiss_failures(recent_history) >= 2:
        non_video = [
            overlay
            for overlay in overlays
            if not (isinstance(overlay, dict) and classify_overlay(overlay) == "video")
        ]
        if len(non_video) < len(overlays):
            overlays = non_video
        if not overlays and not looks_like_age_gate(snapshot):
            return None

    if _cookie_dismiss_failed(recent_history) and has_gate_work:
        action = _suggest_gate_field_action(
            snapshot,
            form_values,
            field_mapping,
            recent_history=recent_history,
            blocked=blocked,
        )
        if action:
            return action

    for overlay_kind, overlay in _prioritized_overlays(overlays, snapshot):
        if overlay_kind != "cookie":
            continue
        controls = _controls_for_overlay(interactables, overlay, overlay_kind, all_overlays=overlays)
        action = _pick_consent_action(controls, blocked=blocked)
        if action:
            return action

    cookie_buttons = _cookie_buttons_from_interactables(interactables)
    if cookie_buttons:
        action = _pick_consent_action(cookie_buttons, blocked=blocked)
        if action:
            return action

    # Video / "Keep Watching" layers that intercept article clicks.
    # Prefer Skip/Close over the timestamped "Keep Watching" chrome itself.
    for overlay_kind, overlay in _prioritized_overlays(overlays, snapshot):
        if overlay_kind != "video":
            continue
        controls = _controls_for_overlay(interactables, overlay, "generic", all_overlays=overlays)
        skip_close = [
            item
            for item in controls
            if re.search(
                r"\b(skip(?:\s+ad)?|close(?:\s+video)?|not interested|dismiss)\b",
                _primary_label(item),
                re.I,
            )
        ]
        action = _pick_consent_action(
            skip_close or controls,
            prefer_reject=False,
            reason="Dismiss video/promo overlay",
            blocked=blocked,
        )
        if action:
            # Annotate with a stable label so Playwright does not match ticking timestamps.
            target = next(
                (
                    raw
                    for raw in interactables
                    if isinstance(raw, dict) and str(raw.get("id") or "") == action.get("target_id")
                ),
                None,
            )
            if isinstance(target, dict):
                stable = stable_video_dismiss_label(_primary_label(target))
                if stable:
                    action["stable_label"] = stable
                    action["force_click"] = True
            return action
        for raw in interactables:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            primary = _primary_label(raw)
            if not (
                OVERLAY_DISMISS_RE.search(primary)
                or _VIDEO_DISMISS_LABEL_RE.search(primary)
            ):
                continue
            # Never keep re-clicking the same ticking player chrome under a new id.
            if is_video_chrome_label(primary) and video_dismiss_failures(recent_history) >= 1:
                continue
            candidate = {
                "action": "click",
                "target_id": str(raw["id"]),
                "reason": f"Dismiss video/promo overlay ({stable_video_dismiss_label(primary) or primary})",
                "stable_label": stable_video_dismiss_label(primary),
                "force_click": True,
            }
            if not _action_is_blocked(candidate, blocked):
                return candidate

    action = _suggest_gate_field_action(
        snapshot,
        form_values,
        field_mapping,
        recent_history=recent_history,
        blocked=blocked,
    )
    if action:
        return action

    # Generic overlays — still prefer reject, then accept/dismiss.
    for overlay_kind, overlay in _prioritized_overlays(overlays, snapshot):
        if overlay_kind == "age_gate":
            continue
        controls = _controls_for_overlay(interactables, overlay, overlay_kind, all_overlays=overlays)
        action = _pick_consent_action(controls, reason="Dismiss blocking overlay", blocked=blocked)
        if action:
            return action

    return None


def is_gate_form_field(field: dict[str, Any]) -> bool:
    label = f"{field.get('label') or ''} {field.get('name') or ''} {field.get('placeholder') or ''}"
    if NON_GATE_FIELD_RE.search(label):
        return False
    name = str(field.get("name") or "").lower()
    if name in {"year", "month", "day", "country"}:
        return True
    return bool(GATE_FIELD_RE.search(label))


def collect_gate_fields(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        field
        for field in collect_form_fields(snapshot)
        if not field.get("disabled") and is_gate_form_field(field)
    ]


def looks_like_age_gate(snapshot: dict[str, Any]) -> bool:
    for blocker in _snapshot_blockers(snapshot):
        if not isinstance(blocker, dict):
            continue
        blob = f"{blocker.get('text') or ''} {blocker.get('label') or ''}"
        if AGE_GATE_RE.search(blob):
            return True
    return len(collect_gate_fields(snapshot)) >= 2


def _field_options_from_snapshot(snapshot: dict[str, Any] | None, semantic_key: str) -> list[Any]:
    if not snapshot:
        return []
    key_hints = {
        "birth_year": ("year", r"\byear\b"),
        "birth_month": ("month", r"\bmonth\b"),
        "birth_day": ("day", r"\bday\b"),
    }
    hint = key_hints.get(semantic_key)
    for raw in snapshot.get("interactables") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").lower()
        label = _field_description(raw).lower()
        if hint and (name == hint[0] or re.search(hint[1], label)):
            options = raw.get("options")
            if isinstance(options, list):
                return options
    return []


def enforce_adult_verification_values(
    form_values: dict[str, str],
    *,
    snapshot: dict[str, Any] | None = None,
    min_age_years: int = MIN_ADULT_AGE_YEARS,
) -> dict[str, str]:
    """Clamp birth-related values so age gates do not reject them as too young."""
    enforced = dict(form_values)
    cutoff = _latest_adult_birth_year(min_age_years=min_age_years)
    if "birth_year" in enforced:
        year = _parse_year(enforced["birth_year"])
        if year is None or year > cutoff:
            enforced["birth_year"] = _pick_adult_year(
                _field_options_from_snapshot(snapshot, "birth_year"),
                min_age_years=min_age_years,
            )
    if "birth_date" in enforced:
        match = re.match(r"^(\d{4})-", enforced["birth_date"])
        if match:
            year = int(match.group(1))
            if year > cutoff:
                enforced["birth_date"] = DEFAULT_BIRTH_DATE
        elif looks_like_age_gate(snapshot or {}):
            enforced["birth_date"] = DEFAULT_BIRTH_DATE
    for key, default in (("birth_month", "Jan"), ("birth_day", "1")):
        if key not in enforced:
            continue
        field_name = key.removeprefix("birth_")
        for raw in (snapshot or {}).get("interactables") or []:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("name") or "").lower() != field_name:
                continue
            enforced[key] = normalize_gate_select_value(enforced[key], raw)
            break
        else:
            enforced[key] = normalize_gate_select_value(enforced[key], {"name": field_name, "options": [default]})
    return enforced


def is_verification_field(target: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    if _snapshot_blockers(snapshot):
        return True
    return bool(VERIFICATION_FIELD_RE.search(_field_description(target).lower()))


def is_form_interactable(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    kind = str(item.get("kind") or item.get("role") or "").lower()
    action_hint = str(item.get("action_hint") or "").lower()
    if kind in FORM_KINDS or action_hint in {"fill", "select"}:
        return True
    tag = str(item.get("tag") or "").lower()
    return tag in {"input", "select", "textarea"}


def collect_form_fields(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for raw in snapshot.get("interactables") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        if not is_form_interactable(raw):
            continue
        fields.append(
            {
                "id": str(raw.get("id")),
                "kind": str(raw.get("kind") or raw.get("role") or ""),
                "widget": str(raw.get("widget") or raw.get("kind") or raw.get("role") or ""),
                "label": _field_description(raw),
                "name": str(raw.get("name") or ""),
                "placeholder": str(raw.get("placeholder") or ""),
                "action_hint": str(raw.get("action_hint") or ""),
                "options": raw.get("options") or [],
                "option_values": raw.get("option_values") or [],
                "value": str(raw.get("value") or raw.get("selected_label") or ""),
                "disabled": bool(raw.get("disabled")),
            }
        )
    return fields


def form_context_fingerprint(snapshot: dict[str, Any]) -> str:
    blockers = _snapshot_blockers(snapshot)
    fields = collect_form_fields(snapshot)
    if not blockers and not fields:
        return ""
    parts = [
        *(f"blocker:{item.get('id')}:{item.get('text')}" for item in blockers if isinstance(item, dict)),
        *(
            f"field:{item['id']}:{item['label']}:{item['placeholder']}"
            for item in fields
        ),
    ]
    return "|".join(parts)


def overlay_blocks_collect(snapshot: dict[str, Any], *, min_visible_chars: int = 400) -> tuple[bool, str]:
    """Whether extract/filter should wait for overlay dismissal."""
    blockers = _snapshot_blockers(snapshot)
    if not blockers:
        return False, ""
    kinds = {classify_overlay(item) for item in blockers if isinstance(item, dict)}
    visible_len = len(str(snapshot.get("visible_text") or "").strip())
    overlay_error = "clear blocking overlay first — dismiss consent or complete age verification"
    if looks_like_age_gate(snapshot) or "age_gate" in kinds or len(collect_gate_fields(snapshot)) >= 2:
        return True, overlay_error
    if kinds == {"cookie"} and visible_len >= min_visible_chars:
        return False, ""
    if visible_len >= min_visible_chars and kinds <= {"cookie", "generic"}:
        return False, ""
    return True, overlay_error


def needs_form_value_plan(snapshot: dict[str, Any], form_values: dict[str, str] | None) -> bool:
    blockers = _snapshot_blockers(snapshot)
    gate_fields = collect_gate_fields(snapshot)
    if not blockers and not gate_fields:
        return False
    blocker_kinds = {classify_overlay(item) for item in blockers if isinstance(item, dict)}
    if blocker_kinds == {"cookie"}:
        return False
    if looks_like_age_gate(snapshot) and (gate_fields or blockers):
        return True
    if "age_gate" in blocker_kinds and (gate_fields or blockers):
        return True
    return False


def sanitize_form_values(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in list(raw.items())[:MAX_FORM_KEYS]:
        name = re.sub(r"[^a-z0-9_]+", "_", str(key).strip().lower()).strip("_")
        if not name:
            continue
        text = str(value).strip()
        if not text:
            continue
        cleaned[name] = text[:MAX_VALUE_LEN]
    return cleaned


def _json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.I | re.S)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def ollama_form_values_provider(
    *,
    ollama_url: str,
    model: str,
    timeout_sec: float,
) -> FormValuesProvider:
    def plan(context: dict[str, Any]) -> dict[str, Any]:
        parsed = ollama_chat_json(
            prompt_key="web_research.plan_form_values",
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_get_prompt("web_research.plan_form_values"),
            user=json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            session_id=str(context.get("session_id") or ""),
            step_id=str(context.get("step_id") or ""),
        )
        if parsed is None:
            raise ValueError("Ollama returned no valid form-values JSON")
        return parsed

    return plan


def fallback_form_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Deterministic synthetic values when the planner model is unavailable."""
    from web_surf.form_values import collect_gate_fields, looks_like_age_gate

    values: dict[str, str] = {}
    mapping: dict[str, str] = {}
    fields = collect_gate_fields(snapshot) if looks_like_age_gate(snapshot) else collect_form_fields(snapshot)
    for field in fields:
        label = field["label"].lower()
        field_name = str(field.get("name") or "").lower()
        field_id = field["id"]
        if re.search(r"\b(country|region|nation)\b", label) or field_name == "country":
            key = "country"
            values.setdefault(key, "United States")
            mapping[field_id] = key
        elif re.search(r"\b(year)\b", label) or field_name == "year":
            key = "birth_year"
            values.setdefault(
                key,
                _pick_adult_year(field.get("options") if isinstance(field.get("options"), list) else None),
            )
            mapping[field_id] = key
        elif re.search(r"\b(month)\b", label) or field_name == "month":
            key = "birth_month"
            values.setdefault(key, "Jan")
            mapping[field_id] = key
        elif re.search(r"\b(day)\b", label) or field_name == "day":
            key = "birth_day"
            values.setdefault(key, "1")
            mapping[field_id] = key
        elif re.search(r"\b(birth|dob|age|date)\b", label):
            key = "birth_date"
            values.setdefault(key, DEFAULT_BIRTH_DATE)
            mapping[field_id] = key
        elif re.search(r"\b(email|e-mail)\b", label):
            key = "email"
            values.setdefault(key, "research.agent@example.com")
            mapping[field_id] = key
        elif re.search(r"\b(zip|postal)\b", label):
            key = "postal_code"
            values.setdefault(key, "10115")
            mapping[field_id] = key
        elif re.search(r"\b(phone|mobile|tel)\b", label):
            key = "phone"
            values.setdefault(key, "+1-555-0100")
            mapping[field_id] = key
        elif field.get("action_hint") == "select" or field.get("kind") == "combobox":
            options = field.get("options") or []
            if isinstance(options, list) and options:
                if re.search(r"\b(year)\b", label) or field_name == "year":
                    key = "birth_year"
                    values.setdefault(key, _pick_adult_year(options))
                else:
                    first = str(options[0])
                    key = re.sub(r"[^a-z0-9_]+", "_", label[:40].lower()).strip("_") or f"select_{field_id}"
                    values.setdefault(key, first)
                mapping[field_id] = key
    values = enforce_adult_verification_values(values, snapshot=snapshot)
    return {
        "form_values": values,
        "field_mapping": mapping,
        "reasoning": (
            "Generated fallback synthetic values from visible form labels. "
            "Birth years are set old enough to pass age gates (not too young to access)."
        ),
    }


def plan_form_values(
    *,
    query: str,
    snapshot: dict[str, Any],
    existing_values: dict[str, str] | None = None,
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5:14b",
    timeout_sec: float = 120.0,
    provider: FormValuesProvider | None = None,
) -> dict[str, Any]:
    fields = collect_form_fields(snapshot)
    blockers = _snapshot_blockers(snapshot)
    if not fields and not blockers:
        return {"form_values": {}, "field_mapping": {}, "reasoning": "No form context."}

    context = curate_form_plan_context(
        query=query,
        snapshot=snapshot,
        existing_keys=sorted((existing_values or {}).keys()),
    )
    plan = provider(context) if provider else ollama_form_values_provider(
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
    )(context)
    form_values = sanitize_form_values(plan.get("form_values"))
    form_values = enforce_adult_verification_values(form_values, snapshot=snapshot)
    field_mapping_raw = plan.get("field_mapping")
    field_mapping: dict[str, str] = {}
    if isinstance(field_mapping_raw, dict):
        for field_id, key in field_mapping_raw.items():
            semantic_key = re.sub(r"[^a-z0-9_]+", "_", str(key).strip().lower()).strip("_")
            if semantic_key and str(field_id).strip():
                field_mapping[str(field_id)] = semantic_key
    merged_values = dict(existing_values or {})
    merged_values.update(form_values)
    return {
        "form_values": form_values,
        "merged_values": merged_values,
        "field_mapping": field_mapping,
        "reasoning": str(plan.get("reasoning") or "")[:1000],
    }


def ensure_form_values(
    *,
    query: str,
    snapshot: dict[str, Any],
    form_values: dict[str, str],
    planned_fingerprints: set[str],
    ollama_url: str,
    model: str,
    timeout_sec: float,
    provider: FormValuesProvider | None = None,
) -> dict[str, Any] | None:
    fingerprint = form_context_fingerprint(snapshot)
    if not fingerprint or fingerprint in planned_fingerprints:
        return None
    if not needs_form_value_plan(snapshot, form_values):
        planned_fingerprints.add(fingerprint)
        return None
    try:
        result = plan_form_values(
            query=query,
            snapshot=snapshot,
            existing_values=form_values,
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            provider=provider,
        )
    except Exception as exc:
        logger.warning("Form value planning failed: %s", exc)
        return None
    planned_fingerprints.add(fingerprint)
    return result

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
FORM_KINDS = {"textbox", "combobox", "input", "select", "textarea", "spinbutton"}
MAX_FORM_KEYS = 24
MAX_VALUE_LEN = 2000


def _field_description(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("text", "aria", "label", "placeholder", "name", "nearby_text")
    ).strip()


def is_verification_field(target: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    if snapshot.get("blocking_overlays"):
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
                "label": _field_description(raw),
                "placeholder": str(raw.get("placeholder") or ""),
                "action_hint": str(raw.get("action_hint") or ""),
                "options": raw.get("options") or [],
                "value": str(raw.get("value") or ""),
                "disabled": bool(raw.get("disabled")),
            }
        )
    return fields


def form_context_fingerprint(snapshot: dict[str, Any]) -> str:
    blockers = snapshot.get("blocking_overlays") or []
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


def needs_form_value_plan(snapshot: dict[str, Any], form_values: dict[str, str] | None) -> bool:
    blockers = snapshot.get("blocking_overlays") or []
    fields = [field for field in collect_form_fields(snapshot) if not field.get("disabled")]
    if not blockers and not fields:
        return False
    if blockers and fields:
        return True
    if blockers:
        return True
    return any(is_verification_field({"label": field["label"]}, snapshot) for field in fields) and not form_values


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
    values: dict[str, str] = {}
    mapping: dict[str, str] = {}
    for field in collect_form_fields(snapshot):
        label = field["label"].lower()
        field_id = field["id"]
        if re.search(r"\b(country|region|nation)\b", label):
            key = "country"
            values.setdefault(key, "United States")
            mapping[field_id] = key
        elif re.search(r"\b(birth|dob|age|date)\b", label):
            key = "birth_date"
            values.setdefault(key, "1990-06-15")
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
                first = str(options[0])
                key = re.sub(r"[^a-z0-9_]+", "_", label[:40].lower()).strip("_") or f"select_{field_id}"
                values.setdefault(key, first)
                mapping[field_id] = key
    return {
        "form_values": values,
        "field_mapping": mapping,
        "reasoning": "Generated fallback synthetic values from visible form labels.",
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
    blockers = snapshot.get("blocking_overlays") or []
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
        logger.warning("Form value planning failed, using fallback: %s", exc)
        result = fallback_form_values(snapshot)
        result["merged_values"] = {**form_values, **sanitize_form_values(result.get("form_values"))}
        result["form_values"] = sanitize_form_values(result.get("form_values"))
    planned_fingerprints.add(fingerprint)
    return result

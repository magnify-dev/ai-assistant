from __future__ import annotations

import re
from typing import Any

_COLLAPSE_TOGGLE_RE = re.compile(
    r"data-toggle=[\"']collapse[\"']|data-bs-toggle=[\"']collapse[\"']|"
    r"panel-heading|panel-title",
    re.I,
)
_PATCH_SECTION_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?\s+build\b", re.I)
_VERSION_FRAGMENT_RE = re.compile(r"^\d+(?:\.\d+)+$")


def is_collapse_toggle(item: dict[str, Any]) -> bool:
    """True for Bootstrap-style accordion/collapse section headers."""
    if not isinstance(item, dict):
        return False
    if item.get("expands_section"):
        return True
    if str(item.get("data_toggle") or "").lower() == "collapse":
        return True
    if str(item.get("data_bs_toggle") or "").lower() == "collapse":
        return True
    href = str(item.get("href") or "")
    kind = str(item.get("kind") or "").lower()
    label = " ".join(
        str(item.get(key) or "")
        for key in ("text", "aria", "label", "nearest_heading", "nearby_text")
    )
    if kind in {"blz-button", "summary"}:
        if item.get("expands_section") or str(item.get("expanded") or "").lower() == "false":
            return True
        if _PATCH_SECTION_RE.search(label):
            return True
    if href.startswith("#") and kind in {"link", "button", "blz-button"}:
        landmark = " ".join(
            str(item.get(key) or "")
            for key in ("nearest_heading", "landmark", "nearby_text", "text")
        ).lower()
        if "panel" in landmark or _COLLAPSE_TOGGLE_RE.search(landmark):
            return True
    if "#" in href and kind in {"link", "button", "blz-button"}:
        fragment = href.split("#", 1)[1]
        if _VERSION_FRAGMENT_RE.match(fragment) and (
            _PATCH_SECTION_RE.search(label) or re.search(r"build\s*#", label, re.I)
        ):
            return True
    return False


def is_collapsed_section(item: dict[str, Any]) -> bool:
    if item.get("collapsed") is True:
        return True
    if item.get("collapsed") is False:
        return False
    expanded = str(item.get("expanded") or "").lower()
    if expanded == "false":
        return True
    if expanded == "true":
        return False
    return is_collapse_toggle(item)


def wait_for_section_expand(page: Any, item: dict[str, Any], *, timeout_ms: int = 5000) -> bool:
    """Wait until a collapse/accordion section reveals more body text."""
    href = str(item.get("href") or item.get("toggle_target") or "")
    fragment = href.split("#", 1)[1] if "#" in href else ""
    script = """({ fragment, startLen }) => {
      const root = document.querySelector("main,[role='main'],article") || document.body;
      const textLen = (root.innerText || "").length;
      if (textLen > startLen + 180) return { expanded: true, textLen };
      if (fragment) {
        const panel = document.querySelector(fragment)
          || document.getElementById(fragment)
          || document.querySelector(`[id="${fragment}"]`);
        if (panel) {
          const style = getComputedStyle(panel);
          const visible = style.display !== "none"
            && style.visibility !== "hidden"
            && panel.offsetHeight > 12;
          if (visible && (panel.innerText || "").trim().length > 80) {
            return { expanded: true, textLen, panelLen: (panel.innerText || "").length };
          }
        }
      }
      const open = document.querySelector(".panel-collapse.in, .panel-collapse.show, .collapse.in, .collapse.show");
      if (open && (open.innerText || "").trim().length > 80) {
        return { expanded: true, textLen, panelLen: (open.innerText || "").length };
      }
      return { expanded: false, textLen };
    }"""
    try:
        start_len = page.evaluate(
            "() => (document.querySelector('main,[role=main],article')||document.body).innerText.length"
        )
    except Exception:
        start_len = 0
    waited = 0
    step = 250
    while waited < timeout_ms:
        try:
            page.wait_for_timeout(step)
        except Exception:
            break
        waited += step
        try:
            result = page.evaluate(script, {"fragment": fragment, "startLen": start_len})
        except Exception:
            continue
        if isinstance(result, dict) and result.get("expanded"):
            return True
    return False


def section_text_growth(before: dict[str, Any], after: dict[str, Any], *, min_growth: int = 180) -> bool:
    before_len = len(str(before.get("visible_text") or ""))
    after_len = len(str(after.get("visible_text") or ""))
    return after_len >= before_len + min_growth

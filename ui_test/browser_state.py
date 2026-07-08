from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

_INTERACTABLE_JS = """() => {
  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0") return false;
    return true;
  }
  function textOf(el) {
    return (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 120);
  }
  const selector =
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [tabindex]:not([tabindex='-1'])";
  const raw = Array.from(document.querySelectorAll(selector)).filter(isVisible);
  const seen = new Set();
  const out = [];
  for (const el of raw) {
    const testId = el.getAttribute("data-testid") || "";
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || "";
    let kind = tag;
    if (tag === "a") kind = "link";
    else if (tag === "button") kind = "button";
    else if (tag === "input") kind = "input";
    const text = textOf(el);
    const aria = (el.getAttribute("aria-label") || "").trim();
    const href = el.href || "";
    const disabled = Boolean(el.disabled || el.getAttribute("aria-disabled") === "true");
    const key = `${kind}|${testId}|${text}|${aria}|${href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (!text && !aria && !testId && !href && kind !== "input" && kind !== "select" && kind !== "textarea") continue;
    out.push({
      index: out.length,
      kind,
      test_id: testId || null,
      role: role || null,
      text: text || null,
      aria: aria || null,
      href: href || null,
      input_type: el.type || null,
      disabled,
      name: el.name || null,
      placeholder: el.placeholder || null,
    });
    if (out.length >= 100) break;
  }
  return out;
}"""


def collect_page_state(page: Page) -> dict[str, Any]:
    """Snapshot current URL, title, and visible interactable elements."""
    try:
        interactables = page.evaluate(_INTERACTABLE_JS)
    except Exception:
        interactables = []
    try:
        title = page.title()
    except Exception:
        title = ""
    return {
        "url": page.url,
        "title": title,
        "interactables": interactables if isinstance(interactables, list) else [],
    }


def emit_page_state(page: Page, *, context: str = "", node_url: str = "") -> dict[str, Any]:
    """Collect and emit browser_state event for the test-runner UI."""
    state = collect_page_state(page)
    try:
        from ui_test.events import browser_state_event

        browser_state_event(
            url=state["url"],
            title=state["title"],
            interactables=state["interactables"],
            context=context,
            node_url=node_url,
        )
    except ImportError:
        pass
    return state

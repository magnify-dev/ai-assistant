from __future__ import annotations

import base64
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


def capture_screenshot_b64(page: Page) -> str | None:
    """JPEG preview cropped to page content — avoids black viewport margins in headless Chrome."""
    try:
        page.evaluate(
            """() => {
          for (const el of [document.documentElement, document.body]) {
            el.style.setProperty("background", "#ffffff", "important");
          }
        }"""
        )
        bounds = page.evaluate(
            """() => {
          const candidates = [
            document.querySelector("main"),
            document.querySelector("[role='main']"),
            document.querySelector("#root > *"),
            document.querySelector("#root"),
            document.body,
          ].filter(Boolean);
          let best = { x: 0, y: 0, width: 0, height: 0, area: 0 };
          for (const el of candidates) {
            const r = el.getBoundingClientRect();
            const area = r.width * r.height;
            if (area > best.area) best = { x: r.x, y: r.y, width: r.width, height: r.height, area };
          }
          const width = Math.ceil(Math.min(Math.max(best.width, 320), window.innerWidth));
          const height = Math.ceil(Math.min(Math.max(best.height, 240), window.innerHeight));
          return {
            x: Math.max(0, Math.floor(best.x)),
            y: Math.max(0, Math.floor(best.y)),
            width,
            height,
          };
        }"""
        )
        if isinstance(bounds, dict) and bounds.get("width", 0) > 0 and bounds.get("height", 0) > 0:
            raw = page.screenshot(
                type="jpeg",
                quality=55,
                clip={
                    "x": float(bounds["x"]),
                    "y": float(bounds["y"]),
                    "width": float(bounds["width"]),
                    "height": float(bounds["height"]),
                },
                timeout=5000,
            )
        else:
            raw = page.screenshot(type="jpeg", quality=55, full_page=False, timeout=5000)
        if not raw:
            return None
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def collect_page_state(page: Page, *, include_screenshot: bool = True) -> dict[str, Any]:
    """Snapshot current URL, title, visible interactables, and optional screenshot."""
    try:
        interactables = page.evaluate(_INTERACTABLE_JS)
    except Exception:
        interactables = []
    try:
        title = page.title()
    except Exception:
        title = ""
    state: dict[str, Any] = {
        "url": page.url,
        "title": title,
        "interactables": interactables if isinstance(interactables, list) else [],
    }
    if include_screenshot:
        shot = capture_screenshot_b64(page)
        if shot:
            state["screenshot_b64"] = shot
    return state


def emit_page_state(
    page: Page,
    *,
    context: str = "",
    node_url: str = "",
    error: str = "",
    include_screenshot: bool = True,
) -> dict[str, Any]:
    """Collect and emit browser_state event for the test-runner UI."""
    state = collect_page_state(page, include_screenshot=include_screenshot)
    try:
        from ui_test.events import browser_state_event

        browser_state_event(
            url=state["url"],
            title=state["title"],
            interactables=state["interactables"],
            context=context,
            node_url=node_url,
            screenshot_b64=state.get("screenshot_b64"),
            error=error or None,
        )
    except ImportError:
        pass
    try:
        from ui_test.playwright_session import notify_page_state

        notify_page_state(page, context=context)
    except ImportError:
        pass
    return state

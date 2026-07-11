from __future__ import annotations

import base64
import hashlib
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

if TYPE_CHECKING:
    from playwright.sync_api import Page
else:
    Page = Any

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
  function clean(value, limit = 160) {
    return String(value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  }
  function associatedLabel(el) {
    if (el.labels && el.labels.length) return clean(Array.from(el.labels).map(textOf).join(" "));
    const id = el.getAttribute("id");
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label) return textOf(label);
    }
    const parent = el.closest("label");
    return parent ? textOf(parent) : "";
  }
  function nearestHeading(el) {
    const section = el.closest("section,article,main,nav,aside,form,[role='region'],[role='dialog']");
    const heading = section && section.querySelector("h1,h2,h3,h4,[role='heading']");
    return heading ? textOf(heading) : "";
  }
  function landmarkOf(el) {
    const landmark = el.closest("nav,main,aside,header,footer,form,[role]");
    if (!landmark) return "";
    return clean(landmark.getAttribute("aria-label") || landmark.getAttribute("role") || landmark.tagName.toLowerCase(), 80);
  }
  function nearbyText(el) {
    const parent = el.closest("li,td,th,p,div,section,form") || el.parentElement;
    return parent ? clean(parent.innerText || parent.textContent, 220) : "";
  }
  const selector =
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [tabindex]:not([tabindex='-1'])";
  const roots = [document];
  const seenRoots = new Set(roots);
  const raw = [];
  for (let rootIndex = 0; rootIndex < roots.length; rootIndex++) {
    const root = roots[rootIndex];
    raw.push(...Array.from(root.querySelectorAll(selector)).filter(isVisible));
    for (const host of root.querySelectorAll("*")) {
      if (host.shadowRoot && !seenRoots.has(host.shadowRoot)) {
        seenRoots.add(host.shadowRoot);
        roots.push(host.shadowRoot);
      }
    }
  }
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
      value: (tag === "input" || tag === "textarea" || tag === "select") ? String(el.value || "").slice(0, 120) : null,
      label: associatedLabel(el) || null,
      title: clean(el.getAttribute("title"), 120) || null,
      nearest_heading: nearestHeading(el) || null,
      landmark: landmarkOf(el) || null,
      nearby_text: nearbyText(el) || null,
      expanded: el.getAttribute("aria-expanded"),
      selected: Boolean(el.selected || el.getAttribute("aria-selected") === "true"),
      checked: Boolean(el.checked || el.getAttribute("aria-checked") === "true"),
    });
  }
  return { items: out.slice(0, 400), total: out.length };
}"""

_BLOCKING_OVERLAYS_JS = """() => {
  function visible(el) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }
  function clean(value, limit = 300) {
    return String(value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  }
  const roots = [document];
  const seenRoots = new Set(roots);
  const overlays = [];
  for (let rootIndex = 0; rootIndex < roots.length; rootIndex++) {
    const root = roots[rootIndex];
    for (const el of root.querySelectorAll("[role='dialog'], [aria-modal='true'], dialog[open], blz-age-gate, [class*='modal'], [class*='overlay']")) {
      if (!visible(el)) continue;
      const text = clean(el.innerText || el.textContent);
      const label = clean(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby"));
      const tag = el.tagName.toLowerCase();
      if (!text && !label && !tag.includes("age")) continue;
      overlays.push({
        id: el.id || `${tag}-${overlays.length + 1}`,
        tag,
        role: el.getAttribute("role") || null,
        label: label || null,
        text: text || null,
        shadow_host: root instanceof ShadowRoot ? root.host.tagName.toLowerCase() : null,
      });
    }
    for (const host of root.querySelectorAll("*")) {
      if (host.shadowRoot && !seenRoots.has(host.shadowRoot)) {
        seenRoots.add(host.shadowRoot);
        roots.push(host.shadowRoot);
      }
    }
  }
  return overlays.slice(0, 20);
}"""

_SEMANTIC_JS = """() => {
  function visible(el) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  }
  function clean(value, limit) {
    return String(value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  }
  const headings = Array.from(document.querySelectorAll("h1,h2,h3,[role='heading']"))
    .filter(visible).map(el => clean(el.innerText || el.textContent, 180)).filter(Boolean).slice(0, 30);
  const landmarks = Array.from(document.querySelectorAll("main,nav,aside,header,footer,[role='main'],[role='navigation'],[role='region']"))
    .filter(visible).map(el => ({
      kind: el.getAttribute("role") || el.tagName.toLowerCase(),
      label: clean(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby"), 120),
    })).slice(0, 20);
  const root = document.querySelector("main,[role='main'],article") || document.body;
  return {
    headings,
    landmarks,
    visible_text: clean(root ? (root.innerText || root.textContent) : "", 12000),
  };
}"""


def _id_slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:48]


def _interactable_action_hint(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "").lower()
    label = str(item.get("text") or item.get("aria") or item.get("label") or "").strip()
    href = str(item.get("href") or "").strip()
    if kind == "link" and href:
        return f"Follow this link to {href}."
    if kind == "button":
        return f'Click this button to "{label}".' if label else "Click this button."
    if kind in {"input", "textarea"}:
        field = label or str(item.get("placeholder") or "this field").strip()
        return f"Enter text in {field}."
    if kind == "select":
        return f"Choose an option in {label or 'this menu'}."
    if kind == "summary":
        return f"Expand or collapse {label or 'this section'}."
    return f"Interact with {label or 'this control'}."


def _stable_interactable_id(item: dict[str, Any], occurrence: int) -> str:
    kind = _id_slug(item.get("kind") or item.get("role") or "element")
    label = _id_slug(item.get("test_id") or item.get("text") or item.get("aria") or item.get("name"))
    if label:
        suffix = f"-{occurrence + 1}" if occurrence else ""
        return f"el-{kind}-{label}{suffix}"
    signature = "|".join(
        str(item.get(key) or "").strip().lower()
        for key in ("kind", "role", "test_id", "text", "aria", "href", "name", "placeholder")
    )
    digest = hashlib.sha256(f"{signature}|{occurrence}".encode("utf-8")).hexdigest()[:12]
    return f"el_{digest}"


def _enrich_interactables(items: Any, page_url: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    occurrences: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        href = str(item.get("href") or "").strip()
        if href:
            item["href"] = urljoin(page_url, href)
        signature = "|".join(
            str(item.get(key) or "").strip().lower()
            for key in ("kind", "role", "test_id", "text", "aria", "href", "name", "placeholder")
        )
        occurrence = occurrences.get(signature, 0)
        occurrences[signature] = occurrence + 1
        item["id"] = _stable_interactable_id(item, occurrence)
        item["action_hint"] = _interactable_action_hint(item)
        result.append(item)
    return result


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
    """Create a compact semantic snapshot with stable IDs and page context."""
    try:
        raw_interactables = page.evaluate(_INTERACTABLE_JS)
    except Exception:
        raw_interactables = []
    try:
        semantic = page.evaluate(_SEMANTIC_JS)
    except Exception:
        semantic = {}
    try:
        blocking_overlays = page.evaluate(_BLOCKING_OVERLAYS_JS)
    except Exception:
        blocking_overlays = []
    try:
        title = page.title()
    except Exception:
        title = ""
    page_url = str(page.url)
    raw_items = raw_interactables.get("items") if isinstance(raw_interactables, dict) else raw_interactables
    interactables = _enrich_interactables(raw_items, page_url)
    interactable_total = (
        int(raw_interactables.get("total") or len(interactables))
        if isinstance(raw_interactables, dict)
        else len(interactables)
    )
    semantic = semantic if isinstance(semantic, dict) else {}
    routes = sorted(
        {
            str(item["href"])
            for item in interactables
            if item.get("href") and re.match(r"^https?://", str(item["href"]), re.I)
        }
    )
    state: dict[str, Any] = {
        "url": page_url,
        "title": title,
        "interactables": interactables,
        "interactables_total": interactable_total,
        "interactables_truncated": interactable_total > len(interactables),
        "headings": semantic.get("headings") if isinstance(semantic.get("headings"), list) else [],
        "landmarks": semantic.get("landmarks") if isinstance(semantic.get("landmarks"), list) else [],
        "visible_text": str(semantic.get("visible_text") or ""),
        "discovered_routes": routes,
        "blocking_overlays": blocking_overlays if isinstance(blocking_overlays, list) else [],
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

        notify_page_state(page, context=context, snapshot=state)
    except ImportError:
        pass
    return state

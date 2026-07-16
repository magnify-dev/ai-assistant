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
    const tag = el.tagName.toLowerCase();
    if (el.closest("[role='dialog'], [aria-modal='true'], dialog[open]")) {
      if (tag === "select" || tag === "input" || tag === "textarea") return true;
    }
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
  function collapseMeta(el) {
    const toggle = el.matches("[data-toggle='collapse'], [data-bs-toggle='collapse']")
      ? el
      : el.closest("[data-toggle='collapse'], [data-bs-toggle='collapse']");
    if (!toggle) return null;
    const targetSel = toggle.getAttribute("data-target")
      || toggle.getAttribute("data-bs-target")
      || toggle.getAttribute("href")
      || "";
    let panel = null;
    if (targetSel && targetSel.startsWith("#")) {
      panel = document.querySelector(targetSel);
    }
    if (!panel) {
      panel = toggle.closest(".panel, [class*='accordion']")
        ?.querySelector(".panel-collapse, .collapse");
    }
    let collapsed = true;
    if (panel) {
      const style = getComputedStyle(panel);
      collapsed = !panel.classList.contains("in")
        && !panel.classList.contains("show")
        && (style.display === "none" || panel.offsetHeight < 8);
    }
    return {
      expands_section: true,
      collapsed,
      toggle_target: targetSel || null,
      data_toggle: toggle.getAttribute("data-toggle") || toggle.getAttribute("data-bs-toggle") || "collapse",
    };
  }
  function nearbyText(el) {
    const parent = el.closest("li,td,th,p,div,section,form") || el.parentElement;
    return parent ? clean(parent.innerText || parent.textContent, 220) : "";
  }
  function cssPath(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      const testId = node.getAttribute("data-testid");
      if (testId) {
        parts.unshift(`[data-testid="${CSS.escape(testId)}"]`);
        break;
      }
      if (node.parentElement) {
        const siblings = Array.from(node.parentElement.children).filter(
          (candidate) => candidate.tagName === node.tagName
        );
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }
  const selector =
    "a[href], button, blz-button, blz-select, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='textbox'], [role='combobox'], [role='spinbutton'], [role='searchbox'], [contenteditable=''], [contenteditable='true'], [tabindex]:not([tabindex='-1']), [data-toggle='collapse'], [data-bs-toggle='collapse'], [onclick]";
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
    else if (tag === "button" || tag === "blz-button") kind = tag === "blz-button" ? "blz-button" : "button";
    else if (tag === "input") kind = "input";
    else if (role === "textbox" || role === "searchbox") kind = "textbox";
    else if (role === "combobox") kind = "combobox";
    else if (role === "spinbutton") kind = "spinbutton";
    const aria = (el.getAttribute("aria-label") || "").trim();
    const fieldName = (el.getAttribute("name") || "").trim();
    let text = textOf(el);
    if (tag === "select") {
      text = aria || fieldName || associatedLabel(el) || text.slice(0, 40);
    } else if (kind === "textbox" || kind === "combobox" || kind === "spinbutton") {
      text = aria || fieldName || associatedLabel(el) || el.getAttribute("placeholder") || text.slice(0, 40);
    }
    const href = el.href || "";
    const disabled = Boolean(el.disabled || el.getAttribute("aria-disabled") === "true");
    const bounds = el.getBoundingClientRect();
    const key = `${kind}|${testId}|${text}|${aria}|${href}|${fieldName}|${Math.round(bounds.x)}|${Math.round(bounds.y)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (!text && !aria && !testId && !href && !fieldName && kind !== "input" && kind !== "select" && kind !== "textarea" && kind !== "textbox" && kind !== "combobox" && kind !== "spinbutton") continue;
    const collapse = collapseMeta(el);
    const row = {
      index: out.length,
      kind,
      test_id: testId || null,
      role: role || null,
      text: text || null,
      aria: aria || null,
      href: href || null,
      input_type: el.type || null,
      disabled,
      name: fieldName || el.name || null,
      placeholder: el.placeholder || null,
      value: (tag === "input" || tag === "textarea" || tag === "select" || kind === "textbox" || kind === "combobox" || kind === "spinbutton" || el.isContentEditable) ? String(el.value || "").slice(0, 120) : null,
      readonly: Boolean(el.readOnly || el.getAttribute("aria-readonly") === "true"),
      label: associatedLabel(el) || null,
      title: clean(el.getAttribute("title"), 120) || null,
      nearest_heading: nearestHeading(el) || null,
      landmark: landmarkOf(el) || null,
      nearby_text: nearbyText(el) || null,
      expanded: el.getAttribute("aria-expanded"),
      selected: Boolean(el.selected || el.getAttribute("aria-selected") === "true"),
      checked: Boolean(el.checked || el.getAttribute("aria-checked") === "true"),
      tag,
      rect: {
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
      },
      css_path: cssPath(el),
      z_index: getComputedStyle(el).zIndex || null,
      pointer_events: getComputedStyle(el).pointerEvents || null,
      cursor: getComputedStyle(el).cursor || null,
      frame_url: location.href,
      shadow_host: el.getRootNode() instanceof ShadowRoot
        ? el.getRootNode().host.tagName.toLowerCase()
        : null,
    };
    if (collapse) Object.assign(row, collapse);
    out.push(row);
    if (tag === "select") {
      const last = out[out.length - 1];
      const optEntries = Array.from(el.options || []).slice(0, 40);
      const opts = optEntries
        .map((o) => String(o.label || o.text || o.value || "").trim())
        .filter(Boolean);
      const optValues = optEntries
        .map((o) => String(o.value || "").trim())
        .filter(Boolean);
      if (opts.length) last.options = opts;
      if (optValues.length) last.option_values = optValues;
      const selected = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
      if (selected) {
        last.selected_label = String(selected.label || selected.text || selected.value || "").trim() || null;
      }
    }
  }
  return {
    items: out.slice(0, 400),
    total: out.length,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_x: window.scrollX,
      scroll_y: window.scrollY,
      document_width: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
      document_height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
    },
  };
}"""

VISUAL_TILE_JS = """() => {
  const cols = 48;
  const rows = 32;
  const width = window.innerWidth;
  const height = window.innerHeight;
  if (width <= 0 || height <= 0) return null;
  const cellW = width / cols;
  const cellH = height / rows;
  function cleanColor(value) {
    const raw = String(value || "").trim();
    if (!raw || raw === "transparent" || raw === "rgba(0, 0, 0, 0)") return "";
    return raw.slice(0, 32);
  }
  function backgroundColor(el) {
    let node = el;
    while (node && node !== document.documentElement) {
      const style = getComputedStyle(node);
      const bg = cleanColor(style.backgroundColor);
      if (bg) return bg;
      node = node.parentElement;
    }
    return cleanColor(getComputedStyle(el).color) || "#e5e7eb";
  }
  function kindOf(el) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || "";
    if (tag === "button" || role === "button") return "button";
    if (tag === "a" || role === "link") return "link";
    if (tag === "input" || tag === "textarea" || tag === "select") return "input";
    if (tag === "img" || tag === "video" || tag === "svg") return "media";
    if (["h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "label"].includes(tag)) return "text";
    if (["nav", "header", "footer", "aside", "main"].includes(tag)) return tag;
    return "chrome";
  }
  const cells = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const x = Math.min(width - 1, Math.floor(col * cellW + cellW / 2));
      const y = Math.min(height - 1, Math.floor(row * cellH + cellH / 2));
      const el = document.elementFromPoint(x, y);
      if (!el) {
        cells.push({ color: "#ffffff", kind: "empty" });
        continue;
      }
      cells.push({ color: backgroundColor(el), kind: kindOf(el) });
    }
  }
  return {
    cols,
    rows,
    cells,
    viewport: {
      width,
      height,
      scroll_x: window.scrollX,
      scroll_y: window.scrollY,
      document_width: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
      document_height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
    },
  };
}"""

_DIALOG_FORM_CONTROLS_JS = """() => {
  function clean(value, limit = 160) {
    return String(value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  }
  function textOf(el) {
    return clean(el.innerText || el.textContent || "", 120);
  }
  function associatedLabel(el) {
    if (el.labels && el.labels.length) {
      return clean(Array.from(el.labels).map((node) => textOf(node)).join(" "));
    }
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
    const landmark = el.closest("nav,main,aside,header,footer,form,[role='dialog'],[role]");
    if (!landmark) return "";
    return clean(landmark.getAttribute("aria-label") || landmark.getAttribute("role") || landmark.tagName.toLowerCase(), 80);
  }
  function nearbyText(el) {
    const parent = el.closest("li,td,th,p,div,section,form,label") || el.parentElement;
    return parent ? clean(parent.innerText || parent.textContent, 220) : "";
  }
  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0") return false;
    return true;
  }
  function cssPath(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      const testId = node.getAttribute("data-testid");
      if (testId) {
        parts.unshift(`[data-testid="${CSS.escape(testId)}"]`);
        break;
      }
      if (node.parentElement) {
        const siblings = Array.from(node.parentElement.children).filter(
          (candidate) => candidate.tagName === node.tagName
        );
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }
  function collectFromRoot(root, modalRoot) {
    const selector =
      "select, input:not([type='hidden']), textarea, button, blz-button, blz-select, [role='combobox'], [role='textbox'], [role='spinbutton'], [role='button']";
    const rows = [];
    for (const el of root.querySelectorAll(selector)) {
      if (!isVisible(el)) continue;
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute("role") || "";
      let kind = tag;
      if (tag === "button" || tag === "blz-button") kind = tag === "blz-button" ? "blz-button" : "button";
      else if (tag === "input") kind = "input";
      else if (role === "textbox") kind = "textbox";
      else if (role === "combobox") kind = "combobox";
      else if (role === "spinbutton") kind = "spinbutton";
      const aria = clean(el.getAttribute("aria-label") || "", 120);
      const fieldName = clean(el.getAttribute("name") || el.name || "", 80);
      let text = textOf(el);
      if (tag === "select") {
        text = aria || fieldName || associatedLabel(el) || text.slice(0, 40);
      } else if (kind === "textbox" || kind === "combobox" || kind === "spinbutton") {
        text = aria || fieldName || associatedLabel(el) || clean(el.getAttribute("placeholder") || "", 80) || text.slice(0, 40);
      }
      const bounds = el.getBoundingClientRect();
      const row = {
        index: rows.length,
        kind,
        test_id: clean(el.getAttribute("data-testid") || "", 80) || null,
        role: role || null,
        text: text || null,
        aria: aria || null,
        href: el.href || null,
        input_type: el.type || null,
        disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
        name: fieldName || null,
        placeholder: clean(el.placeholder || "", 120) || null,
        value: (tag === "input" || tag === "textarea" || tag === "select" || kind === "textbox" || kind === "combobox" || kind === "spinbutton" || el.isContentEditable)
          ? String(el.value || "").slice(0, 120)
          : null,
        readonly: Boolean(el.readOnly || el.getAttribute("aria-readonly") === "true"),
        label: associatedLabel(el) || null,
        title: clean(el.getAttribute("title") || "", 120) || null,
        nearest_heading: nearestHeading(el) || null,
        landmark: landmarkOf(el) || null,
        nearby_text: nearbyText(el) || null,
        in_dialog: true,
        dialog_label: clean(modalRoot.getAttribute("aria-label") || modalRoot.getAttribute("aria-labelledby") || textOf(modalRoot).slice(0, 80), 120) || null,
        rect: {
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
        },
        css_path: cssPath(el),
      };
      if (tag === "select") {
        const optEntries = Array.from(el.options || []).slice(0, 40);
        const opts = optEntries.map((o) => clean(o.label || o.text || o.value || "", 80)).filter(Boolean);
        const optValues = optEntries.map((o) => clean(o.value || "", 80)).filter(Boolean);
        if (opts.length) row.options = opts;
        if (optValues.length) row.option_values = optValues;
        const selected = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
        if (selected) row.selected_label = clean(selected.label || selected.text || selected.value || "", 80) || null;
      }
      rows.push(row);
    }
    return rows;
  }
  function modalRoots() {
    const roots = [document];
    const seen = new Set(roots);
    for (let i = 0; i < roots.length; i++) {
      for (const host of roots[i].querySelectorAll("*")) {
        if (host.shadowRoot && !seen.has(host.shadowRoot)) {
          seen.add(host.shadowRoot);
          roots.push(host.shadowRoot);
        }
      }
    }
    const modals = [];
    const modalSelector = "[role='dialog'], [aria-modal='true'], dialog[open]";
    for (const root of roots) {
      for (const modal of root.querySelectorAll(modalSelector)) {
        modals.push({ modal, root });
        for (const host of modal.querySelectorAll("*")) {
          if (host.shadowRoot && !seen.has(host.shadowRoot)) {
            seen.add(host.shadowRoot);
            roots.push(host.shadowRoot);
            modals.push({ modal, root: host.shadowRoot });
          }
        }
      }
    }
    return modals;
  }
  const out = [];
  const seen = new Set();
  for (const { modal, root } of modalRoots()) {
    for (const row of collectFromRoot(root === modal.getRootNode() ? modal : root, modal)) {
      const key = `${row.kind}|${row.name || ""}|${row.text || ""}|${row.aria || ""}|${row.label || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      row.index = out.length;
      out.push(row);
    }
    if (root !== modal.getRootNode()) {
      for (const row of collectFromRoot(modal, modal)) {
        const key = `${row.kind}|${row.name || ""}|${row.text || ""}|${row.aria || ""}|${row.label || ""}`;
        if (seen.has(key)) continue;
        seen.add(key);
        row.index = out.length;
        out.push(row);
      }
    }
  }
  return out.slice(0, 80);
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
  function isBlockingOverlay(el) {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const role = el.getAttribute("role") || "";
    const ariaModal = el.getAttribute("aria-modal") === "true";
    const tag = el.tagName.toLowerCase();
    const text = clean(el.innerText || el.textContent, 400).toLowerCase();
    const label = clean(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby"), 200).toLowerCase();
    const blob = `${text} ${label}`.trim();
    const gateText = /\\b(age|cookie|consent|privacy|verify|gdpr|ccpa|before you continue|date of birth|too young)\\b/.test(blob);
    const falsePositive = /\\b(launch trailer|watch next|play video|leaderboard|permadeath|difficulty|mini.?game)\\b/.test(blob);
    if (falsePositive && !gateText) return false;
    if (role === "dialog" || ariaModal || (tag === "dialog" && el.open)) return true;
    if (gateText) {
      const fixed = style.position === "fixed" || style.position === "sticky";
      const coversViewport = rect.width > window.innerWidth * 0.35 && rect.height > window.innerHeight * 0.15;
      if (fixed || coversViewport || gateText) return true;
    }
    const className = String(el.className || "").toLowerCase();
    if (/\\b(modal|gate|consent)\\b/.test(className) && gateText) return true;
    return false;
  }
  const roots = [document];
  const seenRoots = new Set(roots);
  const overlays = [];
  for (let rootIndex = 0; rootIndex < roots.length; rootIndex++) {
    const root = roots[rootIndex];
    for (const el of root.querySelectorAll("[role='dialog'], [aria-modal='true'], dialog[open], [class*='modal'], [class*='overlay'], [class*='gate'], [class*='consent']")) {
      if (!visible(el) || !isBlockingOverlay(el)) continue;
      const text = clean(el.innerText || el.textContent);
      const label = clean(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby"));
      const tag = el.tagName.toLowerCase();
      const isCustomHost = tag.includes("-") && el.shadowRoot;
      if (!text && !label && !isCustomHost) continue;
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


_OVERLAY_GATE_KEYWORDS_RE = re.compile(
    r"\b(age.?verif\w*|verify your age|confirm your age|date of birth|birth.?year|"
    r"too young|enter your age|before you continue|we value your privacy|"
    r"cookie|consent|privacy preferences|gdpr|ccpa|accept all cookies)\b",
    re.I,
)
_OVERLAY_FALSE_POSITIVE_RE = re.compile(
    r"\b(launch trailer|watch next|play video|leaderboard|permadeath|"
    r"mini.?game|difficulty|start leaderboard)\b",
    re.I,
)


def _overlay_text_blob(overlay: dict[str, Any]) -> str:
    return " ".join(
        str(overlay.get(key) or "")
        for key in ("text", "label", "id", "tag", "role")
    ).lower()


def filter_blocking_overlays(overlays: list[Any]) -> list[dict[str, Any]]:
    """Drop video players and other non-blocking elements mis-tagged as overlays."""
    filtered: list[dict[str, Any]] = []
    for raw in overlays or []:
        if not isinstance(raw, dict):
            continue
        blob = _overlay_text_blob(raw)
        if not blob.strip():
            continue
        if _OVERLAY_FALSE_POSITIVE_RE.search(blob) and not _OVERLAY_GATE_KEYWORDS_RE.search(blob):
            continue
        role = str(raw.get("role") or "").lower()
        tag = str(raw.get("tag") or "").lower()
        if role == "dialog" or tag == "dialog":
            filtered.append(raw)
            continue
        if _OVERLAY_GATE_KEYWORDS_RE.search(blob):
            filtered.append(raw)
    return filtered

_SEMANTIC_JS = """() => {
  function clean(value, limit) {
    return String(value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  }
  function isVisible(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName;
    if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "SVG") return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || parseFloat(s.opacity || "1") === 0) return false;
    return true;
  }
  function inViewport(el) {
    const r = el.getBoundingClientRect();
    return r.bottom >= 0 && r.top <= window.innerHeight && r.right >= 0 && r.left <= window.innerWidth;
  }
  function textFromNode(root, limit) {
    const parts = [];
    const seen = new Set();
    function add(text) {
      text = clean(text, limit);
      if (!text || text.length < 2) return;
      const key = text.slice(0, 96);
      if (seen.has(key)) return;
      seen.add(key);
      parts.push(text);
    }
    function walk(node, allowHidden) {
      if (!node) return;
      if (node.nodeType === 3) {
        const t = (node.textContent || "").trim();
        if (t.length >= 2) add(t);
        return;
      }
      if (node.nodeType !== 1) return;
      const el = node;
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "SVG") return;
      const visible = allowHidden || isVisible(el);
      if (!visible) return;
      if (el.shadowRoot) walk(el.shadowRoot, true);
      for (const child of el.childNodes) walk(child, false);
    }
    walk(root, false);
    return clean(parts.join(" "), limit);
  }
  function blockText(el, limit) {
    if (!el || !isVisible(el)) return "";
    return clean(el.innerText || el.textContent || "", limit);
  }
  function collectVisibleText(limit) {
    const candidates = [];
    const regionSelectors = [
      "main",
      "[role='main']",
      "article",
      "[role='article']",
      ".article-body",
      ".article-content",
      ".article__content",
      ".content",
      ".post-content",
      ".entry-content",
      "#content",
      "#main-content",
    ];
    for (const sel of regionSelectors) {
      for (const el of document.querySelectorAll(sel)) {
        const t = blockText(el, limit);
        if (t) candidates.push(t);
      }
    }
    for (const el of document.querySelectorAll("*")) {
      if (!el.shadowRoot || !isVisible(el)) continue;
      const t = textFromNode(el.shadowRoot, limit);
      if (t) candidates.push(t);
    }
    const viewportBlocks = [];
    for (const el of document.querySelectorAll("p,li,h1,h2,h3,h4,h5,blockquote,pre,td,th,figcaption,span,div")) {
      if (!isVisible(el) || !inViewport(el)) continue;
      if (el.children.length > 8) continue;
      const t = blockText(el, 2000);
      if (t && t.length >= 12) viewportBlocks.push(t);
    }
    if (viewportBlocks.length) {
      candidates.push(clean(viewportBlocks.join("\\n"), limit));
    }
    if (document.body) {
      candidates.push(blockText(document.body, limit));
      candidates.push(textFromNode(document.body, limit));
    }
    candidates.sort((a, b) => b.length - a.length);
    return clean(candidates[0] || "", limit);
  }
  const limit = 24000;
  const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4,[role='heading']"))
    .filter(isVisible).map(el => clean(el.innerText || el.textContent, 180)).filter(Boolean).slice(0, 40);
  const landmarks = Array.from(document.querySelectorAll("main,nav,aside,header,footer,[role='main'],[role='navigation'],[role='region']"))
    .filter(isVisible).map(el => ({
      kind: el.getAttribute("role") || el.tagName.toLowerCase(),
      label: clean(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby"), 120),
    })).slice(0, 20);
  return {
    headings,
    landmarks,
    visible_text: collectVisibleText(limit),
  };
}"""


def _id_slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:48]


def _widget_for_item(item: dict[str, Any]) -> str:
    """Describe how the agent should interact with a form control."""
    kind = str(item.get("kind") or "").lower()
    role = str(item.get("role") or "").lower()
    input_type = str(item.get("input_type") or "").lower()
    if kind == "select":
        return "select"
    if kind == "combobox" or role == "combobox":
        return "combobox"
    if kind == "textarea":
        return "textarea"
    if role == "spinbutton" or input_type == "number":
        return "number"
    if input_type in {"date", "email", "tel", "search", "password"}:
        return input_type
    if kind in {"input", "textbox"} or role in {"textbox", "searchbox"}:
        return "text"
    return kind or "text"


def _interactable_action_hint(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "").lower()
    label = str(item.get("text") or item.get("aria") or item.get("label") or "").strip()
    href = str(item.get("href") or "").strip()
    widget = _widget_for_item(item)
    if kind == "link" and href:
        return f"Follow this link to {href}."
    if kind == "button":
        return f'Click this button to "{label}".' if label else "Click this button."
    if widget in {"select", "combobox"}:
        options = item.get("options") if isinstance(item.get("options"), list) else []
        field = label or str(item.get("name") or "this menu").strip()
        if options:
            preview = ", ".join(str(opt) for opt in options[:4])
            suffix = "..." if len(options) > 4 else ""
            return f'Choose an option in {field} (dropdown: {preview}{suffix}).'
        return f"Choose an option in {field}."
    if widget in {"text", "number", "date", "email", "tel", "search", "textarea"}:
        field = label or str(item.get("placeholder") or item.get("name") or "this field").strip()
        if widget == "number":
            return f"Enter a number in {field}."
        return f"Enter text in {field}."
    if kind == "summary" or item.get("expands_section"):
        state = "collapsed" if item.get("collapsed") else "expanded"
        return f"Expand or collapse {label or 'this section'} ({state})."
    return f"Interact with {label or 'this control'}."


def _stable_interactable_id(item: dict[str, Any], occurrence: int) -> str:
    kind = _id_slug(item.get("kind") or item.get("role") or "element")
    if str(item.get("kind") or "").lower() == "select":
        label = _id_slug(
            item.get("name") or item.get("aria") or item.get("label") or item.get("test_id")
        )
    else:
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


def _interactable_dedupe_key(item: dict[str, Any]) -> str:
    semantic = "|".join(
        str(item.get(key) or "").strip().lower()
        for key in (
            "kind", "role", "test_id", "text", "aria", "href", "name",
            "placeholder", "label", "frame_url",
        )
    )
    rect = item.get("rect") if isinstance(item.get("rect"), dict) else {}
    return f"{semantic}|{round(float(rect.get('x') or 0))}|{round(float(rect.get('y') or 0))}"


def _merge_interactables(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            if not isinstance(raw, dict):
                continue
            key = _interactable_dedupe_key(raw)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(raw))
    for index, item in enumerate(merged):
        item["index"] = index
    return merged


def _infer_gate_interactables_from_overlays(overlays: list[Any]) -> list[dict[str, Any]]:
    """When modal controls are not exposed in the DOM tree, infer year/month/day fields from overlay copy."""
    inferred: list[dict[str, Any]] = []
    for raw in overlays or []:
        if not isinstance(raw, dict):
            continue
        blob = f"{raw.get('text') or ''} {raw.get('label') or ''}".lower()
        if not re.search(r"\b(year|month|day|date of birth|birth)\b", blob):
            continue
        if not re.search(r"\bage\b|\bverif", blob):
            continue
        for name, label in (("year", "year"), ("month", "month"), ("day", "day")):
            if name not in blob:
                continue
            inferred.append(
                {
                    "index": len(inferred),
                    "kind": "select",
                    "role": None,
                    "text": label,
                    "aria": label,
                    "name": name,
                    "label": label,
                    "disabled": False,
                    "in_dialog": True,
                    "dialog_label": str(raw.get("label") or raw.get("text") or "")[:120],
                    "inferred_from_overlay": True,
                }
            )
        if inferred:
            break
    return inferred


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
        item["widget"] = _widget_for_item(item)
        item["action_hint"] = _interactable_action_hint(item)
        result.append(item)
    return result


def _collect_iframe_interactables(page: Page) -> list[dict[str, Any]]:
    """Collect visible controls from child frames and translate them to viewport coordinates."""
    collected: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)[1:]
    except Exception:
        return collected
    for frame_index, frame in enumerate(frames):
        try:
            raw = frame.evaluate(_INTERACTABLE_JS)
            handle = frame.frame_element()
            bounds = handle.bounding_box()
        except Exception:
            continue
        if not isinstance(raw, dict) or not isinstance(bounds, dict):
            continue
        for item in raw.get("items") or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            rect = row.get("rect") if isinstance(row.get("rect"), dict) else {}
            row["rect"] = {
                "x": float(bounds.get("x") or 0) + float(rect.get("x") or 0),
                "y": float(bounds.get("y") or 0) + float(rect.get("y") or 0),
                "width": float(rect.get("width") or 0),
                "height": float(rect.get("height") or 0),
            }
            row["frame_url"] = str(frame.url)
            row["frame_name"] = str(frame.name or "")
            row["frame_index"] = frame_index
            collected.append(row)
    return collected


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


def _resolve_viewport(page: Page, raw_viewport: Any) -> dict[str, Any]:
    """Use browser-reported viewport, falling back to Playwright viewport size."""
    if isinstance(raw_viewport, dict) and raw_viewport.get("width") and raw_viewport.get("height"):
        return raw_viewport
    try:
        size = page.viewport_size
        if size and size.get("width") and size.get("height"):
            return {
                "width": float(size["width"]),
                "height": float(size["height"]),
                "scroll_x": 0.0,
                "scroll_y": 0.0,
                "document_width": float(size["width"]),
                "document_height": float(size["height"]),
            }
    except Exception:
        pass
    return {
        "width": 1280.0,
        "height": 720.0,
        "scroll_x": 0.0,
        "scroll_y": 0.0,
        "document_width": 1280.0,
        "document_height": 720.0,
    }


def _collect_iframe_visible_text(page: Page, *, limit: int = 12000) -> str:
    """Merge readable text from child frames into the main snapshot."""
    chunks: list[str] = []
    try:
        frames = list(page.frames)[1:]
    except Exception:
        return ""
    for frame in frames:
        try:
            text = frame.evaluate(
                """(limit) => {
                  function clean(v, lim) {
                    return String(v || "").trim().replace(/\\s+/g, " ").slice(0, lim);
                  }
                  const body = document.body;
                  if (!body) return "";
                  return clean(body.innerText || body.textContent || "", limit);
                }""",
                limit,
            )
        except Exception:
            continue
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    merged = "\n\n".join(chunks)
    return merged[:limit]


def build_semantic_snapshot(state: dict[str, Any], *, max_chars: int = 10000) -> str:
    """Human-readable page text for prompts and UI — not limited to interactables."""
    parts: list[str] = []
    title = str(state.get("title") or "").strip()
    if title:
        parts.append(f"# {title}")
    headings = state.get("headings")
    if isinstance(headings, list) and headings:
        parts.append("## " + " · ".join(str(item) for item in headings[:12] if str(item).strip()))
    visible = str(state.get("visible_text") or "").strip()
    if visible:
        parts.append(visible)
    return "\n\n".join(parts)[:max_chars]


def collect_page_state(page: Page, *, include_screenshot: bool = True) -> dict[str, Any]:
    """Create a compact semantic snapshot with stable IDs and page context."""
    try:
        raw_interactables = page.evaluate(_INTERACTABLE_JS)
    except Exception:
        raw_interactables = []
    try:
        dialog_controls = page.evaluate(_DIALOG_FORM_CONTROLS_JS)
    except Exception:
        dialog_controls = []
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
    iframe_items = _collect_iframe_interactables(page)
    filtered_overlays = filter_blocking_overlays(
        blocking_overlays if isinstance(blocking_overlays, list) else []
    )
    dialog_items = dialog_controls if isinstance(dialog_controls, list) else []
    inferred_items = _infer_gate_interactables_from_overlays(filtered_overlays)
    merged_items = _merge_interactables(
        raw_items if isinstance(raw_items, list) else [],
        iframe_items,
        dialog_items,
        inferred_items,
    )
    interactables = _enrich_interactables(merged_items, page_url)
    interactable_total = (
        int(raw_interactables.get("total") or len(interactables)) + len(iframe_items)
        if isinstance(raw_interactables, dict)
        else len(interactables)
    )
    semantic = semantic if isinstance(semantic, dict) else {}
    visible_text = str(semantic.get("visible_text") or "")
    iframe_text = _collect_iframe_visible_text(page)
    if iframe_text:
        if iframe_text not in visible_text:
            visible_text = f"{visible_text}\n\n{iframe_text}".strip() if visible_text else iframe_text
        visible_text = visible_text[:24000]
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
        "viewport": _resolve_viewport(
            page,
            raw_interactables.get("viewport")
            if isinstance(raw_interactables, dict)
            else {},
        ),
        "interactables_total": interactable_total,
        "interactables_truncated": interactable_total > len(interactables),
        "headings": semantic.get("headings") if isinstance(semantic.get("headings"), list) else [],
        "landmarks": semantic.get("landmarks") if isinstance(semantic.get("landmarks"), list) else [],
        "visible_text": visible_text,
        "discovered_routes": routes,
        "blocking_overlays": filtered_overlays,
    }
    state["semantic_snapshot"] = build_semantic_snapshot(state)
    if include_screenshot:
        shot = capture_screenshot_b64(page)
        if shot:
            state["screenshot_b64"] = shot
    return state


def attach_web_capture(
    page: Page,
    state: dict[str, Any],
    *,
    context: str = "",
    analyze: bool = True,
    emit_progress: bool = True,
) -> dict[str, Any]:
    """Build a spatial capture while the originating Playwright page is still live."""
    url = str(state.get("url") or "")

    def _progress(
        phase: str,
        *,
        capture: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if not emit_progress:
            return
        try:
            from web_capture.progress import capture_progress_event

            element_count = None
            if capture and isinstance(capture.get("elements"), list):
                element_count = len(capture["elements"])
            capture_progress_event(
                phase=phase,
                url=url,
                capture=capture,
                error=error,
                element_count=element_count,
                screenshot_b64=state.get("screenshot_b64"),
                title=str(state.get("title") or ""),
                interactables=list(state.get("interactables") or []),
            )
        except Exception:
            pass

    try:
        from web_capture.analyzer import analyze_capture
        from web_capture.capture import build_capture
        from web_capture.context import get_active_project
        from web_capture.locators import validate_capture_locators
        from web_capture.maps import apply_site_map, sync_interactables_from_capture
        from web_capture.visual import collect_visual_tiles, resolve_visual_map

        _progress("geometry")
        capture = build_capture(state, context=context)
        _progress("geometry", capture=capture)
        _progress("locators")
        validate_capture_locators(page, capture)
        capture.setdefault("ai", {"status": "pending"})
        _progress("locators", capture=capture)
        if analyze:
            _progress("analyzing")
            analyze_capture(capture)
        project = get_active_project()
        apply_site_map(capture, project)
        _progress("visual")
        fresh_tiles = collect_visual_tiles(page)
        capture["visual"] = resolve_visual_map(
            project,
            url=str(capture.get("url") or ""),
            capture_id=str(capture.get("capture_id") or ""),
            viewport=capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {},
            elements=list(capture.get("elements") or []),
            fresh_tiles=fresh_tiles,
        )
        sync_interactables_from_capture(state, capture)
        state["web_capture"] = capture
        _progress("complete", capture=capture)
    except Exception as exc:
        state["web_capture_error"] = str(exc)[:300]
        _progress("error", error=str(exc)[:300])
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
    attach_web_capture(page, state, context=context, analyze=True)
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
            web_capture=state.get("web_capture"),
        )
    except ImportError:
        pass
    try:
        from ui_test.playwright_session import notify_page_state

        notify_page_state(page, context=context, snapshot=state)
    except ImportError:
        pass
    return state

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

_EXTRACT_VISIBLE_JS = """() => {
  const root = document.querySelector("main")
    || document.querySelector("[role='main']")
    || document.querySelector("#root")
    || document.body;

  const norm = (s) => (s || "").trim().replace(/\\s+/g, " ");
  const lines = root.innerText.split("\\n").map(norm).filter(Boolean);

  const headings = Array.from(root.querySelectorAll("h1,h2,h3,h4"))
    .map((h) => norm(h.innerText))
    .filter(Boolean)
    .slice(0, 20);
  const heading = headings[0] || lines[0] || "";

  const metrics = [];
  const seenMetrics = new Set();
  const addMetric = (label, value) => {
    const l = norm(label);
    const v = norm(value);
    if (!l || !v || l.length > 80 || v.length > 80) return;
    const key = l.toLowerCase();
    if (seenMetrics.has(key)) return;
    seenMetrics.add(key);
    metrics.push({ label: l, value: v });
  };

  for (const dl of root.querySelectorAll("dl")) {
    const dts = dl.querySelectorAll("dt");
    const dds = dl.querySelectorAll("dd");
    for (let i = 0; i < Math.min(dts.length, dds.length); i++) {
      addMetric(dts[i].innerText, dds[i].innerText);
    }
  }

  for (let i = 0; i < lines.length - 1; i++) {
    const label = lines[i];
    const value = lines[i + 1];
    if (label.endsWith(":") && label.length < 60) {
      addMetric(label.slice(0, -1), value);
    }
    if (label.length < 40 && value.length < 30 && /^[\\d,.]+$/.test(value)) {
      addMetric(label, value);
    }
  }

  const tables = [];
  for (const table of root.querySelectorAll("table")) {
    const headers = Array.from(table.querySelectorAll("thead th, thead td")).map((th) =>
      norm(th.innerText)
    );
    const rows = [];
    for (const tr of table.querySelectorAll("tbody tr")) {
      const cells = Array.from(tr.querySelectorAll("td, th")).map((td) => norm(td.innerText));
      if (cells.some((c) => c)) rows.push(cells.slice(0, 12));
      if (rows.length >= 30) break;
    }
    if (!headers.length && !rows.length) continue;
    if (!headers.length && rows.length) {
      tables.push({ headers: [], rows });
      continue;
    }
    tables.push({ headers, rows });
  }

  const lists = [];
  for (const listEl of root.querySelectorAll("ul, ol")) {
    if (listEl.closest("nav")) continue;
    const items = Array.from(listEl.querySelectorAll(":scope > li"))
      .map((li) => norm(li.innerText))
      .filter((t) => t && t.length < 200);
    if (items.length >= 1 && items.length <= 40) {
      lists.push({ items: items.slice(0, 25) });
    }
    if (lists.length >= 8) break;
  }

  const sections = [];
  const sectionSeen = new Set();
  for (const el of root.querySelectorAll("section, article, [role='region']")) {
    const titleEl = el.querySelector("h1,h2,h3,h4,[class*='title'],[class*='Title']");
    const title = titleEl ? norm(titleEl.innerText) : "";
    if (!title || sectionSeen.has(title)) continue;
    sectionSeen.add(title);
    const preview = norm(el.innerText).slice(0, 240);
    sections.push({ title, preview });
    if (sections.length >= 12) break;
  }

  let empty_message = null;
  const emptyPatterns = [
    /no (results|items|data|records|entries|rows)/i,
    /nothing (here|found|to show)/i,
    /empty/i,
    /not found/i,
    /get started/i,
    /create your first/i,
  ];
  for (const line of lines) {
    if (line.length > 160) continue;
    if (emptyPatterns.some((re) => re.test(line))) {
      empty_message = line;
      break;
    }
  }

  return {
    path: location.pathname,
    url: location.href,
    heading,
    headings,
    lines,
    metrics,
    tables,
    lists,
    sections,
    empty_message,
    main_text: root.innerText.trim().slice(0, 6000),
  };
}"""

_WAIT_FOR_CONTENT_JS = """() => {
  const root = document.querySelector("main")
    || document.querySelector("[role='main']")
    || document.querySelector("#root")
    || document.body;
  const spinner = root.querySelector(
    '[class*="animate-spin"], [class*="Spinner"], [aria-busy="true"]'
  );
  const skeletons = root.querySelectorAll('[class*="skeleton"], [class*="Skeleton"]');
  const text = (root.innerText || "").trim();
  const stable = !spinner && skeletons.length === 0;
  const hasContent = text.length > 60
    || root.querySelector("table tbody tr, ul li, h1, h2, [role='table'], [role='grid']");
  return stable && !!hasContent;
}"""


def wait_for_page_content(page: Page, *, timeout_ms: int = 15000) -> None:
    try:
        page.wait_for_function(_WAIT_FOR_CONTENT_JS, timeout=timeout_ms)
    except PlaywrightTimeout:
        page.wait_for_timeout(2000)


def extract_visible_content(page: Page) -> dict[str, Any]:
    wait_for_page_content(page)
    parsed = urlparse(page.url)
    path = parsed.path or "/"
    try:
        data = page.evaluate(_EXTRACT_VISIBLE_JS)
        if isinstance(data, dict):
            data.setdefault("path", path)
            data.setdefault("url", page.url)
            return data
    except Exception:
        pass
    return {
        "path": path,
        "url": page.url,
        "main_text": _page_text_fallback(page),
        "metrics": [],
        "tables": [],
        "lists": [],
        "sections": [],
    }


def _page_text_fallback(page: Page) -> str:
    try:
        return page.locator("main, [role='main'], body").first.inner_text(timeout=5000)[:6000]
    except Exception:
        return ""


_METRIC_KEYWORDS = (
    "views",
    "view",
    "followers",
    "follower",
    "likes",
    "like",
    "comments",
    "comment",
    "shares",
    "share",
    "subscribers",
    "subscriber",
)
_NAME_COLUMN_KEYWORDS = ("account", "name", "channel", "title", "label")


def parse_metric_value(raw: str) -> float | None:
    s = str(raw).strip().replace(",", "")
    if not s or s in ("—", "-", "–", "N/A", "n/a", "null", "None"):
        return None
    match = re.match(r"^([\d.]+)\s*([KMBkmb])?$", s)
    if not match:
        return None
    value = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}.get(suffix, 1)
    return value * multiplier


def _task_metric_keyword(task_text: str) -> str | None:
    task_l = task_text.lower()
    for keyword in _METRIC_KEYWORDS:
        if keyword in task_l:
            return keyword.rstrip("s") if keyword.endswith("s") and keyword != "views" else keyword
    return None


def _task_superlative(task_text: str) -> tuple[Callable[[float, float], float], str]:
    task_l = task_text.lower()
    if any(word in task_l for word in ("least", "lowest", "minimum", "fewest")):
        return min, "least"
    return max, "most"


def _column_index(headers: list[str], keyword: str) -> int | None:
    keyword_l = keyword.lower()
    for index, header in enumerate(headers):
        header_l = str(header).strip().lower()
        if keyword_l in header_l or header_l.startswith(keyword_l):
            return index
    return None


def _name_column_index(headers: list[str]) -> int:
    for index, header in enumerate(headers):
        header_l = str(header).strip().lower()
        if any(keyword in header_l for keyword in _NAME_COLUMN_KEYWORDS):
            return index
    return 0


def derive_task_answer(content: dict[str, Any], task_text: str) -> str:
    """Build a plain-English answer from visible table data when the task asks for a superlative."""
    if not task_text.strip():
        return ""

    metric_kw = _task_metric_keyword(task_text)
    if not metric_kw:
        return ""

    aggregate, direction = _task_superlative(task_text)
    best_name = ""
    best_value: float | None = None
    best_raw = ""

    for table in content.get("tables") or []:
        if not isinstance(table, dict):
            continue
        headers = [str(header).strip() for header in (table.get("headers") or [])]
        col_idx = _column_index(headers, metric_kw)
        if col_idx is None:
            continue
        name_idx = _name_column_index(headers)

        for row in table.get("rows") or []:
            if not isinstance(row, list) or len(row) <= col_idx:
                continue
            name = str(row[name_idx]).split("\n")[0].strip() if name_idx < len(row) else ""
            raw = str(row[col_idx]).strip()
            value = parse_metric_value(raw)
            if value is None or not name:
                continue
            if best_value is None:
                is_better = True
            elif aggregate is max:
                is_better = value > best_value
            else:
                is_better = value < best_value
            if is_better:
                best_value = value
                best_name = name
                best_raw = raw

    if not best_name:
        return ""

    metric_label = metric_kw if metric_kw.endswith("s") else f"{metric_kw}s"
    return (
        f"**{best_name}** has the {direction} {metric_label} "
        f"({best_raw}) among the channels shown on the page."
    )


def format_visible_report(content: dict[str, Any], *, task_text: str = "", answer: str = "") -> str:
    heading = str(content.get("heading") or "").strip()
    if heading.startswith("http"):
        heading = ""
    path = str(content.get("path") or "")
    if not heading:
        heading = path.strip("/").replace("/", " ").title() or "Page"
    url = str(content.get("url") or "")

    report_lines: list[str] = [
        f"# UI Report: {heading}",
        "",
        f"**URL:** {url}",
        "",
        "_Only text and values visible on the page are listed — nothing invented._",
        "",
    ]

    subheadings = [str(h).strip() for h in (content.get("headings") or []) if str(h).strip()]
    if len(subheadings) > 1:
        report_lines.append("## Page sections")
        for h in subheadings[:12]:
            report_lines.append(f"- {h}")
        report_lines.append("")

    metrics = content.get("metrics") or []
    if metrics:
        report_lines.append("## Metrics")
        for m in metrics:
            if isinstance(m, dict):
                label = str(m.get("label") or "").strip()
                value = str(m.get("value") or "").strip()
                if label and value:
                    report_lines.append(f"- **{label}:** {value}")
        report_lines.append("")

    tables = content.get("tables") or []
    for i, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        headers = table.get("headers") or []
        rows = table.get("rows") or []
        if not rows and not headers:
            continue
        title = " | ".join(str(h) for h in headers[:8]) if headers else f"Table {i + 1}"
        report_lines.append(f"## {title} ({len(rows)} row(s))")
        for row in rows:
            if isinstance(row, list):
                report_lines.append("- " + " | ".join(str(c) for c in row))
        report_lines.append("")

    sections = content.get("sections") or []
    if sections:
        report_lines.append("## Sections")
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = str(section.get("title") or "").strip()
            preview = str(section.get("preview") or "").strip()
            if title:
                report_lines.append(f"- **{title}**")
                if preview and preview != title:
                    report_lines.append(f"  {preview[:200]}")
        report_lines.append("")

    lists = content.get("lists") or []
    if lists:
        report_lines.append("## Lists")
        for lst in lists:
            if not isinstance(lst, dict):
                continue
            for item in lst.get("items") or []:
                report_lines.append(f"- {item}")
        report_lines.append("")

    empty = str(content.get("empty_message") or "").strip()
    has_data = bool(metrics or tables or lists or sections)
    if not has_data:
        if empty:
            report_lines.append("## Empty state")
            report_lines.append(f'The page shows: "{empty}"')
        else:
            lines_list = [str(l) for l in (content.get("lines") or [])[:40] if str(l).strip()]
            if lines_list:
                report_lines.append("## Visible page text")
                report_lines.append("```")
                report_lines.extend(lines_list)
                report_lines.append("```")
            else:
                main_text = str(content.get("main_text") or "").strip()
                if main_text:
                    report_lines.append("## Visible page text")
                    report_lines.append("```")
                    report_lines.extend(main_text.splitlines()[:40])
                    report_lines.append("```")
        report_lines.append("")

    if task_text.strip():
        report_lines.extend(["## Task", task_text.strip(), ""])

    if answer.strip():
        report_lines.extend(["## Answer", "", answer.strip(), ""])

    return "\n".join(report_lines).strip() + "\n"


def extract_answer_from_report(report: str) -> str:
    match = re.search(r"^## Answer\s*\n\n([\s\S]*?)(?=\n## |\n# |\Z)", report, re.MULTILINE)
    return match.group(1).strip() if match else ""


def resolve_task_answer(
    content: dict[str, Any],
    task_text: str,
    *,
    ollama_url: str = "",
    ollama_model: str = "",
    timeout_sec: float = 120,
) -> str:
    answer = derive_task_answer(content, task_text)
    if not answer and task_text.strip() and ollama_url and ollama_model:
        from ui_test.exploration_agent import synthesize_task_answer

        answer = synthesize_task_answer(
            task_text=task_text,
            content=content,
            ollama_url=ollama_url,
            model=ollama_model,
            timeout_sec=timeout_sec,
        )
    return answer.strip()


def build_grounded_report(
    page: Page,
    *,
    task_text: str = "",
    ollama_url: str = "",
    ollama_model: str = "",
    timeout_sec: float = 120,
) -> str:
    content = extract_visible_content(page)
    answer = resolve_task_answer(
        content,
        task_text,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        timeout_sec=timeout_sec,
    )
    return format_visible_report(content, task_text=task_text, answer=answer)

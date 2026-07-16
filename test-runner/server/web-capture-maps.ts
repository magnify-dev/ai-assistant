import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

type CaptureElement = Record<string, unknown>;

export function siteKey(url: string): string {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    const pathname = parsed.pathname.replace(/\/$/, "") || "/";
    return `${host}${pathname}`;
  } catch {
    return "unknown";
  }
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || "page";
}

export function mapFileForUrl(projectPath: string, url: string): string {
  return path.join(projectPath, ".agent", "web-capture", "maps", `${slug(siteKey(url))}.json`);
}

export function trainingPath(projectPath: string): string {
  return path.join(projectPath, ".agent", "web-capture", "training.jsonl");
}

export function elementSignature(element: CaptureElement): string {
  let hrefPath = "";
  const href = String(element.href ?? "").trim();
  if (href) {
    try {
      hrefPath = href.startsWith("http") ? new URL(href).pathname : href;
    } catch {
      hrefPath = href;
    }
  }
  const parts = [
    String(element.kind ?? "").toLowerCase(),
    String(element.role ?? "").toLowerCase(),
    String(element.test_id ?? "").toLowerCase(),
    String(element.name ?? "").toLowerCase(),
    String(element.aria ?? "").toLowerCase(),
    String(element.text ?? "").trim().toLowerCase().slice(0, 80),
    hrefPath.toLowerCase().slice(0, 120),
  ];
  const digest = crypto.createHash("sha256").update(parts.join("|")).digest("hex").slice(0, 16);
  return `sig_${digest}`;
}

export function loadSiteMap(projectPath: string, url: string): Record<string, unknown> | null {
  const file = mapFileForUrl(projectPath, url);
  if (!fs.existsSync(file)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, unknown>;
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

export function appendTrainingRecord(projectPath: string, record: Record<string, unknown>) {
  const file = trainingPath(projectPath);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFileSync(file, JSON.stringify({ ts: new Date().toISOString(), ...record }) + "\n", "utf8");
}

export function saveElementCorrection(
  projectPath: string,
  args: {
    url: string;
    captureId: string;
    element: CaptureElement;
    interactive: boolean;
    note?: string;
  },
) {
  const signature = elementSignature(args.element);
  const existing = loadSiteMap(projectPath, args.url) ?? {};
  const entries =
    existing.elements && typeof existing.elements === "object"
      ? { ...(existing.elements as Record<string, unknown>) }
      : {};
  const prior =
    entries[signature] && typeof entries[signature] === "object"
      ? (entries[signature] as Record<string, unknown>)
      : {};
  const entry = {
    signature,
    interactive: args.interactive,
    kind: args.element.kind,
    role: args.element.role,
    text: args.element.text,
    aria: args.element.aria,
    label: args.element.label,
    name: args.element.name,
    test_id: args.element.test_id,
    href: args.element.href,
    locator: args.element.locator,
    locator_status: args.element.locator_status,
    rect: args.element.rect,
    last_capture_id: args.captureId,
    last_element_id: args.element.id,
    note: args.note?.slice(0, 500) ?? prior.note,
    corrected_at: new Date().toISOString(),
    correction_count: Number(prior.correction_count ?? 0) + 1,
  };
  entries[signature] = entry;
  const payload = {
    version: 1,
    site_key: siteKey(args.url),
    url: args.url,
    updated_at: new Date().toISOString(),
    elements: entries,
  };
  const file = mapFileForUrl(projectPath, args.url);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2) + "\n", "utf8");
  fs.renameSync(tmp, file);
  appendTrainingRecord(projectPath, {
    kind: "element_correction",
    capture_id: args.captureId,
    url: args.url,
    site_key: siteKey(args.url),
    element_signature: signature,
    element_id: args.element.id,
    raw_element: args.element,
    ai_interactive: args.element.ai_interactive,
    user_interactive: args.interactive,
    locator: args.element.locator,
    note: args.note?.slice(0, 500) ?? null,
  });
  return entry;
}

export function applySavedMapToCapture(
  projectPath: string,
  capture: Record<string, unknown>,
): Record<string, unknown> {
  const url = String(capture.url ?? "");
  const saved = url ? loadSiteMap(projectPath, url) : null;
  if (!saved) {
    return { ...capture, map: { status: "missing", site_key: siteKey(url) } };
  }
  const entries =
    saved.elements && typeof saved.elements === "object"
      ? (saved.elements as Record<string, Record<string, unknown>>)
      : {};
  const elements = Array.isArray(capture.elements) ? [...capture.elements] : [];
  let matched = 0;
  let userKept = 0;
  let userRejected = 0;
  const nextElements = elements.map((raw) => {
    if (!raw || typeof raw !== "object") return raw;
    const item = { ...(raw as Record<string, unknown>) };
    const signature = elementSignature(item);
    const row = entries[signature];
    if (!row) {
      item.user_interactive = null;
      item.map_matched = false;
      item.effective_interactive = item.ai_interactive ?? true;
      return item;
    }
    matched += 1;
    const interactive = Boolean(row.interactive);
    item.user_interactive = interactive;
    item.map_matched = true;
    item.map_signature = signature;
    item.map_corrected_at = row.corrected_at;
    item.effective_interactive = interactive;
    if (interactive) userKept += 1;
    else userRejected += 1;
    if (row.locator && typeof row.locator === "object") {
      item.locator = row.locator;
      item.locator_status = row.locator_status ?? item.locator_status ?? "unique";
    }
    return item;
  });
  const summary =
    capture.summary && typeof capture.summary === "object"
      ? { ...(capture.summary as Record<string, unknown>) }
      : {};
  summary.user_kept = userKept;
  summary.user_rejected = userRejected;
  summary.map_matched = matched;
  return {
    ...capture,
    elements: nextElements,
    summary,
    map: {
      status: "applied",
      site_key: siteKey(url),
      matched,
      saved_entries: Object.keys(entries).length,
      user_kept: userKept,
      user_rejected: userRejected,
      updated_at: saved.updated_at,
    },
  };
}

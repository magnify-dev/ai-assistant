import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { siteKey } from "./web-capture-maps.js";

type CaptureElement = Record<string, unknown>;

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || "page";
}

export function visualFileForUrl(projectPath: string, url: string): string {
  return path.join(projectPath, ".agent", "web-capture", "visual", `${slug(siteKey(url))}.json`);
}

function rectCells(
  rect: Record<string, unknown>,
  viewport: Record<string, unknown>,
  cols: number,
  rows: number,
): number[] {
  const width = Number(viewport.width ?? 1);
  const height = Number(viewport.height ?? 1);
  const left = Math.max(0, Number(rect.x ?? 0));
  const top = Math.max(0, Number(rect.y ?? 0));
  const right = Math.min(width, left + Math.max(0, Number(rect.width ?? 0)));
  const bottom = Math.min(height, top + Math.max(0, Number(rect.height ?? 0)));
  if (right <= left || bottom <= top) return [];
  const cellW = width / cols;
  const cellH = height / rows;
  const colStart = Math.max(0, Math.floor(left / cellW));
  const colEnd = Math.min(cols - 1, Math.floor((right - 1) / cellW));
  const rowStart = Math.max(0, Math.floor(top / cellH));
  const rowEnd = Math.min(rows - 1, Math.floor((bottom - 1) / cellH));
  const indices: number[] = [];
  for (let row = rowStart; row <= rowEnd; row += 1) {
    for (let col = colStart; col <= colEnd; col += 1) {
      indices.push(row * cols + col);
    }
  }
  return indices;
}

function mergeDisplayCells(baseCells: string[], overlay: Array<string | null>): string[] {
  return baseCells.map((cell, index) => {
    const mark = overlay[index];
    if (mark === "+") return cell.replace(/\|[^|]+$/, "|kept");
    if (mark === "-") return cell.replace(/\|[^|]+$/, "|rejected");
    return cell;
  });
}

export function loadVisualMap(projectPath: string, url: string): Record<string, unknown> | null {
  const file = visualFileForUrl(projectPath, url);
  if (!fs.existsSync(file)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, unknown>;
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

export function stampVisualCorrection(
  projectPath: string,
  args: { url: string; element: CaptureElement; interactive: boolean },
) {
  const stored = loadVisualMap(projectPath, args.url);
  if (!stored) return null;
  const cols = Number(stored.cols ?? 48);
  const rows = Number(stored.rows ?? 32);
  const viewport =
    stored.viewport && typeof stored.viewport === "object"
      ? (stored.viewport as Record<string, unknown>)
      : { width: cols, height: rows };
  const overlay = Array.isArray(stored.overlay)
    ? stored.overlay.map((item) => (item === "+" || item === "-" ? String(item) : null))
    : Array<string | null>(cols * rows).fill(null);
  while (overlay.length < cols * rows) overlay.push(null);
  const rect = args.element.rect;
  const mark = args.interactive ? "+" : "-";
  if (rect && typeof rect === "object") {
    for (const index of rectCells(rect as Record<string, unknown>, viewport, cols, rows)) {
      overlay[index] = mark;
    }
  }
  const baseCells = Array.isArray(stored.cells) ? stored.cells.map(String) : [];
  const correctedAt = new Date().toISOString();
  const payload = {
    ...stored,
    overlay,
    display_cells: mergeDisplayCells(baseCells, overlay),
    corrected_at: correctedAt,
    updated_at: correctedAt,
  };
  const file = visualFileForUrl(projectPath, args.url);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2) + "\n", "utf8");
  fs.renameSync(tmp, file);
  return payload;
}

export function attachVisualToCapture(
  projectPath: string,
  capture: Record<string, unknown>,
): Record<string, unknown> {
  const url = String(capture.url ?? "");
  const stored = url ? loadVisualMap(projectPath, url) : null;
  if (!stored) {
    return {
      ...capture,
      visual: {
        status: "missing",
        site_key: siteKey(url),
        cols: 48,
        rows: 32,
        cells: [],
        overlay: [],
        display_cells: [],
        active_source: "none",
      },
    };
  }
  const builtAt = String(stored.built_at ?? "");
  const correctedAt = String(stored.corrected_at ?? "");
  return {
    ...capture,
    visual: {
      status: "reused",
      site_key: siteKey(url),
      cols: Number(stored.cols ?? 48),
      rows: Number(stored.rows ?? 32),
      cells: Array.isArray(stored.cells) ? stored.cells : [],
      overlay: Array.isArray(stored.overlay) ? stored.overlay : [],
      display_cells: Array.isArray(stored.display_cells) ? stored.display_cells : [],
      built_at: builtAt || undefined,
      corrected_at: correctedAt || undefined,
      active_source: correctedAt && correctedAt >= builtAt ? "corrected" : "built",
    },
  };
}

export function visualFingerprint(cells: string[]): string {
  return crypto.createHash("sha256").update(cells.join(",")).digest("hex").slice(0, 16);
}

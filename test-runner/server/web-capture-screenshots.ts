import fs from "node:fs";
import path from "node:path";
import { siteKey } from "./web-capture-maps.js";

function buildArtifactUrl(projectPath: string, runId: string, fileRel: string) {
  return `/api/project/run-artifact?path=${encodeURIComponent(projectPath)}&runId=${encodeURIComponent(runId)}&file=${encodeURIComponent(fileRel)}`;
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || "page";
}

export function screenshotRelForUrl(url: string): string {
  return `screenshots/${slug(siteKey(url))}.jpg`;
}

export function screenshotFileForUrl(projectPath: string, url: string): string {
  return path.join(projectPath, ".agent", "web-capture", screenshotRelForUrl(url));
}

export function resolveWebCaptureScreenshot(
  projectPath: string,
  runId: string,
  fileRel: string,
): string | null {
  const normalized = path.normalize(fileRel).replace(/^(\.\.(\/|\\|$))+/, "");
  if (normalized.startsWith("..") || path.isAbsolute(normalized)) {
    return null;
  }
  const runRoot = path.join(
    projectPath,
    ".agent",
    runId === "current" ? "current" : path.join("history", runId),
  );
  const runFile = path.join(runRoot, "web-capture", normalized);
  if (fs.existsSync(runFile) && fs.statSync(runFile).isFile()) {
    return buildArtifactUrl(projectPath, runId, `web-capture/${normalized}`);
  }
  const projectFile = path.join(projectPath, ".agent", "web-capture", normalized);
  if (fs.existsSync(projectFile) && fs.statSync(projectFile).isFile()) {
    return `/api/project/web-capture/screenshot?path=${encodeURIComponent(projectPath)}&file=${encodeURIComponent(normalized)}`;
  }
  return null;
}

export function resolveWebCaptureScreenshotFile(projectPath: string, fileRel: string): string | null {
  const normalized = path.normalize(fileRel).replace(/^(\.\.(\/|\\|$))+/, "");
  if (normalized.startsWith("..") || path.isAbsolute(normalized) || !normalized.startsWith("screenshots/")) {
    return null;
  }
  const full = path.join(projectPath, ".agent", "web-capture", normalized);
  if (!full.startsWith(path.join(projectPath, ".agent", "web-capture"))) {
    return null;
  }
  if (!fs.existsSync(full) || !fs.statSync(full).isFile()) {
    return null;
  }
  return full;
}

export function attachScreenshotToCapture(
  projectPath: string,
  runId: string,
  capture: Record<string, unknown>,
): Record<string, unknown> {
  const url = String(capture.url ?? "");
  const rel =
    typeof capture.screenshot === "string" && capture.screenshot.trim()
      ? capture.screenshot.trim()
      : url
        ? screenshotRelForUrl(url)
        : "";
  if (!rel) {
    return capture;
  }
  const screenshotUrl = resolveWebCaptureScreenshot(projectPath, runId, rel);
  const out: Record<string, unknown> = screenshotUrl
    ? { ...capture, screenshot: rel, screenshotUrl }
    : { ...capture, screenshot: rel };
  const scrollMap = capture.scroll_map;
  if (scrollMap && typeof scrollMap === "object" && Array.isArray((scrollMap as { slices?: unknown }).slices)) {
    const map = { ...(scrollMap as Record<string, unknown>) };
    map.slices = (map.slices as Record<string, unknown>[]).map((slice) => {
      const shot = typeof slice.screenshot === "string" ? slice.screenshot.trim() : "";
      if (!shot) return slice;
      const sliceUrl = resolveWebCaptureScreenshot(projectPath, runId, shot);
      return sliceUrl ? { ...slice, screenshotUrl: sliceUrl } : slice;
    });
    out.scroll_map = map;
  }
  return out;
}

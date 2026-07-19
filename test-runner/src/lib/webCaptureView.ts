import type { WebCapture, WebCaptureElement } from "./webCaptureTypes";

export type WebCaptureFilter = "all" | "kept" | "rejected" | "saved" | "problems" | "content" | "controls";

export function captureUsesDocumentCoords(capture: WebCapture): boolean {
  const map = capture.scroll_map;
  if (!map) return false;
  if (map.coords === "document" || map.stitched) return true;
  return (map.slice_count ?? 0) > 1;
}

export function captureCanvasHeight(capture: WebCapture): number {
  const map = capture.scroll_map;
  // Stitched maps use document-space rects against explored canvas height.
  if (map && captureUsesDocumentCoords(capture) && map.canvas_height > 0) {
    return map.canvas_height;
  }
  // Single viewport slices keep viewport-space rects — never stretch to document_height.
  return Math.max(1, capture.viewport.height);
}

/**
 * True when the capture has a document-space scrollable map image ready to draw.
 * Viewport-only shots must not be stretched into the overlay canvas.
 */
export function isCaptureMapReady(capture: WebCapture | null | undefined): boolean {
  if (!capture?.scroll_map) return false;
  const map = capture.scroll_map;
  const documentReady =
    Boolean(map.stitched) ||
    map.mode === "full_page" ||
    (map.coords === "document" && (map.canvas_height ?? 0) > 0);
  if (!documentReady || !(map.canvas_height > 0)) return false;
  const hasRootShot = Boolean(capture.screenshot || capture.screenshotUrl);
  const hasSliceShot = Boolean(
    map.slices?.some((slice) => Boolean(slice.screenshot || slice.screenshotUrl)),
  );
  return hasRootShot || hasSliceShot;
}

/** Prefer the saved full-page / stitch image over a live viewport JPEG. */
export function captureMapScreenshotSrc(
  capture: WebCapture | null | undefined,
  resolveUrl?: (relOrUrl: string) => string,
): string | undefined {
  if (!capture || !isCaptureMapReady(capture)) return undefined;
  const map = capture.scroll_map;
  const sliceShot =
    map?.slices?.find((slice) => slice.screenshotUrl || slice.screenshot)?.screenshotUrl ||
    map?.slices?.find((slice) => slice.screenshot)?.screenshot;
  const raw = capture.screenshotUrl || sliceShot || capture.screenshot;
  if (!raw) return undefined;
  if (raw.startsWith("http") || raw.startsWith("data:") || raw.startsWith("/api/")) {
    return raw;
  }
  return resolveUrl ? resolveUrl(raw) : raw;
}

export function effectiveInteractive(element: WebCaptureElement): boolean | null {
  if (element.user_interactive != null) return element.user_interactive;
  if (element.ai_interactive != null) return element.ai_interactive;
  return null;
}

export function filterCaptureElements(
  elements: WebCaptureElement[],
  filter: WebCaptureFilter,
) {
  if (filter === "kept") {
    return elements.filter((item) => effectiveInteractive(item) === true);
  }
  if (filter === "rejected") {
    return elements.filter((item) => effectiveInteractive(item) === false);
  }
  if (filter === "saved") {
    return elements.filter((item) => Boolean(item.map_matched));
  }
  if (filter === "problems") {
    return elements.filter(
      (item) => item.locator_status !== "unique" || Boolean(item.deterministic_issues?.length),
    );
  }
  if (filter === "content") {
    return elements.filter((item) => item.map_layer === "content");
  }
  if (filter === "controls") {
    return elements.filter((item) => item.map_layer !== "content");
  }
  return elements;
}

export function captureBoxStyle(element: WebCaptureElement, capture: WebCapture) {
  const width = Math.max(1, capture.viewport.width);
  const height = Math.max(1, captureCanvasHeight(capture));
  return {
    left: `${(element.rect.x / width) * 100}%`,
    top: `${(element.rect.y / height) * 100}%`,
    width: `${Math.max(0.7, (element.rect.width / width) * 100)}%`,
    height: `${Math.max(0.7, (element.rect.height / height) * 100)}%`,
  };
}

export function boxTone(element: WebCaptureElement): string {
  if (element.map_layer === "content") {
    if (element.likely_clickable) {
      return "border-cyan-500 bg-cyan-300/20 text-cyan-950";
    }
    return "border-slate-400 bg-slate-300/15 text-slate-900";
  }
  if (element.map_matched) {
    return element.user_interactive
      ? "border-violet-600 bg-violet-300/30 text-violet-950"
      : "border-fuchsia-500 bg-fuchsia-300/20 text-fuchsia-950";
  }
  if (element.locator_status !== "unique") {
    return "border-amber-500 bg-amber-300/25 text-amber-950";
  }
  if (element.ai_interactive === true) {
    return "border-emerald-600 bg-emerald-300/25 text-emerald-950";
  }
  if (element.ai_interactive === false) {
    return "border-rose-500 bg-rose-300/20 text-rose-950";
  }
  return "border-sky-500 bg-sky-300/20 text-sky-950";
}

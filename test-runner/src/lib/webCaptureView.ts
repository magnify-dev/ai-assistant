import type { WebCapture, WebCaptureElement } from "./webCaptureTypes";

export type WebCaptureFilter = "all" | "kept" | "rejected" | "saved" | "problems";

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
  return elements;
}

export function captureBoxStyle(element: WebCaptureElement, capture: WebCapture) {
  const width = Math.max(1, capture.viewport.width);
  const height = Math.max(1, capture.viewport.height);
  return {
    left: `${(element.rect.x / width) * 100}%`,
    top: `${(element.rect.y / height) * 100}%`,
    width: `${Math.max(0.7, (element.rect.width / width) * 100)}%`,
    height: `${Math.max(0.7, (element.rect.height / height) * 100)}%`,
  };
}

export function boxTone(element: WebCaptureElement): string {
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

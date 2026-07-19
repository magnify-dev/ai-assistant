/** Normalize page URLs so one map is stored per logical page (ignore hash). */
export function normalizeCaptureUrl(url: string | undefined | null): string {
  const raw = String(url || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    parsed.hash = "";
    // Drop trailing slash except for origin root.
    if (parsed.pathname.length > 1 && parsed.pathname.endsWith("/")) {
      parsed.pathname = parsed.pathname.slice(0, -1);
    }
    return parsed.toString();
  } catch {
    return raw.split("#")[0].replace(/\/$/, "") || raw;
  }
}

export function captureUrlLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    const label = `${parsed.hostname}${path}`;
    return label.length > 56 ? `${label.slice(0, 54)}…` : label;
  } catch {
    return url.length > 56 ? `${url.slice(0, 54)}…` : url;
  }
}

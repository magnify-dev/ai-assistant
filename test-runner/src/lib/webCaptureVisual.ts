export type WebCaptureVisualCell = {
  color: string;
  kind: string;
};

const KIND_FALLBACK: Record<string, string> = {
  button: "#3b82f6",
  link: "#0ea5e9",
  input: "#f59e0b",
  text: "#9ca3af",
  media: "#a855f7",
  nav: "#64748b",
  header: "#64748b",
  footer: "#64748b",
  aside: "#64748b",
  main: "#f3f4f6",
  kept: "#22c55e",
  rejected: "#ef4444",
  empty: "#ffffff",
  chrome: "#e5e7eb",
};

export function parseVisualCell(raw: string): WebCaptureVisualCell {
  const [color, kind = "chrome"] = raw.split("|");
  return {
    color: color || KIND_FALLBACK.chrome,
    kind: kind || "chrome",
  };
}

export function visualCellStyle(cell: WebCaptureVisualCell): { backgroundColor: string; outline?: string } {
  const color = cell.color.startsWith("#") || cell.color.startsWith("rgb") ? cell.color : KIND_FALLBACK[cell.kind] ?? KIND_FALLBACK.chrome;
  if (cell.kind === "kept") {
    return { backgroundColor: color, outline: "1px solid rgba(34,197,94,0.9)" };
  }
  if (cell.kind === "rejected") {
    return { backgroundColor: color, outline: "1px solid rgba(239,68,68,0.9)" };
  }
  return { backgroundColor: color };
}

export function visualStatusLabel(source?: string, status?: string): string {
  if (status === "missing") return "No pixel map yet — first capture will build one";
  if (source === "corrected") return "Using corrected pixel map";
  if (source === "built") return "Using rendered pixel map";
  if (status === "reused") return "Reused saved pixel map";
  if (status === "built") return "Built pixel map from live page";
  return "Pixel map unavailable";
}

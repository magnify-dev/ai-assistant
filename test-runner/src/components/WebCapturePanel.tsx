import { useEffect, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  WebCapture,
  WebCaptureElement,
  WebCaptureReview,
} from "@/lib/webCaptureTypes";
import {
  captureMapScreenshotSrc,
  filterCaptureElements,
  isCaptureMapReady,
  type WebCaptureFilter,
} from "@/lib/webCaptureView";
import { MapOverlayView } from "@/components/MapOverlayView";

type Props = {
  capture: WebCapture;
  latestReview?: WebCaptureReview | null;
  onReview?: (review: Omit<WebCaptureReview, "captureId" | "ts">) => Promise<void>;
};

function elementLabel(element: WebCaptureElement): string {
  return (
    element.text?.trim() ||
    element.title?.trim() ||
    element.aria?.trim() ||
    element.label?.trim() ||
    element.name?.trim() ||
    element.kind
  ).slice(0, 48);
}

export function WebCapturePanel({ capture, latestReview, onReview }: Props) {
  const [filter, setFilter] = useState<WebCaptureFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(capture.elements[0]?.id ?? null);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    setSelectedId(capture.elements[0]?.id ?? null);
    setSavedMessage("");
  }, [capture.capture_id, capture.elements]);

  const visible = useMemo(
    () => filterCaptureElements(capture.elements, filter),
    [capture.elements, filter],
  );
  const selected = visible.find((item) => item.id === selectedId) ?? visible[0] ?? null;

  const review = async (value: Omit<WebCaptureReview, "captureId" | "ts">) => {
    if (!onReview || saving) return;
    setSaving(true);
    setSavedMessage("");
    try {
      await onReview(value);
      setSavedMessage("Review saved");
    } catch {
      setSavedMessage("Review could not be saved");
    } finally {
      setSaving(false);
    }
  };

  const aiLabel =
    capture.ai.status === "ready"
      ? `${capture.ai.model ?? "AI"}${capture.ai.cached ? " · cached" : ""}`
      : capture.ai.status === "unavailable"
        ? "AI unavailable — raw capture shown"
        : capture.ai.status === "disabled"
          ? "AI disabled — raw capture shown"
          : "AI analysis pending";

  const mapReady = isCaptureMapReady(capture);
  const screenshotSrc = mapReady
    ? captureMapScreenshotSrc(capture, (rel) =>
        rel.startsWith("http") || rel.startsWith("data:") || rel.startsWith("/")
          ? rel.startsWith("/")
            ? apiUrl(rel)
            : rel
          : apiUrl(rel),
      )
    : undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-white/55">
            Web Capture
          </h2>
          <p className="mt-1 truncate font-mono text-xs text-sky-200/75">{capture.url}</p>
          <p className="mt-1 text-[10px] text-white/45">
            {capture.viewport.width} × {capture.viewport.height} viewport · {aiLabel}
          </p>
        </div>
        <div className="flex flex-wrap gap-1 text-[10px]">
          <span className="rounded-full bg-emerald-500/15 px-2 py-1 text-emerald-200">
            {capture.summary.ai_kept} kept
          </span>
          <span className="rounded-full bg-rose-500/15 px-2 py-1 text-rose-200">
            {capture.summary.ai_rejected} rejected
          </span>
          <span className="rounded-full bg-amber-500/15 px-2 py-1 text-amber-200">
            {capture.summary.ambiguous + capture.summary.unresolved} locator problems
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {(["all", "kept", "rejected", "problems"] as WebCaptureFilter[]).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            className={cn(
              "rounded-md border px-2.5 py-1 text-xs capitalize",
              filter === value
                ? "border-sky-400/50 bg-sky-500/15 text-sky-100"
                : "border-white/10 text-white/55 hover:bg-white/5",
            )}
          >
            {value === "kept" ? "AI kept" : value === "rejected" ? "AI rejected" : value}
          </button>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
        {mapReady && screenshotSrc ? (
          <MapOverlayView
            capture={capture}
            elements={visible}
            screenshotSrc={screenshotSrc}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
          />
        ) : (
          <div className="flex min-h-[240px] flex-col items-center justify-center rounded-lg border border-dashed border-sky-400/25 bg-sky-500/5 p-8 text-center">
            <p className="text-sm text-sky-100/90">Waiting for scrollable full-page map…</p>
          </div>
        )}

        <aside className="space-y-3 rounded-lg border border-white/10 bg-black/25 p-3">
          {selected ? (
            <>
              <div>
                <p className="text-[10px] uppercase tracking-wide text-white/40">Selected element</p>
                <p className="mt-1 text-sm font-medium text-white/90">{elementLabel(selected)}</p>
                <p className="mt-1 font-mono text-[10px] text-white/45">{selected.id}</p>
              </div>
              <dl className="grid grid-cols-[90px_1fr] gap-x-2 gap-y-1 text-xs">
                <dt className="text-white/40">DOM</dt>
                <dd className="text-white/75">
                  {selected.tag ?? selected.kind}
                  {selected.role ? ` · role=${selected.role}` : ""}
                </dd>
                <dt className="text-white/40">Geometry</dt>
                <dd className="font-mono text-white/65">
                  {Math.round(selected.rect.x)},{Math.round(selected.rect.y)} ·{" "}
                  {Math.round(selected.rect.width)}×{Math.round(selected.rect.height)}
                </dd>
                <dt className="text-white/40">Locator</dt>
                <dd
                  className={cn(
                    selected.locator_status === "unique" ? "text-emerald-300" : "text-amber-300",
                  )}
                >
                  {selected.locator_status}
                </dd>
                <dt className="text-white/40">AI decision</dt>
                <dd className="text-white/75">
                  {selected.ai_interactive == null
                    ? "Not classified"
                    : selected.ai_interactive
                      ? "Keep"
                      : "Reject"}
                  {typeof selected.ai_confidence === "number"
                    ? ` · ${Math.round(selected.ai_confidence * 100)}%`
                    : ""}
                </dd>
                {selected.dates?.length ? (
                  <>
                    <dt className="text-white/40">Date</dt>
                    <dd className="text-white/75">{selected.dates.filter(Boolean).join(", ")}</dd>
                  </>
                ) : null}
                {selected.authors?.length ? (
                  <>
                    <dt className="text-white/40">Author</dt>
                    <dd className="text-white/75">{selected.authors.filter(Boolean).join(", ")}</dd>
                  </>
                ) : null}
                {selected.byline ? (
                  <>
                    <dt className="text-white/40">Byline</dt>
                    <dd className="text-white/75">{selected.byline}</dd>
                  </>
                ) : null}
              </dl>
              {selected.locator ? (
                <div>
                  <p className="text-[10px] uppercase tracking-wide text-white/40">
                    Validated Playwright locator
                  </p>
                  <code className="mt-1 block break-all rounded bg-black/35 p-2 text-[10px] text-sky-100">
                    {selected.locator.kind}: {selected.locator.value}
                  </code>
                </div>
              ) : null}
              <div>
                <p className="text-[10px] uppercase tracking-wide text-white/40">AI reason</p>
                <p className="mt-1 text-xs leading-relaxed text-white/65">
                  {selected.ai_reason ?? "No AI explanation available."}
                </p>
              </div>
              {selected.deterministic_issues?.length ? (
                <p className="rounded border border-amber-500/25 bg-amber-950/20 p-2 text-[10px] text-amber-100">
                  {selected.deterministic_issues.join(", ")}
                </p>
              ) : null}
              {onReview ? (
                <div className="border-t border-white/10 pt-3">
                  <p className="mb-2 text-[10px] uppercase tracking-wide text-white/40">
                    Correct this element
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() =>
                        void review({
                          verdict: "element_correction",
                          elementId: selected.id,
                          correctedInteractive: true,
                        })
                      }
                      className="rounded border border-emerald-500/30 px-2 py-1 text-[10px] text-emerald-200 disabled:opacity-50"
                    >
                      Should keep
                    </button>
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() =>
                        void review({
                          verdict: "element_correction",
                          elementId: selected.id,
                          correctedInteractive: false,
                        })
                      }
                      className="rounded border border-rose-500/30 px-2 py-1 text-[10px] text-rose-200 disabled:opacity-50"
                    >
                      Should reject
                    </button>
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-white/45">Select a mapped element to inspect it.</p>
          )}
        </aside>
      </div>

      {onReview ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
          <span className="text-xs text-white/50">Is this capture useful?</span>
          <button
            type="button"
            disabled={saving}
            onClick={() => void review({ verdict: "good" })}
            className="rounded-md border border-emerald-500/35 bg-emerald-950/20 px-3 py-1.5 text-xs text-emerald-100 disabled:opacity-50"
          >
            Good capture
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => void review({ verdict: "needs_work" })}
            className="rounded-md border border-amber-500/35 bg-amber-950/20 px-3 py-1.5 text-xs text-amber-100 disabled:opacity-50"
          >
            Needs work
          </button>
          <span className="text-[10px] text-white/40">
            {savedMessage ||
              (latestReview ? `Last review: ${latestReview.verdict.replace("_", " ")}` : "")}
          </span>
        </div>
      ) : null}
    </div>
  );
}

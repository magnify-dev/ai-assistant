import { useEffect, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BrowserState, PlaywrightSession, PlaywrightSessionFrame } from "@/lib/projectTypes";
import type { WebCapture, WebCaptureBuildStatus, WebCaptureElement, WebCaptureReview } from "@/lib/webCaptureTypes";
import {
  boxTone,
  captureBoxStyle,
  effectiveInteractive,
  filterCaptureElements,
  type WebCaptureFilter,
} from "@/lib/webCaptureView";
import { parseVisualCell, visualCellStyle, visualStatusLabel } from "@/lib/webCaptureVisual";

type InspectView = "map" | "pixels" | "screenshot" | "split";

type Props = {
  state: BrowserState | null;
  session?: PlaywrightSession | null;
  capture?: WebCapture | null;
  captureBuild?: WebCaptureBuildStatus | null;
  frameIndex?: number;
  onFrameIndexChange?: (index: number) => void;
  lastAction?: string;
  replayMode?: boolean;
  latestReview?: WebCaptureReview | null;
  onReview?: (review: Omit<WebCaptureReview, "captureId" | "ts"> & { element?: WebCaptureElement }) => Promise<void>;
};

const BUILDING_PHASES = new Set(["geometry", "locators", "analyzing", "visual"]);

function CaptureBuildBanner({
  captureBuild,
  capture,
}: {
  captureBuild?: WebCaptureBuildStatus | null;
  capture?: WebCapture | null;
}) {
  const phase = captureBuild?.phase ?? (capture ? "complete" : "idle");
  if (phase === "idle") return null;

  const building = BUILDING_PHASES.has(phase);
  const draft = capture && capture.ai?.status === "pending";
  const complete = phase === "complete";
  const failed = phase === "error";

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs",
        building && "border-sky-400/30 bg-sky-500/10 text-sky-100",
        draft && !building && "border-amber-400/30 bg-amber-500/10 text-amber-100",
        complete && "border-violet-400/40 bg-violet-500/15 text-violet-100",
        failed && "border-rose-400/35 bg-rose-500/10 text-rose-100",
      )}
    >
      {building ? (
        <span className="inline-flex h-3 w-3 animate-spin rounded-full border-2 border-sky-200/30 border-t-sky-100" />
      ) : complete ? (
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-violet-300 shadow-[0_0_10px_rgba(196,181,253,0.9)]" />
      ) : failed ? (
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-rose-300" />
      ) : (
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-300" />
      )}
      <span className="font-medium">
        {failed
          ? "Map build failed"
          : complete
            ? "Map ready — inspect below"
            : draft
              ? "Draft map visible — finishing analysis…"
              : captureBuild?.message ?? "Building page map…"}
      </span>
      {captureBuild?.elementCount != null ? (
        <span className="rounded-full bg-black/20 px-2 py-0.5 text-[10px] text-white/70">
          {captureBuild.elementCount} controls
        </span>
      ) : capture?.elements?.length ? (
        <span className="rounded-full bg-black/20 px-2 py-0.5 text-[10px] text-white/70">
          {capture.elements.length} controls
        </span>
      ) : null}
      {failed && captureBuild?.error ? (
        <span className="text-[10px] text-rose-200/90">{captureBuild.error}</span>
      ) : null}
    </div>
  );
}

function MapBuildSkeleton({ capture }: { capture?: WebCapture | null }) {
  const cols = capture?.visual?.cols ?? 12;
  const rows = capture?.visual?.rows ?? 8;
  const cells = Array.from({ length: Math.min(cols * rows, 96) }, (_, index) => index);
  return (
    <div
      className="grid gap-px overflow-hidden rounded bg-neutral-800/80 p-1"
      style={{
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        aspectRatio: capture ? `${capture.viewport.width} / ${capture.viewport.height}` : "16 / 10",
      }}
    >
      {cells.map((cell) => (
        <div
          key={cell}
          className="min-h-[8px] animate-pulse bg-gradient-to-br from-white/10 to-white/5"
          style={{ animationDelay: `${(cell % 7) * 120}ms` }}
        />
      ))}
    </div>
  );
}

function elementLabel(element: WebCaptureElement): string {
  return (
    element.text?.trim() ||
    element.aria?.trim() ||
    element.label?.trim() ||
    element.name?.trim() ||
    element.kind
  ).slice(0, 48);
}

function frameTitle(frame: PlaywrightSessionFrame | undefined): string {
  if (!frame) return "Recorded step";
  if (frame.title?.trim()) return frame.title.trim();
  if (frame.label?.trim()) return frame.label.replace(/_/g, " ");
  return "Recorded step";
}

function ScreenshotPane({
  src,
  emptyLabel,
}: {
  src?: string;
  emptyLabel: string;
}) {
  return (
    <div className="flex min-h-[220px] items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-neutral-950/90 p-1">
      {src ? (
        <img src={src} alt="Page screenshot" className="max-h-[min(52vh,480px)] w-auto max-w-full object-contain" />
      ) : (
        <p className="text-sm text-white/40">{emptyLabel}</p>
      )}
    </div>
  );
}

function PixelMapPane({
  capture,
  elements,
}: {
  capture: WebCapture;
  elements: WebCaptureElement[];
}) {
  const visual = capture.visual;
  const cells = visual?.display_cells?.length ? visual.display_cells : visual?.cells ?? [];
  if (!visual || !cells.length) {
    return (
      <div className="flex min-h-[220px] items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 p-6 text-center text-sm text-white/45">
        Pixel map builds automatically on first capture. HTML structure alone is not enough — we sample rendered
        colors and layout from the live page.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-white/45">
        {visual.cols} × {visual.rows} tiles · {visualStatusLabel(visual.active_source, visual.status)}
        {visual.built_at ? ` · built ${new Date(visual.built_at).toLocaleString()}` : ""}
        {visual.corrected_at ? ` · corrected ${new Date(visual.corrected_at).toLocaleString()}` : ""}
      </p>
      <div className="rounded-lg border border-white/15 bg-neutral-900 p-2">
        <div
          className="relative mx-auto max-h-[520px] w-full overflow-hidden rounded shadow-inner"
          style={{
            aspectRatio: `${capture.viewport.width} / ${capture.viewport.height}`,
            display: "grid",
            gridTemplateColumns: `repeat(${visual.cols}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${visual.rows}, minmax(0, 1fr))`,
          }}
        >
          {cells.map((raw, index) => {
            const cell = parseVisualCell(raw);
            return <div key={index} style={visualCellStyle(cell)} title={`${cell.kind} · ${cell.color}`} />;
          })}
          {elements.map((element) => (
            <button
              key={`overlay-${element.id}`}
              type="button"
              title={elementLabel(element)}
              className={cn("pointer-events-none absolute border opacity-70", boxTone(element))}
              style={captureBoxStyle(element, capture)}
            />
          ))}
        </div>
      </div>
      <p className="text-[10px] text-white/40">
        Each tile samples rendered background color + semantic kind (button, link, input, text). Green/red outlines
        come from your saved corrections when they are newer than the base map.
      </p>
    </div>
  );
}

export function PageInspectPanel({
  state,
  session,
  capture,
  captureBuild,
  frameIndex = 0,
  onFrameIndexChange,
  lastAction,
  replayMode,
  latestReview,
  onReview,
}: Props) {
  const [view, setView] = useState<InspectView>(capture ? "map" : "screenshot");
  const [filter, setFilter] = useState<WebCaptureFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(capture?.elements[0]?.id ?? null);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    if (capture) {
      setSelectedId(capture.elements[0]?.id ?? null);
      setSavedMessage("");
      setView((current) => (current === "screenshot" && !state?.screenshot_b64 ? "map" : current));
    }
  }, [capture?.capture_id, capture?.elements, state?.screenshot_b64]);

  const frames = session?.frames ?? [];
  const activeFrame = frames[frameIndex] ?? frames[frames.length - 1];
  const screenshotUrl = activeFrame?.screenshotUrl;
  const screenshotSrc = state?.screenshot_b64
    ? `data:image/jpeg;base64,${state.screenshot_b64}`
    : screenshotUrl
      ? screenshotUrl.startsWith("http") || screenshotUrl.startsWith("data:")
        ? screenshotUrl
        : apiUrl(screenshotUrl)
      : undefined;

  const title = capture?.title || state?.title || frameTitle(activeFrame) || "Untitled page";
  const url = capture?.url || state?.url || activeFrame?.url || "";

  const visible = useMemo(
    () => (capture ? filterCaptureElements(capture.elements, filter) : []),
    [capture, filter],
  );
  const selected = visible.find((item) => item.id === selectedId) ?? visible[0] ?? null;

  const review = async (value: Omit<WebCaptureReview, "captureId" | "ts"> & { element?: WebCaptureElement }) => {
    if (!onReview || saving) return;
    setSaving(true);
    setSavedMessage("");
    try {
      await onReview(value);
      setSavedMessage("Saved for training and future runs on this page");
    } catch {
      setSavedMessage("Could not save review");
    } finally {
      setSaving(false);
    }
  };

  const mapStatus =
    capture?.map?.status === "applied"
      ? `Saved map applied · ${capture.map.matched ?? 0}/${capture.map.saved_entries ?? 0} matched`
      : capture?.map?.status === "missing"
        ? "No saved map for this page yet — your corrections will create one"
        : null;

  const building = BUILDING_PHASES.has(captureBuild?.phase ?? "");
  const hasDraftCapture = Boolean(capture?.elements?.length);
  const showWorkingMap = Boolean(capture) && (view === "map" || view === "split");
  const showMap = showWorkingMap && (hasDraftCapture || !building);
  const showPixels = Boolean(capture) && view === "pixels" && !building;
  const showScreenshot = view === "screenshot" || view === "split" || !capture;
  const showEmpty = !capture && !screenshotSrc && !building && !captureBuild;

  return (
    <div className="space-y-3">
      <CaptureBuildBanner captureBuild={captureBuild} capture={capture} />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-white/55">Page inspect</h2>
          <p className="mt-1 truncate text-sm font-medium text-white/90">{title}</p>
          <p className="truncate font-mono text-xs text-sky-200/80">{url || "Waiting for browser page…"}</p>
          {capture ? (
            <p className="mt-1 text-[10px] text-white/45">
              {capture.viewport.width} × {capture.viewport.height} viewport
              {capture.ai.status === "ready" ? ` · ${capture.ai.model ?? "AI"}` : ""}
              {mapStatus ? ` · ${mapStatus}` : ""}
            </p>
          ) : null}
        </div>
        {capture || screenshotSrc || building ? (
          <div className="flex flex-wrap gap-1">
            {(["map", "pixels", "screenshot", "split"] as InspectView[]).map((mode) => (
              <button
                key={mode}
                type="button"
                disabled={mode !== "screenshot" && building && !hasDraftCapture}
                onClick={() => setView(mode)}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-xs capitalize",
                  view === mode
                    ? "border-sky-400/50 bg-sky-500/15 text-sky-100"
                    : "border-white/10 text-white/55 hover:bg-white/5",
                  mode !== "screenshot" && building && !hasDraftCapture && "opacity-40",
                )}
              >
                {mode}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {lastAction ? (
        <p className="rounded border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white/70">{lastAction}</p>
      ) : null}

      {replayMode && frames.length > 1 ? (
        <div className="space-y-1">
          <div className="text-xs text-white/55">
            Step {frameIndex + 1} / {frames.length}
          </div>
          <input
            type="range"
            min={0}
            max={Math.max(0, frames.length - 1)}
            value={frameIndex}
            onChange={(e) => onFrameIndexChange?.(Number(e.target.value))}
            className="w-full accent-violet-400"
          />
        </div>
      ) : null}

      {showEmpty ? (
        <div className="flex min-h-[200px] flex-col items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 p-8 text-center">
          <p className="text-sm text-white/50">Run a task with browser activity to inspect the page map.</p>
        </div>
      ) : (
        <div className={cn("grid gap-3", view === "split" ? "xl:grid-cols-2" : "grid-cols-1")}>
          {building && !hasDraftCapture && (view === "map" || view === "split" || view === "pixels") ? (
            <div className="space-y-2">
              <p className="text-[10px] text-white/45">Sampling page geometry and controls…</p>
              <MapBuildSkeleton capture={capture ?? undefined} />
            </div>
          ) : null}

          {showMap && capture ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-1 text-[10px]">
                <span className="rounded-full bg-violet-500/15 px-2 py-1 text-violet-200">
                  {capture.summary.user_kept ?? 0} saved kept
                </span>
                <span className="rounded-full bg-emerald-500/15 px-2 py-1 text-emerald-200">
                  {capture.summary.ai_kept} AI kept
                </span>
                <span className="rounded-full bg-rose-500/15 px-2 py-1 text-rose-200">
                  {capture.summary.ai_rejected} AI rejected
                </span>
                <span className="rounded-full bg-amber-500/15 px-2 py-1 text-amber-200">
                  {capture.summary.ambiguous + capture.summary.unresolved} locator problems
                </span>
              </div>

              <div className="flex flex-wrap gap-1">
                {(["all", "saved", "kept", "rejected", "problems"] as WebCaptureFilter[]).map((value) => (
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
                    {value === "kept" ? "Effective kept" : value === "rejected" ? "Effective rejected" : value}
                  </button>
                ))}
              </div>

              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
                <div className="rounded-lg border border-white/15 bg-neutral-900 p-2">
                  <div
                    className="relative mx-auto max-h-[520px] w-full overflow-hidden rounded bg-white shadow-inner"
                    style={{ aspectRatio: `${capture.viewport.width} / ${capture.viewport.height}` }}
                  >
                    {visible.map((element) => (
                      <button
                        key={element.id}
                        type="button"
                        title={`${elementLabel(element)} · ${element.locator_status}`}
                        onClick={() => setSelectedId(element.id)}
                        className={cn(
                          "absolute overflow-hidden border text-left text-[9px] leading-tight transition",
                          boxTone(element),
                          selected?.id === element.id && "z-20 ring-2 ring-violet-600 ring-offset-1",
                        )}
                        style={captureBoxStyle(element, capture)}
                      >
                        <span className="block truncate px-0.5">{elementLabel(element)}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <aside className="space-y-3 rounded-lg border border-white/10 bg-black/25 p-3">
                  {selected ? (
                    <>
                      <div>
                        <p className="text-[10px] uppercase tracking-wide text-white/40">Selected element</p>
                        <p className="mt-1 text-sm font-medium text-white/90">{elementLabel(selected)}</p>
                        <p className="mt-1 font-mono text-[10px] text-white/45">{selected.id}</p>
                      </div>
                      <dl className="grid grid-cols-[92px_1fr] gap-x-2 gap-y-1 text-xs">
                        <dt className="text-white/40">Saved map</dt>
                        <dd className={selected.map_matched ? "text-violet-300" : "text-white/55"}>
                          {selected.map_matched ? "Matched from prior correction" : "No prior correction"}
                        </dd>
                        <dt className="text-white/40">Effective</dt>
                        <dd className="text-white/75">
                          {effectiveInteractive(selected) === true
                            ? "Keep for Playwright"
                            : effectiveInteractive(selected) === false
                              ? "Reject for Playwright"
                              : "Unclassified"}
                        </dd>
                        <dt className="text-white/40">Locator</dt>
                        <dd className={selected.locator_status === "unique" ? "text-emerald-300" : "text-amber-300"}>
                          {selected.locator_status}
                        </dd>
                      </dl>
                      {selected.locator ? (
                        <code className="block break-all rounded bg-black/35 p-2 text-[10px] text-sky-100">
                          {selected.locator.kind}: {selected.locator.value}
                        </code>
                      ) : null}
                      {selected.ai_reason ? (
                        <p className="text-xs leading-relaxed text-white/65">{selected.ai_reason}</p>
                      ) : null}
                      {onReview ? (
                        <div className="flex gap-2 border-t border-white/10 pt-3">
                          <button
                            type="button"
                            disabled={saving}
                            onClick={() =>
                              void review({
                                verdict: "element_correction",
                                elementId: selected.id,
                                element: selected,
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
                                element: selected,
                                correctedInteractive: false,
                              })
                            }
                            className="rounded border border-rose-500/30 px-2 py-1 text-[10px] text-rose-200 disabled:opacity-50"
                          >
                            Should reject
                          </button>
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <p className="text-sm text-white/45">Select a box on the map.</p>
                  )}
                </aside>
              </div>
            </div>
          ) : null}

          {showPixels && capture ? (
            <PixelMapPane capture={capture} elements={visible} />
          ) : null}

          {showScreenshot ? (
            <ScreenshotPane src={screenshotSrc} emptyLabel="No screenshot yet" />
          ) : null}
        </div>
      )}

      {capture && onReview ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
          <span className="text-xs text-white/50">Save this capture for training?</span>
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
              (latestReview ? `Last review: ${latestReview.verdict.replace("_", " ")}` : "Raw + corrections stored under .agent/web-capture/")}
          </span>
        </div>
      ) : null}
    </div>
  );
}

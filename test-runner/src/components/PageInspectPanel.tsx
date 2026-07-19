import { useEffect, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BrowserState, PlaywrightSession } from "@/lib/projectTypes";
import type { WebCapture, WebCaptureBuildStatus } from "@/lib/webCaptureTypes";
import { isScreenshotAheadOfMap, WEB_CAPTURE_BUILDING_PHASES } from "@/lib/webCaptureTypes";
import { captureCanvasHeight } from "@/lib/webCaptureView";
import { captureUrlLabel, normalizeCaptureUrl } from "@/lib/webCaptureUrl";
import { MapOverlayView } from "@/components/MapOverlayView";

type Props = {
  state: BrowserState | null;
  session?: PlaywrightSession | null;
  /** Latest capture from the live stream (may be mid-build). */
  capture?: WebCapture | null;
  /** Stable maps keyed by normalized URL — keep showing while a new map builds. */
  capturesByUrl?: Record<string, WebCapture>;
  captureBuild?: WebCaptureBuildStatus | null;
  frameIndex?: number;
  onFrameIndexChange?: (index: number) => void;
  lastAction?: string;
  replayMode?: boolean;
  projectPath?: string;
  /** When null, follow the live browser URL. */
  selectedMapUrl?: string | null;
  onSelectedMapUrlChange?: (url: string | null) => void;
};

const BUILDING_PHASES = WEB_CAPTURE_BUILDING_PHASES;

function CaptureBuildBanner({
  captureBuild,
  displayCapture,
  liveUrl,
}: {
  captureBuild?: WebCaptureBuildStatus | null;
  displayCapture?: WebCapture | null;
  liveUrl: string;
}) {
  const phase = captureBuild?.phase ?? (displayCapture ? "complete" : "idle");
  if (phase === "idle" && !displayCapture && !liveUrl) return null;

  const building = BUILDING_PHASES.has(phase);
  const label =
    phase === "error"
      ? captureBuild?.error || captureBuild?.message || "Map build failed"
      : building
        ? captureBuild?.message || "Building page map…"
        : phase === "complete"
          ? "Map ready"
          : liveUrl
            ? "Waiting for page map…"
            : null;

  if (!label) return null;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs",
        phase === "error"
          ? "border-rose-500/30 bg-rose-500/10 text-rose-100"
          : building
            ? "border-sky-400/30 bg-sky-500/10 text-sky-100"
            : "border-emerald-500/25 bg-emerald-500/10 text-emerald-100",
      )}
    >
      {building ? (
        <span className="inline-flex h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200/30 border-t-sky-100" />
      ) : null}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {displayCapture && building ? (
        <span className="shrink-0 text-[10px] text-white/50">showing previous map</span>
      ) : null}
    </div>
  );
}

export function PageInspectPanel({
  state,
  session,
  capture,
  capturesByUrl = {},
  captureBuild,
  frameIndex = 0,
  onFrameIndexChange,
  lastAction,
  replayMode,
  projectPath,
  selectedMapUrl = null,
  onSelectedMapUrlChange,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const liveUrl = normalizeCaptureUrl(
    state?.url || captureBuild?.url || capture?.url || "",
  );

  const urlKeys = useMemo(() => {
    const keys = new Set<string>(Object.keys(capturesByUrl));
    if (liveUrl) keys.add(liveUrl);
    if (capture?.url) keys.add(normalizeCaptureUrl(capture.url));
    return Array.from(keys);
  }, [capturesByUrl, liveUrl, capture?.url]);

  const viewingUrl = useMemo(() => {
    if (selectedMapUrl && urlKeys.includes(selectedMapUrl)) return selectedMapUrl;
    if (liveUrl) return liveUrl;
    return urlKeys[0] ?? "";
  }, [selectedMapUrl, urlKeys, liveUrl]);

  const followingLive = !selectedMapUrl || selectedMapUrl === liveUrl;

  /** Prefer the stable per-URL map so rebuilds don't blank the overlay. */
  const displayCapture = useMemo(() => {
    const stored = viewingUrl ? capturesByUrl[viewingUrl] : undefined;
    if (stored) return stored;
    if (capture && normalizeCaptureUrl(capture.url) === viewingUrl) return capture;
    // Live navigation often arrives before the new page map — keep the previous map
    // visible instead of blanking the panel (URL tabs still list every saved page).
    if (followingLive) {
      if (capture) return capture;
      const keys = Object.keys(capturesByUrl);
      if (keys.length) return capturesByUrl[keys[keys.length - 1]] ?? null;
    }
    return null;
  }, [capturesByUrl, viewingUrl, capture, followingLive]);

  useEffect(() => {
    setSelectedId(displayCapture?.elements[0]?.id ?? null);
  }, [displayCapture?.capture_id, viewingUrl]);

  const frames = session?.frames ?? [];
  const activeFrame = frames[frameIndex] ?? frames[frames.length - 1];

  const screenshotSrc = useMemo(() => {
    // Prefer the saved full-page / document map image whenever we have one.
    const fromCapture = displayCapture?.screenshotUrl;
    if (fromCapture) {
      return fromCapture.startsWith("http") || fromCapture.startsWith("data:")
        ? fromCapture
        : apiUrl(fromCapture);
    }
    // Live viewport shot only when there is no document map yet.
    if (followingLive && state?.screenshot_b64 && !displayCapture?.scroll_map?.stitched) {
      return `data:image/jpeg;base64,${state.screenshot_b64}`;
    }
    if (followingLive && state?.screenshot_b64) {
      return `data:image/jpeg;base64,${state.screenshot_b64}`;
    }
    const screenshotUrl = activeFrame?.screenshotUrl;
    if (screenshotUrl) {
      return screenshotUrl.startsWith("http") || screenshotUrl.startsWith("data:")
        ? screenshotUrl
        : apiUrl(screenshotUrl);
    }
    return undefined;
  }, [followingLive, state?.screenshot_b64, displayCapture, activeFrame?.screenshotUrl]);

  const showingPreviousMap =
    followingLive &&
    Boolean(displayCapture?.url) &&
    normalizeCaptureUrl(displayCapture?.url) !== viewingUrl;

  const title = displayCapture?.title || state?.title || "Untitled page";
  const url = showingPreviousMap
    ? normalizeCaptureUrl(displayCapture?.url) || viewingUrl
    : viewingUrl || liveUrl || displayCapture?.url || "";

  const building = BUILDING_PHASES.has(captureBuild?.phase ?? "");
  const buildingThisUrl =
    building && normalizeCaptureUrl(captureBuild?.url || liveUrl) === viewingUrl;
  const updatingMapOnScreenshot =
    followingLive &&
    Boolean(screenshotSrc && state?.screenshot_b64) &&
    (buildingThisUrl || isScreenshotAheadOfMap(state?.ts, captureBuild));
  const updatingMapMessage = showingPreviousMap
    ? "Keeping previous page map while this URL builds…"
    : (captureBuild?.message ?? "Updating map…");

  const showEmpty = !displayCapture && !screenshotSrc && !building && !captureBuild && urlKeys.length === 0;

  const elements = displayCapture?.elements ?? [];

  return (
    <div className="space-y-3">
      <CaptureBuildBanner
        captureBuild={captureBuild}
        displayCapture={displayCapture}
        liveUrl={liveUrl}
      />

      <div className="min-w-0">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/55">Page map</h2>
        <p className="mt-1 truncate text-sm font-medium text-white/90">{title}</p>
        <p className="truncate font-mono text-xs text-sky-200/80">{url || "Waiting for browser page…"}</p>
        {displayCapture ? (
          <p className="mt-1 text-[10px] text-white/45">
            {displayCapture.scroll_map?.mode === "full_page"
              ? "Full-page screenshot + overlay"
              : "Screenshot + overlay"}{" "}
            · {displayCapture.viewport.width} × {Math.round(captureCanvasHeight(displayCapture))}px
            {showingPreviousMap ? " · previous page (new map pending)" : ""}
          </p>
        ) : null}
      </div>

      {urlKeys.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {liveUrl ? (
            <button
              type="button"
              onClick={() => onSelectedMapUrlChange?.(null)}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs",
                followingLive
                  ? "border-sky-400/50 bg-sky-500/15 text-sky-100"
                  : "border-white/10 text-white/55 hover:bg-white/5",
              )}
            >
              Live
            </button>
          ) : null}
          {urlKeys.map((key) => {
            const hasMap = Boolean(capturesByUrl[key]);
            const isLive = key === liveUrl;
            const active = key === viewingUrl;
            return (
              <button
                key={key}
                type="button"
                title={key}
                onClick={() => onSelectedMapUrlChange?.(isLive ? null : key)}
                className={cn(
                  "max-w-[220px] truncate rounded-md border px-2.5 py-1 text-xs",
                  active
                    ? "border-violet-400/50 bg-violet-500/15 text-violet-100"
                    : "border-white/10 text-white/55 hover:bg-white/5",
                  !hasMap && "opacity-60",
                )}
              >
                {captureUrlLabel(key)}
                {!hasMap ? " · …" : ""}
              </button>
            );
          })}
        </div>
      ) : null}

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
      ) : displayCapture ? (
        <MapOverlayView
          capture={displayCapture}
          elements={elements}
          screenshotSrc={screenshotSrc}
          selectedId={selectedId}
          onSelect={setSelectedId}
          projectPath={projectPath}
          updatingMap={updatingMapOnScreenshot || buildingThisUrl}
          updatingMapMessage={
            buildingThisUrl && displayCapture
              ? "Redrawing map… previous map shown"
              : updatingMapMessage
          }
        />
      ) : buildingThisUrl || building ? (
        <div className="flex min-h-[240px] flex-col items-center justify-center rounded-lg border border-dashed border-sky-400/25 bg-sky-500/5 p-8 text-center">
          <span className="mb-3 inline-flex h-5 w-5 animate-spin rounded-full border-2 border-sky-200/30 border-t-sky-100" />
          <p className="text-sm text-sky-100/90">Building first map for this URL…</p>
          <p className="mt-1 max-w-md truncate font-mono text-[10px] text-white/40">{url}</p>
        </div>
      ) : screenshotSrc ? (
        <div className="overflow-auto rounded-lg border border-white/10 bg-black/30">
          <img src={screenshotSrc} alt="Page screenshot" className="mx-auto block max-w-full" />
        </div>
      ) : null}
    </div>
  );
}

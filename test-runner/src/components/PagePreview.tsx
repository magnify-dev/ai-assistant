import { useMemo } from "react";
import { cn } from "@/lib/utils";
import type { BrowserState, PlaywrightSession } from "@/lib/projectTypes";

type Props = {
  state: BrowserState | null;
  session?: PlaywrightSession | null;
  frameIndex?: number;
  onFrameIndexChange?: (index: number) => void;
  lastAction?: string;
  replayMode?: boolean;
};

function MediaFrame({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-neutral-950/90",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function PagePreview({ state, session, frameIndex = 0, onFrameIndexChange, lastAction, replayMode }: Props) {
  const frames = session?.frames ?? [];
  const activeFrame = frames[frameIndex] ?? frames[frames.length - 1];

  const replayState = useMemo((): BrowserState | null => {
    if (!activeFrame) return null;
    return {
      url: activeFrame.url ?? "",
      title: activeFrame.label ?? "Recorded step",
      interactables: [],
      context: activeFrame.context,
      screenshot_b64: undefined,
    };
  }, [activeFrame]);

  const screenshotUrl = activeFrame?.screenshotUrl;

  if (replayMode && session) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-white/90">{activeFrame?.label || "Recorded session"}</p>
            <p className="truncate font-mono text-xs text-violet-200/80">{activeFrame?.url || session.recorded_at}</p>
          </div>
          <span className="shrink-0 rounded-full bg-violet-500/15 px-2 py-0.5 text-[10px] text-violet-100">
            Replay
          </span>
        </div>

        {session.videoUrl ? (
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wide text-white/40">Session video</p>
            <MediaFrame className="p-1">
              <video src={session.videoUrl} controls className="max-h-44 w-full rounded object-contain" />
            </MediaFrame>
          </div>
        ) : null}

        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-white/40">Screenshot</p>
          <MediaFrame className="min-h-[120px] max-h-[min(48vh,420px)] p-1">
            {screenshotUrl ? (
              <img src={screenshotUrl} alt="Recorded step" className="max-h-[min(48vh,416px)] w-auto max-w-full object-contain" />
            ) : (
              <div className="flex h-32 w-full items-center justify-center text-sm text-white/40">No screenshot for this step</div>
            )}
          </MediaFrame>
        </div>

        {frames.length > 1 ? (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-white/55">
              <span>
                Step {frameIndex + 1} / {frames.length}
              </span>
              {session.traceUrl ? (
                <a href={session.traceUrl} className="text-sky-300 hover:underline" download>
                  Download trace
                </a>
              ) : null}
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
        ) : session.traceUrl ? (
          <a href={session.traceUrl} className="text-xs text-sky-300 hover:underline" download>
            Download Playwright trace
          </a>
        ) : null}
      </div>
    );
  }

  const display = state ?? replayState;

  if (!display) {
    return (
      <div className="flex min-h-[200px] flex-col items-center justify-center rounded-lg border border-dashed border-white/15 bg-black/20 p-8 text-center">
        <p className="text-sm text-white/50">Page preview will appear when Playwright opens a page.</p>
        {lastAction ? <p className="mt-2 text-xs text-white/40">{lastAction}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white/90">{display.title || "Untitled page"}</p>
          <p className="truncate font-mono text-xs text-sky-200/80">{display.url}</p>
        </div>
        {display.context ? (
          <span className="shrink-0 rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/60">{display.context}</span>
        ) : null}
      </div>

      <MediaFrame className="min-h-[120px] max-h-[min(52vh,480px)] p-1">
        {state?.screenshot_b64 ? (
          <img
            src={`data:image/jpeg;base64,${state.screenshot_b64}`}
            alt="Live page preview"
            className="max-h-[min(52vh,476px)] w-auto max-w-full object-contain"
          />
        ) : screenshotUrl ? (
          <img src={screenshotUrl} alt="Recorded page preview" className="max-h-[min(52vh,476px)] w-auto max-w-full object-contain" />
        ) : (
          <div className="flex h-32 w-full items-center justify-center text-sm text-white/40">No screenshot yet</div>
        )}
      </MediaFrame>

      {lastAction ? (
        <p className={cn("rounded border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white/70")}>{lastAction}</p>
      ) : null}

      {display.error ? (
        <p className="rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-xs text-red-200">{display.error}</p>
      ) : null}
    </div>
  );
}

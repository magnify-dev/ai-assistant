import { useMemo } from "react";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BrowserState, PlaywrightSession, PlaywrightSessionFrame } from "@/lib/projectTypes";

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

function frameTitle(frame: PlaywrightSessionFrame | undefined): string {
  if (!frame) return "Recorded step";
  if (frame.title?.trim()) return frame.title.trim();
  if (frame.label?.trim()) return frame.label.replace(/_/g, " ");
  return "Recorded step";
}

function decisionSummary(frame: PlaywrightSessionFrame | undefined): string | null {
  if (!frame?.decision) return null;
  const action = String(frame.decision.action ?? "").trim();
  const reason = String(frame.decision.reason ?? "").trim();
  const target = frame.selected_interactable;
  const targetId = String(frame.decision.target_id ?? "").trim();
  const targetLabel = target
    ? `${target.kind ?? "element"} “${target.text ?? target.aria ?? target.id ?? frame.selected_interactable_id}”`
    : targetId
      ? targetId
      : frame.selected_interactable_id
        ? String(frame.selected_interactable_id)
        : "";
  const parts = [action, targetLabel, reason ? `— ${reason}` : ""].filter(Boolean);
  return parts.join(" ").trim() || null;
}

export function PagePreview({ state, session, frameIndex = 0, onFrameIndexChange, lastAction, replayMode }: Props) {
  const frames = session?.frames ?? [];
  const activeFrame = frames[frameIndex] ?? frames[frames.length - 1];
  const recordedControls = activeFrame?.interactables ?? [];
  const recordedDecision = activeFrame?.decision;
  const selectedId = activeFrame?.selected_interactable_id;
  const decisionLine = decisionSummary(activeFrame);

  const replayState = useMemo((): BrowserState | null => {
    if (!activeFrame) return null;
    return {
      url: activeFrame.url ?? "",
      title: frameTitle(activeFrame),
      interactables: [],
      context: activeFrame.context,
      screenshot_b64: undefined,
      error: activeFrame.error,
    };
  }, [activeFrame]);

  const screenshotUrl = activeFrame?.screenshotUrl;
  const screenshotSrc = screenshotUrl
    ? screenshotUrl.startsWith("http") || screenshotUrl.startsWith("data:")
      ? screenshotUrl
      : apiUrl(screenshotUrl)
    : undefined;

  if (replayMode && session) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-white/90">{frameTitle(activeFrame)}</p>
            <p className="truncate font-mono text-xs text-violet-200/80">
              {activeFrame?.url || session.recorded_at || "Recorded session"}
            </p>
            {activeFrame?.label ? (
              <p className="text-[10px] uppercase tracking-wide text-white/40">
                Step {activeFrame.step ?? frameIndex + 1}
                {activeFrame.label ? ` · ${activeFrame.label}` : ""}
              </p>
            ) : null}
          </div>
          <span className="shrink-0 rounded-full bg-violet-500/15 px-2 py-0.5 text-[10px] text-violet-100">
            {session.source === "web" ? "Web replay" : "Replay"}
          </span>
        </div>

        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-white/40">Screenshot</p>
          <MediaFrame className="min-h-[120px] max-h-[min(48vh,420px)] p-1">
            {screenshotSrc ? (
              <img src={screenshotSrc} alt="Recorded step" className="max-h-[min(48vh,416px)] w-auto max-w-full object-contain" />
            ) : (
              <div className="flex h-32 w-full items-center justify-center text-sm text-white/40">No screenshot for this step</div>
            )}
          </MediaFrame>
        </div>

        <section className="rounded border border-white/10 bg-black/30 p-2">
          <p className="text-[10px] uppercase tracking-wide text-white/40">AI decision</p>
          {recordedDecision || decisionLine ? (
            <div className="mt-1 space-y-1 text-xs text-white/75">
              {decisionLine ? <p>{decisionLine}</p> : null}
              {activeFrame?.action_ok === true ? (
                <p className="text-emerald-300/90">Action succeeded</p>
              ) : activeFrame?.action_ok === false ? (
                <p className="text-red-200/90">Action failed</p>
              ) : null}
              {activeFrame?.error ? (
                <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded bg-red-950/30 px-2 py-1 text-[10px] text-red-100/90">
                  {activeFrame.error}
                </pre>
              ) : null}
            </div>
          ) : (
            <p className="mt-1 text-xs text-white/40">No AI action was recorded for this snapshot.</p>
          )}
        </section>

        <section className="rounded border border-white/10 bg-black/30 p-2">
          <p className="text-[10px] uppercase tracking-wide text-white/40">
            Controls available to the AI ({recordedControls.length})
          </p>
          {recordedControls.length ? (
            <ul className="mt-1 max-h-32 space-y-1 overflow-y-auto text-xs text-white/65">
              {recordedControls.map((control) => (
                <li
                  key={control.id ?? `${control.kind}-${control.index}`}
                  className={cn(
                    "rounded px-1 py-0.5",
                    control.id === selectedId && "bg-violet-500/20 text-violet-100",
                  )}
                >
                  {control.kind} #{control.id ?? control.index} — {control.text ?? control.aria ?? control.placeholder ?? "Unlabelled control"}
                  {control.action_hint ? ` · ${control.action_hint}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs text-white/40">No controls were captured for this frame.</p>
          )}
        </section>

        {frames.length > 1 ? (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-white/55">
              <span>
                Step {frameIndex + 1} / {frames.length}
              </span>
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
        ) : screenshotSrc ? (
          <img src={screenshotSrc} alt="Recorded page preview" className="max-h-[min(52vh,476px)] w-auto max-w-full object-contain" />
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

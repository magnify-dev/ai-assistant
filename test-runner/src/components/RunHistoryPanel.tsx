import { cn } from "@/lib/utils";
import type { RunHistoryEntry } from "@/lib/projectTypes";
import { RUN_HISTORY_PAGE_SIZE } from "@/lib/projectTypes";

type Props = {
  runs: RunHistoryEntry[];
  loading?: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  total?: number;
  running?: boolean;
  onInspect: (runId: string) => void;
  onResume?: (runId: string) => void;
  onLoadOlder?: () => void;
};

function formatGeneratedAt(value: string): string {
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value;
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(ms));
  } catch {
    return value;
  }
}

function runKindLabel(kind: RunHistoryEntry["runKind"]): string | null {
  if (kind === "web_research") return "Web research";
  if (kind === "exploration") return "Exploration";
  if (kind === "ui_test") return "UI test";
  return null;
}

function statusBadge(run: RunHistoryEntry): { label: string; className: string } {
  const cancelled = /cancel/i.test(run.statusText ?? "");
  if (run.overallOk === true) {
    return { label: "pass", className: "bg-green-500/20 text-green-200" };
  }
  if (run.overallOk === false && cancelled) {
    return { label: "stopped", className: "bg-amber-500/20 text-amber-200" };
  }
  if (run.overallOk === false) {
    return { label: "fail", className: "bg-red-500/20 text-red-200" };
  }
  if (run.canResume) {
    return { label: "stopped", className: "bg-amber-500/20 text-amber-200" };
  }
  return { label: "—", className: "bg-white/10 text-white/50" };
}

export function RunHistoryPanel({
  runs,
  loading,
  loadingMore,
  hasMore,
  total,
  running,
  onInspect,
  onResume,
  onLoadOlder,
}: Props) {
  if (loading && !runs.length) {
    return <p className="text-xs text-white/50">Loading run history…</p>;
  }
  if (!runs.length) {
    return <p className="text-xs text-white/50">No saved runs yet for this project.</p>;
  }

  const remaining = Math.max(0, (total ?? runs.length) - runs.length);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white/70">Run history</h3>
        {total !== undefined && total > runs.length ? (
          <span className="text-[10px] text-white/40">
            Showing {runs.length} of {total}
          </span>
        ) : null}
      </div>
      <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
        {runs.map((run) => {
          const badge = statusBadge(run);
          const kindLabel = runKindLabel(run.runKind);
          const detail =
            run.statusText && run.statusText !== run.summary
              ? run.statusText
              : run.finalUrl || undefined;

          return (
            <li
              key={run.id}
              className="flex items-start justify-between gap-2 rounded-md border border-white/10 bg-black/20 px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium text-white/90">{run.label}</span>
                  <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", badge.className)}>
                    {badge.label}
                  </span>
                  {kindLabel ? (
                    <span className="rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] text-sky-200">
                      {kindLabel}
                    </span>
                  ) : null}
                  {run.hasSession ? (
                    <span className="rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200">
                      {run.frameCount} frame{run.frameCount === 1 ? "" : "s"}
                      {run.sessionSource === "web" ? " · web" : run.sessionSource === "ui" ? " · ui" : ""}
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-white/75">{run.summary}</p>
                {detail ? (
                  <p className="mt-0.5 truncate text-[10px] text-white/45">
                    {run.finalUrl && detail === run.finalUrl ? (
                      <span className="font-mono">{detail}</span>
                    ) : (
                      detail
                    )}
                  </p>
                ) : null}
                {run.generatedAt ? (
                  <p className="mt-0.5 text-[10px] text-white/35">{formatGeneratedAt(run.generatedAt)}</p>
                ) : null}
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                <button
                  type="button"
                  onClick={() => onInspect(run.id)}
                  className="rounded border border-white/15 px-2 py-1 text-xs text-white/75 hover:bg-white/5"
                >
                  Inspect
                </button>
                {run.canResume && onResume ? (
                  <button
                    type="button"
                    disabled={running}
                    onClick={() => onResume(run.id)}
                    className="rounded border border-amber-500/30 px-2 py-1 text-xs text-amber-100 hover:bg-amber-950/30 disabled:opacity-50"
                  >
                    Resume
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {hasMore && onLoadOlder ? (
        <button
          type="button"
          disabled={loadingMore}
          onClick={onLoadOlder}
          className="w-full rounded-md border border-white/15 px-3 py-1.5 text-xs text-white/75 hover:bg-white/5 disabled:opacity-50"
        >
          {loadingMore
            ? "Loading older runs…"
            : `Load ${Math.min(RUN_HISTORY_PAGE_SIZE, remaining)} older run${Math.min(RUN_HISTORY_PAGE_SIZE, remaining) === 1 ? "" : "s"}`}
        </button>
      ) : null}
    </div>
  );
}

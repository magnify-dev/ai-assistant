import { cn } from "@/lib/utils";
import type { RunHistoryEntry } from "@/lib/projectTypes";

type Props = {
  runs: RunHistoryEntry[];
  loading?: boolean;
  onInspect: (runId: string) => void;
};

export function RunHistoryPanel({ runs, loading, onInspect }: Props) {
  if (loading) {
    return <p className="text-xs text-white/50">Loading run history…</p>;
  }
  if (!runs.length) {
    return <p className="text-xs text-white/50">No saved runs yet for this project.</p>;
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-white/70">Run history</h3>
      <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
        {runs.map((run) => (
          <li
            key={run.id}
            className="flex items-start justify-between gap-2 rounded-md border border-white/10 bg-black/20 px-3 py-2"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-white/90">{run.label}</span>
                <span
                  className={cn(
                    "rounded-full px-1.5 py-0.5 text-[10px]",
                    run.overallOk === true && "bg-green-500/20 text-green-200",
                    run.overallOk === false && "bg-red-500/20 text-red-200",
                    run.overallOk == null && "bg-white/10 text-white/50",
                  )}
                >
                  {run.overallOk === true ? "pass" : run.overallOk === false ? "fail" : "—"}
                </span>
                {run.hasSession ? (
                  <span className="rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200">
                    {run.frameCount} frame{run.frameCount === 1 ? "" : "s"}
                  </span>
                ) : null}
              </div>
              <p className="truncate text-xs text-white/55">{run.summary}</p>
              {run.finalUrl ? <p className="truncate font-mono text-[10px] text-white/40">{run.finalUrl}</p> : null}
            </div>
            <button
              type="button"
              onClick={() => onInspect(run.id)}
              className="shrink-0 rounded border border-white/15 px-2 py-1 text-xs text-white/75 hover:bg-white/5"
            >
              Inspect
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

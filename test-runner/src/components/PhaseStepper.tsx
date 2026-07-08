import { AlertTriangle, CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { PHASES, type PhaseMap } from "@/types";

function statusIcon(status?: string) {
  if (status === "running") return <Loader2 className="size-4 animate-spin text-sky-300" />;
  if (status === "done") return <CheckCircle2 className="size-4 text-green-400" />;
  if (status === "warning") return <AlertTriangle className="size-4 text-amber-400" />;
  if (status === "failed") return <XCircle className="size-4 text-red-400" />;
  return <Circle className="size-4 text-white/30" />;
}

export function PhaseStepper({
  phases,
  activePhase,
}: {
  phases: PhaseMap;
  activePhase?: string;
}) {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div>
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/50">
          Local agent (Ollama + Playwright)
        </h3>
        <ol className="space-y-2">
          {PHASES.filter((p) => p.group === "local").map((phase) => {
            const info = phases[phase.key];
            const isActive = activePhase === phase.key || info?.status === "running";
            return (
              <li
                key={phase.key}
                className={cn(
                  "flex items-start gap-3 rounded-md border px-3 py-2",
                  isActive ? "border-sky-500/40 bg-sky-950/20" : "border-white/5 bg-white/5",
                )}
              >
                {statusIcon(info?.status)}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{phase.label}</div>
                  {info?.message ? (
                    <div className="truncate text-xs text-white/60">{info.message}</div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
      <div>
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/50">
          Cursor agent (SDK)
        </h3>
        <ol className="space-y-2">
          {PHASES.filter((p) => p.group === "cursor").map((phase) => {
            const info = phases[phase.key];
            const isActive = activePhase === phase.key || info?.status === "running";
            return (
              <li
                key={phase.key}
                className={cn(
                  "flex items-start gap-3 rounded-md border px-3 py-2",
                  isActive ? "border-violet-500/40 bg-violet-950/20" : "border-white/5 bg-white/5",
                )}
              >
                {statusIcon(info?.status)}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{phase.label}</div>
                  {info?.message ? (
                    <div className="truncate text-xs text-white/60">{info.message}</div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
        <p className="mt-3 text-xs leading-relaxed text-white/55">
          Use <strong className="text-white/80">Cloud</strong> runtime to follow the agent in Cursor&apos;s{" "}
          <strong className="text-white/80">Agents</strong> sidebar (same UI as the desktop app). Local
          runtime streams here via the SDK bridge.
        </p>
      </div>
    </div>
  );
}

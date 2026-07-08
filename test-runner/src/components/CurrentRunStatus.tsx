import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import type { AgentRunCard } from "@/lib/collaborationTypes";
import { PHASES, type PhaseKey, type PhaseMap } from "@/types";
import { cn } from "@/lib/utils";

const STATUS_PHASE_ORDER: PhaseKey[] = [
  "ollama",
  "task_structure",
  "git",
  "local_server",
  "deploy",
  "health",
  "structure",
  "exploration",
  "ui_test",
];

const EXTRA_PHASE_LABELS: Record<string, string> = {
  collaboration: "Local agent",
  cursor: "Helper agent",
};

function phaseLabel(key: string): string {
  return EXTRA_PHASE_LABELS[key] ?? PHASES.find((p) => p.key === key)?.label ?? key;
}

export function resolveRunStatus(
  phases: PhaseMap,
  agentCards: AgentRunCard[],
  running: boolean,
): { key: string; label: string; message: string } | null {
  if (!running) return null;

  for (const key of STATUS_PHASE_ORDER) {
    const phase = phases[key];
    if (phase?.status === "running") {
      return {
        key,
        label: phaseLabel(key),
        message: phase.message?.trim() || "In progress…",
      };
    }
  }

  const cursor = phases.cursor;
  if (cursor?.status === "running") {
    return {
      key: "cursor",
      label: "Helper agent",
      message: cursor.message?.trim() || "Implementing changes…",
    };
  }

  const collab = phases.collaboration;
  if (collab?.status === "running") {
    return {
      key: "collaboration",
      label: "Local agent",
      message: collab.message?.trim() || "Working…",
    };
  }

  const helper = agentCards.find((c) => c.agent === "helper" && c.status === "running");
  if (helper) {
    return {
      key: "helper",
      label: "Helper agent",
      message: helper.summary?.trim() || "Implementing changes…",
    };
  }

  const local = agentCards.find((c) => c.agent === "local" && c.status === "running");
  if (local) {
    return {
      key: "local",
      label: "Local agent",
      message: local.summary?.trim() || "Working…",
    };
  }

  return { key: "starting", label: "Run", message: "Starting…" };
}

type Props = {
  phases: PhaseMap;
  agentCards: AgentRunCard[];
  running: boolean;
  /** Compact deploy/git/health/explore strip shown during collaboration runs */
  showPipelineStrip?: boolean;
  testTargetMode?: "local" | "deployed";
  skipDeploy?: boolean;
};

function pipelineStripKeys(
  testTargetMode: "local" | "deployed",
  skipDeploy: boolean,
): string[] {
  const keys = ["git"];
  if (testTargetMode === "local") keys.push("local_server");
  else if (!skipDeploy) keys.push("deploy");
  keys.push("health", "exploration");
  return keys;
}

function stripDotStatus(
  key: string,
  phases: PhaseMap,
  activeKey: string | undefined,
): "done" | "running" | "failed" | "idle" {
  const phase = phases[key];
  if (phase?.status === "failed") return "failed";
  if (phase?.status === "done") return "done";
  if (phase?.status === "running" || key === activeKey) return "running";
  return "idle";
}

export function CurrentRunStatus({
  phases,
  agentCards,
  running,
  showPipelineStrip = false,
  testTargetMode = "deployed",
  skipDeploy = false,
}: Props) {
  const status = useMemo(() => resolveRunStatus(phases, agentCards, running), [phases, agentCards, running]);
  const stripKeys = useMemo(
    () => pipelineStripKeys(testTargetMode, skipDeploy),
    [testTargetMode, skipDeploy],
  );

  if (!status) return null;

  const isPipeline = STATUS_PHASE_ORDER.includes(status.key as PhaseKey) || status.key === "collaboration";

  return (
    <section className="surface-card shrink-0 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <Loader2
            className={cn(
              "mt-0.5 size-4 shrink-0 animate-spin",
              status.key === "helper" || status.key === "cursor" ? "text-violet-300" : "text-sky-300",
            )}
          />
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-white/45">Current step</p>
            <p className="text-sm font-medium text-white/95">{status.label}</p>
            <p className="mt-0.5 text-sm leading-snug text-white/65">{status.message}</p>
          </div>
        </div>

        {showPipelineStrip ? (
          <div className="flex shrink-0 flex-wrap items-center gap-1.5 rounded-lg border border-white/10 bg-black/25 px-2.5 py-2">
            {stripKeys.map((key, index) => {
              const dot = stripDotStatus(key, phases, isPipeline ? status.key : undefined);
              return (
                <div key={key} className="flex items-center gap-1.5">
                  {index > 0 ? <span className="text-white/20">→</span> : null}
                  <div className="flex items-center gap-1">
                    <span
                      className={cn(
                        "size-2 rounded-full",
                        dot === "running" && "bg-sky-400 shadow-[0_0_6px_rgba(56,189,248,0.6)]",
                        dot === "done" && "bg-emerald-400",
                        dot === "failed" && "bg-red-400",
                        dot === "idle" && "bg-white/20",
                      )}
                    />
                    <span
                      className={cn(
                        "text-[10px] font-medium",
                        dot === "running" && "text-sky-200",
                        dot === "done" && "text-emerald-200/80",
                        dot === "failed" && "text-red-200",
                        dot === "idle" && "text-white/35",
                      )}
                    >
                      {phaseLabel(key)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </section>
  );
}

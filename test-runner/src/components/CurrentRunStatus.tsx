import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import { OperationWaitBanner } from "@/components/OperationWaitBanner";
import type { AgentRunCard } from "@/lib/collaborationTypes";
import {
  resolveActiveRunStep,
  stripPipelineKeys,
  stripStepStatus,
  stepLabel,
  type RunStepKey,
} from "@/lib/runProgress";
import type { WebCaptureBuildStatus } from "@/lib/webCaptureTypes";
import type { WebResearchState } from "@/lib/webResearchTypes";
import { resolveWebResearchWaitState } from "@/lib/webResearchWait";
import type { PhaseKey, PhaseMap } from "@/types";
import { cn } from "@/lib/utils";

export { resolveActiveRunStep as resolveRunStatus };

type Props = {
  phases: PhaseMap;
  agentCards: AgentRunCard[];
  running: boolean;
  /** Compact deploy/git/health/explore strip shown during collaboration runs */
  showPipelineStrip?: boolean;
  testTargetMode?: "local" | "deployed";
  skipDeploy?: boolean;
  webResearch?: WebResearchState | null;
  captureBuild?: WebCaptureBuildStatus | null;
  onStop?: () => void;
};

const PIPELINE_KEYS = new Set<string>([
  "ollama",
  "task_structure",
  "git",
  "local_server",
  "deploy",
  "health",
  "structure",
  "exploration",
  "ui_test",
  "collaboration",
  "cursor",
  "helper",
  "local",
]);

function dotClass(status: ReturnType<typeof stripStepStatus>): string {
  if (status === "running") return "bg-sky-400 shadow-[0_0_6px_rgba(56,189,248,0.6)]";
  if (status === "done") return "bg-emerald-400";
  if (status === "failed") return "bg-red-400";
  return "bg-white/20";
}

function labelClass(status: ReturnType<typeof stripStepStatus>): string {
  if (status === "running") return "text-sky-200";
  if (status === "done") return "text-emerald-200/80";
  if (status === "failed") return "text-red-200";
  return "text-white/35";
}

export function CurrentRunStatus({
  phases,
  agentCards,
  running,
  showPipelineStrip = false,
  testTargetMode = "deployed",
  skipDeploy = false,
  webResearch,
  captureBuild,
  onStop,
}: Props) {
  const status = useMemo(
    () => resolveActiveRunStep(phases, agentCards, running),
    [phases, agentCards, running],
  );
  const webWait = useMemo(
    () =>
      phases.web_research?.status === "running"
        ? resolveWebResearchWaitState(webResearch, captureBuild, running)
        : null,
    [phases.web_research?.status, webResearch, captureBuild, running],
  );
  const hasHelper = Boolean(agentCards.find((c) => c.agent === "helper"));
  const uiPhase = phases.ui_test ? "ui_test" : "exploration";
  const stripKeys = useMemo(
    () => stripPipelineKeys(testTargetMode, skipDeploy, hasHelper, uiPhase),
    [testTargetMode, skipDeploy, hasHelper, uiPhase],
  );

  if (!status) return null;

  const activeKey = status.key;
  const isPipeline = PIPELINE_KEYS.has(status.key);

  const displayLabel = webWait && status.key === "web_research" ? webWait.label : status.label;
  const displayMessage = webWait && status.key === "web_research" ? webWait.message : status.message;

  return (
    <section className="surface-card shrink-0 space-y-3 px-4 py-3">
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
            <p className="text-sm font-medium text-white/95">{displayLabel}</p>
            <p className="mt-0.5 text-sm leading-snug text-white/65">{displayMessage}</p>
          </div>
        </div>

        {onStop ? (
          <button
            type="button"
            onClick={onStop}
            className="shrink-0 rounded-md border border-red-400/40 bg-red-950/40 px-3 py-1.5 text-xs font-semibold text-red-100 hover:bg-red-900/50"
          >
            Stop
          </button>
        ) : null}

        {showPipelineStrip ? (
          <div className="flex shrink-0 flex-wrap items-center gap-1.5 rounded-lg border border-white/10 bg-black/25 px-2.5 py-2">
            {stripKeys.map((key, index) => {
              const dot = stripStepStatus(key, phases, agentCards, isPipeline ? activeKey : undefined);
              return (
                <div key={key} className="flex items-center gap-1.5">
                  {index > 0 ? <span className="text-white/20">→</span> : null}
                  <div className="flex items-center gap-1">
                    <span className={cn("size-2 rounded-full", dotClass(dot))} />
                    <span className={cn("text-[10px] font-medium", labelClass(dot))}>
                      {stepLabel(key as RunStepKey)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
      {webWait && status.key === "web_research" ? (
        <OperationWaitBanner wait={webWait} compact />
      ) : null}
    </section>
  );
}

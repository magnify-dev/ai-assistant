import { ConversationThread } from "@/components/ConversationThread";
import type { AgentRunCard, CollaborationResult } from "@/lib/collaborationTypes";
import { cn } from "@/lib/utils";

type Props = {
  agentCards: AgentRunCard[];
  collaborationResult: CollaborationResult | null;
  running: boolean;
  compact?: boolean;
};

export function CollaborationPanel({ agentCards, collaborationResult, running, compact = false }: Props) {
  const lastAnswer = [...agentCards].reverse().find((c) => c.outcomeType === "answer");

  if (agentCards.length === 0 && !running) return null;

  return (
    <div className={cn("flex min-h-0 flex-col gap-2", compact ? "max-h-none" : "flex-1")}>
      <div className="flex shrink-0 items-center justify-between gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-white/40">Conversation</h3>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[10px] font-medium",
            running && "bg-sky-500/20 text-sky-200",
            !running && collaborationResult?.ok && "bg-green-500/20 text-green-200",
            !running && collaborationResult && !collaborationResult.ok && "bg-red-500/20 text-red-200",
            !running && !collaborationResult && "bg-white/10 text-white/50",
          )}
        >
          {running ? "in progress" : collaborationResult?.ok ? "complete" : collaborationResult ? "stopped" : "…"}
        </span>
      </div>

      {collaborationResult?.error && !running ? (
        <p className="shrink-0 rounded border border-red-500/25 bg-red-950/20 px-2 py-1.5 text-xs text-red-200/90">
          {collaborationResult.error}
        </p>
      ) : null}

      {agentCards.some((c) => c.historical) && running ? (
        <p className="shrink-0 rounded border border-amber-500/25 bg-amber-950/15 px-2 py-1.5 text-xs text-amber-100/90">
          Resuming — earlier messages are shown for context.
        </p>
      ) : null}

      {lastAnswer && !running ? (
        <div className="shrink-0 rounded-lg border border-green-500/25 bg-green-950/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wide text-green-200/70">Final answer</p>
          <p className="mt-1 max-h-24 overflow-y-auto text-sm leading-relaxed text-green-100/95">
            {lastAnswer.outcomeText}
          </p>
        </div>
      ) : null}

      <ConversationThread agentCards={agentCards} />
    </div>
  );
}

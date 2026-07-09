import { useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, XCircle } from "lucide-react";
import { CollaborationTimeline } from "@/components/CollaborationTimeline";
import type { AgentRunCard, CollaborationResult } from "@/lib/collaborationTypes";
import { cn } from "@/lib/utils";

type Props = {
  agentCards: AgentRunCard[];
  collaborationResult: CollaborationResult | null;
  running: boolean;
  compact?: boolean;
};

function OutcomeBanner({
  result,
  fallbackAnswer,
}: {
  result: CollaborationResult;
  fallbackAnswer?: string;
}) {
  const [open, setOpen] = useState(false);
  const ok = Boolean(result.ok);
  const text = (ok ? result.answer || fallbackAnswer : result.error) ?? "";
  const firstLine = text.split("\n").find((l) => l.trim()) ?? (ok ? "Task complete" : "Run stopped");
  const hasMore = text.trim().length > firstLine.length + 10;

  return (
    <div
      className={cn(
        "shrink-0 rounded-lg border px-3 py-2",
        ok ? "border-green-500/30 bg-green-950/20" : "border-red-500/25 bg-red-950/15",
      )}
    >
      <button
        type="button"
        onClick={hasMore ? () => setOpen((v) => !v) : undefined}
        className={cn("flex w-full items-start gap-2 text-left", hasMore && "cursor-pointer")}
      >
        {ok ? (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-green-300" />
        ) : (
          <XCircle className="mt-0.5 size-4 shrink-0 text-red-300" />
        )}
        <div className="min-w-0 flex-1">
          <p className={cn("text-[10px] font-semibold uppercase tracking-wide", ok ? "text-green-200/70" : "text-red-200/70")}>
            {ok ? "Final answer" : "Stopped"}
            {typeof result.iterations === "number" ? ` · ${result.iterations} round${result.iterations === 1 ? "" : "s"}` : ""}
          </p>
          <p className={cn("mt-0.5 text-sm leading-relaxed", ok ? "text-green-100/95" : "text-red-100/90")}>
            {open ? null : firstLine}
          </p>
          {open ? (
            <p
              className={cn(
                "mt-0.5 max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-sm leading-relaxed scrollbar-thin",
                ok ? "text-green-100/95" : "text-red-100/90",
              )}
            >
              {text}
            </p>
          ) : null}
        </div>
        {hasMore ? (
          open ? (
            <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-white/40" />
          ) : (
            <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-white/40" />
          )
        ) : null}
      </button>
    </div>
  );
}

export function CollaborationPanel({ agentCards, collaborationResult, running, compact = false }: Props) {
  const lastAnswer = useMemo(
    () => [...agentCards].reverse().find((c) => c.outcomeType === "answer")?.outcomeText,
    [agentCards],
  );
  const rounds = useMemo(
    () => agentCards.reduce((max, c) => Math.max(max, c.iteration), 0),
    [agentCards],
  );

  if (agentCards.length === 0 && !running) return null;

  return (
    <div className={cn("flex min-h-0 flex-col gap-2", compact ? "max-h-none" : "flex-1")}>
      <div className="flex shrink-0 items-center justify-between gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-white/40">
          Conversation
          {rounds > 0 ? <span className="ml-1.5 font-normal text-white/30">{rounds} round{rounds === 1 ? "" : "s"}</span> : null}
        </h3>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
            running && "bg-sky-500/20 text-sky-200",
            !running && collaborationResult?.ok && "bg-green-500/20 text-green-200",
            !running && collaborationResult && !collaborationResult.ok && "bg-red-500/20 text-red-200",
            !running && !collaborationResult && "bg-white/10 text-white/50",
          )}
        >
          {running ? (
            <>
              <Loader2 className="size-2.5 animate-spin" />
              in progress
            </>
          ) : collaborationResult?.ok ? (
            "complete"
          ) : collaborationResult ? (
            "stopped"
          ) : (
            "…"
          )}
        </span>
      </div>

      {agentCards.some((c) => c.historical) && running ? (
        <p className="shrink-0 rounded border border-amber-500/25 bg-amber-950/15 px-2 py-1.5 text-xs text-amber-100/90">
          Resuming — dimmed messages are from the earlier run.
        </p>
      ) : null}

      {collaborationResult && !running ? (
        <OutcomeBanner result={collaborationResult} fallbackAnswer={lastAnswer} />
      ) : null}

      {agentCards.length === 0 && running ? (
        <p className="inline-flex items-center gap-2 py-2 text-sm text-white/60">
          <Loader2 className="size-3.5 animate-spin" />
          Starting collaboration…
        </p>
      ) : null}

      <CollaborationTimeline agentCards={agentCards} />
    </div>
  );
}

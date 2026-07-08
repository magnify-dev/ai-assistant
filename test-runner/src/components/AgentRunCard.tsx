import { ProgressCard, type ProgressStatus } from "@/components/ProgressCard";
import type { AgentRunCard } from "@/lib/collaborationTypes";
import { cn } from "@/lib/utils";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function durationMs(start: string, end?: string): string {
  if (!end) return "";
  try {
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 1000) return `${ms}ms`;
    const sec = Math.round(ms / 1000);
    if (sec < 60) return `${sec}s`;
    return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  } catch {
    return "";
  }
}

function cardStatus(card: AgentRunCard): ProgressStatus {
  if (card.status === "running") return "running";
  if (card.status === "done") return "done";
  if (card.status === "failed") return "failed";
  return "idle";
}

function outcomeLabel(card: AgentRunCard): string {
  if (card.outcomeType === "answer") return "Answer";
  if (card.outcomeType === "prompt") return "Handoff to helper";
  if (card.outcomeType === "response") return "Helper response";
  return "";
}

function agentBadgeClass(agent: AgentRunCard["agent"]): string {
  return agent === "local"
    ? "bg-emerald-500/20 text-emerald-200"
    : "bg-violet-500/20 text-violet-200";
}

type Props = {
  card: AgentRunCard;
  defaultOpen?: boolean;
};

export function AgentRunCardView({ card, defaultOpen = false }: Props) {
  const timeRange = card.completedAt
    ? `${formatTime(card.startedAt)} → ${formatTime(card.completedAt)} (${durationMs(card.startedAt, card.completedAt)})`
    : `${formatTime(card.startedAt)} → running…`;

  const summaryParts = [
    card.summary,
    card.outcomeType && card.outcomeText ? `${outcomeLabel(card)}: ${card.outcomeText.slice(0, 80)}${card.outcomeText.length > 80 ? "…" : ""}` : undefined,
  ].filter(Boolean);

  const hasExpandable =
    Boolean(card.outcomeText) ||
    (card.messages?.length ?? 0) > 0 ||
    card.agent === "local";

  return (
    <div className={cn(card.historical && "opacity-75")}>
    <ProgressCard
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium uppercase", agentBadgeClass(card.agent))}>
            {card.agent === "local" ? "Local" : "Helper"}
          </span>
          <span>{card.agentLabel}</span>
          {card.historical ? (
            <span className="text-[10px] font-normal text-white/35">prior</span>
          ) : null}
          {card.iteration > 1 ? (
            <span className="text-[10px] font-normal text-white/40">#{card.iteration}</span>
          ) : null}
        </span>
      }
      status={cardStatus(card)}
      summary={summaryParts.join(" · ") || timeRange}
      defaultOpen={defaultOpen && !card.historical}
      highlight={!card.historical && (card.outcomeType === "answer" || card.status === "running")}
    >
      {hasExpandable ? (
        <div className="space-y-3">
          <p className="font-mono text-[10px] text-white/45">{timeRange}</p>

          {card.outcomeText ? (
            <section>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">{outcomeLabel(card)}</p>
              <pre
                className={cn(
                  "max-h-48 overflow-auto whitespace-pre-wrap rounded border p-2 font-mono text-[11px] leading-relaxed",
                  card.outcomeType === "answer" && "border-green-500/25 bg-green-950/20 text-green-100/90",
                  card.outcomeType === "prompt" && "border-amber-500/25 bg-amber-950/15 text-amber-100/90",
                  card.outcomeType === "response" && "border-violet-500/25 bg-violet-950/20 text-violet-100/90",
                )}
              >
                {card.outcomeText}
              </pre>
            </section>
          ) : null}

          {(card.messages?.length ?? 0) > 0 ? (
            <section>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">
                  {card.agent === "helper" ? "Conversation" : "Agent decisions"}
              </p>
              <ul className="max-h-56 space-y-1.5 overflow-y-auto">
                {card.messages!.map((msg, i) => (
                  <li
                    key={`${msg.ts ?? i}-${i}`}
                    className={cn(
                      "rounded border px-2 py-1.5 text-[11px] leading-relaxed",
                      msg.role === "assistant"
                        ? "border-violet-500/20 bg-violet-950/15 text-violet-100/90"
                        : msg.role === "user"
                          ? "border-amber-500/20 bg-amber-950/15 text-amber-100/90"
                          : msg.role === "triage"
                            ? "border-sky-500/20 bg-sky-950/15 text-sky-100/90"
                            : "border-white/10 bg-black/25 text-white/75",
                    )}
                  >
                    {msg.role === "user" ? (
                      <span className="mb-0.5 block text-[9px] uppercase tracking-wide text-amber-300/60">Prompt</span>
                    ) : msg.role === "assistant" ? (
                      <span className="mb-0.5 block text-[9px] uppercase tracking-wide text-violet-300/60">Response</span>
                    ) : msg.role === "triage" ? (
                      <span className="mb-0.5 block text-[9px] uppercase tracking-wide text-sky-300/60">Decision</span>
                    ) : null}
                    {msg.text.length > 2000 && card.outcomeType !== "response" ? `${msg.text.slice(0, 2000)}…` : msg.text}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : undefined}
    </ProgressCard>
    </div>
  );
}

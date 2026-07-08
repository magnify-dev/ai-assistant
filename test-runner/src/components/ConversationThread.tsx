import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { AgentRunCard } from "@/lib/collaborationTypes";
import { cn } from "@/lib/utils";

const SCROLL_BODY =
  "max-h-52 overflow-y-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed scrollbar-thin";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function extractUserPrompt(card: AgentRunCard): string | null {
  const user = card.messages?.find((m) => m.role === "user");
  if (!user?.text) return null;
  const marker = "\n---\n";
  const idx = user.text.lastIndexOf(marker);
  return idx >= 0 ? user.text.slice(idx + marker.length).trim() : user.text.trim();
}

type BubbleProps = {
  side: "left" | "right" | "center";
  label: string;
  time?: string;
  tone: "local" | "helper" | "system" | "error" | "answer";
  children: React.ReactNode;
  historical?: boolean;
};

function Bubble({ side, label, time, tone, children, historical }: BubbleProps) {
  const align =
    side === "left" ? "justify-start" : side === "right" ? "justify-end" : "justify-center";

  const bubbleClass = cn(
    "max-w-[92%] rounded-2xl px-3 py-2 shadow-sm",
    side === "center" && "max-w-full rounded-lg px-2.5 py-1.5",
    tone === "local" && "rounded-bl-md border border-emerald-500/25 bg-emerald-950/30 text-emerald-50",
    tone === "helper" && "rounded-br-md border border-violet-500/25 bg-violet-950/30 text-violet-50",
    tone === "system" && "border border-white/10 bg-white/[0.04] text-white/60",
    tone === "error" && "border border-red-500/30 bg-red-950/25 text-red-100",
    tone === "answer" && "rounded-bl-md border border-green-500/30 bg-green-950/25 text-green-50",
    historical && "opacity-70",
  );

  return (
    <div className={cn("flex w-full", align)}>
      <div className={bubbleClass}>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-white/90">{label}</span>
          {time ? <span className="text-[10px] text-white/40">{time}</span> : null}
          {historical ? <span className="text-[10px] text-white/35">prior run</span> : null}
        </div>
        {children}
      </div>
    </div>
  );
}

function ExpandableDetails({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="mt-2 border-t border-white/10 pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 text-left text-[10px] font-medium uppercase tracking-wide text-white/45 hover:text-white/65"
      >
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        {title}
      </button>
      {open ? <div className="mt-1.5">{children}</div> : null}
    </div>
  );
}

function priorLocalHandoff(cards: AgentRunCard[], index: number): boolean {
  if (index <= 0) return false;
  const prev = cards[index - 1];
  return prev?.agent === "local" && prev.outcomeType === "prompt" && Boolean(prev.outcomeText?.trim());
}

type Props = {
  agentCards: AgentRunCard[];
};

export function ConversationThread({ agentCards }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [agentCards]);

  if (agentCards.length === 0) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
      {agentCards.map((card, index) => {
        const time = formatTime(card.startedAt);
        const triage = card.messages?.filter((m) => m.role === "triage") ?? [];
        const pipeline = card.messages?.filter((m) => m.role === "pipeline") ?? [];
        const userPrompt = card.agent === "helper" ? extractUserPrompt(card) : null;
        const mainText = card.outcomeText?.trim() ?? "";
        const showHelperPrompt = Boolean(userPrompt) && !priorLocalHandoff(agentCards, index);

        return (
          <div key={card.id} className="space-y-2">
            {triage.map((msg, i) => (
              <Bubble key={`${card.id}-triage-${i}`} side="center" label="Decision" time={time} tone="system" historical={card.historical}>
                <p className="text-xs">{msg.text}</p>
              </Bubble>
            ))}

            {pipeline.map((msg, i) => (
              <Bubble key={`${card.id}-pipe-${i}`} side="center" label="Pipeline" time={time} tone="error" historical={card.historical}>
                <div className={cn(SCROLL_BODY, "max-h-32 font-mono text-xs")}>{msg.text}</div>
              </Bubble>
            ))}

            {card.agent === "local" && card.outcomeType === "prompt" && mainText ? (
              <Bubble side="left" label={card.agentLabel} time={time} tone="local" historical={card.historical}>
                <div className={SCROLL_BODY}>{mainText}</div>
                {card.summary ? (
                  <ExpandableDetails title="Why hand off">
                    <p className="text-xs text-white/70">{card.summary}</p>
                  </ExpandableDetails>
                ) : null}
              </Bubble>
            ) : null}

            {card.agent === "local" && card.outcomeType === "answer" && mainText ? (
              <Bubble side="left" label={card.agentLabel} time={time} tone="answer" historical={card.historical}>
                <div className={SCROLL_BODY}>{mainText}</div>
              </Bubble>
            ) : null}

            {card.agent === "helper" && showHelperPrompt ? (
              <Bubble side="left" label="Local agent" time={time} tone="local" historical={card.historical}>
                <div className={SCROLL_BODY}>{userPrompt}</div>
              </Bubble>
            ) : null}

            {card.agent === "helper" && mainText && card.status !== "running" ? (
              <Bubble
                side="right"
                label={card.agentLabel}
                time={card.completedAt ? formatTime(card.completedAt) : time}
                tone={card.status === "failed" ? "error" : "helper"}
                historical={card.historical}
              >
                <div className={SCROLL_BODY}>{mainText}</div>
                {card.summary ? (
                  <ExpandableDetails title="Status">
                    <p className="text-xs text-white/70">{card.summary}</p>
                  </ExpandableDetails>
                ) : null}
              </Bubble>
            ) : null}

            {card.agent === "local" && card.status === "running" && !mainText ? (
              <Bubble side="left" label={card.agentLabel} time={time} tone="local">
                <p className="text-sm text-white/70">{card.summary ?? "Working…"}</p>
              </Bubble>
            ) : null}

            {card.agent === "helper" && card.status === "running" ? (
              <Bubble side="right" label={card.agentLabel} time={time} tone="helper">
                {mainText ? (
                  <div className={SCROLL_BODY}>{mainText}</div>
                ) : (
                  <p className="text-sm text-white/70">Implementing…</p>
                )}
              </Bubble>
            ) : null}
          </div>
        );
      })}
      <div ref={bottomRef} className="h-px shrink-0" aria-hidden />
    </div>
  );
}

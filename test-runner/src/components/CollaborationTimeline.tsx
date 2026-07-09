import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Monitor,
  User,
  Wrench,
  XCircle,
} from "lucide-react";
import type { AgentRunCard } from "@/lib/collaborationTypes";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* Helpers                                                              */
/* ------------------------------------------------------------------ */

function formatTime(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function formatDuration(card: AgentRunCard): string | null {
  if (!card.completedAt) return null;
  const ms = new Date(card.completedAt).getTime() - new Date(card.startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 1000) return null;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** The brief the helper received, without the (long, repeated) system context. */
function extractBrief(card: AgentRunCard): string | null {
  const user = card.messages?.find((m) => m.role === "user");
  if (!user?.text) return null;
  const marker = "\n---\n";
  const idx = user.text.lastIndexOf(marker);
  return idx >= 0 ? user.text.slice(idx + marker.length).trim() : user.text.trim();
}

function headline(card: AgentRunCard): string {
  if (card.summary?.trim()) return card.summary.trim();
  if (card.status === "running") {
    return card.agent === "helper" ? "Implementing changes…" : "Exploring the live app…";
  }
  switch (card.outcomeType) {
    case "answer":
      return "Answer ready";
    case "prompt":
      return "Handed off to helper";
    case "response":
      return "Helper responded";
    case "note":
      return "Context added";
    default:
      return card.status === "failed" ? "Failed" : "Done";
  }
}

type Badge = { label: string; className: string };

const BADGE_STYLES = {
  info: "border-sky-500/30 bg-sky-950/30 text-sky-200",
  check: "border-emerald-500/30 bg-emerald-950/30 text-emerald-200",
  answer: "border-green-500/30 bg-green-950/30 text-green-200",
  warn: "border-amber-500/30 bg-amber-950/30 text-amber-200",
} as const;

/** Small chips that explain WHAT kind of message this was, derived from its content. */
function cardBadges(card: AgentRunCard): Badge[] {
  const text = card.outcomeText ?? "";
  if (!text.trim()) return [];
  const out: Badge[] = [];
  if (/^#{1,3}\s*Info(?:rmation)?\s+needed/im.test(text)) {
    out.push({ label: "Info requested", className: BADGE_STYLES.info });
  }
  if (/^#{1,3}\s*UI verification request/im.test(text)) {
    out.push({ label: "UI checks requested", className: BADGE_STYLES.check });
  }
  if (/^##\s*Answers from the local testing agent/im.test(text)) {
    out.push({ label: "Info answers", className: BADGE_STYLES.info });
  }
  if (/^##\s*Question from the local testing agent/im.test(text)) {
    out.push({ label: "Question to helper", className: BADGE_STYLES.info });
  }
  if (/^##\s*Escalation/im.test(text)) {
    out.push({ label: "Escalation — rethink", className: BADGE_STYLES.warn });
  }
  if (card.outcomeType === "answer") {
    out.push({ label: "Answer", className: BADGE_STYLES.answer });
  }
  return out;
}

/** First non-heading, non-empty lines of a text — used as the collapsed preview. */
function previewText(text: string, maxChars = 220): string {
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !/^#{1,3}\s/.test(l) && l !== "---");
  const joined = lines.join(" ");
  return joined.length > maxChars ? `${joined.slice(0, maxChars).trimEnd()}…` : joined;
}

/* ------------------------------------------------------------------ */
/* Lightweight markdown-ish rendering (headings become section titles)  */
/* ------------------------------------------------------------------ */

type TextBlock = { title?: string; body: string };

function parseBlocks(text: string): TextBlock[] {
  const blocks: TextBlock[] = [];
  let title: string | undefined;
  let body: string[] = [];
  const flush = () => {
    const joined = body.join("\n").trim();
    if (title || joined) blocks.push({ title, body: joined });
  };
  for (const line of text.split("\n")) {
    const m = line.match(/^#{1,3}\s+(.+)$/);
    if (m) {
      flush();
      title = m[1].trim();
      body = [];
    } else {
      body.push(line);
    }
  }
  flush();
  return blocks;
}

function Markdownish({ text }: { text: string }) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className="space-y-2">
      {blocks.map((block, i) => (
        <div key={i}>
          {block.title ? (
            <p className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/50">{block.title}</p>
          ) : null}
          {block.body ? (
            <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-white/85">{block.body}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Expandable detail sections                                           */
/* ------------------------------------------------------------------ */

type Section = {
  id: string;
  title: string;
  text: string;
  mono?: boolean;
  markdownish?: boolean;
};

function cardSections(card: AgentRunCard): Section[] {
  const sections: Section[] = [];
  const messages = card.messages ?? [];

  const triage = messages.filter((m) => m.role === "triage");
  if (triage.length) {
    sections.push({
      id: "triage",
      title: "Triage decision",
      text: triage.map((m) => m.text).join("\n"),
    });
  }

  if (card.agent === "helper") {
    const brief = extractBrief(card);
    if (brief) {
      sections.push({ id: "brief", title: "Brief the helper received", text: brief, markdownish: true });
    }
  }

  const decisions = messages.filter((m) => m.role === "agent");
  if (decisions.length) {
    sections.push({
      id: "decisions",
      title: `Browser agent decisions (${decisions.length})`,
      text: decisions.map((m) => `• ${m.text}`).join("\n"),
    });
  }

  const pipeline = messages.filter((m) => m.role === "pipeline");
  if (pipeline.length) {
    sections.push({
      id: "pipeline",
      title: "Pipeline failures",
      text: pipeline.map((m) => m.text).join("\n"),
      mono: true,
    });
  }

  const system = messages.filter((m) => m.role === "system");
  if (system.length) {
    sections.push({ id: "system", title: "System notes", text: system.map((m) => m.text).join("\n") });
  }

  if (card.outcomeText?.trim()) {
    sections.push({
      id: "outcome",
      title:
        card.agent === "helper"
          ? "Full helper response"
          : card.outcomeType === "prompt"
            ? "Full message sent to helper"
            : "Full text",
      text: card.outcomeText.trim(),
      markdownish: true,
    });
  }

  return sections;
}

function DetailSection({ section }: { section: Section }) {
  const [open, setOpen] = useState(section.id === "outcome");
  return (
    <div className="rounded-md border border-white/10 bg-black/25">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] font-medium text-white/55 hover:text-white/80"
      >
        {open ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        {section.title}
      </button>
      {open ? (
        <div className="max-h-72 overflow-y-auto border-t border-white/10 px-2.5 py-2 scrollbar-thin">
          {section.markdownish ? (
            <Markdownish text={section.text} />
          ) : (
            <p
              className={cn(
                "whitespace-pre-wrap break-words text-xs leading-relaxed text-white/80",
                section.mono && "font-mono",
              )}
            >
              {section.text}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Live stream (running helper card)                                    */
/* ------------------------------------------------------------------ */

function LiveStream({ status, text }: { status?: string; text?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);

  return (
    <div className="space-y-1.5">
      <p className="inline-flex items-center gap-2 text-xs text-violet-200/85">
        <Loader2 className="size-3 shrink-0 animate-spin" />
        {status?.trim() || "Working…"}
      </p>
      {text?.trim() ? (
        <div
          ref={ref}
          className="max-h-44 overflow-y-auto rounded-md border border-violet-500/20 bg-black/30 px-2.5 py-2 scrollbar-thin"
        >
          <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-violet-100/85">
            {text.trim()}
            <span className="ml-0.5 inline-block animate-pulse text-violet-200/80">▍</span>
          </p>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* One timeline entry                                                   */
/* ------------------------------------------------------------------ */

const ACTOR_STYLE = {
  local: {
    label: "Local agent",
    icon: Monitor,
    iconClass: "border-emerald-500/40 bg-emerald-950/50 text-emerald-300",
    line: "bg-emerald-500/25",
  },
  helper: {
    label: "Helper agent",
    icon: Wrench,
    iconClass: "border-violet-500/40 bg-violet-950/50 text-violet-300",
    line: "bg-violet-500/25",
  },
  user: {
    label: "You",
    icon: User,
    iconClass: "border-amber-500/40 bg-amber-950/50 text-amber-300",
    line: "bg-amber-500/25",
  },
} as const;

function StatusChip({ card }: { card: AgentRunCard }) {
  if (card.status === "running") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-200">
        <Loader2 className="size-2.5 animate-spin" />
        running
      </span>
    );
  }
  if (card.status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-200">
        <XCircle className="size-2.5" />
        failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-200/80">
      <CheckCircle2 className="size-2.5" />
      done
    </span>
  );
}

function TimelineEntry({
  card,
  expanded,
  onToggle,
  isLast,
}: {
  card: AgentRunCard;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}) {
  const actor = ACTOR_STYLE[card.agent] ?? ACTOR_STYLE.local;
  const Icon = actor.icon;
  const running = card.status === "running";
  const badges = cardBadges(card);
  const sections = useMemo(() => cardSections(card), [card]);
  const preview = card.outcomeText?.trim() ? previewText(card.outcomeText) : "";
  const duration = formatDuration(card);
  const hasDetails = sections.length > 0;

  return (
    <div className="relative flex gap-2.5 pl-0.5">
      {/* Timeline gutter: icon + connecting line */}
      <div className="flex flex-col items-center">
        <div
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full border",
            actor.iconClass,
            running && "animate-pulse",
          )}
        >
          <Icon className="size-3.5" />
        </div>
        {!isLast ? <div className={cn("w-px flex-1", actor.line)} /> : null}
      </div>

      {/* Entry body */}
      <div className={cn("min-w-0 flex-1 pb-4", card.historical && "opacity-60")}>
        <div
          className={cn(
            "rounded-lg border border-white/10 bg-white/[0.03]",
            running && "border-sky-500/30 bg-sky-950/10",
            card.status === "failed" && "border-red-500/25 bg-red-950/10",
          )}
        >
          <button
            type="button"
            onClick={hasDetails ? onToggle : undefined}
            className={cn(
              "flex w-full flex-col gap-1 px-3 py-2 text-left",
              hasDetails && "cursor-pointer hover:bg-white/[0.03]",
            )}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-xs font-semibold text-white/90">{card.agentLabel || actor.label}</span>
              <span className="text-[10px] text-white/40">{formatTime(card.startedAt)}</span>
              {duration ? <span className="text-[10px] text-white/35">· {duration}</span> : null}
              {card.historical ? (
                <span className="rounded-full bg-white/10 px-1.5 py-0.5 text-[10px] text-white/50">prior run</span>
              ) : null}
              <span className="ml-auto flex items-center gap-1.5">
                <StatusChip card={card} />
                {hasDetails ? (
                  expanded ? (
                    <ChevronDown className="size-3.5 text-white/40" />
                  ) : (
                    <ChevronRight className="size-3.5 text-white/40" />
                  )
                ) : null}
              </span>
            </div>

            <p className="text-[13px] font-medium leading-snug text-white/90">{headline(card)}</p>

            {badges.length ? (
              <div className="flex flex-wrap gap-1">
                {badges.map((b) => (
                  <span
                    key={b.label}
                    className={cn("rounded-full border px-1.5 py-0.5 text-[10px] font-medium", b.className)}
                  >
                    {b.label}
                  </span>
                ))}
              </div>
            ) : null}

            {!expanded && !running && preview ? (
              <p className="text-xs leading-relaxed text-white/55">{preview}</p>
            ) : null}
          </button>

          {running && card.agent === "helper" ? (
            <div className="border-t border-white/10 px-3 py-2">
              <LiveStream status={card.streamStatus} text={card.streamText} />
            </div>
          ) : null}

          {expanded && hasDetails ? (
            <div className="space-y-1.5 border-t border-white/10 px-3 py-2.5">
              {sections.map((section) => (
                <DetailSection key={`${card.id}-${section.id}`} section={section} />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* The timeline: entries grouped into rounds                            */
/* ------------------------------------------------------------------ */

type Group = { iteration: number; cards: AgentRunCard[] };

function groupByIteration(cards: AgentRunCard[]): Group[] {
  const groups: Group[] = [];
  for (const card of cards) {
    const last = groups[groups.length - 1];
    if (last && last.iteration === card.iteration) {
      last.cards.push(card);
    } else {
      groups.push({ iteration: card.iteration, cards: [card] });
    }
  }
  return groups;
}

type Props = {
  agentCards: AgentRunCard[];
};

export function CollaborationTimeline({ agentCards }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Follow new events only while the user is already at the bottom —
  // never yank the scroll away while they are inspecting history.
  useEffect(() => {
    const el = containerRef.current;
    if (el && stickToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [agentCards]);

  const groups = useMemo(() => groupByIteration(agentCards), [agentCards]);

  if (agentCards.length === 0) return null;

  return (
    <div
      ref={containerRef}
      onScroll={(e) => {
        const el = e.currentTarget;
        stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="flex min-h-0 flex-1 flex-col overflow-y-auto pr-1 scrollbar-thin"
    >
      {groups.map((group) => (
        <div key={`round-${group.iteration}-${group.cards[0]?.id}`}>
          <div className="mb-2.5 mt-1 flex items-center gap-2">
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/55">
              Round {group.iteration}
            </span>
            <div className="h-px flex-1 bg-white/10" />
          </div>
          {group.cards.map((card, index) => (
            <TimelineEntry
              key={card.id}
              card={card}
              expanded={Boolean(expanded[card.id])}
              onToggle={() => setExpanded((prev) => ({ ...prev, [card.id]: !prev[card.id] }))}
              isLast={index === group.cards.length - 1}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
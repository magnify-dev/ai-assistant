import { useEffect, useRef, useState, type ReactNode } from "react";
import { NextActionCard } from "@/components/NextActionCard";
import { OperationWaitBanner } from "@/components/OperationWaitBanner";
import type {
  WebResearchItem,
  WebResearchLlmExchange,
  WebResearchMemoryEntry,
  WebResearchState,
} from "@/lib/webResearchTypes";
import { resolveWebResearchWaitState } from "@/lib/webResearchWait";
import type { WebCaptureBuildStatus } from "@/lib/webCaptureTypes";
import { cn } from "@/lib/utils";

type Props = {
  state: WebResearchState;
  captureBuild?: WebCaptureBuildStatus | null;
  running?: boolean;
};

function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function itemLabel(item: WebResearchItem): string {
  const fact =
    item.fact && typeof item.fact === "object" && !Array.isArray(item.fact)
      ? (item.fact as WebResearchItem)
      : undefined;
  if (fact) {
    const field = text(fact.field ?? "fact");
    const value = text(fact.value);
    const quote = text(fact.quote);
    return `${field}: ${value}${quote ? ` — “${quote}”` : ""}`;
  }
  return text(
    item.label ??
      item.title ??
      item.text ??
      item.url ??
      item.criterion ??
      item.action ??
      item.id ??
      item,
  );
}

function interactableLabel(item: WebResearchItem): string {
  const kind = text(item.kind || item.role || "element");
  const label = text(
    item.text || item.title || item.aria || item.label || item.placeholder || "Unlabelled control",
  );
  const action = text(item.action_hint);
  return `${kind} #${text(item.id)} — ${label}${action ? ` · ${action}` : ""}`;
}

function CollapsibleSection({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-md border border-white/10 bg-black/20">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="text-[10px] font-semibold uppercase tracking-wide text-white/45">
          {title}
          {count != null ? <span className="font-normal"> ({count})</span> : null}
        </span>
        <span className="text-[10px] text-white/35">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <div className="border-t border-white/10 p-3">{children}</div> : null}
    </section>
  );
}

function StepTimeline({ steps }: { steps?: WebResearchItem[] }) {
  const items = steps ?? [];
  if (!items.length) {
    return <p className="text-xs text-white/35">No actions recorded yet.</p>;
  }
  return (
    <ol className="max-h-48 space-y-1 overflow-y-auto text-xs">
      {[...items].slice(-12).reverse().map((item, index) => {
        const ok = item.ok;
        const failed = ok === false || item.progress === false;
        return (
          <li
            key={text(item.step_id ?? item.id ?? index)}
            className={cn(
              "rounded px-2 py-1.5",
              failed ? "border border-rose-400/25 bg-rose-500/10 text-rose-50/90" : "bg-white/[0.03] text-white/75",
            )}
          >
            <div className="flex items-start gap-2">
              <span className="shrink-0 font-mono text-[10px]">{failed ? "✗" : ok === true ? "✓" : "…"}</span>
              <div className="min-w-0">
                <p className="font-medium">
                  {[text(item.action), text(item.target_id)].filter(Boolean).join(" → ")}
                </p>
                {item.error || item.message ? (
                  <p className="mt-0.5 text-[10px] text-white/55">{text(item.error ?? item.message)}</p>
                ) : null}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function InteractablesList({
  items,
  highlightId,
}: {
  items?: WebResearchItem[];
  highlightId?: string;
}) {
  if (!items?.length) {
    return <p className="text-xs text-white/35">Waiting for page controls…</p>;
  }
  return (
    <ul className="max-h-48 space-y-1 overflow-y-auto text-xs">
      {items.slice(-40).map((item, index) => {
        const id = text(item.id ?? index);
        const active = highlightId && id === highlightId;
        return (
          <li
            key={id}
            className={cn(
              "break-words rounded px-2 py-1",
              active
                ? "border border-sky-400/40 bg-sky-500/15 text-sky-50"
                : "bg-white/[0.03] text-white/70",
            )}
          >
            {interactableLabel(item)}
          </li>
        );
      })}
    </ul>
  );
}

function memoryLabel(entry: WebResearchMemoryEntry): string {
  if (entry.summary) return text(entry.summary);
  const decision = entry.decision && typeof entry.decision === "object" ? entry.decision : {};
  const outcome = entry.outcome && typeof entry.outcome === "object" ? entry.outcome : {};
  const action = text(decision.action ?? "step");
  const reason = text(decision.reason);
  const status = text(outcome.status ?? outcome.ok);
  return [text(entry.step_id), action, reason, status].filter(Boolean).join(" — ");
}

function PromptBlock({
  label,
  body,
  tone = "neutral",
}: {
  label: string;
  body: string;
  tone?: "neutral" | "in" | "out" | "error";
}) {
  if (!body) return null;
  const toneClass =
    tone === "in"
      ? "border-sky-400/25 bg-sky-500/5"
      : tone === "out"
        ? "border-emerald-400/25 bg-emerald-500/5"
        : tone === "error"
          ? "border-rose-400/30 bg-rose-500/10"
          : "border-white/10 bg-black/30";
  return (
    <div className={cn("rounded border px-2 py-1.5", toneClass)}>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">{label}</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-white/80">
        {body}
      </pre>
    </div>
  );
}

function LlmExchangeCard({ exchange, index }: { exchange: WebResearchLlmExchange; index: number }) {
  const [expanded, setExpanded] = useState(true);
  const seq = exchange.seq ?? index + 1;
  const title = text(exchange.label ?? exchange.prompt_key) || "model call";
  const meta = [
    exchange.model ? `model ${exchange.model}` : "",
    exchange.step_id ? String(exchange.step_id) : "",
    exchange.url ? String(exchange.url) : "",
  ].filter(Boolean);
  const failed = exchange.ok === false;
  return (
    <li
      className={cn(
        "rounded border px-2 py-2",
        failed ? "border-rose-400/30 bg-rose-500/10" : "border-white/10 bg-white/[0.03]",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full cursor-pointer items-start justify-between gap-2 text-left"
      >
        <div className="min-w-0">
          <p className="text-xs font-medium text-white/85">
            <span className="font-mono text-[10px] text-white/45">#{seq}</span> {title}
            {failed ? (
              <span className="ml-2 text-rose-200">failed</span>
            ) : (
              <span className="ml-2 text-emerald-200/80">ok</span>
            )}
            {exchange.truncated ? (
              <span className="ml-2 text-[10px] font-normal text-amber-100/70">truncated</span>
            ) : null}
          </p>
          {meta.length ? (
            <p className="mt-0.5 truncate text-[10px] text-white/45">{meta.join(" · ")}</p>
          ) : null}
        </div>
        <span className="shrink-0 text-[10px] text-white/35">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded ? (
        <div className="mt-2 space-y-2">
          <PromptBlock label="System prompt" body={text(exchange.system_prompt)} tone="neutral" />
          <PromptBlock label="Input to local AI" body={text(exchange.user_input)} tone="in" />
          <PromptBlock label="Output from local AI" body={text(exchange.response)} tone="out" />
          {failed && exchange.error ? (
            <PromptBlock label="Error" body={text(exchange.error)} tone="error" />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function LlmPromptTrace({
  exchanges,
  followLatest = true,
}: {
  exchanges?: WebResearchLlmExchange[];
  followLatest?: boolean;
}) {
  const items = [...(exchanges ?? [])].sort((a, b) => Number(a.seq ?? 0) - Number(b.seq ?? 0));
  const listRef = useRef<HTMLOListElement | null>(null);
  const lastSeq = items.length ? Number(items[items.length - 1]?.seq ?? items.length) : 0;

  useEffect(() => {
    if (!followLatest || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [followLatest, lastSeq, items.length]);

  if (!items.length) {
    return <p className="text-xs text-white/35">Waiting for local AI prompts…</p>;
  }

  // Newest last so the live tail stays at the bottom while auto-scrolling.
  const visible = items.slice(-40);
  return (
    <div className="space-y-2">
      <p className="text-[10px] text-white/40">
        Live prompt history for the local model — expands as each call finishes.
        {items.length > visible.length ? ` Showing last ${visible.length} of ${items.length}.` : null}
      </p>
      <ol ref={listRef} className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
        {visible.map((exchange, index) => (
          <LlmExchangeCard
            key={String(exchange.seq ?? `${exchange.prompt_key}-${index}`)}
            exchange={exchange}
            index={index}
          />
        ))}
      </ol>
    </div>
  );
}

export function WebResearchPanel({ state, captureBuild, running = true }: Props) {
  const wait = resolveWebResearchWaitState(state, captureBuild, running);
  const snapshot = state.snapshot;
  const interactables = (snapshot?.interactables ?? []) as WebResearchItem[];
  const blockers = Array.isArray(snapshot?.blocking_overlays)
    ? (snapshot.blocking_overlays as WebResearchItem[])
    : [];
  const highlightId = wait?.targetId || text(state.decision?.target);
  const semantic = text(
    snapshot?.semantic_snapshot ??
      snapshot?.semanticSnapshot ??
      snapshot?.visible_text ??
      snapshot?.text,
  );

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Web exploration</h2>
      </div>

      {wait ? <OperationWaitBanner wait={wait} /> : null}

      <NextActionCard state={state} />

      <CollapsibleSection
        title="Prompt history (local AI)"
        count={state.llmExchanges?.length ?? 0}
        defaultOpen={running || (state.llmExchanges?.length ?? 0) > 0}
      >
        <LlmPromptTrace exchanges={state.llmExchanges} followLatest={running} />
      </CollapsibleSection>

      {blockers.length ? (
        <section className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3">
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-100">
            Blocking overlay
          </h3>
          <p className="text-xs text-amber-50/85">{blockers.map(itemLabel).join(" · ")}</p>
        </section>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-2">
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">Action history</h3>
          <StepTimeline steps={state.steps} />
        </section>
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">
            Menu targets
            {highlightId ? (
              <span className="ml-2 font-normal normal-case text-sky-200/80">→ #{highlightId}</span>
            ) : null}
          </h3>
          <InteractablesList items={interactables} highlightId={highlightId} />
        </section>
      </div>

      {state.answer ? (
        <section className="rounded-md border border-emerald-400/25 bg-emerald-400/5 p-3">
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-100">Answer</h3>
          <p className="whitespace-pre-wrap text-sm text-emerald-50/90">{state.answer}</p>
        </section>
      ) : null}

      <CollapsibleSection title="Page text" count={semantic ? semantic.length : undefined} defaultOpen>
        <p className="mb-1 text-[10px] text-white/40">
          {snapshot?.visible_text
            ? `${String(snapshot.visible_text).length.toLocaleString()} chars captured from the visible page`
            : "Waiting for page text…"}
        </p>
        <p className="max-h-56 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-white/65">
          {semantic || "No page text captured yet."}
        </p>
      </CollapsibleSection>

      <CollapsibleSection title="Evidence & criteria" count={(state.evidence?.length ?? 0) + (state.unmetCriteria?.length ?? 0)}>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <p className="mb-1 text-[10px] uppercase text-white/40">Evidence ({state.evidence?.length ?? 0})</p>
            {state.evidence?.length ? (
              <ul className="max-h-32 space-y-1 overflow-y-auto text-xs text-white/70">
                {state.evidence.slice(-10).map((item, index) => (
                  <li key={text(item.id ?? index)} className="rounded bg-white/[0.03] px-2 py-1">
                    {itemLabel(item)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-white/35">None yet.</p>
            )}
          </div>
          <div>
            <p className="mb-1 text-[10px] uppercase text-white/40">Unmet criteria</p>
            {state.unmetCriteria?.length ? (
              <ul className="space-y-1 text-xs text-white/70">
                {state.unmetCriteria.map((criterion) => (
                  <li key={criterion} className="rounded bg-white/[0.03] px-2 py-1">
                    {criterion}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-white/35">All criteria met or not reported.</p>
            )}
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="Agent memory" count={state.agentMemory?.length ?? 0}>
        {state.agentMemory?.length ? (
          <ol className="max-h-48 space-y-1 overflow-y-auto text-xs text-white/75">
            {state.agentMemory.slice(-20).map((entry, index) => (
              <li key={text(entry.step_id ?? index)} className="rounded bg-white/[0.03] px-2 py-1 font-mono text-[11px]">
                {memoryLabel(entry)}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-xs text-white/35">No memory entries yet.</p>
        )}
      </CollapsibleSection>

    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import type {
  WebResearchItem,
  WebResearchLlmExchange,
  WebResearchMemoryEntry,
  WebResearchState,
} from "@/lib/webResearchTypes";

type Props = {
  state: WebResearchState;
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
  const label = text(item.text || item.aria || item.label || item.placeholder || "Unlabelled control");
  const action = text(item.action_hint);
  const href = text(item.href);
  return `${kind} #${text(item.id)} — ${label}${action ? ` · ${action}` : href ? ` · Opens ${href}` : ""}`;
}

function InteractablesSection({ items }: { items?: WebResearchItem[] }) {
  return (
    <section className="rounded-md border border-white/10 bg-black/20 p-3">
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">
        Available controls <span className="font-normal">({items?.length ?? 0})</span>
      </h3>
      <p className="mb-2 text-xs text-white/45">
        Each control maps its stable ID to the action it performs. The agent chooses its next step from this list.
      </p>
      {items?.length ? (
        <ul className="max-h-40 space-y-1 overflow-y-auto text-xs text-white/70">
          {items.slice(-30).map((item, index) => (
            <li key={text(item.id ?? index)} className="break-words rounded bg-white/[0.03] px-2 py-1">
              {interactableLabel(item)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-white/35">Waiting for the page controls…</p>
      )}
    </section>
  );
}

function ListSection({
  title,
  items,
  empty,
}: {
  title: string;
  items?: WebResearchItem[];
  empty?: string;
}) {
  return (
    <section className="rounded-md border border-white/10 bg-black/20 p-3">
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">
        {title} <span className="font-normal">({items?.length ?? 0})</span>
      </h3>
      {items?.length ? (
        <ul className="max-h-40 space-y-1 overflow-y-auto text-xs text-white/70">
          {items.slice(-30).map((item, index) => (
            <li key={text(item.id ?? item.url ?? index)} className="break-words rounded bg-white/[0.03] px-2 py-1">
              {itemLabel(item)}
              {item.status ? <span className="ml-2 text-white/40">{text(item.status)}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-white/35">{empty ?? "Waiting…"}</p>
      )}
    </section>
  );
}

function isRunFinished(state: WebResearchState): boolean {
  if (state.runFinished) return true;
  if (state.answer) return true;
  const status = text(state.controller?.status ?? state.progress?.step);
  return ["complete", "incomplete", "blocked"].includes(status);
}

function exchangeMeta(exchange: WebResearchLlmExchange): string {
  const parts = [
    exchange.label ?? exchange.prompt_key,
    exchange.step_id ? `step ${exchange.step_id}` : "",
    exchange.url ? exchange.url : "",
    exchange.ok === false ? "failed" : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function PromptBlock({ title, body, tone }: { title: string; body: string; tone: "in" | "out" }) {
  if (!body) return null;
  const toneClass =
    tone === "in"
      ? "border-white/10 bg-black/30 text-white/75"
      : "border-emerald-400/20 bg-emerald-400/5 text-emerald-50/90";
  return (
    <div className={`rounded border px-2 py-2 ${toneClass}`}>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/40">{title}</p>
      <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">
        {body}
      </pre>
    </div>
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

function AgentMemorySection({
  entries,
  finished,
}: {
  entries?: WebResearchMemoryEntry[];
  finished: boolean;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const stickToBottom = useRef(true);
  const items = entries ?? [];

  useEffect(() => {
    const el = listRef.current;
    if (el && stickToBottom.current && !finished) {
      el.scrollTop = el.scrollHeight;
    }
  }, [items.length, finished]);

  return (
    <section className="rounded-md border border-cyan-400/25 bg-cyan-400/5 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-wide text-cyan-100/80">
            Agent memory
          </h3>
          <p className="mt-1 text-xs text-white/50">
            Structured log of every decision and outcome — injected into each new prompt.
          </p>
        </div>
        <span className="rounded-full bg-cyan-400/15 px-2 py-0.5 text-[10px] text-cyan-50/90">
          {items.length} steps
        </span>
      </div>
      {items.length ? (
        <ol
          ref={listRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
          className="max-h-56 space-y-1 overflow-y-auto overscroll-y-contain pr-1 text-xs text-white/75"
        >
          {items.map((entry, index) => (
            <li
              key={text(entry.step_id ?? index)}
              className="rounded border border-white/10 bg-black/25 px-2 py-1.5"
            >
              <p className="font-mono text-[11px] leading-relaxed">{memoryLabel(entry)}</p>
              {entry.page_url ? (
                <p className="mt-1 truncate text-[10px] text-white/40">{text(entry.page_url)}</p>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-xs text-white/35">
          {finished ? "No agent memory recorded." : "Waiting for first completed step…"}
        </p>
      )}
    </section>
  );
}

function LlmPromptTrace({ exchanges, finished }: { exchanges?: WebResearchLlmExchange[]; finished: boolean }) {
  const listRef = useRef<HTMLOListElement>(null);
  const stickToBottom = useRef(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const items = [...(exchanges ?? [])].sort((a, b) => Number(a.seq ?? 0) - Number(b.seq ?? 0));

  useEffect(() => {
    if (!items.length) return;
    const latestKey = String(items[items.length - 1].seq ?? items.length - 1);
    setExpanded((prev) => ({ ...prev, [latestKey]: true }));
  }, [items.length, items[items.length - 1]?.seq]);

  useEffect(() => {
    const el = listRef.current;
    if (el && stickToBottom.current && !finished) {
      el.scrollTop = el.scrollHeight;
    }
  }, [items, finished, expanded]);

  if (!items.length) {
    if (!finished) return null;
    return (
      <section className="rounded-md border border-white/10 bg-black/20 p-3">
        <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">Local AI prompt trace</h3>
        <p className="text-xs text-white/35">No Ollama exchanges were recorded for this run.</p>
      </section>
    );
  }

  return (
    <section
      className={`rounded-md border p-3 ${
        finished ? "border-violet-400/30 bg-violet-400/5" : "border-white/10 bg-black/20"
      }`}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-wide text-white/45">Local AI prompt trace</h3>
          <p className="mt-1 text-xs text-white/50">
            {finished
              ? "Ordered prompt inputs and model outputs from this run."
              : "Recording exchanges as the run progresses…"}
          </p>
        </div>
        <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/70">{items.length} calls</span>
      </div>
      <ol
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className="max-h-[32rem] min-h-0 space-y-2 overflow-y-auto overscroll-y-contain pr-1 scrollbar-thin"
      >
        {items.map((exchange, index) => {
          const input = [
            exchange.system_prompt ? `SYSTEM\n${exchange.system_prompt}` : "",
            exchange.user_input ? `USER\n${exchange.user_input}` : "",
          ]
            .filter(Boolean)
            .join("\n\n");
          const key = String(exchange.seq ?? index);
          const isExpanded = Boolean(expanded[key]);
          return (
            <li key={key} className="rounded-md border border-white/10 bg-black/25">
              <button
                type="button"
                onClick={() => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))}
                className="flex w-full cursor-pointer items-start gap-2 px-3 py-2 text-left text-xs text-white/80"
              >
                <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/60">
                  #{exchange.seq ?? index + 1}
                </span>
                <span className="min-w-0 flex-1 font-medium text-white/85">{exchangeMeta(exchange)}</span>
                {exchange.ok === false ? (
                  <span className="shrink-0 rounded-full bg-rose-500/15 px-2 py-0.5 text-[10px] text-rose-100">failed</span>
                ) : (
                  <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] text-emerald-100">ok</span>
                )}
                {exchange.truncated ? (
                  <span className="shrink-0 text-[10px] text-white/40">truncated</span>
                ) : null}
                <span className="shrink-0 text-[10px] text-white/40">{isExpanded ? "▾" : "▸"}</span>
              </button>
              {isExpanded ? (
                <div className="space-y-2 border-t border-white/10 px-3 py-3">
                  <PromptBlock title="Input" body={input} tone="in" />
                  <PromptBlock title="Output" body={text(exchange.response)} tone="out" />
                  {exchange.error ? (
                    <p className="text-xs text-rose-200/90">Error: {text(exchange.error)}</p>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function WebResearchPanel({ state }: Props) {
  const snapshot = state.snapshot;
  const semantic = text(
    snapshot?.semantic_snapshot ??
      snapshot?.semanticSnapshot ??
      snapshot?.visible_text ??
      snapshot?.text,
  );
  const interactables = snapshot?.interactables ?? [];
  const blockers = Array.isArray(snapshot?.blocking_overlays)
    ? (snapshot.blocking_overlays as WebResearchItem[])
    : [];
  const controllerStatus = text(
    state.controller?.status ?? state.controller?.phase ?? state.progress?.step ?? "running",
  );
  const graphNodes = Array.isArray(state.visitGraph?.nodes)
    ? state.visitGraph.nodes
    : state.visitGraph?.nodes && typeof state.visitGraph.nodes === "object"
      ? Object.entries(state.visitGraph.nodes).map(([id, value]) => ({
          id,
          ...(typeof value === "object" && value ? value : { label: text(value) }),
        }))
      : [];
  const graphEdges = Array.isArray(state.visitGraph?.edges) ? state.visitGraph.edges : [];
  const runFinished = isRunFinished(state);

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Stepwise web exploration</h2>
          <p className="mt-1 truncate font-mono text-xs text-sky-200/80">
            {state.currentUrl ?? snapshot?.url ?? state.progress?.url ?? "Waiting for browser…"}
          </p>
        </div>
        <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] text-sky-100">
          {controllerStatus}
        </span>
      </div>

      {snapshot?.screenshot_b64 ? (
        <div className="overflow-hidden rounded-md border border-white/10 bg-black/30 p-1">
          <img
            src={`data:image/jpeg;base64,${snapshot.screenshot_b64}`}
            alt="Current web exploration page"
            className="max-h-72 w-full object-contain"
          />
        </div>
      ) : null}

      {blockers.length ? (
        <section className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3">
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-100">
            Blocking overlay detected
          </h3>
          <p className="text-xs text-amber-50/85">
            {blockers.map(itemLabel).join(" · ")}
          </p>
        </section>
      ) : null}

      <div className="grid gap-3 xl:grid-cols-2">
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">Controller</h3>
          <p className="text-xs text-white/70">
            {text(state.controller?.reason ?? state.progress?.message ?? state.controller) || "Waiting for state…"}
          </p>
        </section>
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">Latest decision</h3>
          <p className="text-xs text-white/75">
            {state.decision
              ? [state.decision.action, state.decision.target, state.decision.reason].filter(Boolean).map(text).join(" — ")
              : "Waiting for decision…"}
          </p>
        </section>
      </div>

      <section className="rounded-md border border-white/10 bg-black/20 p-3">
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">Semantic snapshot</h3>
        <p className="max-h-36 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-white/65">
          {semantic || "No semantic snapshot yet."}
        </p>
      </section>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <InteractablesSection items={interactables as WebResearchItem[]} />
        <ListSection title="Steps" items={state.steps} />
        <ListSection title="Candidates" items={state.candidates} />
        <ListSection title="Evidence" items={state.evidence} />
        <ListSection
          title="Unmet criteria"
          items={state.unmetCriteria?.map((criterion) => ({ text: criterion }))}
          empty="All reported criteria are met."
        />
        <ListSection title="Helper exchange" items={state.helperExchanges} />
        <ListSection
          title="Form value plans"
          items={state.formValuePlans}
          empty="No AI-generated form values yet."
        />
        <ListSection title="State changes" items={state.transitions} empty="No action transition captured yet." />
      </div>

      <section className="rounded-md border border-white/10 bg-black/20 p-3">
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">
          Visit graph ({graphNodes.length} nodes / {graphEdges.length} edges)
        </h3>
        <div className="grid gap-2 md:grid-cols-2">
          <ListSection title="Visited pages" items={graphNodes} empty="No visits reported." />
          <ListSection title="Transitions" items={graphEdges} empty="No transitions reported." />
        </div>
      </section>

      <AgentMemorySection entries={state.agentMemory} finished={runFinished} />

      <LlmPromptTrace exchanges={state.llmExchanges} finished={runFinished} />
    </div>
  );
}

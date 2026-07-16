import { useState, type ReactNode } from "react";
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
  const label = text(item.text || item.aria || item.label || item.placeholder || "Unlabelled control");
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

function LlmPromptTrace({ exchanges }: { exchanges?: WebResearchLlmExchange[] }) {
  const items = [...(exchanges ?? [])].sort((a, b) => Number(a.seq ?? 0) - Number(b.seq ?? 0));
  if (!items.length) {
    return <p className="text-xs text-white/35">No model calls recorded yet.</p>;
  }
  return (
    <ol className="max-h-56 space-y-1 overflow-y-auto text-xs text-white/70">
      {items.slice(-8).reverse().map((exchange, index) => (
        <li key={String(exchange.seq ?? index)} className="rounded bg-white/[0.03] px-2 py-1">
          <span className="font-mono text-[10px] text-white/45">#{exchange.seq ?? index + 1}</span>{" "}
          {text(exchange.label ?? exchange.prompt_key)}
          {exchange.ok === false ? (
            <span className="ml-2 text-rose-200">failed</span>
          ) : (
            <span className="ml-2 text-emerald-200/80">ok</span>
          )}
        </li>
      ))}
    </ol>
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

      {blockers.length ? (
        <section className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3">
          <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-100">
            Blocking overlay
          </h3>
          <p className="text-xs text-amber-50/85">{blockers.map(itemLabel).join(" · ")}</p>
        </section>
      ) : null}

      {snapshot?.screenshot_b64 ? (
        <div className="overflow-hidden rounded-md border border-white/10 bg-black/30 p-1">
          <img
            src={`data:image/jpeg;base64,${snapshot.screenshot_b64}`}
            alt="Current web exploration page"
            className="max-h-80 w-full object-contain"
          />
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-2">
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">Recent actions</h3>
          <StepTimeline steps={state.steps} />
        </section>
        <section className="rounded-md border border-white/10 bg-black/20 p-3">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-white/45">
            Page controls
            {highlightId ? (
              <span className="ml-2 font-normal normal-case text-sky-200/80">target #{highlightId}</span>
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

      <CollapsibleSection title="Model calls" count={state.llmExchanges?.length ?? 0}>
        <LlmPromptTrace exchanges={state.llmExchanges} />
      </CollapsibleSection>
    </div>
  );
}

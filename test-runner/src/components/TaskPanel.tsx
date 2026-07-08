import type { StructuredTask } from "@/lib/projectTypes";
import { cn } from "@/lib/utils";

type Props = {
  structuredTask: StructuredTask | null;
  running?: boolean;
};

export function TaskPanel({ structuredTask, running = false }: Props) {
  if (!structuredTask) {
    return (
      <p className="text-xs text-white/50">
        {running
          ? "Structuring task with Ollama…"
          : "Task interpretation from the current run will appear here."}
      </p>
    );
  }

  const prompt = structuredTask.source_text?.trim() || "";
  const summary = structuredTask.summary?.trim();
  const criteria = structuredTask.success_criteria ?? [];
  const deliverables = structuredTask.deliverables ?? [];
  const scopeUrls = structuredTask.scope_urls ?? [];
  const notes = structuredTask.notes_for_cursor ?? [];
  const suggested = structuredTask.suggested_steps ?? [];
  const gaps = structuredTask.intent_gaps ?? [];
  const specRuns = structuredTask.spec_runs;

  const summaryDiffers =
    summary &&
    prompt &&
    !prompt.toLowerCase().includes(summary.toLowerCase().slice(0, 40)) &&
    summary.toLowerCase() !== prompt.toLowerCase();

  return (
    <details open className="group rounded-md border border-white/10 bg-black/20">
      <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium text-white/90 marker:content-none">
        <span>Task (this run)</span>
        {gaps.length > 0 ? (
          <p className="mt-1 text-xs font-normal text-amber-300/90">Alignment warning — structured task may not match your prompt</p>
        ) : summary ? (
          <p className="mt-1 text-xs font-normal text-sky-200/90">{summary}</p>
        ) : null}
      </summary>
      <div className="space-y-3 border-t border-white/10 px-3 py-3 text-xs text-white/80">
        {prompt ? (
          <div className="rounded border border-white/10 bg-white/5 px-2 py-2">
            <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Your prompt (source of truth)</p>
            <p className="whitespace-pre-wrap leading-relaxed text-white/90">{prompt}</p>
          </div>
        ) : null}

        {summary && summaryDiffers ? (
          <div className="rounded border border-sky-500/20 bg-sky-950/20 px-2 py-2">
            <p className="mb-1 text-[10px] uppercase tracking-wide text-sky-300/70">Structured interpretation</p>
            <p className="text-sky-100/90">{summary}</p>
            <p className="mt-1 text-[10px] text-white/45">
              Ollama rephrased your goal — compare with your prompt above.
            </p>
          </div>
        ) : null}

        {gaps.length > 0 ? (
          <div className="rounded border border-amber-500/30 bg-amber-950/20 px-2 py-2">
            <p className="mb-1 text-[10px] uppercase tracking-wide text-amber-300/80">Intent gaps</p>
            <ul className="list-disc space-y-1 pl-4 text-amber-100/90">
              {gaps.map((g) => (
                <li key={g}>{g}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {specRuns ? (
          <div className="rounded border border-violet-500/20 bg-violet-950/15 px-2 py-2">
            <p className="mb-1 text-[10px] uppercase tracking-wide text-violet-300/70">
              {specRuns.includes("Exploration mode") ? "How this run executes" : "What Playwright actually runs"}
            </p>
            <p className="font-mono text-[11px] text-violet-100/85">{specRuns}</p>
            <p className="mt-1 text-[10px] text-white/45">
              {specRuns.includes("Exploration mode")
                ? "Discover → decide → act → evaluate. Exploration map in .agent/exploration.yaml grows each run."
                : "The YAML spec executes on each run — not the suggested steps below unless the spec is updated."}
            </p>
          </div>
        ) : null}

        {deliverables.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Deliverables</p>
            <ul className="list-disc space-y-1 pl-4">
              {deliverables.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {criteria.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Success criteria</p>
            <ul className="list-disc space-y-1 pl-4">
              {criteria.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {scopeUrls.length > 0 ? (
          <p className="font-mono text-[11px] text-white/55">Scope: {scopeUrls.join(", ")}</p>
        ) : null}

        {suggested.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Suggested steps (advisory)</p>
            <ul className="space-y-1">
              {suggested.map((step, i) => (
                <li key={i} className={cn("rounded border border-white/5 px-2 py-1 text-white/70")}>
                  {step.action}: {step.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {notes.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Notes for Cursor</p>
            <ul className="list-disc space-y-1 pl-4 text-white/65">
              {notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </details>
  );
}

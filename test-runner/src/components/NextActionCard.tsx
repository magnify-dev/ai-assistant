import { cn } from "@/lib/utils";
import type { WebResearchDecisionProcess, WebResearchState } from "@/lib/webResearchTypes";

type Props = {
  state: WebResearchState;
  className?: string;
};

function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

function distillProcess(state: WebResearchState): WebResearchDecisionProcess | null {
  if (state.decisionProcess && typeof state.decisionProcess === "object") {
    return state.decisionProcess;
  }
  const decision = state.decision;
  if (!decision) return null;
  return {
    goal: text(state.query),
    action: text(decision.action),
    target_id: text(decision.target_id ?? decision.target),
    reason: text(decision.reason),
    page_url: text(state.currentUrl ?? state.snapshot?.url),
  };
}

/** One clear card: plan step → chosen action → why. */
export function NextActionCard({ state, className }: Props) {
  const process = distillProcess(state);
  const controller = state.controller;
  const status = text(controller?.status || controller?.phase || "");
  const planSteps = Array.isArray(state.accomplishmentSteps) ? state.accomplishmentSteps : [];
  const lastStep = state.steps?.length ? state.steps[state.steps.length - 1] : undefined;
  const lastOutcome =
    lastStep == null
      ? ""
      : lastStep.ok === false
        ? `Failed: ${text(lastStep.error ?? lastStep.message ?? "error")}`
        : lastStep.ok === true
          ? "Succeeded"
          : status === "deciding" || status === "next_action"
            ? "Deciding…"
            : "";

  if (!process && !status && !lastStep) {
    return (
      <section className={cn("rounded-md border border-white/10 bg-black/25 p-3", className)}>
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-white/45">Next action</h3>
        <p className="mt-2 text-xs text-white/40">Waiting for the agent to decide…</p>
      </section>
    );
  }

  const action = text(process?.action || lastStep?.action);
  const target = text(process?.target_id || lastStep?.target_id);
  const reason = text(process?.reason || lastStep?.reason);
  const current = process?.current_step;
  const goal = text(process?.goal || state.query);

  return (
    <section
      className={cn(
        "rounded-md border border-sky-400/25 bg-sky-500/10 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]",
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-sky-100/80">
          Next action
        </h3>
        {status ? (
          <span className="rounded-full bg-black/25 px-2 py-0.5 font-mono text-[10px] text-sky-50/70">
            {status}
          </span>
        ) : null}
      </div>

      {goal ? (
        <p className="mt-2 text-[11px] leading-snug text-white/55">
          <span className="text-white/35">Goal · </span>
          {goal}
        </p>
      ) : null}

      {current?.description ? (
        <div className="mt-2 rounded border border-white/10 bg-black/20 px-2.5 py-2">
          <p className="text-[10px] uppercase tracking-wide text-white/40">Working on plan step</p>
          <p className="mt-0.5 text-sm font-medium text-white/90">
            {current.id ? `${current.id}: ` : ""}
            {current.description}
          </p>
          {current.done_when ? (
            <p className="mt-1 text-[11px] text-white/50">Done when: {current.done_when}</p>
          ) : null}
        </div>
      ) : null}

      {planSteps.length ? (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {planSteps.map((step, index) => {
            const id = text(step.id || index);
            const done = text(step.status) === "done";
            const active = text(current?.id) === id;
            return (
              <li
                key={id}
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px]",
                  done
                    ? "bg-emerald-500/15 text-emerald-100"
                    : active
                      ? "bg-sky-500/25 text-sky-50 ring-1 ring-sky-300/40"
                      : "bg-white/5 text-white/45",
                )}
                title={text(step.description)}
              >
                {done ? "✓ " : active ? "→ " : ""}
                {text(step.description).slice(0, 36)}
              </li>
            );
          })}
        </ol>
      ) : null}

      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-start">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-white/40">AI chose</p>
          <p className="mt-0.5 text-base font-semibold tracking-tight text-white">
            {action || "…"}
            {target ? (
              <span className="ml-2 font-mono text-sm font-normal text-sky-100/80">→ {target}</span>
            ) : null}
          </p>
          {process?.url ? (
            <p className="mt-1 truncate font-mono text-[11px] text-sky-100/60">{process.url}</p>
          ) : null}
        </div>
        {lastOutcome ? (
          <span
            className={cn(
              "rounded-md px-2 py-1 text-[11px]",
              lastStep?.ok === false
                ? "bg-rose-500/15 text-rose-100"
                : lastStep?.ok === true
                  ? "bg-emerald-500/15 text-emerald-100"
                  : "bg-white/10 text-white/70",
            )}
          >
            {lastOutcome}
          </span>
        ) : null}
      </div>

      {reason ? (
        <p className="mt-2 text-sm leading-relaxed text-white/75">
          <span className="text-white/40">Why · </span>
          {reason}
        </p>
      ) : null}

      {process?.page_url ? (
        <p className="mt-2 truncate font-mono text-[10px] text-white/35">{process.page_url}</p>
      ) : null}
    </section>
  );
}

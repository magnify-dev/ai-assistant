import type { BrowserState, InteractableElement } from "@/lib/projectTypes";
import { cn } from "@/lib/utils";

type Props = {
  state: BrowserState | null;
  lastStep?: { action?: string; target?: string; ok?: boolean; page_url?: string; message?: string } | null;
};

function labelFor(el: InteractableElement): string {
  if (el.test_id) return `[testid] ${el.test_id}`;
  if (el.text) return el.text;
  if (el.aria) return el.aria;
  if (el.placeholder) return el.placeholder;
  if (el.href) return el.href.slice(0, 60);
  return el.kind;
}

export function BrowserStatePanel({ state, lastStep }: Props) {
  if (!state) {
    return (
      <div className="space-y-2 text-xs text-white/50">
        <p>Browser preview and interactables appear here during Playwright runs.</p>
        {lastStep ? (
          <p className="text-white/40">
            Last step: {lastStep.action} {lastStep.target}{" "}
            {lastStep.ok ? "✓" : "✗"} {lastStep.message ?? ""}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {state.screenshot_b64 ? (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Page preview</p>
          <div className="inline-block max-w-full overflow-hidden rounded-md border border-white/10 bg-white p-0.5">
            <img
              src={`data:image/jpeg;base64,${state.screenshot_b64}`}
              alt="Playwright page preview"
              className="mx-auto block max-h-64 w-auto max-w-full"
            />
          </div>
        </div>
      ) : null}

      <div>
        <p className="text-[10px] uppercase tracking-wide text-white/40">Current URL</p>
        <p className="break-all font-mono text-xs text-sky-200/90">{state.url || "(not loaded)"}</p>
        {state.title ? <p className="mt-1 text-xs text-white/50">{state.title}</p> : null}
        {state.context ? (
          <p className="mt-1 text-[10px] text-white/40">Context: {state.context}</p>
        ) : null}
        {state.error ? (
          <p className="mt-1 rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-200/90">
            {state.error}
          </p>
        ) : null}
      </div>

      {lastStep ? (
        <div
          className={cn(
            "rounded border px-2 py-1.5 text-xs",
            lastStep.ok
              ? "border-green-500/30 bg-green-500/10 text-green-200/90"
              : "border-red-500/30 bg-red-500/10 text-red-200/90",
          )}
        >
          Last action: {lastStep.action} → {lastStep.target}{" "}
          {lastStep.ok ? "succeeded" : "failed"}
          {lastStep.message ? ` — ${lastStep.message}` : ""}
        </div>
      ) : null}

      <div>
        <p className="mb-2 text-[10px] uppercase tracking-wide text-white/40">
          Interactables ({state.interactables.length})
        </p>
        <ul className="max-h-64 space-y-1 overflow-auto pr-1">
          {state.interactables.map((el) => (
            <li
              key={el.index}
              className={cn(
                "rounded border border-white/5 bg-black/20 px-2 py-1.5 text-xs",
                el.disabled && "opacity-50",
              )}
            >
              <div className="flex items-start gap-2">
                <span className="shrink-0 rounded bg-white/10 px-1 py-0.5 font-mono text-[10px] text-white/60">
                  {el.kind}
                </span>
                <span className="min-w-0 flex-1 truncate text-white/85">{labelFor(el)}</span>
              </div>
              {el.test_id ? (
                <p className="mt-0.5 font-mono text-[10px] text-violet-300/80">data-testid={el.test_id}</p>
              ) : null}
            </li>
          ))}
          {state.interactables.length === 0 ? (
            <li className="text-xs text-white/40">No visible interactables on this page.</li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}

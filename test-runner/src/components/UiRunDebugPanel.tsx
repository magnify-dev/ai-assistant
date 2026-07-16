import { useEffect, useState } from "react";
import {
  clearUiDebugLog,
  isUiDebugEnabled,
  readUiDebugLog,
  type UiDisplaySnapshot,
} from "@/lib/uiRunDebug";

function formatVisible(entry: UiDisplaySnapshot): string {
  return Object.entries(entry.visible)
    .filter(([, on]) => on)
    .map(([name]) => name)
    .join(", ");
}

export function UiRunDebugPanel({ current }: { current: Omit<UiDisplaySnapshot, "ts"> | null }) {
  const [open, setOpen] = useState(true);
  const [history, setHistory] = useState<UiDisplaySnapshot[]>(() => readUiDebugLog());

  useEffect(() => {
    if (!isUiDebugEnabled()) return;

    const onEntry = (event: Event) => {
      const detail = (event as CustomEvent<UiDisplaySnapshot>).detail;
      if (!detail) return;
      setHistory((prev) => [...prev.slice(-119), detail]);
    };

    window.addEventListener("test-runner-ui-debug", onEntry);
    return () => window.removeEventListener("test-runner-ui-debug", onEntry);
  }, []);

  if (!isUiDebugEnabled()) return null;

  const latest = history[history.length - 1];

  return (
    <div className="fixed bottom-3 right-3 z-50 w-[min(420px,calc(100vw-1.5rem))] rounded-lg border border-amber-500/35 bg-black/90 shadow-xl backdrop-blur-sm">
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
        <p className="text-xs font-semibold text-amber-100">UI run debug</p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              clearUiDebugLog();
              setHistory([]);
            }}
            className="text-[10px] text-white/50 hover:text-white/80"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[10px] text-white/50 hover:text-white/80"
          >
            {open ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      {open ? (
        <div className="max-h-72 space-y-2 overflow-auto p-3 text-[10px] leading-relaxed text-white/75">
          {current ? (
            <div className="rounded border border-sky-500/25 bg-sky-950/20 p-2">
              <p className="font-semibold text-sky-100">Now showing</p>
              <p className="mt-1 font-mono text-white/85">
                {current.showConfigPanel ? "config panel" : "run view"} · inRunMode=
                {String(current.inRunMode)} · running={String(current.running)} · starting=
                {String(current.startingRun)}
              </p>
              <p className="mt-1 text-white/60">{formatVisible({ ...current, ts: "" })}</p>
            </div>
          ) : null}

          <div>
            <p className="mb-1 font-semibold text-white/55">Recent transitions</p>
            <ul className="space-y-1">
              {[...history].reverse().slice(0, 12).map((entry, index) => (
                <li key={`${entry.ts}-${index}`} className="rounded border border-white/10 bg-white/[0.03] px-2 py-1">
                  <p className="text-white/85">
                    <span className="text-amber-200/90">{entry.trigger}</span>
                    {" → "}
                    {entry.showConfigPanel ? "config" : "run"}
                    {entry.note ? ` (${entry.note})` : ""}
                  </p>
                  <p className="text-white/45">{formatVisible(entry) || "—"}</p>
                </li>
              ))}
              {!history.length && !current ? (
                <li className="text-white/45">Click Run to record UI transitions.</li>
              ) : null}
            </ul>
          </div>

          {latest ? (
            <p className="text-white/35">
              Tip: add <code className="text-white/55">?uiDebug=1</code> or set{" "}
              <code className="text-white/55">localStorage.test_runner_ui_debug = &quot;1&quot;</code>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

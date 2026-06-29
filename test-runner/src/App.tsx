import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PhaseStepper } from "@/components/PhaseStepper";
import { cn } from "@/lib/utils";
import type { PhaseMap, RunEvent } from "@/types";

type Config = {
  defaultProject: string;
  hasCursorApiKey: boolean;
};

function formatEventLine(event: RunEvent): string {
  if (event.type === "step") {
    const mark = event.ok ? "✓" : "✗";
    return `[${event.mode ?? "strict"}] ${event.action} ${event.target} ${mark} ${event.message ?? ""}`.trim();
  }
  if (event.type === "cursor_text" && event.text) {
    return `[cursor] ${event.text}`;
  }
  if (event.type === "cursor") {
    return `[cursor] ${event.status ?? ""} ${event.message ?? ""}`.trim();
  }
  if (event.type === "phase") {
    return `[phase:${event.phase}] ${event.status} ${event.message ?? ""}`.trim();
  }
  if (event.type === "log") {
    return event.message ?? "";
  }
  if (event.type === "done") {
    return `[done] overall_ok=${String(event.ok ?? (event as { overall_ok?: boolean }).overall_ok)}`;
  }
  return JSON.stringify(event);
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [project, setProject] = useState("");
  const [task, setTask] = useState("");
  const [cursorPrompt, setCursorPrompt] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [cursorRuntime, setCursorRuntime] = useState<"cloud" | "local">("cloud");
  const [push, setPush] = useState(false);
  const [skipDeploy, setSkipDeploy] = useState(true);
  const [skipCursor, setSkipCursor] = useState(false);
  const [running, setRunning] = useState(false);
  const [phases, setPhases] = useState<PhaseMap>({});
  const [activePhase, setActivePhase] = useState<string>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [lastResult, setLastResult] = useState<{ overall_ok?: boolean } | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((data: Config) => {
        setConfig(data);
        const saved = localStorage.getItem("test_runner_project");
        setProject(saved || data.defaultProject || "");
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  const applyEvent = useCallback((event: RunEvent) => {
    setEvents((prev) => [...prev.slice(-800), event]);
    if (event.type === "phase" && event.phase) {
      setActivePhase(event.phase);
      setPhases((prev) => ({
        ...prev,
        [event.phase!]: { status: event.status, message: event.message },
      }));
    }
    if (event.type === "run_state") {
      setRunning(Boolean((event as { running?: boolean }).running));
    }
    if (event.type === "done") {
      setRunning(false);
      setLastResult({ overall_ok: (event as { overall_ok?: boolean }).overall_ok });
    }
    if (event.type === "process_exit") {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = (msg) => {
      try {
        applyEvent(JSON.parse(msg.data) as RunEvent);
      } catch {
        /* ignore */
      }
    };
    return () => source.close();
  }, [applyEvent]);

  const logLines = useMemo(() => events.map(formatEventLine).filter(Boolean), [events]);

  const startFullLoop = async () => {
    if (!project.trim()) return;
    localStorage.setItem("test_runner_project", project);
    setEvents([]);
    setPhases({});
    setLastResult(null);
    setRunning(true);
    const res = await fetch("/api/run/full", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project,
        task,
        push,
        skipDeploy,
        skipCursor,
        cursorRuntime,
        repoUrl: repoUrl || undefined,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      applyEvent({ type: "log", message: err.error ?? "Failed to start", level: "error" });
      setRunning(false);
    }
  };

  const startLocalOnly = async () => {
    if (!project.trim()) return;
    localStorage.setItem("test_runner_project", project);
    setEvents([]);
    setPhases({});
    setRunning(true);
    const res = await fetch("/api/run/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, task, push, skipDeploy }),
    });
    if (!res.ok) {
      const err = await res.json();
      applyEvent({ type: "log", message: err.error ?? "Failed to start", level: "error" });
      setRunning(false);
    }
  };

  const startCursorOnly = async () => {
    if (!project.trim()) return;
    setRunning(true);
    const res = await fetch("/api/run/cursor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project,
        prompt: cursorPrompt,
        runtime: cursorRuntime,
        repoUrl: repoUrl || undefined,
        useReport: !cursorPrompt.trim(),
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      applyEvent({ type: "log", message: err.error ?? "Failed to start Cursor", level: "error" });
      setRunning(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">AI Assistant Test Runner</h1>
        <p className="mt-1 text-sm text-white/60">
          Local agent (Ollama → Railway → Playwright) then Cursor SDK handoff
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-5">
        <section className="surface-card space-y-4 p-4 lg:col-span-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Configuration</h2>
          <label className="block text-xs text-white/60">Target project</label>
          <input
            className="w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            placeholder="C:\path\to\content-manager"
          />
          <label className="block text-xs text-white/60">Task (free text)</label>
          <textarea
            className="min-h-24 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="Verify login and home page load…"
          />
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
            Git push before deploy wait
          </label>
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={skipDeploy} onChange={(e) => setSkipDeploy(e.target.checked)} />
            Skip deploy wait
          </label>
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={skipCursor} onChange={(e) => setSkipCursor(e.target.checked)} />
            Skip Cursor step after local run
          </label>

          <hr className="border-white/10" />
          <h3 className="text-sm font-semibold text-white/70">Cursor SDK</h3>
          {!config?.hasCursorApiKey ? (
            <p className="text-xs text-amber-300/90">
              Set <code className="text-white/80">CURSOR_API_KEY</code> in ai-assistant/.env
            </p>
          ) : null}
          <label className="block text-xs text-white/60">Runtime</label>
          <select
            className="w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
            value={cursorRuntime}
            onChange={(e) => setCursorRuntime(e.target.value as "cloud" | "local")}
          >
            <option value="cloud">Cloud — visible in Cursor Agents sidebar</option>
            <option value="local">Local — SDK bridge on this machine</option>
          </select>
          {cursorRuntime === "cloud" ? (
            <>
              <label className="block text-xs text-white/60">Git repo URL (for cloud agent)</label>
              <input
                className="w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/you/content-manager"
              />
            </>
          ) : null}
          <label className="block text-xs text-white/60">Cursor prompt (optional — uses REPORT.md if empty)</label>
          <textarea
            className="min-h-20 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
            value={cursorPrompt}
            onChange={(e) => setCursorPrompt(e.target.value)}
            placeholder="Read .agent/current/REPORT.md and implement…"
          />

          <div className="flex flex-col gap-2 pt-2">
            <button
              type="button"
              disabled={running}
              onClick={startFullLoop}
              className={cn(
                "rounded-md bg-white px-4 py-2 text-sm font-semibold text-black",
                running && "opacity-50",
              )}
            >
              Run full loop
            </button>
            <button
              type="button"
              disabled={running}
              onClick={startLocalOnly}
              className="rounded-md border border-white/20 px-4 py-2 text-sm text-white/90"
            >
              Local agent only
            </button>
            <button
              type="button"
              disabled={running || !config?.hasCursorApiKey}
              onClick={startCursorOnly}
              className="rounded-md border border-violet-400/30 px-4 py-2 text-sm text-violet-100"
            >
              Cursor agent only
            </button>
          </div>
        </section>

        <section className="surface-card p-4 lg:col-span-3">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Progress</h2>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                running
                  ? "bg-sky-500/20 text-sky-200"
                  : lastResult?.overall_ok
                    ? "bg-green-500/20 text-green-200"
                    : lastResult
                      ? "bg-red-500/20 text-red-200"
                      : "bg-white/10 text-white/60",
              )}
            >
              {running ? "running" : lastResult?.overall_ok ? "pass" : lastResult ? "fail" : "idle"}
            </span>
          </div>
          <PhaseStepper phases={phases} activePhase={activePhase} />
        </section>
      </div>

      <section className="surface-card mt-6 p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">Live log</h2>
        <pre
          ref={logRef}
          className="max-h-[420px] overflow-auto rounded-md border border-white/10 bg-black/40 p-3 font-mono text-xs leading-relaxed text-white/85"
        >
          {logLines.join("\n") || "Waiting for run…"}
        </pre>
      </section>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserStatePanel } from "@/components/BrowserStatePanel";
import { CheatsheetPanel } from "@/components/CheatsheetPanel";
import { PhaseStepper } from "@/components/PhaseStepper";
import { ProjectSelector } from "@/components/ProjectSelector";
import { cn } from "@/lib/utils";
import { apiFetch, eventsUrl } from "@/lib/api";
import type { BrowserState, LocalEnvStatus, ProjectsRegistry, TestTarget } from "@/lib/projectTypes";
import type { PhaseMap, RunEvent } from "@/types";

const SETTINGS_KEY = "test_runner_settings_v2";

type StoredSettings = {
  project?: string;
  task?: string;
  cursorPrompt?: string;
  repoUrl?: string;
  cursorRuntime?: "cloud" | "local";
  push?: boolean;
  skipDeploy?: boolean;
  skipCursor?: boolean;
};

function loadStoredSettings(): StoredSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) return JSON.parse(raw) as StoredSettings;
    const legacyProject = localStorage.getItem("test_runner_project");
    if (legacyProject) return { project: legacyProject };
  } catch {
    /* ignore */
  }
  return {};
}

function saveStoredSettings(settings: StoredSettings) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  if (settings.project) localStorage.setItem("test_runner_project", settings.project);
}

type OllamaStatus = {
  url: string;
  model: string;
  reachable: boolean;
  modelAvailable: boolean;
  modelLoaded: boolean;
  loadedModels: string[];
};

type Config = {
  defaultProject: string;
  hasCursorApiKey: boolean;
  ollama?: OllamaStatus;
};

function applyStateFromServer(
  data: {
    running?: boolean;
    phase?: string;
    phases?: PhaseMap;
    events?: RunEvent[];
    lastResult?: { overall_ok?: boolean };
    browserState?: BrowserState;
    testTarget?: TestTarget;
  },
  setters: {
    setRunning: (v: boolean) => void;
    setActivePhase: (v: string | undefined) => void;
    setPhases: (v: PhaseMap) => void;
    setEvents: (v: RunEvent[]) => void;
    setLastResult: (v: { overall_ok?: boolean } | null) => void;
    setBrowserState?: (v: BrowserState | null) => void;
    setTestTarget?: (v: TestTarget | null) => void;
  },
) {
  if (typeof data.running === "boolean") setters.setRunning(data.running);
  if (data.phase) setters.setActivePhase(data.phase);
  if (data.phases) setters.setPhases(data.phases);
  if (Array.isArray(data.events) && data.events.length > 0) {
    setters.setEvents(data.events);
  }
  if (data.lastResult) setters.setLastResult(data.lastResult);
  if (data.browserState && setters.setBrowserState) {
    setters.setBrowserState(data.browserState as BrowserState);
  }
  if (data.testTarget && setters.setTestTarget) {
    setters.setTestTarget(data.testTarget as TestTarget);
  }
}

function formatEventLine(event: RunEvent): string {
  if (event.type === "step") {
    const mark = event.ok ? "✓" : "✗";
    const url = event.page_url ? ` @ ${event.page_url}` : "";
    return `[${event.mode ?? "strict"}] ${event.action} ${event.target} ${mark}${url} ${event.message ?? ""}`.trim();
  }
  if (event.type === "browser_state") {
    const count = event.interactables?.length ?? 0;
    const ctx = event.context ? ` (${event.context})` : "";
    return `[browser] ${event.url} — ${count} interactables${ctx}`;
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
  if (event.type === "test_target") {
    const source = (event as { source?: string }).source ?? "unknown";
    return `[target] ${source}: ${event.url ?? ""}`.trim();
  }
  if (event.type === "connected") {
    return "";
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
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [preloadingOllama, setPreloadingOllama] = useState(false);
  const [lastResult, setLastResult] = useState<{ overall_ok?: boolean } | null>(null);
  const [browserState, setBrowserState] = useState<BrowserState | null>(null);
  const [testTarget, setTestTarget] = useState<TestTarget | null>(null);
  const [lastStep, setLastStep] = useState<RunEvent | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const persistSettings = useCallback(() => {
    saveStoredSettings({
      project,
      task,
      cursorPrompt,
      repoUrl,
      cursorRuntime,
      push,
      skipDeploy,
      skipCursor,
    });
  }, [project, task, cursorPrompt, repoUrl, cursorRuntime, push, skipDeploy, skipCursor]);

  const saveProjectToRegistry = useCallback(async () => {
    if (!project.trim()) return;
    persistSettings();
    await apiFetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: project,
        settings: { task, push, skipDeploy, skipCursor, cursorRuntime, repoUrl, cursorPrompt },
      }),
    });
  }, [project, task, push, skipDeploy, skipCursor, cursorRuntime, repoUrl, cursorPrompt, persistSettings]);

  const applyProjectSettings = useCallback((settings?: ProjectsRegistry["projects"][0]["settings"]) => {
    if (!settings) return;
    if (settings.task !== undefined) setTask(settings.task);
    if (settings.push !== undefined) setPush(settings.push);
    if (settings.skipDeploy !== undefined) setSkipDeploy(settings.skipDeploy);
    if (settings.skipCursor !== undefined) setSkipCursor(settings.skipCursor);
    if (settings.cursorRuntime) setCursorRuntime(settings.cursorRuntime);
    if (settings.repoUrl !== undefined) setRepoUrl(settings.repoUrl);
    if (settings.cursorPrompt !== undefined) setCursorPrompt(settings.cursorPrompt);
  }, []);

  const refreshConfig = useCallback(() => {
    apiFetch("/api/config")
      .then((r) => r.json())
      .then((data: Config) => {
        setConfig(data);
        if (data.ollama) setOllamaStatus(data.ollama);
      })
      .catch(() => {});
  }, []);

  const refreshState = useCallback(() => {
    apiFetch("/api/state")
      .then((r) => r.json())
      .then((data) => {
        applyStateFromServer(data, {
          setRunning,
          setActivePhase,
          setPhases,
          setEvents,
          setLastResult,
          setBrowserState,
          setTestTarget,
        });
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const stored = loadStoredSettings();
    apiFetch("/api/config")
      .then((r) => r.json())
      .then((data: Config) => {
        setConfig(data);
        if (data.ollama) setOllamaStatus(data.ollama);
        setProject(stored.project || data.defaultProject || "");
        if (stored.task) setTask(stored.task);
        if (stored.cursorPrompt) setCursorPrompt(stored.cursorPrompt);
        if (stored.repoUrl) setRepoUrl(stored.repoUrl);
        if (stored.cursorRuntime) setCursorRuntime(stored.cursorRuntime);
        if (stored.push !== undefined) setPush(stored.push);
        if (stored.skipDeploy !== undefined) setSkipDeploy(stored.skipDeploy);
        if (stored.skipCursor !== undefined) setSkipCursor(stored.skipCursor);
      })
      .catch(() => {});

    apiFetch("/api/projects")
      .then((r) => r.json())
      .then((registry: ProjectsRegistry) => {
        const active = registry.projects.find((p) => p.id === registry.activeProjectId);
        if (active) {
          setProject(active.path);
          applyProjectSettings(active.settings);
        }
      })
      .catch(() => {});

    refreshState();
  }, [refreshState, applyProjectSettings]);

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
    if (event.type === "step") {
      setLastStep(event);
    }
    if (event.type === "browser_state" && event.url) {
      setBrowserState({
        url: event.url,
        title: event.title,
        interactables: event.interactables ?? [],
        context: event.context,
        node_url: event.node_url,
        ts: event.ts,
      });
    }
    if (event.type === "test_target" && event.url) {
      setTestTarget({
        url: event.url,
        source: (event as { source?: string }).source ?? "unknown",
        local_url: (event as { local_url?: string }).local_url,
        ts: event.ts,
      });
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
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      source = new EventSource(eventsUrl());
      source.onopen = () => {
        refreshState();
      };
      source.onmessage = (msg) => {
        try {
          applyEvent(JSON.parse(msg.data) as RunEvent);
        } catch {
          /* ignore */
        }
      };
      source.onerror = () => {
        source?.close();
        setRunning(false);
        refreshState();
        retryTimer = setTimeout(connect, 2000);
      };
    };

    connect();
    return () => {
      source?.close();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [applyEvent, refreshState]);

  const preloadOllama = async () => {
    setPreloadingOllama(true);
    try {
      const res = await apiFetch("/api/ollama/preload", { method: "POST" });
      const body = await res.json();
      if (!res.ok) {
        applyEvent({ type: "log", message: body.error ?? "Ollama preload failed", level: "error" });
      } else {
        applyEvent({ type: "log", message: body.message ?? "Ollama model ready", level: "info" });
      }
      refreshConfig();
    } finally {
      setPreloadingOllama(false);
    }
  };

  const logLines = useMemo(() => events.map(formatEventLine).filter(Boolean), [events]);

  const startFullLoop = async () => {
    if (!project.trim()) return;
    persistSettings();
    void saveProjectToRegistry();
    setEvents([]);
    setPhases({});
    setBrowserState(null);
    setTestTarget(null);
    setLastStep(null);
    setLastResult(null);
    setRunning(true);
    const res = await apiFetch("/api/run/full", {
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
    persistSettings();
    void saveProjectToRegistry();
    setEvents([]);
    setPhases({});
    setBrowserState(null);
    setTestTarget(null);
    setLastStep(null);
    setRunning(true);
    const res = await apiFetch("/api/run/local", {
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
    const res = await apiFetch("/api/run/cursor", {
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

  const testTargetLabel = useMemo(() => {
    if (!testTarget?.url) return null;
    if (testTarget.source === "local") return { text: "Local dev", className: "bg-green-500/20 text-green-200" };
    if (testTarget.source === "deployed_fallback") {
      return { text: "Railway fallback (local failed)", className: "bg-amber-500/20 text-amber-200" };
    }
    return { text: "Railway", className: "bg-sky-500/20 text-sky-200" };
  }, [testTarget]);

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
          <ProjectSelector
            projectPath={project}
            onSelect={(path, settings) => {
              setProject(path);
              applyProjectSettings(settings);
            }}
            onSaveCurrent={saveProjectToRegistry}
          />
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
            Skip deploy wait (start local dev from cheatsheet)
          </label>
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={skipCursor} onChange={(e) => setSkipCursor(e.target.checked)} />
            Skip Cursor step after local run
          </label>

          <hr className="border-white/10" />
          <h3 className="text-sm font-semibold text-white/70">Ollama</h3>
          {ollamaStatus ? (
            <div className="space-y-2 text-xs">
              <p className="text-white/60">
                Model: <code className="text-white/80">{ollamaStatus.model}</code>
              </p>
              <p
                className={cn(
                  ollamaStatus.modelLoaded
                    ? "text-green-300/90"
                    : ollamaStatus.reachable
                      ? "text-amber-300/90"
                      : "text-red-300/90",
                )}
              >
                {!ollamaStatus.reachable
                  ? "Ollama not reachable — start the Ollama app"
                  : !ollamaStatus.modelAvailable
                    ? `Model not installed — run: ollama pull ${ollamaStatus.model}`
                    : ollamaStatus.modelLoaded
                      ? "Model loaded in VRAM"
                      : "Model not loaded — first run will load it (30–90s)"}
              </p>
              {ollamaStatus.reachable && ollamaStatus.modelAvailable && !ollamaStatus.modelLoaded ? (
                <button
                  type="button"
                  disabled={preloadingOllama || running}
                  onClick={preloadOllama}
                  className="rounded-md border border-white/20 px-3 py-1.5 text-xs text-white/90 disabled:opacity-50"
                >
                  {preloadingOllama ? "Loading model…" : "Preload model now"}
                </button>
              ) : null}
            </div>
          ) : null}

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
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Progress</h2>
            <div className="flex flex-wrap items-center gap-2">
              {testTarget?.url && testTargetLabel ? (
                <span
                  className={cn("max-w-xs truncate rounded-full px-2 py-0.5 text-[10px] font-medium", testTargetLabel.className)}
                  title={testTarget.url}
                >
                  {testTargetLabel.text}: {testTarget.url}
                </span>
              ) : null}
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
          </div>
          <PhaseStepper phases={phases} activePhase={activePhase} />
        </section>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <section className="surface-card p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">Playwright browser state</h2>
          <BrowserStatePanel
            state={browserState}
            lastStep={
              lastStep
                ? {
                    action: lastStep.action,
                    target: lastStep.target,
                    ok: lastStep.ok,
                    page_url: lastStep.page_url,
                    message: lastStep.message,
                  }
                : null
            }
          />
        </section>
        <section className="surface-card p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">Project cheatsheets</h2>
          <CheatsheetPanel projectPath={project} />
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

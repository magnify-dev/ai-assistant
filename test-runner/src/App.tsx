import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheatsheetPanel } from "@/components/CheatsheetPanel";
import { ExplorationPanel } from "@/components/ExplorationPanel";
import { PagePreview } from "@/components/PagePreview";
import { ProjectSelector } from "@/components/ProjectSelector";
import { RunHistoryPanel } from "@/components/RunHistoryPanel";
import { RunProgressPanel } from "@/components/RunProgressPanel";
import { cn } from "@/lib/utils";
import { apiFetch, eventsUrl } from "@/lib/api";
import type {
  BrowserState,
  LocalEnvStatus,
  PlaywrightSession,
  ProjectsRegistry,
  RunHistoryEntry,
  RunReport,
  StructuredTask,
  TestTarget,
} from "@/lib/projectTypes";
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
  testTarget?: "local" | "deployed";
  skipDeployWait?: boolean;
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
    structuredTask?: StructuredTask;
    runReport?: RunReport;
  },
  setters: {
    setRunning: (v: boolean) => void;
    setActivePhase: (v: string | undefined) => void;
    setPhases: (v: PhaseMap) => void;
    setEvents: (v: RunEvent[]) => void;
    setLastResult: (v: { overall_ok?: boolean } | null) => void;
    setBrowserState?: (v: BrowserState | null) => void;
    setTestTarget?: (v: TestTarget | null) => void;
    setStructuredTask?: (v: StructuredTask | null) => void;
    setRunReport?: (v: RunReport | null) => void;
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
  if (data.structuredTask && setters.setStructuredTask) {
    setters.setStructuredTask(data.structuredTask as StructuredTask);
  }
  if (data.runReport && setters.setRunReport) {
    setters.setRunReport(data.runReport as RunReport);
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
  if (event.type === "site_map") {
    const pages = (event as { pages?: Record<string, unknown> }).pages;
    return `[site_map] ${pages ? Object.keys(pages).length : 0} page(s)`;
  }
  if (event.type === "nav_tree") {
    const routes = (event as { routes?: Record<string, unknown> }).routes;
    return `[nav_tree] ${routes ? Object.keys(routes).length : 0} route(s)`;
  }
  if (event.type === "agent_decision") {
    const e = event as { action?: string; reason?: string };
    return `[agent] ${e.action ?? ""}: ${e.reason ?? ""}`.trim();
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
  const [testTargetMode, setTestTargetMode] = useState<"local" | "deployed">("local");
  const [skipDeployWait, setSkipDeployWait] = useState(false);
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
  const [structuredTask, setStructuredTask] = useState<StructuredTask | null>(null);
  const [runReport, setRunReport] = useState<RunReport | null>(null);
  const [lastStep, setLastStep] = useState<RunEvent | null>(null);
  const [view, setView] = useState<"config" | "run">("config");
  const [logOpen, setLogOpen] = useState(false);
  const [hasLatestRun, setHasLatestRun] = useState(false);
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);
  const [playwrightSession, setPlaywrightSession] = useState<PlaywrightSession | null>(null);
  const [sessionFrameIndex, setSessionFrameIndex] = useState(0);
  const logRef = useRef<HTMLPreElement>(null);
  const runningRef = useRef(running);
  runningRef.current = running;

  const runApiOptions = useMemo(
    () => ({
      testTarget: testTargetMode,
      skipDeploy: testTargetMode === "local" ? true : !skipDeployWait,
    }),
    [testTargetMode, skipDeployWait],
  );

  const clearRunPanels = useCallback(() => {
    setEvents([]);
    setPhases({});
    setActivePhase(undefined);
    setBrowserState(null);
    setTestTarget(null);
    setStructuredTask(null);
    setRunReport(null);
    setLastStep(null);
    setLastResult(null);
    setViewingRunId(null);
    setPlaywrightSession(null);
    setSessionFrameIndex(0);
  }, []);

  const persistSettings = useCallback(() => {
    saveStoredSettings({
      project,
      task,
      cursorPrompt,
      repoUrl,
      cursorRuntime,
      push,
      testTarget: testTargetMode,
      skipDeployWait,
      skipCursor,
    });
  }, [project, task, cursorPrompt, repoUrl, cursorRuntime, push, testTargetMode, skipDeployWait, skipCursor]);

  const saveProjectToRegistry = useCallback(async () => {
    if (!project.trim()) return;
    persistSettings();
    await apiFetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: project,
        settings: { task, push, testTarget: testTargetMode, skipDeployWait, skipCursor, cursorRuntime, repoUrl, cursorPrompt },
      }),
    });
  }, [project, task, push, testTargetMode, skipDeployWait, skipCursor, cursorRuntime, repoUrl, cursorPrompt, persistSettings]);

  const applyProjectSettings = useCallback((settings?: ProjectsRegistry["projects"][0]["settings"]) => {
    if (!settings) return;
    if (settings.task !== undefined) setTask(settings.task);
    if (settings.push !== undefined) setPush(settings.push);
    if (settings.testTarget) setTestTargetMode(settings.testTarget);
    else if (settings.skipDeploy !== undefined) setTestTargetMode(settings.skipDeploy ? "local" : "deployed");
    if (settings.skipDeployWait !== undefined) setSkipDeployWait(settings.skipDeployWait);
    else if (settings.skipDeploy === false) setSkipDeployWait(true);
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
          setStructuredTask,
          setRunReport,
        });
      })
      .catch(() => {});
  }, []);

  const loadRunHistory = useCallback(() => {
    if (!project.trim()) {
      setRunHistory([]);
      setHasLatestRun(false);
      return;
    }
    setHistoryLoading(true);
    apiFetch(`/api/project/run-history?path=${encodeURIComponent(project)}`)
      .then((r) => r.json())
      .then((data: { runs?: RunHistoryEntry[] }) => {
        const runs = data.runs ?? [];
        setRunHistory(runs);
        setHasLatestRun(runs.some((run) => run.id === "current"));
      })
      .catch(() => {
        setRunHistory([]);
        setHasLatestRun(false);
      })
      .finally(() => setHistoryLoading(false));
  }, [project]);

  const loadRunReport = useCallback(() => {
    if (!project.trim() || running) return;
    apiFetch(`/api/project/run-report?path=${encodeURIComponent(project)}`)
      .then((r) => r.json())
      .then((data: { report?: RunReport | null; pageReport?: string; hasRun?: boolean; playwrightSession?: PlaywrightSession | null }) => {
        if (runningRef.current) return;
        if (data.report) {
          const report = data.report;
          if (!report.page_report && data.pageReport) {
            report.page_report = data.pageReport;
          }
          if (data.playwrightSession) {
            report.playwright_session = data.playwrightSession;
          }
          setRunReport(report);
          if (!running && data.playwrightSession) {
            setPlaywrightSession(data.playwrightSession);
            if (!viewingRunId) setViewingRunId("current");
          } else if (!running && viewingRunId === "current") {
            setPlaywrightSession(data.playwrightSession ?? report.playwright_session ?? null);
          }
        }
        setHasLatestRun(Boolean(data.hasRun));
      })
      .catch(() => setHasLatestRun(false));
  }, [project, running, viewingRunId]);

  useEffect(() => {
    if (!running) loadRunReport();
    loadRunHistory();
  }, [loadRunReport, loadRunHistory, running, project]);

  useEffect(() => {
    if (running) setView("run");
  }, [running]);

  useEffect(() => {
    if (view === "run") refreshState();
  }, [view, refreshState]);

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
        if (stored.testTarget) setTestTargetMode(stored.testTarget);
        else if (stored.skipDeploy !== undefined) setTestTargetMode(stored.skipDeploy ? "local" : "deployed");
        if (stored.skipDeployWait !== undefined) setSkipDeployWait(stored.skipDeployWait);
        else if (stored.skipDeploy === false) setSkipDeployWait(true);
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
        screenshot_b64: (event as { screenshot_b64?: string }).screenshot_b64,
        error: (event as { error?: string }).error,
      });
    }
    if (event.type === "structured_task") {
      setStructuredTask({
        summary: (event as { summary?: string }).summary,
        source_text: (event as { source_text?: string }).source_text,
        scope_urls: (event as { scope_urls?: string[] }).scope_urls,
        success_criteria: (event as { success_criteria?: string[] }).success_criteria,
        deliverables: (event as { deliverables?: string[] }).deliverables,
        suggested_steps: (event as { suggested_steps?: StructuredTask["suggested_steps"] }).suggested_steps,
        notes_for_cursor: (event as { notes_for_cursor?: string[] }).notes_for_cursor,
        intent_gaps: (event as { intent_gaps?: string[] }).intent_gaps,
        preserves_intent: (event as { preserves_intent?: boolean }).preserves_intent,
        spec_runs: (event as { spec_runs?: string }).spec_runs,
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
    if (event.type === "run_report" && (event as { report?: RunReport }).report) {
      const report = (event as unknown as { report: RunReport }).report;
      setRunReport(report);
    }
    if (event.type === "site_map" || event.type === "nav_tree" || event.type === "agent_decision") {
      window.dispatchEvent(new CustomEvent("test-runner-event", { detail: event }));
    }
    if (event.type === "run_cleared") {
      setStructuredTask(null);
      setRunReport(null);
      setTestTarget(null);
      setBrowserState(null);
      setLastStep(null);
      setLastResult(null);
      setPhases({});
      setActivePhase(undefined);
      setViewingRunId(null);
      setPlaywrightSession(null);
      setSessionFrameIndex(0);
    }
    if (event.type === "done") {
      setRunning(false);
      setLastResult({ overall_ok: (event as { overall_ok?: boolean }).overall_ok });
      loadRunHistory();
    }
    if (event.type === "process_exit") {
      setRunning(false);
    }
  }, [loadRunHistory]);

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

  const viewRun = useCallback(
    async (runId: string) => {
      if (!project.trim()) return;
      try {
        const res = await apiFetch(
          `/api/project/run?path=${encodeURIComponent(project)}&runId=${encodeURIComponent(runId)}`,
        );
        const data = (await res.json()) as {
          report?: RunReport | null;
          pageReport?: string;
          structuredTask?: StructuredTask;
          playwrightSession?: PlaywrightSession | null;
        };
        if (!data.report) return;
        const report = { ...data.report };
        if (!report.page_report && data.pageReport) {
          report.page_report = data.pageReport;
        }
        if (data.playwrightSession) {
          report.playwright_session = data.playwrightSession;
        }
        setRunReport(report);
        setPlaywrightSession(data.playwrightSession ?? report.playwright_session ?? null);
        setSessionFrameIndex(0);
        setViewingRunId(runId);
        if (data.structuredTask) {
          setStructuredTask(data.structuredTask as StructuredTask);
        } else if (report.requested) {
          setStructuredTask({
            summary: report.requested.summary,
            source_text: report.requested.source_text,
            success_criteria: report.requested.success_criteria,
            scope_urls: report.requested.scope_urls,
            deliverables: report.requested.deliverables,
            intent_gaps: report.requested.intent_gaps,
          });
        }
        const target = report.test_target as { url?: string; source?: string; local_url?: string } | undefined;
        if (target?.url) {
          setTestTarget({ url: target.url, source: target.source ?? "unknown", local_url: target.local_url });
        }
        setLastResult({ overall_ok: report.overall_ok });
        const phaseMap: PhaseMap = {};
        if (report.mode === "exploration") {
          phaseMap.exploration = {
            status: report.overall_ok ? "done" : "failed",
            message: report.ui_error || "Exploration complete",
          };
        } else {
          phaseMap.ui_test = {
            status: report.overall_ok ? "done" : "failed",
            message: report.ui_error || "UI test complete",
          };
        }
        for (const phase of report.phases ?? []) {
          const key =
            phase.name === "Local dev"
              ? "local_server"
              : phase.name === "Exploration"
                ? "exploration"
                : phase.name === "UI test"
                  ? "ui_test"
                  : undefined;
          if (key) {
            phaseMap[key] = { status: phase.ok ? "done" : "failed", message: phase.detail };
          }
        }
        setPhases(phaseMap);
        setView("run");
      } catch {
        /* ignore */
      }
    },
    [project],
  );

  const logLines = useMemo(() => events.map(formatEventLine).filter(Boolean), [events]);

  const startFullLoop = async () => {
    if (!project.trim()) return;
    persistSettings();
    void saveProjectToRegistry();
    clearRunPanels();
    setView("run");
    setRunning(true);
    refreshState();
    const res = await apiFetch("/api/run/full", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project,
        task,
        push,
        ...runApiOptions,
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
    clearRunPanels();
    setView("run");
    setRunning(true);
    refreshState();
    const res = await apiFetch("/api/run/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, task, push, ...runApiOptions }),
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

  const lastActionLine = useMemo(() => {
    if (!lastStep || lastStep.type !== "step") return undefined;
    const mark = lastStep.ok ? "✓" : "✗";
    return `${lastStep.action} ${lastStep.target} ${mark}${lastStep.message ? ` — ${lastStep.message}` : ""}`.trim();
  }, [lastStep]);

  const replayMode = Boolean(playwrightSession?.frames?.length) && !running;
  const viewingRunLabel = runHistory.find((run) => run.id === viewingRunId)?.label;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Assistant Test Runner</h1>
          <p className="mt-1 text-sm text-white/60">
            Local agent (Ollama → Railway → Playwright) then Cursor SDK handoff
          </p>
        </div>
        {view === "run" ? (
          <div className="flex flex-wrap items-center gap-2">
            {viewingRunId ? (
              <span className="rounded-full bg-violet-500/15 px-2 py-0.5 text-xs text-violet-100">
                Inspecting: {viewingRunLabel ?? viewingRunId}
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => {
                setView("config");
                if (!running) {
                  setViewingRunId(null);
                  setPlaywrightSession(null);
                }
              }}
              className="rounded-md border border-white/20 px-3 py-1.5 text-sm text-white/80 hover:bg-white/5"
            >
              Back to configuration
            </button>
          </div>
        ) : null}
      </header>

      {view === "config" ? (
        <section className="surface-card mx-auto max-w-2xl space-y-4 p-6">
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
          <label className="block text-xs text-white/60">Test against</label>
          <div className="flex flex-col gap-2 rounded-md border border-white/10 bg-black/20 p-3 text-sm">
            <label className="flex cursor-pointer items-center gap-2 text-white/80">
              <input
                type="radio"
                name="testTarget"
                checked={testTargetMode === "local"}
                onChange={() => setTestTargetMode("local")}
              />
              Local dev (cheatsheet — starts or reuses local server)
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-white/80">
              <input
                type="radio"
                name="testTarget"
                checked={testTargetMode === "deployed"}
                onChange={() => setTestTargetMode("deployed")}
              />
              Deployed (Railway production URL)
            </label>
          </div>
          {testTargetMode === "deployed" ? (
            <label className="flex items-center gap-2 text-sm text-white/70">
              <input
                type="checkbox"
                checked={skipDeployWait}
                onChange={(e) => setSkipDeployWait(e.target.checked)}
              />
              Wait for Railway deploy before testing
            </label>
          ) : null}
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
            Git push before deploy wait
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

          <hr className="border-white/10" />
          <RunHistoryPanel runs={runHistory} loading={historyLoading} onInspect={(runId) => void viewRun(runId)} />

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

          <details className="rounded-md border border-white/10 bg-black/20 p-3 text-sm">
            <summary className="cursor-pointer text-xs font-medium text-white/60">Run settings &amp; exploration</summary>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <CheatsheetPanel projectPath={project} testTargetMode={testTargetMode} />
              <ExplorationPanel projectPath={project} />
            </div>
          </details>
        </section>
      ) : (
        <div className="flex min-h-[calc(100vh-10rem)] flex-col gap-4">
          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_340px] xl:grid-cols-[1fr_380px]">
            <section className="surface-card flex min-h-[480px] flex-col p-4 lg:min-h-0">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">
                {replayMode ? "Recorded session" : "Live page"}
              </h2>
              <div className="min-h-0 flex-1">
                <PagePreview
                  state={replayMode ? null : browserState}
                  session={playwrightSession}
                  frameIndex={sessionFrameIndex}
                  onFrameIndexChange={setSessionFrameIndex}
                  lastAction={lastActionLine}
                  replayMode={replayMode}
                />
              </div>
            </section>
            <section className="surface-card flex min-h-[480px] flex-col p-4 lg:min-h-0">
              <RunProgressPanel
                phases={phases}
                structuredTask={structuredTask}
                runReport={runReport}
                testTarget={testTarget}
                running={running}
                projectPath={project}
                lastResult={lastResult}
                testTargetMode={testTargetMode}
                skipDeploy={runApiOptions.skipDeploy}
                hasTask={Boolean(task.trim())}
                skipCursor={skipCursor}
              />
            </section>
          </div>

          <section className="surface-card p-4">
            <button
              type="button"
              onClick={() => setLogOpen((v) => !v)}
              className="flex w-full items-center justify-between text-sm font-semibold uppercase tracking-wide text-white/50"
            >
              Live log
              <span className="text-xs font-normal normal-case text-white/40">{logOpen ? "Hide" : "Show"}</span>
            </button>
            {logOpen ? (
              <pre
                ref={logRef}
                className="mt-3 max-h-64 overflow-auto rounded-md border border-white/10 bg-black/40 p-3 font-mono text-xs leading-relaxed text-white/85"
              >
                {logLines.join("\n") || "Waiting for run…"}
              </pre>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}

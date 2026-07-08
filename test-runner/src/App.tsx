import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CollaborationPanel } from "@/components/CollaborationPanel";
import { CurrentRunStatus } from "@/components/CurrentRunStatus";
import { CheatsheetPanel } from "@/components/CheatsheetPanel";
import { ExplorationPanel } from "@/components/ExplorationPanel";
import { PagePreview } from "@/components/PagePreview";
import { ProjectSelector } from "@/components/ProjectSelector";
import { RunHistoryPanel } from "@/components/RunHistoryPanel";
import { RunProgressPanel } from "@/components/RunProgressPanel";
import { cn } from "@/lib/utils";
import { apiFetch, eventsUrl } from "@/lib/api";
import { runTargetOptions } from "@/lib/runTarget";
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
import type { AgentRunCard, CollaborationConfig, CollaborationResult } from "@/lib/collaborationTypes";
import type { PhaseMap, RunEvent } from "@/types";

const BROWSER_PHASES = ["exploration", "ui_test"] as const;

const SETTINGS_KEY = "test_runner_settings_v2";

type StoredSettings = {
  project?: string;
  task?: string;
  repoUrl?: string;
  cursorRuntime?: "cloud" | "local";
  testTarget?: "local" | "deployed";
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
    agentCards?: AgentRunCard[];
    collaborationResult?: CollaborationResult | null;
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
    setAgentCards?: (v: AgentRunCard[]) => void;
    setCollaborationResult?: (v: CollaborationResult | null) => void;
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
  if (Array.isArray(data.agentCards) && setters.setAgentCards) {
    setters.setAgentCards(data.agentCards as AgentRunCard[]);
  }
  if (data.collaborationResult !== undefined && setters.setCollaborationResult) {
    setters.setCollaborationResult(data.collaborationResult as CollaborationResult | null);
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
  if (event.type === "agent_card") {
    const card = (event as { card?: AgentRunCard }).card;
    return `[${card?.agent ?? "agent"}] ${card?.status ?? ""} ${card?.summary ?? ""}`.trim();
  }
  if (event.type === "collaboration_done") {
    const e = event as { ok?: boolean; error?: string; answer?: string };
    return `[collaboration] ok=${String(e.ok)} ${e.error ?? e.answer ?? ""}`.trim();
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
  const [repoUrl, setRepoUrl] = useState("");
  const [cursorRuntime, setCursorRuntime] = useState<"cloud" | "local">("cloud");
  const [testTargetMode, setTestTargetMode] = useState<"local" | "deployed">("local");
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
  const [agentCards, setAgentCards] = useState<AgentRunCard[]>([]);
  const [collaborationResult, setCollaborationResult] = useState<CollaborationResult | null>(null);
  const [collabConfig, setCollabConfig] = useState<CollaborationConfig | null>(null);
  const [helperPromptDraft, setHelperPromptDraft] = useState("");
  const [savingCollabConfig, setSavingCollabConfig] = useState(false);
  const [projectEnv, setProjectEnv] = useState<LocalEnvStatus | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const runningRef = useRef(running);
  runningRef.current = running;

  const runApiOptions = useMemo(() => runTargetOptions(testTargetMode), [testTargetMode]);

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
    setAgentCards([]);
    setCollaborationResult(null);
  }, []);

  const persistSettings = useCallback(() => {
    saveStoredSettings({
      project,
      task,
      repoUrl,
      cursorRuntime,
      testTarget: testTargetMode,
      skipCursor,
    });
  }, [project, task, repoUrl, cursorRuntime, testTargetMode, skipCursor]);

  const saveProjectToRegistry = useCallback(async () => {
    if (!project.trim()) return;
    persistSettings();
    await apiFetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: project,
        settings: { task, testTarget: testTargetMode, skipCursor, cursorRuntime, repoUrl },
      }),
    });
  }, [project, task, testTargetMode, skipCursor, cursorRuntime, repoUrl, persistSettings]);

  const applyProjectSettings = useCallback((settings?: ProjectsRegistry["projects"][0]["settings"]) => {
    if (!settings) return;
    if (settings.task !== undefined) setTask(settings.task);
    if (settings.testTarget) setTestTargetMode(settings.testTarget);
    else if (settings.skipDeploy !== undefined) setTestTargetMode(settings.skipDeploy ? "local" : "deployed");
    if (settings.skipCursor !== undefined) setSkipCursor(settings.skipCursor);
    if (settings.cursorRuntime) setCursorRuntime(settings.cursorRuntime);
    if (settings.repoUrl !== undefined) setRepoUrl(settings.repoUrl);
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
          setAgentCards,
          setCollaborationResult,
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
        if (stored.repoUrl) setRepoUrl(stored.repoUrl);
        if (stored.cursorRuntime) setCursorRuntime(stored.cursorRuntime);
        if (stored.testTarget) setTestTargetMode(stored.testTarget);
        else if (stored.skipDeploy !== undefined) setTestTargetMode(stored.skipDeploy ? "local" : "deployed");
        if (stored.skipCursor !== undefined) setSkipCursor(stored.skipCursor);
      })
      .catch(() => {});

    apiFetch("/api/collaboration/config")
      .then((r) => r.json())
      .then((data: CollaborationConfig) => {
        setCollabConfig(data);
        setHelperPromptDraft(data.helperPrompt);
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
    if (!project.trim()) {
      setProjectEnv(null);
      return;
    }
    apiFetch(`/api/project/local-env?path=${encodeURIComponent(project)}`)
      .then((r) => r.json())
      .then((data: LocalEnvStatus) => setProjectEnv(data))
      .catch(() => setProjectEnv(null));
  }, [project]);

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
    if (event.type === "agent_card" && (event as { card?: AgentRunCard }).card) {
      const card = (event as { card: AgentRunCard }).card;
      setAgentCards((prev) => {
        const idx = prev.findIndex((c) => c.id === card.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = card;
          return next;
        }
        return [...prev, card];
      });
    }
    if (event.type === "collaboration_start") {
      const resumed = Boolean((event as { resumed?: boolean }).resumed);
      if (!resumed) {
        setAgentCards([]);
      }
      setCollaborationResult(null);
    }
    if (event.type === "phases_reset") {
      setPhases({});
      setStructuredTask(null);
      setRunReport(null);
      setTestTarget(null);
      setLastResult(null);
    }
    if (event.type === "collaboration_done") {
      const e = event as { ok?: boolean; answer?: string; error?: string; iterations?: number };
      setCollaborationResult({ ok: e.ok, answer: e.answer, error: e.error, iterations: e.iterations });
      setRunning(false);
      loadRunHistory();
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
      setAgentCards([]);
      setCollaborationResult(null);
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
          collaborationTranscript?: {
            task?: string;
            agentCards?: AgentRunCard[];
            collaborationResult?: CollaborationResult;
          } | null;
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
        if (data.collaborationTranscript?.agentCards?.length) {
          setAgentCards(
            data.collaborationTranscript.agentCards.map((c) => ({ ...c, historical: true })),
          );
          setCollaborationResult(data.collaborationTranscript.collaborationResult ?? null);
        } else {
          setAgentCards([]);
          setCollaborationResult(null);
        }
        setView("run");
      } catch {
        /* ignore */
      }
    },
    [project],
  );

  const resumeFromRun = useCallback(
    async (runId: string) => {
      if (!project.trim() || running) return;
      persistSettings();
      void saveProjectToRegistry();
      clearRunPanels();
      setView("run");
      setRunning(true);
      setViewingRunId(null);
      refreshState();
      const res = await apiFetch("/api/run/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          runId,
          ...runApiOptions,
          cursorRuntime,
          repoUrl: repoUrl || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        applyEvent({ type: "log", message: err.error ?? "Failed to resume run", level: "error" });
        setRunning(false);
      }
    },
    [
      project,
      running,
      persistSettings,
      saveProjectToRegistry,
      clearRunPanels,
      refreshState,
      runApiOptions,
      cursorRuntime,
      repoUrl,
      applyEvent,
    ],
  );

  const saveCollabConfig = async () => {
    setSavingCollabConfig(true);
    try {
      const res = await apiFetch("/api/collaboration/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          helperPrompt: helperPromptDraft,
          helperModel: collabConfig?.helperModel ?? "composer-2.5",
          maxTestRetries: collabConfig?.maxTestRetries ?? 3,
          maxIterations: collabConfig?.maxIterations ?? 10,
        }),
      });
      const data = (await res.json()) as CollaborationConfig;
      setCollabConfig(data);
      setHelperPromptDraft(data.helperPrompt);
    } finally {
      setSavingCollabConfig(false);
    }
  };

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
      body: JSON.stringify({ project, task, ...runApiOptions }),
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
        runtime: cursorRuntime,
        repoUrl: repoUrl || undefined,
        useReport: true,
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
  const viewingRunCanResume = Boolean(
    viewingRunId && runHistory.find((run) => run.id === viewingRunId)?.canResume && !running,
  );

  const hasCollaboration = agentCards.length > 0 || Boolean(collaborationResult) || (running && !skipCursor);

  const pipelineUiActive = useMemo(
    () =>
      running &&
      BROWSER_PHASES.some((key) => {
        const phase = phases[key as keyof PhaseMap];
        return phase?.status === "running";
      }),
    [running, phases],
  );

  const showLiveSession = replayMode || pipelineUiActive;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Assistant Test Runner</h1>
          <p className="mt-1 text-sm text-white/60">
            Local agent tests the app, helper agent (Cursor) implements — they collaborate until the task is done
          </p>
        </div>
        {view === "run" ? (
          <div className="flex flex-wrap items-center gap-2">
            {viewingRunId ? (
              <span className="rounded-full bg-violet-500/15 px-2 py-0.5 text-xs text-violet-100">
                Inspecting: {viewingRunLabel ?? viewingRunId}
              </span>
            ) : null}
            {viewingRunCanResume ? (
              <button
                type="button"
                onClick={() => viewingRunId && void resumeFromRun(viewingRunId)}
                className="rounded-md border border-amber-500/35 bg-amber-950/25 px-3 py-1.5 text-sm text-amber-100 hover:bg-amber-950/40"
              >
                Resume from failure
              </button>
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
              Local dev (cheatsheet — no git push or Railway wait)
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-white/80">
              <input
                type="radio"
                name="testTarget"
                checked={testTargetMode === "deployed"}
                onChange={() => setTestTargetMode("deployed")}
              />
              <span>
                Deployed (Railway — git push, wait for deploy, then test)
              </span>
            </label>
          </div>
          {testTargetMode === "deployed" && projectEnv ? (
            <div
              className={cn(
                "rounded-md border px-3 py-2 text-xs",
                projectEnv.has_railway_token
                  ? "border-green-500/30 bg-green-950/20 text-green-200"
                  : "border-amber-500/30 bg-amber-950/20 text-amber-200",
              )}
            >
              {projectEnv.has_railway_token ? (
                <p>
                  Railway token found in{" "}
                  <code className="text-white/90">{projectEnv.railway_token_path ?? ".agent/.env"}</code>
                  {" "}— deploy wait uses the project env.
                </p>
              ) : (
                <p>
                  No <code className="text-white/90">RAILWAY_TOKEN</code> in project{" "}
                  <code className="text-white/90">.agent/.env</code>. Add it to wait for Railway deploys.
                </p>
              )}
            </div>
          ) : null}
          <label className="flex items-center gap-2 text-sm text-white/70">
            <input type="checkbox" checked={skipCursor} onChange={(e) => setSkipCursor(e.target.checked)} />
            Skip helper agent (Cursor)
          </label>
          {!skipCursor ? (
            <div className="space-y-2 rounded-md border border-white/10 bg-black/20 p-3">
              {!config?.hasCursorApiKey ? (
                <p className="text-xs text-amber-300/90">
                  Set <code className="text-white/80">CURSOR_API_KEY</code> in ai-assistant/.env
                </p>
              ) : null}
              <label className="block text-xs text-white/60">Helper runtime</label>
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
            </div>
          ) : (
            <p className="text-xs text-white/45">Collaboration loop runs local agent only — no helper handoffs.</p>
          )}

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

          {!skipCursor ? (
            <details className="rounded-md border border-white/10 bg-black/20 text-sm">
              <summary className="cursor-pointer px-3 py-2.5 text-sm font-medium text-white/70">
                Helper agent context
                <span className="ml-2 text-xs font-normal text-white/45">
                  {collabConfig?.helperModel ?? "composer-2.5"}
                  {helperPromptDraft !== (collabConfig?.helperPrompt ?? "") ? " · unsaved" : ""}
                </span>
              </summary>
              <div className="space-y-3 border-t border-white/10 px-3 py-3">
                <p className="text-xs text-white/50">
                  Prepended to every helper prompt. The helper implements code, then requests UI checks via{" "}
                  <code className="text-white/70">### UI verification request</code> — the local agent runs those
                  and returns answer + report. Max {collabConfig?.maxTestRetries ?? 3} failed verification retries.
                </p>
                <textarea
                  className="min-h-32 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs leading-relaxed"
                  value={helperPromptDraft}
                  onChange={(e) => setHelperPromptDraft(e.target.value)}
                  placeholder="Explain to the helper agent how the collaboration works…"
                />
                <button
                  type="button"
                  disabled={savingCollabConfig}
                  onClick={() => void saveCollabConfig()}
                  className="rounded-md border border-white/20 px-3 py-1.5 text-xs text-white/90 disabled:opacity-50"
                >
                  {savingCollabConfig ? "Saving…" : "Save helper context"}
                </button>
              </div>
            </details>
          ) : null}

          <hr className="border-white/10" />
          <RunHistoryPanel
            runs={runHistory}
            loading={historyLoading}
            running={running}
            onInspect={(runId) => void viewRun(runId)}
            onResume={(runId) => void resumeFromRun(runId)}
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
              Run collaboration loop
            </button>
            <p className="text-center text-[10px] text-white/40">
              Triage → hand off or test → verify on live UI (max 3 failures)
            </p>
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
              disabled={running || !config?.hasCursorApiKey || skipCursor}
              onClick={startCursorOnly}
              className="rounded-md border border-violet-400/30 px-4 py-2 text-sm text-violet-100 disabled:opacity-50"
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
          {running ? (
            <CurrentRunStatus
              phases={phases}
              agentCards={agentCards}
              running={running}
              showPipelineStrip={hasCollaboration}
              testTargetMode={testTargetMode}
              skipDeploy={runApiOptions.skipDeploy}
            />
          ) : null}

          {showLiveSession ? (
            <section className="surface-card shrink-0 p-4">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">
                {replayMode ? "Recorded session" : "Live page — agent testing"}
              </h2>
              <PagePreview
                state={replayMode ? null : browserState}
                session={playwrightSession}
                frameIndex={sessionFrameIndex}
                onFrameIndexChange={setSessionFrameIndex}
                lastAction={lastActionLine}
                replayMode={replayMode}
              />
            </section>
          ) : null}

          <div
            className={cn(
              "grid min-h-0 flex-1 items-start gap-4",
              hasCollaboration || showLiveSession ? "lg:grid-cols-[1fr_340px] xl:grid-cols-[1fr_380px]" : "lg:grid-cols-1",
            )}
          >
            {hasCollaboration ? (
              <section className="surface-card flex min-h-[min(480px,60vh)] flex-col p-4">
                <CollaborationPanel
                  agentCards={agentCards}
                  collaborationResult={collaborationResult}
                  running={running}
                />
              </section>
            ) : (
              <section className="surface-card p-4">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white/50">
                  {replayMode ? "Recorded session" : "Live page"}
                </h2>
                <PagePreview
                  state={replayMode ? null : browserState}
                  session={playwrightSession}
                  frameIndex={sessionFrameIndex}
                  onFrameIndexChange={setSessionFrameIndex}
                  lastAction={lastActionLine}
                  replayMode={replayMode}
                />
              </section>
            )}

            <section className="surface-card flex max-h-[calc(100vh-10rem)] flex-col p-4 lg:sticky lg:top-4">
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
                agentCards={agentCards}
                collaborationResult={collaborationResult}
                hideCollaboration={hasCollaboration}
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

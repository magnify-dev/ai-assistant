import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { CollaborationPanel } from "@/components/CollaborationPanel";
import { CurrentRunStatus } from "@/components/CurrentRunStatus";
import { CheatsheetPanel } from "@/components/CheatsheetPanel";
import { ExplorationPanel } from "@/components/ExplorationPanel";
import { PageInspectPanel } from "@/components/PageInspectPanel";
import { ProjectSelector } from "@/components/ProjectSelector";
import { RunHistoryPanel } from "@/components/RunHistoryPanel";
import { RunProgressPanel } from "@/components/RunProgressPanel";
import { WebResearchPanel } from "@/components/WebResearchPanel";
import { cn } from "@/lib/utils";
import { apiFetch, eventsUrl } from "@/lib/api";
import { runTargetOptions } from "@/lib/runTarget";
import type {
  BrowserState,
  LocalEnvStatus,
  PlaywrightSession,
  ProjectsRegistry,
  RunHistoryEntry,
  RunHistoryPage,
  RunReport,
  StructuredTask,
  TestTarget,
} from "@/lib/projectTypes";
import { RUN_HISTORY_PAGE_SIZE } from "@/lib/projectTypes";
import type { AgentRunCard, CollaborationConfig, CollaborationResult } from "@/lib/collaborationTypes";
import {
  applyWebResearchEvent,
  isWebResearchEvent,
  type WebResearchState,
} from "@/lib/webResearchTypes";
import type { PhaseMap, RunEvent } from "@/types";
import type { WebCapture, WebCaptureBuildStatus, WebCaptureElement, WebCaptureReview } from "@/lib/webCaptureTypes";
import { buildUiDisplaySnapshot, isUiDebugEnabled, traceUiDisplay } from "@/lib/uiRunDebug";
import { UiRunDebugPanel } from "@/components/UiRunDebugPanel";

const BROWSER_PHASES = ["exploration", "ui_test", "web_research"] as const;

function applyWebCaptureProgress(
  event: RunEvent & {
    phase?: string;
    message?: string;
    capture?: WebCapture;
    error?: string;
    element_count?: number;
    screenshot_b64?: string;
    title?: string;
    interactables?: BrowserState["interactables"];
  },
  setCaptureBuild: (value: WebCaptureBuildStatus | null) => void,
  setWebCapture: (value: WebCapture | null) => void,
  setBrowserState: Dispatch<SetStateAction<BrowserState | null>>,
) {
  const phase = (event.phase ?? "geometry") as WebCaptureBuildStatus["phase"];
  setCaptureBuild({
    phase,
    url: event.url ? String(event.url) : undefined,
    message: event.message ? String(event.message) : undefined,
    error: event.error ? String(event.error) : undefined,
    elementCount: typeof event.element_count === "number" ? event.element_count : undefined,
    updatedAt: event.ts,
  });
  if (event.capture && typeof event.capture === "object") {
    setWebCapture(event.capture);
  }
  if (event.url) {
    setBrowserState((prev) => ({
      url: String(event.url),
      title: event.title ? String(event.title) : prev?.title,
      interactables: Array.isArray(event.interactables) ? event.interactables : (prev?.interactables ?? []),
      context: prev?.context ?? "web_exploration",
      node_url: prev?.node_url,
      ts: event.ts,
      screenshot_b64: event.screenshot_b64 ? String(event.screenshot_b64) : prev?.screenshot_b64,
      error: prev?.error,
    }));
  }
}

const SETTINGS_KEY = "test_runner_settings_v2";
const VIEW_KEY = "test_runner_view_v1";

function loadPersistedView(): "config" | "run" {
  try {
    return sessionStorage.getItem(VIEW_KEY) === "run" ? "run" : "config";
  } catch {
    return "config";
  }
}

function persistView(view: "config" | "run") {
  try {
    sessionStorage.setItem(VIEW_KEY, view);
  } catch {
    /* ignore */
  }
}

type StoredSettings = {
  project?: string;
  task?: string;
  repoUrl?: string;
  cursorRuntime?: "cloud" | "local";
  testTarget?: "local" | "deployed";
  skipCursor?: boolean; // legacy — ignored; collaboration always runs
  /** Legacy setting — migrated to testTarget on load. */
  skipDeploy?: boolean;
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

type OllamaModelOption = {
  id: string;
  label: string;
  description: string;
  installed: boolean;
};

type OllamaSwitchUiState = {
  active: boolean;
  step?: string;
  message?: string;
  progress?: number;
  fromModel?: string;
  toModel?: string;
  error?: string;
};

type OllamaStatus = {
  url: string;
  model: string;
  reachable: boolean;
  modelAvailable: boolean;
  modelLoaded: boolean;
  loadedModels: string[];
  modelOptions?: OllamaModelOption[];
  switch?: OllamaSwitchUiState;
};

type CursorHelperStatus = {
  ok: boolean;
  runtime: "local" | "cloud";
  errors: string[];
  warnings: string[];
  cursorInstalled: boolean;
  cursorRunning: boolean;
  hasApiKey: boolean;
  projectPathOk: boolean;
};

type Config = {
  defaultProject: string;
  hasCursorApiKey: boolean;
  cursorHelper?: CursorHelperStatus;
  ollama?: OllamaStatus;
};

function optimisticStartCard(): AgentRunCard {
  return {
    id: "optimistic-start",
    agent: "local",
    agentLabel: "Local agent",
    iteration: 1,
    status: "running",
    startedAt: new Date().toISOString(),
    summary: "Classifying task and starting…",
  };
}

function optimisticRunPhases(message = "Preparing run…"): PhaseMap {
  return {
    collaboration: { status: "running", message },
  };
}

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
    collaborationActive?: boolean;
    collaborationResult?: CollaborationResult | null;
    webResearch?: WebResearchState | null;
    playwrightSession?: PlaywrightSession | null;
    webCapture?: WebCapture | null;
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
    setWebResearch?: (v: WebResearchState | null) => void;
    setPlaywrightSession?: (v: PlaywrightSession | null) => void;
    setWebCapture?: (v: WebCapture | null) => void;
  },
  options?: { protectActiveRun?: boolean; protectInspectedSession?: boolean },
) {
  const protect = options?.protectActiveRun ?? false;
  const protectSession = options?.protectInspectedSession ?? false;

  // While the client believes a run is active, ignore a "not running" snapshot only when
  // the server has no final result either (start race). If the server has a result, the
  // run genuinely ended while we were disconnected — accept the full snapshot.
  const serverHasResult = Boolean(data.collaborationResult) || Boolean(data.lastResult);
  if (protect && data.running === false && !serverHasResult) {
    if (isUiDebugEnabled()) {
      console.warn(
        "[UI run] refreshState ignored — server snapshot running=false while client protects active run",
      );
    }
    return;
  }

  if (typeof data.running === "boolean") {
    setters.setRunning(data.running);
  }

  if (data.phase) setters.setActivePhase(data.phase);
  if (data.phases) {
    const hasPhases = Object.keys(data.phases).length > 0;
    if (!protect || hasPhases) setters.setPhases(data.phases);
  }
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
    if (!protect || data.agentCards.length > 0) {
      setters.setAgentCards(data.agentCards as AgentRunCard[]);
    }
  }
  if (data.collaborationResult !== undefined && setters.setCollaborationResult) {
    setters.setCollaborationResult(data.collaborationResult as CollaborationResult | null);
  }
  if (data.webResearch !== undefined && setters.setWebResearch) {
    setters.setWebResearch(data.webResearch as WebResearchState | null);
  }
  if (
    data.playwrightSession !== undefined &&
    setters.setPlaywrightSession &&
    !(protectSession && data.playwrightSession === null)
  ) {
    setters.setPlaywrightSession(data.playwrightSession as PlaywrightSession | null);
  }
  if (data.webCapture !== undefined && setters.setWebCapture) {
    setters.setWebCapture(data.webCapture as WebCapture | null);
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
  if (event.type === "web_research_progress") {
    const e = event as { step?: string; url?: string; message?: string };
    return `[web] ${e.step ?? ""} ${e.url ?? e.message ?? ""}`.trim();
  }
  if (event.type === "web_research_result") {
    const e = event as { pages_fetched?: number; facts_added?: number };
    return `[web] ${e.pages_fetched ?? 0} page(s), ${e.facts_added ?? 0} fact(s)`;
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
  if (event.type === "ollama_switch") {
    const e = event as { step?: string; progress?: number; message?: string };
    const progress = typeof e.progress === "number" ? ` ${e.progress}%` : "";
    return `[ollama] ${e.step ?? ""}${progress} ${e.message ?? ""}`.trim();
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
  const [cursorRuntime, setCursorRuntime] = useState<"cloud" | "local">("local");
  const [testTargetMode, setTestTargetMode] = useState<"local" | "deployed">("local");
  const [running, setRunning] = useState(false);
  const [phases, setPhases] = useState<PhaseMap>({});
  const [activePhase, setActivePhase] = useState<string>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [preloadingOllama, setPreloadingOllama] = useState(false);
  const [pullingOllamaModel, setPullingOllamaModel] = useState<string | null>(null);
  const [changingOllamaModel, setChangingOllamaModel] = useState(false);
  const [ollamaSwitch, setOllamaSwitch] = useState<OllamaSwitchUiState | null>(null);
  const [lastResult, setLastResult] = useState<{ overall_ok?: boolean } | null>(null);
  const [browserState, setBrowserState] = useState<BrowserState | null>(null);
  const [testTarget, setTestTarget] = useState<TestTarget | null>(null);
  const [structuredTask, setStructuredTask] = useState<StructuredTask | null>(null);
  const [runReport, setRunReport] = useState<RunReport | null>(null);
  const [lastStep, setLastStep] = useState<RunEvent | null>(null);
  const [view, setViewState] = useState<"config" | "run">(loadPersistedView);
  const [startingRun, setStartingRun] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [hasLatestRun, setHasLatestRun] = useState(false);
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyTotal, setHistoryTotal] = useState(0);
  const preloadedHistoryRef = useRef<{ offset: number; page: RunHistoryPage } | null>(null);
  const historyFetchSeqRef = useRef(0);
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);
  const [playwrightSession, setPlaywrightSession] = useState<PlaywrightSession | null>(null);
  const [sessionFrameIndex, setSessionFrameIndex] = useState(0);
  const [agentCards, setAgentCards] = useState<AgentRunCard[]>([]);
  const [collaborationResult, setCollaborationResult] = useState<CollaborationResult | null>(null);
  const [webResearch, setWebResearch] = useState<WebResearchState | null>(null);
  const [webCapture, setWebCapture] = useState<WebCapture | null>(null);
  const [captureBuild, setCaptureBuild] = useState<WebCaptureBuildStatus | null>(null);
  const [latestWebCaptureReview, setLatestWebCaptureReview] = useState<WebCaptureReview | null>(null);
  const [collabConfig, setCollabConfig] = useState<CollaborationConfig | null>(null);
  const [helperPromptDraft, setHelperPromptDraft] = useState("");
  const [savingCollabConfig, setSavingCollabConfig] = useState(false);
  const [projectEnv, setProjectEnv] = useState<LocalEnvStatus | null>(null);
  const [interveneNote, setInterveneNote] = useState("");
  const logRef = useRef<HTMLPreElement>(null);
  const runningRef = useRef(running);
  /** True from Run click until the server acknowledges start — avoids refreshState clobbering UI. */
  const startingRunRef = useRef(false);
  /** True between collaboration_start and collaboration_done — the python pipeline emits
   * its own done/process_exit mid-loop and those must not end the run in the UI. */
  const collabActiveRef = useRef(false);
  const viewingRunIdRef = useRef<string | null>(null);
  viewingRunIdRef.current = viewingRunId;

  const setView = useCallback((next: "config" | "run") => {
    setViewState(next);
    persistView(next);
  }, []);

  const showRunView = useCallback(() => setView("run"), [setView]);
  const showConfigView = useCallback(() => setView("config"), [setView]);

  runningRef.current = running || startingRunRef.current || collabActiveRef.current;

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
    setWebResearch(null);
    setWebCapture(null);
    setCaptureBuild(null);
    setLatestWebCaptureReview(null);
  }, []);

  const persistSettings = useCallback(() => {
    saveStoredSettings({
      project,
      task,
      repoUrl,
      cursorRuntime,
      testTarget: testTargetMode,
    });
  }, [project, task, repoUrl, cursorRuntime, testTargetMode]);

  const saveProjectToRegistry = useCallback(async () => {
    if (!project.trim()) return;
    persistSettings();
    await apiFetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: project,
        settings: { task, testTarget: testTargetMode, cursorRuntime, repoUrl },
      }),
    });
  }, [project, task, testTargetMode, cursorRuntime, repoUrl, persistSettings]);

  const applyProjectSettings = useCallback((settings?: ProjectsRegistry["projects"][0]["settings"]) => {
    if (!settings) return;
    if (settings.task !== undefined) setTask(settings.task);
    if (settings.testTarget) setTestTargetMode(settings.testTarget);
    else if (settings.skipDeploy !== undefined) setTestTargetMode(settings.skipDeploy ? "local" : "deployed");
    if (settings.cursorRuntime) setCursorRuntime(settings.cursorRuntime);
    if (settings.repoUrl !== undefined) setRepoUrl(settings.repoUrl);
  }, []);

  const refreshConfig = useCallback(() => {
    const query = project.trim() ? `?project=${encodeURIComponent(project)}` : "";
    apiFetch(`/api/config${query}`)
      .then((r) => r.json())
      .then((data: Config) => {
        setConfig(data);
        if (data.ollama) {
          setOllamaStatus(data.ollama);
          if (data.ollama.switch) setOllamaSwitch(data.ollama.switch);
        }
      })
      .catch(() => {});
  }, [project]);

  const refreshState = useCallback(() => {
    apiFetch("/api/state")
      .then((r) => r.json())
      .then((data) => {
        if (data.collaborationActive) collabActiveRef.current = true;
        applyStateFromServer(
          data,
          {
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
            setWebResearch,
            setPlaywrightSession,
            setWebCapture,
          },
          {
            protectActiveRun: runningRef.current || startingRunRef.current,
            protectInspectedSession: Boolean(viewingRunIdRef.current),
          },
        );
      })
      .catch(() => {});
  }, []);

  const fetchHistoryPage = useCallback(async (offset: number) => {
    const response = await apiFetch(
      `/api/project/run-history?path=${encodeURIComponent(project)}&offset=${offset}&limit=${RUN_HISTORY_PAGE_SIZE}`,
    );
    return (await response.json()) as RunHistoryPage;
  }, [project]);

  const preloadHistoryPage = useCallback(
    (offset: number) => {
      if (!project.trim() || offset < 0) return;
      void fetchHistoryPage(offset)
        .then((page) => {
          if (!page.runs?.length) return;
          preloadedHistoryRef.current = { offset, page };
        })
        .catch(() => {
          preloadedHistoryRef.current = null;
        });
    },
    [fetchHistoryPage, project],
  );

  const applyHistoryPage = useCallback(
    (page: RunHistoryPage, mode: "replace" | "append") => {
      const runs = page.runs ?? [];
      setRunHistory((prev) => (mode === "append" ? [...prev, ...runs] : runs));
      setHistoryTotal(page.total ?? runs.length);
      setHistoryHasMore(Boolean(page.hasMore));
      setHasLatestRun(runs.some((run) => run.id === "current"));
      if (page.hasMore) {
        preloadHistoryPage(page.offset + runs.length);
      } else {
        preloadedHistoryRef.current = null;
      }
    },
    [preloadHistoryPage],
  );

  const loadRunHistory = useCallback(() => {
    if (!project.trim()) {
      setRunHistory([]);
      setHistoryTotal(0);
      setHistoryHasMore(false);
      setHasLatestRun(false);
      preloadedHistoryRef.current = null;
      return;
    }
    const fetchId = ++historyFetchSeqRef.current;
    setHistoryLoading(true);
    void fetchHistoryPage(0)
      .then((page) => {
        if (fetchId !== historyFetchSeqRef.current) return;
        preloadedHistoryRef.current = null;
        applyHistoryPage(page, "replace");
      })
      .catch(() => {
        if (fetchId !== historyFetchSeqRef.current) return;
        setRunHistory([]);
        setHistoryTotal(0);
        setHistoryHasMore(false);
        setHasLatestRun(false);
        preloadedHistoryRef.current = null;
      })
      .finally(() => {
        if (fetchId === historyFetchSeqRef.current) setHistoryLoading(false);
      });
  }, [applyHistoryPage, fetchHistoryPage, project]);

  const loadOlderRunHistory = useCallback(() => {
    if (!project.trim() || historyLoadingMore || !historyHasMore) return;

    const nextOffset = runHistory.length;
    const cached = preloadedHistoryRef.current;
    if (cached && cached.offset === nextOffset) {
      applyHistoryPage(cached.page, "append");
      preloadedHistoryRef.current = null;
      return;
    }

    setHistoryLoadingMore(true);
    void fetchHistoryPage(nextOffset)
      .then((page) => applyHistoryPage(page, "append"))
      .finally(() => setHistoryLoadingMore(false));
  }, [
    applyHistoryPage,
    fetchHistoryPage,
    historyHasMore,
    historyLoadingMore,
    project,
    runHistory.length,
  ]);

  const loadRunReport = useCallback(() => {
    if (!project.trim() || running) return;
    apiFetch(`/api/project/run-report?path=${encodeURIComponent(project)}`)
      .then((r) => r.json())
      .then((data: {
        report?: RunReport | null;
        pageReport?: string;
        hasRun?: boolean;
        playwrightSession?: PlaywrightSession | null;
        webCapture?: WebCapture | null;
        webCaptureReviews?: WebCaptureReview[];
      }) => {
        if (runningRef.current) return;
        if (viewingRunIdRef.current && viewingRunIdRef.current !== "current") return;
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
            if (!viewingRunIdRef.current) setViewingRunId("current");
          } else if (!running && viewingRunIdRef.current === "current") {
            setPlaywrightSession(data.playwrightSession ?? report.playwright_session ?? null);
          }
        }
        if (data.webCapture) {
          setWebCapture(data.webCapture);
          setLatestWebCaptureReview(data.webCaptureReviews?.at(-1) ?? null);
        } else {
          setWebCapture(null);
          setLatestWebCaptureReview(null);
        }
        setHasLatestRun(Boolean(data.hasRun));
      })
      .catch(() => setHasLatestRun(false));
  }, [project, running]);

  useEffect(() => {
    if (!running) loadRunReport();
    loadRunHistory();
  }, [loadRunReport, loadRunHistory, running, project]);

  useEffect(() => {
    if ((running || startingRun) && view !== "run") showRunView();
  }, [running, startingRun, view, showRunView]);

  useEffect(() => {
    if (view === "run" && !runningRef.current && !startingRunRef.current) refreshState();
  }, [view, refreshState]);

  useEffect(() => {
    if (project.trim()) refreshConfig();
  }, [project, refreshConfig]);

  useEffect(() => {
    const stored = loadStoredSettings();
    apiFetch("/api/config")
      .then((r) => r.json())
      .then((data: Config) => {
        setConfig(data);
        if (data.ollama) {
          setOllamaStatus(data.ollama);
          if (data.ollama.switch) setOllamaSwitch(data.ollama.switch);
        }
        setProject(stored.project || data.defaultProject || "");
        if (stored.task) setTask(stored.task);
        if (stored.repoUrl) setRepoUrl(stored.repoUrl);
        if (stored.cursorRuntime) setCursorRuntime(stored.cursorRuntime);
        if (stored.testTarget) setTestTargetMode(stored.testTarget);
        else if (stored.skipDeploy !== undefined) setTestTargetMode(stored.skipDeploy ? "local" : "deployed");
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
      setPhases((prev) => {
        const next = { ...prev };
        if (event.status === "running") {
          for (const key of Object.keys(next)) {
            if (key !== event.phase && next[key]?.status === "running") {
              next[key] = { ...next[key], status: "done" };
            }
          }
        }
        next[event.phase!] = { status: event.status, message: event.message };
        return next;
      });
    }
    if (event.type === "run_state") {
      const isRunning = Boolean((event as { running?: boolean }).running);
      if (!isRunning) {
        // Child runners (web research / python) emit run_state:false when their process
        // exits mid-collaboration — only collaboration_done should end the UI run.
        if (startingRunRef.current || collabActiveRef.current || runningRef.current) {
          return;
        }
        setRunning(false);
      } else if (!collabActiveRef.current) {
        setRunning(true);
      }
    }
    if (event.type === "step") {
      setLastStep(event);
      if (event.mode === "web") {
        setWebResearch((prev) =>
          applyWebResearchEvent(prev, { ...event, type: "web_step", step: event }),
        );
      }
    }
    if (event.type === "web_capture_progress") {
      applyWebCaptureProgress(
        event as RunEvent & { phase?: string; message?: string; capture?: WebCapture },
        setCaptureBuild,
        setWebCapture,
        setBrowserState,
      );
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
      const capture = (event as RunEvent & { web_capture?: WebCapture }).web_capture;
      if (capture) {
        setWebCapture(capture);
        setCaptureBuild({
          phase: "complete",
          url: event.url,
          elementCount: capture.elements?.length,
          message: "Map ready — inspect below",
          updatedAt: event.ts,
        });
      }
      if (String(event.context ?? "").startsWith("web_")) {
        setWebResearch((prev) =>
          applyWebResearchEvent(prev, {
            ...event,
            type: "web_page_snapshot",
            snapshot: event,
          }),
        );
      }
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
    if (event.type === "playwright_session") {
      const session = (event as { session?: PlaywrightSession }).session;
      const source = (event as { source?: PlaywrightSession["source"] }).source ?? "web";
      if (session) {
        setPlaywrightSession({ ...session, source });
        setSessionFrameIndex(0);
      }
    }
    if (event.type === "run_report" && (event as { report?: RunReport }).report) {
      const report = (event as unknown as { report: RunReport }).report;
      setRunReport(report);
      const incoming = report.playwright_session;
      if (incoming) {
        setPlaywrightSession((prev) => {
          const incomingUrl = incoming.frames?.[0]?.url ?? "";
          const prevIsWeb = prev?.source === "web";
          const incomingIsLocal =
            incomingUrl.includes("localhost") || incomingUrl.includes("127.0.0.1");
          if (prevIsWeb && incomingIsLocal) return prev;
          return { ...incoming, source: prevIsWeb ? "web" : "ui" };
        });
      }
    }
    if (event.type === "site_map" || event.type === "nav_tree" || event.type === "agent_decision") {
      window.dispatchEvent(new CustomEvent("test-runner-event", { detail: event }));
    }
    if (isWebResearchEvent(event)) {
      setWebResearch((prev) => applyWebResearchEvent(prev, event));
      if (
        event.type === "web_page_snapshot" ||
        event.type === "web_snapshot" ||
        event.type === "web_semantic_snapshot"
      ) {
        const e = event as RunEvent & {
          snapshot?: Partial<BrowserState> & { web_capture?: WebCapture };
          page?: Partial<BrowserState> & { web_capture?: WebCapture };
          web_capture?: WebCapture;
        };
        const snapshot: Partial<BrowserState> & { web_capture?: WebCapture } =
          e.snapshot ?? e.page ?? (e as unknown as Partial<BrowserState> & { web_capture?: WebCapture });
        if (snapshot.url) {
          setBrowserState({
            url: snapshot.url,
            title: snapshot.title,
            interactables: snapshot.interactables ?? [],
            context: snapshot.context ?? "web_exploration",
            node_url: snapshot.node_url,
            ts: event.ts,
            screenshot_b64: snapshot.screenshot_b64,
            error: snapshot.error,
          });
        }
        if (snapshot.web_capture) {
          setWebCapture(snapshot.web_capture);
          setCaptureBuild({
            phase: "complete",
            url: snapshot.url,
            elementCount: snapshot.web_capture.elements?.length,
            message: "Map ready — inspect below",
            updatedAt: event.ts,
          });
        }
      }
      if (event.type === "web_research_progress" || event.type === "web_help_request") {
        setLogOpen(true);
      }
    }
    if (event.type === "agent_card" && (event as { card?: AgentRunCard }).card) {
      const card = (event as unknown as { card: AgentRunCard }).card;
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
      collabActiveRef.current = true;
      startingRunRef.current = false;
      setStartingRun(false);
      const resumed = Boolean((event as { resumed?: boolean }).resumed);
      if (!resumed) {
        setAgentCards((prev) => (prev.some((c) => c.status === "running") ? prev : [optimisticStartCard()]));
      }
      setCollaborationResult(null);
      setRunning(true);
    }
    if (event.type === "phases_reset") {
      setPhases({});
      setStructuredTask(null);
      setRunReport(null);
      setTestTarget(null);
      setLastResult(null);
    }
    if (event.type === "collaboration_done") {
      if (startingRunRef.current) return;
      collabActiveRef.current = false;
      startingRunRef.current = false;
      setStartingRun(false);
      const e = event as { ok?: boolean; answer?: string; error?: string; iterations?: number };
      setCollaborationResult({ ok: e.ok, answer: e.answer, error: e.error, iterations: e.iterations });
      setAgentCards((prev) =>
        prev.map((c) =>
          c.status === "running"
            ? {
                ...c,
                status: "failed" as const,
                summary: e.error?.toLowerCase().includes("cancel") ? "Cancelled" : "Stopped",
                completedAt: new Date().toISOString(),
                streamStatus: undefined,
              }
            : c,
        ),
      );
      setRunning(false);
      runningRef.current = false;
      loadRunHistory();
    }
    if (event.type === "run_cleared") {
      if (runningRef.current || collabActiveRef.current || startingRunRef.current) {
        setStructuredTask(null);
        setRunReport(null);
        setTestTarget(null);
        setBrowserState(null);
        setLastStep(null);
        setLastResult(null);
        setViewingRunId(null);
        setWebResearch(null);
        setWebCapture(null);
        setLatestWebCaptureReview(null);
        return;
      }
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
      setWebResearch(null);
      setWebCapture(null);
      setLatestWebCaptureReview(null);
    }
    if (event.type === "done" && !collabActiveRef.current && !startingRunRef.current) {
      setRunning(false);
      setLastResult({ overall_ok: (event as { overall_ok?: boolean }).overall_ok });
      loadRunHistory();
    }
    if (event.type === "process_exit" && !collabActiveRef.current && !startingRunRef.current) {
      setRunning(false);
    }
    if (event.type === "ollama_switch") {
      const e = event as RunEvent & {
        step?: string;
        message?: string;
        progress?: number;
        fromModel?: string;
        toModel?: string;
        ollama?: OllamaStatus;
      };
      const active = e.step !== "done" && e.step !== "error";
      setOllamaSwitch({
        active,
        step: e.step,
        message: e.message,
        progress: e.progress,
        fromModel: e.fromModel,
        toModel: e.toModel,
        error: e.step === "error" ? e.message : undefined,
      });
      if (e.ollama) {
        setOllamaStatus(e.ollama);
      } else if (e.toModel) {
        setOllamaStatus((prev) => (prev ? { ...prev, model: e.toModel! } : prev));
      }
      if (e.step === "done" || e.step === "error") {
        setChangingOllamaModel(false);
        setPullingOllamaModel(null);
        setPreloadingOllama(false);
      }
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
    if (ollamaSwitch?.active) return;
    setPreloadingOllama(true);
    setOllamaSwitch({
      active: true,
      step: "loading",
      message: `Loading ${ollamaStatus?.model ?? "model"} into VRAM…`,
      progress: 10,
      toModel: ollamaStatus?.model,
    });
    try {
      const res = await apiFetch("/api/ollama/preload", { method: "POST" });
      const body = (await res.json()) as { error?: string; message?: string; ollama?: OllamaStatus };
      if (!res.ok) {
        setOllamaSwitch({
          active: false,
          step: "error",
          message: body.error ?? "Ollama preload failed",
          error: body.error ?? "Ollama preload failed",
        });
        applyEvent({ type: "log", message: body.error ?? "Ollama preload failed", level: "error" });
      } else {
        applyOllamaFromResponse(body.ollama);
        applyEvent({ type: "log", message: body.message ?? "Ollama model ready", level: "info" });
      }
      refreshConfig();
    } finally {
      setPreloadingOllama(false);
    }
  };

  const applyOllamaFromResponse = (ollama?: OllamaStatus) => {
    if (!ollama) return;
    setOllamaStatus(ollama);
    if (ollama.switch) setOllamaSwitch(ollama.switch);
  };

  const changeOllamaModel = async (model: string, opts?: { forceSwitch?: boolean }) => {
    if (!model || ollamaSwitch?.active) return;
    const selected = ollamaStatus?.modelOptions?.find((opt) => opt.id === model);
    if (selected && !selected.installed) {
      setOllamaStatus((prev) =>
        prev ? { ...prev, model, modelAvailable: false, modelLoaded: false } : prev,
      );
      return;
    }
    if (
      !opts?.forceSwitch &&
      model === ollamaStatus?.model &&
      ollamaStatus.modelAvailable &&
      ollamaStatus.modelLoaded
    ) {
      return;
    }
    const previousModel = ollamaStatus?.model;
    setChangingOllamaModel(true);
    setOllamaSwitch({
      active: true,
      step: "checking",
      message: `Switching to ${model}…`,
      progress: 0,
      fromModel: previousModel,
      toModel: model,
    });
    setOllamaStatus((prev) => (prev ? { ...prev, model } : prev));
    try {
      const res = await apiFetch("/api/ollama/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const body = (await res.json()) as { error?: string; message?: string; ollama?: OllamaStatus };
      if (!res.ok) {
        if (previousModel) {
          setOllamaStatus((prev) => (prev ? { ...prev, model: previousModel } : prev));
        }
        setOllamaSwitch({
          active: false,
          step: "error",
          message: body.error ?? "Failed to switch model",
          error: body.error ?? "Failed to switch model",
        });
        applyEvent({ type: "log", message: body.error ?? "Failed to switch model", level: "error" });
        if (body.ollama) applyOllamaFromResponse(body.ollama);
        return;
      }
      applyOllamaFromResponse(body.ollama);
      applyEvent({ type: "log", message: body.message ?? `Switched to ${model}`, level: "info" });
    } catch (err) {
      if (previousModel) {
        setOllamaStatus((prev) => (prev ? { ...prev, model: previousModel } : prev));
      }
      const message = err instanceof Error ? err.message : "Failed to switch model";
      setOllamaSwitch({ active: false, step: "error", message, error: message });
      applyEvent({ type: "log", message, level: "error" });
    } finally {
      setChangingOllamaModel(false);
      refreshConfig();
    }
  };

  const pullOllamaModel = async (model: string) => {
    if (ollamaSwitch?.active) return;
    setPullingOllamaModel(model);
    setOllamaSwitch({
      active: true,
      step: "downloading",
      message: `Downloading ${model}…`,
      progress: 0,
      toModel: model,
    });
    try {
      const res = await apiFetch("/api/ollama/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const body = (await res.json()) as { error?: string; message?: string; ollama?: OllamaStatus };
      if (!res.ok) {
        setOllamaSwitch({
          active: false,
          step: "error",
          message: body.error ?? "Model download failed",
          error: body.error ?? "Model download failed",
        });
        applyEvent({ type: "log", message: body.error ?? "Model download failed", level: "error" });
        if (body.ollama) applyOllamaFromResponse(body.ollama);
        return;
      }
      applyOllamaFromResponse(body.ollama);
      applyEvent({ type: "log", message: body.message ?? `${model} downloaded`, level: "info" });
      if (ollamaStatus?.model === model) {
        await changeOllamaModel(model, { forceSwitch: true });
      }
    } finally {
      setPullingOllamaModel(null);
      refreshConfig();
    }
  };

  const viewRun = useCallback(
    async (runId: string) => {
      if (!project.trim()) return;
      showRunView();
      setViewingRunId(runId);
      try {
        const res = await apiFetch(
          `/api/project/run?path=${encodeURIComponent(project)}&runId=${encodeURIComponent(runId)}`,
        );
        const data = (await res.json()) as {
          report?: RunReport | null;
          pageReport?: string;
          structuredTask?: StructuredTask;
          playwrightSession?: PlaywrightSession | null;
          webCapture?: WebCapture | null;
          webCaptureReviews?: WebCaptureReview[];
          collaborationTranscript?: {
            task?: string;
            agentCards?: AgentRunCard[];
            collaborationResult?: CollaborationResult;
          } | null;
        };
        const transcript = data.collaborationTranscript;
        const hasSession = Boolean(data.playwrightSession?.frames?.length);
        if (!data.report && !transcript?.agentCards?.length && !hasSession) return;

        const transcriptSavedAt = (transcript as { savedAt?: string } | undefined)?.savedAt;
        const reportGeneratedAt = (data.report as { generated_at?: string } | undefined)?.generated_at;
        const transcriptPreferred = Boolean(
          transcript &&
            (!data.report ||
              (transcriptSavedAt &&
                Date.parse(transcriptSavedAt) >= Date.parse(String(reportGeneratedAt ?? 0)))),
        );

        setPlaywrightSession(data.playwrightSession ?? null);
        setWebCapture(data.webCapture ?? null);
        setLatestWebCaptureReview(data.webCaptureReviews?.at(-1) ?? null);
        setSessionFrameIndex(0);

        if (data.report) {
          const report = { ...data.report };
          if (!report.page_report && data.pageReport) {
            report.page_report = data.pageReport;
          }
          setRunReport(report);
          if (!transcriptPreferred) {
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
          }
        } else {
          setRunReport(null);
        }

        if (transcriptPreferred || !data.report) {
          setStructuredTask(
            transcript?.task
              ? { summary: transcript.task, source_text: transcript.task }
              : null,
          );
          setTestTarget(null);
          setLastResult({
            overall_ok: transcript?.collaborationResult?.ok ?? data.report?.overall_ok ?? false,
          });
          setPhases((prev) =>
            Object.keys(prev).length
              ? prev
              : {
                  web_research: {
                    status: transcript?.collaborationResult?.ok ? "done" : "failed",
                    message: transcript?.collaborationResult?.error ?? transcript?.collaborationResult?.answer ?? "Web research",
                  },
                },
          );
        }

        if (transcript?.agentCards?.length) {
          setAgentCards(transcript.agentCards.map((c) => ({ ...c, historical: true })));
          setCollaborationResult(transcript.collaborationResult ?? null);
        } else {
          setAgentCards([]);
          setCollaborationResult(null);
        }
      } catch {
        /* ignore */
      }
    },
    [project, showRunView],
  );

  const resumeFromRun = useCallback(
    async (runId: string, note?: string) => {
      if (!project.trim() || running) return;
      startingRunRef.current = true;
      collabActiveRef.current = true;
      runningRef.current = true;
      setStartingRun(true);
      showRunView();
      setRunning(true);
      setViewingRunId(null);
      persistSettings();
      void saveProjectToRegistry();
      clearRunPanels();
      setAgentCards([optimisticStartCard()]);
      setPhases(optimisticRunPhases("Resuming run…"));
      setActivePhase("collaboration");
      try {
        const res = await apiFetch("/api/run/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project,
            runId,
            note: note?.trim() || undefined,
            ...runApiOptions,
            cursorRuntime,
            repoUrl: repoUrl || undefined,
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          applyEvent({ type: "log", message: err.error ?? "Failed to resume run", level: "error" });
          collabActiveRef.current = false;
          startingRunRef.current = false;
          setStartingRun(false);
          setRunning(false);
          setAgentCards([]);
        } else {
          setInterveneNote("");
        }
      } catch (err) {
        applyEvent({
          type: "log",
          message: err instanceof Error ? err.message : "Failed to resume run",
          level: "error",
        });
        collabActiveRef.current = false;
        startingRunRef.current = false;
        setStartingRun(false);
        setRunning(false);
        setAgentCards([]);
      }
    },
    [
      project,
      running,
      persistSettings,
      saveProjectToRegistry,
      clearRunPanels,
      runApiOptions,
      cursorRuntime,
      repoUrl,
      applyEvent,
      showRunView,
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
          maxQuestionRounds: collabConfig?.maxQuestionRounds ?? 2,
          maxInfoRequests: collabConfig?.maxInfoRequests ?? 2,
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

  const lastActionLine = useMemo(() => {
    const webSteps = webResearch?.steps;
    if (webSteps?.length) {
      const last = webSteps[webSteps.length - 1];
      const mark = last.ok === false || last.progress === false ? "✗" : last.ok === true ? "✓" : "…";
      const err = last.error ?? last.message;
      return `${last.action ?? "action"} ${last.target_id ?? ""} ${mark}${err ? ` — ${err}` : ""}`.trim();
    }
    if (!lastStep || lastStep.type !== "step") return undefined;
    const mark = lastStep.ok ? "✓" : "✗";
    return `${lastStep.action} ${lastStep.target} ${mark}${lastStep.message ? ` — ${lastStep.message}` : ""}`.trim();
  }, [lastStep, webResearch?.steps]);

  const replayMode = Boolean(playwrightSession?.frames?.length) && !running;
  const viewingRunLabel = runHistory.find((run) => run.id === viewingRunId)?.label;
  const viewingRunCanResume = Boolean(
    viewingRunId && runHistory.find((run) => run.id === viewingRunId)?.canResume && !running,
  );
  const currentRunResumable = runHistory.some((run) => run.id === "current" && run.canResume);
  const resumeTargetId = !running
    ? viewingRunId && viewingRunCanResume
      ? viewingRunId
      : currentRunResumable
        ? "current"
        : null
    : null;

  const inRunMode = running || startingRun;
  const showConfigPanel = view === "config";

  const hasCollaboration = agentCards.length > 0 || Boolean(collaborationResult) || inRunMode;

  const pipelineUiActive = useMemo(
    () =>
      inRunMode &&
      BROWSER_PHASES.some((key) => {
        const phase = phases[key as keyof PhaseMap];
        return phase?.status === "running";
      }),
    [inRunMode, phases],
  );

  const webBrowseActive = useMemo(
    () =>
      inRunMode &&
      (phases.web_research?.status === "running" ||
        Boolean(webResearch?.currentUrl || webResearch?.snapshot?.screenshot_b64) ||
        Boolean(
          browserState?.url &&
            !/localhost|127\.0\.0\.1/i.test(browserState.url),
        ) ||
        playwrightSession?.source === "web"),
    [inRunMode, phases, browserState, playwrightSession, webResearch],
  );

  const showLiveSession = replayMode || pipelineUiActive || webBrowseActive;

  const uiDebugSnapshot = useMemo(
    () =>
      buildUiDisplaySnapshot({
        trigger: "state",
        view,
        running,
        startingRun,
        inRunMode,
        showConfigPanel,
        viewingRunId,
        replayMode,
        pipelineUiActive,
        webBrowseActive,
        showLiveSession,
        hasCollaboration,
        webResearch,
        resumeTargetId,
        logOpen,
        agentCardsCount: agentCards.length,
        eventsCount: events.length,
        phaseKeysCount: Object.keys(phases).length,
        hasLatestRun,
        refs: {
          startingRunRef: startingRunRef.current,
          collabActiveRef: collabActiveRef.current,
          runningRef: runningRef.current,
        },
      }),
    [
      view,
      running,
      startingRun,
      inRunMode,
      showConfigPanel,
      viewingRunId,
      replayMode,
      pipelineUiActive,
      webBrowseActive,
      showLiveSession,
      hasCollaboration,
      webResearch,
      resumeTargetId,
      logOpen,
      agentCards.length,
      events.length,
      phases,
      hasLatestRun,
    ],
  );

  const lastUiTraceKeyRef = useRef("");
  const traceUi = useCallback(
    (trigger: string, note?: string) => {
      traceUiDisplay({ ...uiDebugSnapshot, trigger, note });
    },
    [uiDebugSnapshot],
  );

  useEffect(() => {
    const key = JSON.stringify({
      view: uiDebugSnapshot.view,
      running: uiDebugSnapshot.running,
      startingRun: uiDebugSnapshot.startingRun,
      visible: uiDebugSnapshot.visible,
      viewingRunId: uiDebugSnapshot.viewingRunId,
      refs: uiDebugSnapshot.refs,
    });
    if (key === lastUiTraceKeyRef.current) return;
    lastUiTraceKeyRef.current = key;
    traceUiDisplay(uiDebugSnapshot);
  }, [uiDebugSnapshot]);

  const stopRun = useCallback(async () => {
    traceUi("run:stop", "Stop clicked");
    collabActiveRef.current = false;
    startingRunRef.current = false;
    runningRef.current = false;
    setStartingRun(false);
    setRunning(false);
    setAgentCards((prev) =>
      prev.map((c) =>
        c.status === "running"
          ? {
              ...c,
              status: "failed" as const,
              summary: "Cancelled",
              completedAt: new Date().toISOString(),
              streamStatus: undefined,
            }
          : c,
      ),
    );
    setPhases((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(next)) {
        if (next[key]?.status === "running") {
          next[key] = { ...next[key], status: "failed", message: "Cancelled" };
        }
      }
      return next;
    });
    try {
      const res = await apiFetch("/api/run/stop", { method: "POST" });
      if (!res.ok) {
        const err = (await res.json()) as { error?: string };
        applyEvent({ type: "log", message: err.error ?? "Failed to stop run", level: "error" });
      }
    } catch {
      applyEvent({ type: "log", message: "Failed to stop run", level: "error" });
    }
  }, [applyEvent, traceUi]);

  const startRun = async () => {
    if (!project.trim()) return;
    traceUiDisplay(
      buildUiDisplaySnapshot({
        trigger: "run:click",
        view: "run",
        running: true,
        startingRun: true,
        inRunMode: true,
        showConfigPanel: false,
        viewingRunId: null,
        replayMode: false,
        pipelineUiActive: false,
        webBrowseActive: false,
        showLiveSession: false,
        hasCollaboration: true,
        webResearch: null,
        resumeTargetId: null,
        logOpen,
        agentCardsCount: 1,
        eventsCount: 0,
        phaseKeysCount: 1,
        hasLatestRun,
        refs: {
          startingRunRef: true,
          collabActiveRef: true,
          runningRef: true,
        },
        note: "Run button clicked (optimistic)",
      }),
    );
    startingRunRef.current = true;
    collabActiveRef.current = true;
    runningRef.current = true;
    setStartingRun(true);
    showRunView();
    setViewingRunId(null);
    setRunning(true);
    persistSettings();
    void saveProjectToRegistry();
    setAgentCards([optimisticStartCard()]);
    setEvents([]);
    setPhases(optimisticRunPhases());
    setActivePhase("collaboration");
    setBrowserState(null);
    setTestTarget(null);
    setStructuredTask(null);
    setRunReport(null);
    setLastStep(null);
    setLastResult(null);
    setPlaywrightSession(null);
    setSessionFrameIndex(0);
    setCollaborationResult(null);
    setWebResearch(null);
    setWebCapture(null);
    setLatestWebCaptureReview(null);
    try {
      const res = await apiFetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          task,
          ...runApiOptions,
          cursorRuntime,
          repoUrl: repoUrl || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        applyEvent({ type: "log", message: err.error ?? "Failed to start", level: "error" });
        collabActiveRef.current = false;
        startingRunRef.current = false;
        runningRef.current = false;
        setStartingRun(false);
        setRunning(false);
        setAgentCards([]);
        traceUi("run:start-failed", err.error ?? "Failed to start");
      }
    } catch (err) {
      applyEvent({
        type: "log",
        message: err instanceof Error ? err.message : "Failed to start run",
        level: "error",
      });
      collabActiveRef.current = false;
      startingRunRef.current = false;
      runningRef.current = false;
      setStartingRun(false);
      setRunning(false);
      setAgentCards([]);
      traceUi(
        "run:start-failed",
        err instanceof Error ? err.message : "Failed to start run",
      );
    }
  };

  const saveWebCaptureReview = useCallback(
    async (review: Omit<WebCaptureReview, "captureId" | "ts"> & { element?: WebCaptureElement }) => {
      if (!webCapture || !project.trim()) return;
      const res = await apiFetch("/api/project/web-capture/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          runId: viewingRunId ?? "current",
          captureId: webCapture.capture_id,
          ...review,
        }),
      });
      const body = (await res.json()) as {
        error?: string;
        review?: WebCaptureReview;
        capture?: WebCapture;
      };
      if (!res.ok) throw new Error(body.error ?? "Failed to save web capture review");
      if (body.review) setLatestWebCaptureReview(body.review);
      if (body.capture) setWebCapture(body.capture);
    },
    [project, viewingRunId, webCapture],
  );

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Assistant Test Runner</h1>
          <p className="mt-1 text-sm text-white/60">
            One task field — the local agent routes to web research or UI testing and escalates to the helper when stuck
          </p>
        </div>
        {view === "run" || inRunMode ? (
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
            {!inRunMode ? (
              <button
                type="button"
                onClick={() => {
                  showConfigView();
                  setViewingRunId(null);
                  setPlaywrightSession(null);
                }}
                className="rounded-md border border-white/20 px-3 py-1.5 text-sm text-white/80 hover:bg-white/5"
              >
                Back to configuration
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      {showConfigPanel ? (
        <>
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
          <label className="block text-xs text-white/60">What should the agent do?</label>
          <textarea
            className="min-h-24 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="Test the login flow, remove a button on the home page, or research what httpx is used for…"
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
          <div className="space-y-2 rounded-md border border-white/10 bg-black/20 p-3">
            {!config?.hasCursorApiKey ? (
              <p className="text-xs text-amber-300/90">
                Set <code className="text-white/80">CURSOR_API_KEY</code> in ai-assistant/.env — needed when the local
                agent escalates to the helper
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
            ) : (
              <div
                className={cn(
                  "rounded-md border px-3 py-2 text-xs",
                  config?.cursorHelper?.ok
                    ? "border-green-500/30 bg-green-950/20 text-green-200"
                    : "border-amber-500/30 bg-amber-950/20 text-amber-200",
                )}
              >
                {config?.cursorHelper?.ok ? (
                  <p>Local helper ready — Cursor app is running on this machine.</p>
                ) : (
                  <ul className="space-y-1">
                    {!config?.hasCursorApiKey ? (
                      <li>
                        Set <code className="text-white/90">CURSOR_API_KEY</code> in ai-assistant/.env
                      </li>
                    ) : null}
                    {config?.cursorHelper?.errors.map((err) => (
                      <li key={err}>{err}</li>
                    ))}
                    {!config?.cursorHelper ? (
                      <li>Open this page after selecting a project to check helper readiness.</li>
                    ) : null}
                  </ul>
                )}
              </div>
            )}
          </div>

          <hr className="border-white/10" />
          <h3 className="text-sm font-semibold text-white/70">Ollama</h3>
          {ollamaStatus ? (
            <div className="space-y-3 text-xs">
              <div className="space-y-1">
                <label htmlFor="ollama-model" className="text-white/60">
                  Model
                </label>
                <select
                  id="ollama-model"
                  value={ollamaStatus.model}
                  disabled={changingOllamaModel || running || Boolean(ollamaSwitch?.active)}
                  onChange={(e) => void changeOllamaModel(e.target.value)}
                  className="w-full rounded-md border border-white/15 bg-black/30 px-2.5 py-1.5 text-xs text-white/90 disabled:opacity-50"
                >
                  {(ollamaStatus.modelOptions ?? [{ id: ollamaStatus.model, label: ollamaStatus.model, description: "", installed: ollamaStatus.modelAvailable }]).map(
                    (opt) => (
                      <option key={opt.id} value={opt.id}>
                        {opt.label}
                        {!opt.installed ? " (not installed)" : ""}
                      </option>
                    ),
                  )}
                </select>
                {(() => {
                  const selected =
                    ollamaStatus.modelOptions?.find((opt) => opt.id === ollamaStatus.model) ?? null;
                  return selected?.description ? (
                    <p className="text-white/45">{selected.description}</p>
                  ) : null;
                })()}
              </div>

              {ollamaSwitch?.active || ollamaSwitch?.step === "error" ? (
                <div
                  className={cn(
                    "space-y-2 rounded-md border px-3 py-2",
                    ollamaSwitch.step === "error"
                      ? "border-red-500/30 bg-red-950/20"
                      : "border-sky-500/30 bg-sky-950/20",
                  )}
                >
                  <p
                    className={cn(
                      ollamaSwitch.step === "error" ? "text-red-200/90" : "text-sky-200/90",
                    )}
                  >
                    {ollamaSwitch.message}
                  </p>
                  {ollamaSwitch.active && typeof ollamaSwitch.progress === "number" ? (
                    <div className="space-y-1">
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                        <div
                          className="h-full rounded-full bg-sky-400/80 transition-all duration-500"
                          style={{ width: `${Math.max(4, ollamaSwitch.progress)}%` }}
                        />
                      </div>
                      <p className="text-white/45">
                        {ollamaSwitch.step === "unloading"
                          ? "Freeing VRAM"
                          : ollamaSwitch.step === "loading"
                            ? "Loading into VRAM"
                            : ollamaSwitch.step === "downloading"
                              ? "Downloading"
                              : ollamaSwitch.step === "checking"
                                ? "Checking"
                                : "Working"}
                        {typeof ollamaSwitch.progress === "number" ? ` · ${ollamaSwitch.progress}%` : ""}
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : null}

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
                  : ollamaSwitch?.active
                    ? "Switching models…"
                  : !ollamaStatus.modelAvailable
                    ? "Selected model is not installed yet"
                    : ollamaStatus.modelLoaded
                      ? "Model loaded in VRAM"
                      : "Model not loaded — first run will load it (30–90s)"}
              </p>
              {ollamaStatus.loadedModels.length > 0 && !ollamaSwitch?.active ? (
                <p className="text-white/40">
                  In VRAM: {ollamaStatus.loadedModels.join(", ")}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {ollamaStatus.reachable && !ollamaStatus.modelAvailable ? (
                  <button
                    type="button"
                    disabled={Boolean(pullingOllamaModel) || running || Boolean(ollamaSwitch?.active)}
                    onClick={() => void pullOllamaModel(ollamaStatus.model)}
                    className="rounded-md border border-white/20 px-3 py-1.5 text-xs text-white/90 disabled:opacity-50"
                  >
                    {pullingOllamaModel === ollamaStatus.model ? "Downloading…" : "Download model"}
                  </button>
                ) : null}
                {ollamaStatus.reachable && ollamaStatus.modelAvailable && !ollamaStatus.modelLoaded ? (
                  <button
                    type="button"
                    disabled={preloadingOllama || running || Boolean(ollamaSwitch?.active)}
                    onClick={preloadOllama}
                    className="rounded-md border border-white/20 px-3 py-1.5 text-xs text-white/90 disabled:opacity-50"
                  >
                    {preloadingOllama ? "Loading model…" : "Preload model now"}
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}

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
                and returns answer + report. For web research tasks it can ask follow-up questions via{" "}
                <code className="text-white/70">### Info needed</code> (max {collabConfig?.maxInfoRequests ?? 2}{" "}
                rounds). After {collabConfig?.maxTestRetries ?? 3} failed verifications the helper gets one
                escalation round with full history before the run stops.
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

          <hr className="border-white/10" />
          <RunHistoryPanel
            runs={runHistory}
            loading={historyLoading}
            loadingMore={historyLoadingMore}
            hasMore={historyHasMore}
            total={historyTotal}
            running={inRunMode}
            onInspect={(runId) => void viewRun(runId)}
            onResume={(runId) => void resumeFromRun(runId)}
            onLoadOlder={loadOlderRunHistory}
          />

          <div className="flex flex-col gap-2 pt-2">
            <button
              type="button"
              disabled={inRunMode}
              onClick={startRun}
              className={cn(
                "rounded-md bg-white px-4 py-2 text-sm font-semibold text-black",
                inRunMode && "opacity-50",
              )}
            >
              Run
            </button>
            <p className="text-center text-[10px] text-white/40">
              Classifies task → web research or UI test → escalates to helper when stuck
            </p>
          </div>

          <details className="rounded-md border border-white/10 bg-black/20 p-3 text-sm">
            <summary className="cursor-pointer text-xs font-medium text-white/60">Run settings &amp; exploration</summary>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <CheatsheetPanel projectPath={project} testTargetMode={testTargetMode} />
              <ExplorationPanel projectPath={project} />
            </div>
          </details>
        </section>
        </>
      ) : (
        <div className="flex min-h-[calc(100vh-10rem)] flex-col gap-4">
          {inRunMode ? (
            <CurrentRunStatus
              phases={phases}
              agentCards={agentCards}
              running={inRunMode}
              showPipelineStrip={hasCollaboration}
              testTargetMode={testTargetMode}
              skipDeploy={runApiOptions.skipDeploy}
              webResearch={webResearch}
              captureBuild={captureBuild}
              onStop={() => void stopRun()}
            />
          ) : null}

          {resumeTargetId ? (
            <section className="surface-card shrink-0 space-y-2 border border-amber-500/20 p-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-amber-200/70">
                Intervene &amp; resume
              </h2>
              <p className="text-xs text-white/55">
                This run is stopped. Optionally add context or corrections for the agents, then resume where it
                left off — the note is shown in the conversation and passed to both agents.
              </p>
              <textarea
                className="min-h-20 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm"
                value={interveneNote}
                onChange={(e) => setInterveneNote(e.target.value)}
                placeholder="e.g. The button belongs on the settings page, not the navbar. Check /settings after the fix."
              />
              <button
                type="button"
                onClick={() => void resumeFromRun(resumeTargetId, interveneNote)}
                className="rounded-md border border-amber-500/35 bg-amber-950/25 px-3 py-1.5 text-sm text-amber-100 hover:bg-amber-950/40"
              >
                {interveneNote.trim() ? "Resume with added context" : "Resume run"}
              </button>
            </section>
          ) : null}

          {(showLiveSession || webCapture || browserState?.url || captureBuild) ? (
            <section
              className={cn(
                "surface-card shrink-0 p-4 transition-shadow duration-700",
                captureBuild?.phase === "complete" && "ring-2 ring-violet-400/70 shadow-[0_0_28px_rgba(167,139,250,0.25)]",
              )}
            >
              <PageInspectPanel
                state={replayMode ? null : browserState}
                session={playwrightSession}
                capture={webCapture}
                captureBuild={captureBuild}
                frameIndex={sessionFrameIndex}
                onFrameIndexChange={setSessionFrameIndex}
                lastAction={lastActionLine}
                replayMode={replayMode}
                latestReview={latestWebCaptureReview}
                onReview={saveWebCaptureReview}
              />
            </section>
          ) : null}

          {webResearch ? (
            <section className="surface-card shrink-0 p-4">
              <WebResearchPanel state={webResearch} captureBuild={captureBuild} running={inRunMode} />
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
                  running={inRunMode}
                />
              </section>
            ) : null}

            <section className="surface-card flex max-h-[calc(100vh-10rem)] flex-col p-4 lg:sticky lg:top-4">
              <RunProgressPanel
                phases={phases}
                structuredTask={structuredTask}
                runReport={runReport}
                testTarget={testTarget}
                running={inRunMode}
                projectPath={project}
                lastResult={lastResult}
                testTargetMode={testTargetMode}
                skipDeploy={runApiOptions.skipDeploy}
                hasTask={Boolean(task.trim())}
                agentCards={agentCards}
                collaborationResult={collaborationResult}
                hideCollaboration={hasCollaboration}
                webResearch={webResearch}
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
      <UiRunDebugPanel current={uiDebugSnapshot} />
    </div>
  );
}

export type UiVisibleSections = {
  configPanel: boolean;
  runView: boolean;
  currentRunStatus: boolean;
  liveSession: boolean;
  webResearch: boolean;
  collaborationPanel: boolean;
  fallbackPagePreview: boolean;
  runProgress: boolean;
  interveneResume: boolean;
  headerRunControls: boolean;
  runButton: boolean;
  logPanel: boolean;
};

export type UiDisplaySnapshot = {
  ts: string;
  trigger: string;
  view: "config" | "run";
  running: boolean;
  startingRun: boolean;
  inRunMode: boolean;
  showConfigPanel: boolean;
  viewingRunId: string | null;
  visible: UiVisibleSections;
  counts: {
    agentCards: number;
    events: number;
    phaseKeys: number;
  };
  refs: {
    startingRunRef: boolean;
    collabActiveRef: boolean;
    runningRef: boolean;
  };
  flags: {
    replayMode: boolean;
    pipelineUiActive: boolean;
    webBrowseActive: boolean;
    hasCollaboration: boolean;
    hasLatestRun: boolean;
  };
  note?: string;
};

const MAX_HISTORY = 120;
const STORAGE_KEY = "test_runner_ui_debug_log";
const ENABLE_KEY = "test_runner_ui_debug";

export function isUiDebugEnabled(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (localStorage.getItem(ENABLE_KEY) === "1") return true;
    return new URLSearchParams(window.location.search).has("uiDebug");
  } catch {
    return false;
  }
}

export function readUiDebugLog(): UiDisplaySnapshot[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]") as UiDisplaySnapshot[];
  } catch {
    return [];
  }
}

export function clearUiDebugLog(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function traceUiDisplay(snapshot: Omit<UiDisplaySnapshot, "ts">): void {
  if (!isUiDebugEnabled()) return;

  const entry: UiDisplaySnapshot = {
    ...snapshot,
    ts: new Date().toISOString(),
  };

  const visibleNames = Object.entries(entry.visible)
    .filter(([, on]) => on)
    .map(([name]) => name);

  console.groupCollapsed(
    `[UI run] ${entry.trigger} → ${entry.showConfigPanel ? "config" : "run"} (${visibleNames.join(", ") || "nothing"})`,
  );
  console.table(entry.visible);
  console.log(entry);
  console.groupEnd();

  try {
    const prev = readUiDebugLog();
    prev.push(entry);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(prev.slice(-MAX_HISTORY)));
  } catch {
    /* ignore */
  }

  window.dispatchEvent(new CustomEvent("test-runner-ui-debug", { detail: entry }));
}

export function buildUiDisplaySnapshot(input: {
  trigger: string;
  view: "config" | "run";
  running: boolean;
  startingRun: boolean;
  inRunMode: boolean;
  showConfigPanel: boolean;
  viewingRunId: string | null;
  replayMode: boolean;
  pipelineUiActive: boolean;
  webBrowseActive: boolean;
  showLiveSession: boolean;
  hasCollaboration: boolean;
  webResearch: unknown;
  resumeTargetId: string | null;
  logOpen: boolean;
  agentCardsCount: number;
  eventsCount: number;
  phaseKeysCount: number;
  hasLatestRun: boolean;
  refs: UiDisplaySnapshot["refs"];
  note?: string;
}): Omit<UiDisplaySnapshot, "ts"> {
  const showRunView = !input.showConfigPanel;
  const showWebResearch = Boolean(input.webResearch);

  return {
    trigger: input.trigger,
    view: input.view,
    running: input.running,
    startingRun: input.startingRun,
    inRunMode: input.inRunMode,
    showConfigPanel: input.showConfigPanel,
    viewingRunId: input.viewingRunId,
    visible: {
      configPanel: input.showConfigPanel,
      runView: showRunView,
      currentRunStatus: input.inRunMode && showRunView,
      liveSession: showRunView && input.showLiveSession,
      webResearch: showRunView && showWebResearch,
      collaborationPanel: showRunView && input.hasCollaboration,
      fallbackPagePreview: showRunView && !input.hasCollaboration && !input.showLiveSession,
      runProgress: showRunView,
      interveneResume: showRunView && Boolean(input.resumeTargetId),
      headerRunControls: input.view === "run" || input.inRunMode,
      runButton: input.showConfigPanel,
      logPanel: showRunView,
    },
    counts: {
      agentCards: input.agentCardsCount,
      events: input.eventsCount,
      phaseKeys: input.phaseKeysCount,
    },
    refs: input.refs,
    flags: {
      replayMode: input.replayMode,
      pipelineUiActive: input.pipelineUiActive,
      webBrowseActive: input.webBrowseActive,
      hasCollaboration: input.hasCollaboration,
      hasLatestRun: input.hasLatestRun,
    },
    note: input.note,
  };
}

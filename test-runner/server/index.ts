import "./sdk-bootstrap.js";
import cors from "cors";
import dotenv from "dotenv";
import express from "express";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildReportPrompt, CursorRunner } from "./cursor-agent.js";
import { preflightCursorHelper } from "./cursor-preflight.js";
import { CollaborationLoop } from "./collaboration-loop.js";
import { readCollaborationConfig, writeCollaborationConfig } from "./collaboration-config.js";
import { canResumeTranscript, readCollaborationTranscript } from "./collaboration-transcript.js";
import {
  buildOllamaModelCatalog,
  fetchOllamaStatus,
  preloadOllamaModel,
  pullOllamaModel,
  readOllamaConfig,
  switchOllamaModel,
  type OllamaSwitchProgress,
} from "./ollama.js";
import { defaultProjectPath, PythonRunner, REPO_ROOT } from "./python-runner.js";
import { verifyWebSurfDeps } from "./python-env.js";
import { classifyTaskRunKind } from "./task-router.js";
import { WebResearchRunner } from "./web-research-runner.js";
import { composeWebResearchState, isWebResearchEvent } from "./web-research-state.js";
import {
  loadRegistry,
  readProjectBundle,
  readSpec,
  removeProject,
  setActiveProject,
  upsertProject,
  writeCheatsheet,
  writeProfile,
  type ProjectSettings,
} from "./project-store.js";
import { readLocalEnvStatus } from "./local-env.js";
import { resolveCursorRuntime, resolveRunTargetOptions } from "./run-target.js";
import {
  artifactContentType,
  listRunHistory,
  readExploration,
  readNavTree,
  readRunBundle,
  readCheatsheetLearnings,
  readLocalDevStatus,
  readRunReport,
  readSiteMap,
  readWebResearch,
  resolveRunArtifact,
  prepareCurrentForNewRun,
  sessionWithArtifactUrls,
} from "./project-report.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(REPO_ROOT, ".env") });
dotenv.config({ path: path.join(REPO_ROOT, "test-runner", ".env") });

const PORT = Number(process.env.TEST_RUNNER_PORT || 8767);
const pythonRunner = new PythonRunner();
const webResearchRunner = new WebResearchRunner();
const cursorRunner = new CursorRunner();
const collaborationLoop = new CollaborationLoop(pythonRunner, cursorRunner, webResearchRunner);

type StoredEvent = Record<string, unknown>;
const eventLog: StoredEvent[] = [];
const systemEvents = new EventEmitter();
const engineLogPath = path.join(REPO_ROOT, "logs", "test-runner-last-run.log");
let projectRunLogPath: string | null = null;

type OllamaSwitchState = {
  active: boolean;
  step?: OllamaSwitchProgress["step"];
  message?: string;
  progress?: number;
  fromModel?: string;
  toModel?: string;
  error?: string;
};

let ollamaSwitchState: OllamaSwitchState = { active: false };
let ollamaSwitchPromise: Promise<void> | null = null;

function broadcastSystemEvent(event: StoredEvent) {
  const stamped = { ...event, ts: event.ts ?? new Date().toISOString() };
  eventLog.push(stamped);
  if (eventLog.length > 2000) eventLog.splice(0, eventLog.length - 2000);
  appendRunLogs(stamped);
  systemEvents.emit("event", stamped);
}

async function buildOllamaPayload(cfg = readOllamaConfig()) {
  const ollamaStatus = await fetchOllamaStatus(cfg);
  return {
    ...cfg,
    ...ollamaStatus,
    modelOptions: buildOllamaModelCatalog(ollamaStatus.availableModels),
    switch: ollamaSwitchState,
  };
}

function emitOllamaSwitch(progress: OllamaSwitchProgress, extra?: StoredEvent) {
  ollamaSwitchState = {
    active: progress.step !== "done" && progress.step !== "error",
    step: progress.step,
    message: progress.message,
    progress: progress.progress,
    fromModel: progress.fromModel,
    toModel: progress.toModel,
    error: progress.step === "error" ? progress.message : undefined,
  };
  broadcastSystemEvent({
    type: "ollama_switch",
    ...progress,
    ...extra,
  });
}

function finishOllamaSwitch() {
  ollamaSwitchPromise = null;
  if (ollamaSwitchState.active) {
    ollamaSwitchState = { active: false };
  }
}

async function runOllamaModelSwitch(model: string): Promise<void> {
  if (ollamaSwitchPromise) {
    await ollamaSwitchPromise;
    if (readOllamaConfig().model === model) return;
  }

  ollamaSwitchPromise = (async () => {
    try {
      await switchOllamaModel(model, (progress) => {
        emitOllamaSwitch(progress);
      });
      const ollama = await buildOllamaPayload();
      ollamaSwitchState = { active: false };
      broadcastSystemEvent({
        type: "ollama_switch",
        step: "done",
        message: `${model} is ready`,
        progress: 100,
        toModel: model,
        ollama,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      emitOllamaSwitch({ step: "error", message });
      const ollama = await buildOllamaPayload();
      broadcastSystemEvent({
        type: "ollama_switch",
        step: "error",
        message,
        ollama,
      });
      throw err;
    } finally {
      finishOllamaSwitch();
    }
  })();

  await ollamaSwitchPromise;
}

function formatRunnerLogLine(event: StoredEvent): string {
  const type = event.type;
  if (type === "phase") {
    return `[phase:${event.phase}] ${event.status} ${event.message ?? ""}`.trim();
  }
  if (type === "log") return String(event.message ?? "");
  if (type === "step") {
    const mark = event.ok ? "✓" : "✗";
    return `[${event.mode ?? "strict"}] ${event.action} ${event.target} ${mark} ${event.message ?? ""}`.trim();
  }
  if (type === "done") return `[done] overall_ok=${String(event.overall_ok)}`;
  if (type === "cursor") return `[cursor] ${event.status ?? ""} ${event.message ?? ""}`.trim();
  if (type === "cursor_text" && event.text) return `[cursor] ${event.text}`;
  if (type === "cursor_activity" && event.activity) return `[cursor] ${event.activity}`;
  if (type === "browser_state") {
    const count = Array.isArray(event.interactables) ? event.interactables.length : 0;
    const ctx = event.context ? ` (${event.context})` : "";
    return `[browser] ${event.url} — ${count} interactables${ctx}`;
  }
  if (type === "site_map") {
    const pages = event.pages as Record<string, unknown> | undefined;
    return `[site_map] ${pages ? Object.keys(pages).length : 0} page(s), +${String(event.new_elements ?? 0)} capability(s)`;
  }
  if (type === "nav_tree") {
    const routes = event.routes as Record<string, unknown> | undefined;
    return `[nav_tree] ${routes ? Object.keys(routes).length : 0} route(s), +${String(event.new_elements ?? 0)} interactable(s)`;
  }
  if (type === "agent_decision") {
    return `[agent] ${String(event.action ?? "")}: ${String(event.reason ?? "")}`.trim();
  }
  if (type === "agent_card") {
    const card = event.card as Record<string, unknown> | undefined;
    return `[${card?.agent ?? "agent"}] ${card?.status ?? ""} ${card?.summary ?? ""}`.trim();
  }
  if (type === "collaboration_done") {
    return `[collaboration] ok=${String(event.ok)} ${event.error ?? event.answer ?? ""}`.trim();
  }
  if (type === "web_research_progress") {
    return `[web] ${String(event.step ?? "")} ${String(event.url ?? event.message ?? "")}`.trim();
  }
  if (type === "web_research_result") {
    return `[web] answer ready — ${String(event.pages_fetched ?? 0)} page(s), ${String(event.facts_added ?? 0)} fact(s)`;
  }
  if (type === "process_exit") return `[process_exit] code=${String(event.code)}`;
  if (type === "ollama_switch") {
    const progress =
      typeof event.progress === "number" ? ` ${event.progress}%` : "";
    return `[ollama] ${String(event.step ?? "")}${progress} ${String(event.message ?? "")}`.trim();
  }
  return JSON.stringify(event);
}

function initRunLogs(project?: string) {
  fs.mkdirSync(path.dirname(engineLogPath), { recursive: true });
  const header = `# Test runner log — ${new Date().toISOString()}\n`;
  fs.writeFileSync(engineLogPath, header, "utf8");
  projectRunLogPath = project ? path.join(project, ".agent", "current", "RUN-LOG.txt") : null;
}

function appendRunLogs(event: StoredEvent) {
  const line = formatRunnerLogLine(event);
  if (!line) return;
  try {
    fs.appendFileSync(engineLogPath, line + "\n", "utf8");
    // Project RUN-LOG.txt is owned by the Python ui_test process — do not duplicate here.
  } catch {
    /* ignore log write errors */
  }
}

let runState: Record<string, unknown> = {
  running: false,
  phase: "idle",
  phases: {},
  runKind: "ui_test",
};

function resetRunStateForNewRun(
  project: string,
  runKind: "ui_test" | "web_research" = "ui_test",
  task = "",
) {
  runState = {
    running: true,
    phase: "idle",
    phases: {},
    project,
    runKind,
    structuredTask: null,
    runReport: null,
    browserState: null,
    testTarget: null,
    lastResult: null,
    agentCards: [],
    collaborationActive: false,
    collaborationResult: null,
    webResearch: null,
    webIndex: null,
    webFacts: null,
    webResearchProgress: null,
    playwrightSession: null,
  };
  pushEvent({ type: "run_cleared" });
  pushEvent({ type: "run_state", running: true });
  if (task.trim()) {
    pushEvent({ type: "collaboration_start", task: task.trim() });
  }
}

function pushEvent(event: StoredEvent) {
  const stamped = { ...event, ts: event.ts ?? new Date().toISOString() };
  eventLog.push(stamped);
  if (eventLog.length > 2000) eventLog.splice(0, eventLog.length - 2000);
  appendRunLogs(stamped);

  if (event.type === "phase" && typeof event.phase === "string") {
    runState.phase = event.phase;
    const phases = { ...(runState.phases as Record<string, unknown>) };
    phases[event.phase as string] = {
      status: event.status,
      message: event.message ?? "",
    };
    runState.phases = phases;
  }
  if (event.type === "run_state") {
    runState.running = event.running;
  }
  // During a collaboration run the python pipeline emits its own done/process_exit
  // mid-loop — the run is only over when the collaboration loop says so.
  if (event.type === "done") {
    if (!collaborationLoop.isActive) runState.running = false;
    runState.lastResult = event;
  }
  if (event.type === "process_exit") {
    if (!collaborationLoop.isActive) runState.running = false;
  }
  if (event.type === "browser_state") {
    runState.browserState = {
      url: event.url,
      title: event.title,
      interactables: event.interactables,
      context: event.context,
      node_url: event.node_url,
      screenshot_b64: event.screenshot_b64,
      error: event.error,
      ts: event.ts,
    };
  }
  if (event.type === "test_target") {
    runState.testTarget = {
      url: event.url,
      source: event.source,
      local_url: event.local_url,
      ts: event.ts,
    };
  }
  if (event.type === "structured_task") {
    runState.structuredTask = {
      summary: event.summary,
      source_text: event.source_text,
      scope_urls: event.scope_urls,
      success_criteria: event.success_criteria,
      suggested_steps: event.suggested_steps,
      notes_for_cursor: event.notes_for_cursor,
    };
  }
  if (event.type === "run_report" && event.report) {
    runState.runReport = event.report;
  }
  if (event.type === "cheatsheet_refined") {
    runState.cheatsheetRefined = {
      added_learnings: event.added_learnings,
      added_notes: event.added_notes,
    };
  }
  if (event.type === "site_map") {
    runState.siteMap = {
      pages: event.pages,
      new_elements: event.new_elements,
      ts: event.ts,
    };
  }
  if (event.type === "nav_tree") {
    runState.navTree = {
      routes: event.routes,
      global_nav: event.global_nav,
      new_elements: event.new_elements,
      ts: event.ts,
    };
  }
  if (event.type === "agent_decision") {
    runState.lastAgentDecision = {
      action: event.action,
      reason: event.reason,
      phase: event.phase,
      ts: event.ts,
    };
  }
  if (event.type === "agent_card" && event.card) {
    const cards = Array.isArray(runState.agentCards) ? [...(runState.agentCards as StoredEvent[])] : [];
    const card = event.card as Record<string, unknown>;
    const idx = cards.findIndex((c) => c.id === card.id);
    if (idx >= 0) {
      cards[idx] = card;
    } else {
      cards.push(card);
    }
    runState.agentCards = cards;
  }
  if (event.type === "collaboration_start") {
    runState.agentCards = Array.isArray(runState.agentCards) && (runState.agentCards as StoredEvent[]).length
      ? runState.agentCards
      : [];
    runState.collaborationActive = true;
  }
  if (event.type === "collaboration_done") {
    runState.collaborationActive = false;
    runState.running = false;
    runState.collaborationResult = {
      ok: event.ok,
      answer: event.answer,
      error: event.error,
      iterations: event.iterations,
    };
  }
  if (event.type === "web_research_progress") {
    runState.webResearchProgress = {
      step: event.step,
      url: event.url,
      index: event.index,
      total: event.total,
      message: event.message,
      ts: event.ts,
    };
  }
  if (event.type === "web_index") {
    runState.webIndex = {
      pages: event.pages,
      ts: event.ts,
    };
  }
  if (event.type === "web_facts") {
    runState.webFacts = {
      facts: event.facts,
      ts: event.ts,
    };
  }
  if (event.type === "web_research_result") {
    runState.webResearch = composeWebResearchState(
      runState.webResearch as import("./web-research-state.js").WebResearchState | null,
      event,
    );
  } else if (isWebResearchEvent(event)) {
    runState.webResearch = composeWebResearchState(
      runState.webResearch as import("./web-research-state.js").WebResearchState | null,
      event,
    );
    if (
      event.type === "web_page_snapshot" ||
      event.type === "web_snapshot" ||
      event.type === "web_semantic_snapshot"
    ) {
      const nested =
        event.snapshot && typeof event.snapshot === "object"
          ? (event.snapshot as StoredEvent)
          : event.page && typeof event.page === "object"
            ? (event.page as StoredEvent)
            : event;
      runState.browserState = {
        url: nested.url ?? event.url,
        title: nested.title ?? event.title,
        interactables: nested.interactables ?? event.interactables ?? [],
        context: nested.context ?? "web_exploration",
        node_url: nested.node_url,
        screenshot_b64: nested.screenshot_b64,
        error: nested.error,
        ts: event.ts,
      };
    }
  } else if (
    event.type === "browser_state" &&
    (String(event.context ?? "").startsWith("web_") || runState.runKind === "web_research")
  ) {
    runState.webResearch = composeWebResearchState(
      runState.webResearch as import("./web-research-state.js").WebResearchState | null,
      { ...event, type: "web_page_snapshot", snapshot: event },
    );
  } else if (event.type === "step" && event.mode === "web") {
    runState.webResearch = composeWebResearchState(
      runState.webResearch as import("./web-research-state.js").WebResearchState | null,
      { ...event, type: "web_step", step: event },
    );
  }
  if (event.type === "playwright_session" && event.session) {
    const project = String(runState.project ?? "");
    const raw = event.session as Record<string, unknown>;
    const session =
      project && typeof project === "string"
        ? sessionWithArtifactUrls(project, "current", raw)
        : raw;
    if (session) {
      runState.playwrightSession = {
        ...session,
        source: event.source ?? "ui",
      };
    }
  }

  systemEvents.emit("event", stamped);
}

pythonRunner.on("event", pushEvent);
webResearchRunner.on("event", pushEvent);
cursorRunner.on("event", pushEvent);
collaborationLoop.on("event", pushEvent);

function runStartBlocked(): string | null {
  collaborationLoop.resetIfStale();
  if (
    runState.running ||
    pythonRunner.running ||
    webResearchRunner.running ||
    cursorRunner.isRunning ||
    collaborationLoop.isActive
  ) {
    return "A run is already in progress";
  }
  return null;
}

function archivePreviousRun(project: string): string | null {
  try {
    return prepareCurrentForNewRun(project);
  } catch (err) {
    console.warn(
      `Failed to archive previous run for ${project}:`,
      err instanceof Error ? err.message : String(err),
    );
    return null;
  }
}

function startWebResearchForTask(
  project: string,
  task: string,
  options?: { noOllama?: boolean; maxPages?: number },
): void {
  const depError = verifyWebSurfDeps();
  if (depError) {
    throw new Error(depError);
  }
  webResearchRunner.start({
    project,
    query: task.trim(),
    maxPages: options?.maxPages,
    noOllama: Boolean(options?.noOllama),
  });
}

function taskRunKind(task: string, noOllama = false): "web_research" | "ui_test" {
  return classifyTaskRunKind(task, noOllama);
}

function emitRunPreflightWarnings(
  apiKey: string | undefined,
  cursorRuntime: "local" | "cloud",
  project: string,
): void {
  if (!apiKey) {
    pushEvent({
      type: "log",
      message:
        "CURSOR_API_KEY not set — local agent will run but cannot escalate to helper. Add it to ai-assistant/.env",
      level: "warn",
    });
  } else if (cursorRuntime === "local") {
    const preflight = preflightCursorHelper("local", apiKey, project);
    for (const warning of preflight.warnings) {
      pushEvent({ type: "log", message: warning, level: "warn" });
    }
    for (const error of preflight.errors) {
      pushEvent({ type: "log", message: `Helper preflight: ${error}`, level: "error" });
    }
    if (preflight.ok) {
      pushEvent({
        type: "log",
        message: "Local helper ready — Cursor app is running",
        level: "info",
      });
    }
  }
}

type CollaborationRunBody = {
  project: string;
  task: string;
  target: ReturnType<typeof resolveRunTargetOptions>;
  apiKey: string | undefined;
  cursorRuntime: "local" | "cloud";
  repoUrl: string | undefined;
  skipStructure: boolean;
  skipUi: boolean;
  noOllama: boolean;
  resumeFrom?: import("./collaboration-transcript.js").CollaborationTranscript;
  userNote?: string;
};

async function beginCollaborationRun(options: CollaborationRunBody): Promise<void> {
  try {
    const archivedRunId = archivePreviousRun(options.project);
    initRunLogs(options.project);
    if (archivedRunId) {
      pushEvent({
        type: "log",
        message: `Archived previous run to .agent/history/${archivedRunId}`,
        level: "info",
      });
    }
    emitRunPreflightWarnings(options.apiKey, options.cursorRuntime, options.project);

    const result = await collaborationLoop.run({
      project: options.project,
      task: options.task,
      push: options.target.push,
      skipDeploy: options.target.skipDeploy,
      testTarget: options.target.testTarget,
      skipStructure: options.skipStructure,
      skipUi: options.skipUi,
      noOllama: options.noOllama,
      cursorRuntime: options.cursorRuntime,
      repoUrl: options.repoUrl,
      apiKey: options.apiKey,
      resumeFrom: options.resumeFrom,
      userNote: options.userNote,
    });
    pushEvent({
      type: "done",
      overall_ok: result.ok,
      error: result.error,
      answer: result.answer,
    });
  } catch (err) {
    pushEvent({
      type: "collaboration_done",
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

function startCollaborationRun(body: Record<string, unknown>, res: express.Response): void {
  const project = typeof body.project === "string" ? body.project : "";
  if (!project) {
    res.status(400).json({ error: "project is required" });
    return;
  }

  const task = typeof body.task === "string" ? body.task : "";
  const target = resolveRunTargetOptions(
    typeof body.testTarget === "string" ? body.testTarget : undefined,
  );
  const apiKey = process.env.CURSOR_API_KEY;
  const cursorRuntime = body.cursorRuntime === "local" ? "local" : "cloud";
  const repoUrl = typeof body.repoUrl === "string" ? body.repoUrl : undefined;
  const cursorTarget = resolveCursorRuntime(cursorRuntime, repoUrl);
  if (cursorTarget.error) {
    res.status(400).json({ error: cursorTarget.error });
    return;
  }

  eventLog.length = 0;
  resetRunStateForNewRun(project, "ui_test", task);
  pushEvent({
    type: "phase",
    phase: "collaboration",
    status: "running",
    message: "Preparing run…",
  });

  res.json({ started: true });

  void beginCollaborationRun({
    project,
    task,
    target,
    apiKey,
    cursorRuntime,
    repoUrl,
    skipStructure: Boolean(body.skipStructure),
    skipUi: Boolean(body.skipUi),
    noOllama: Boolean(body.noOllama),
  });
}

const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/config", async (req, res) => {
  const project =
    typeof req.query.project === "string" && req.query.project.trim()
      ? req.query.project.trim()
      : defaultProjectPath();
  const cursorHelper = preflightCursorHelper("local", process.env.CURSOR_API_KEY, project);
  res.json({
    defaultProject: defaultProjectPath(),
    hasCursorApiKey: Boolean(process.env.CURSOR_API_KEY),
    cursorHelper,
    repoRoot: REPO_ROOT,
    ollama: await buildOllamaPayload(),
  });
});

app.post("/api/ollama/preload", async (_req, res) => {
  if (ollamaSwitchState.active) {
    res.status(409).json({ error: "An Ollama model switch is already in progress" });
    return;
  }
  const ollama = readOllamaConfig();
  try {
    const status = await fetchOllamaStatus(ollama);
    if (!status.reachable) {
      res.status(503).json({ error: "Ollama is not reachable at " + ollama.url });
      return;
    }
    if (!status.modelAvailable) {
      res.status(400).json({ error: `Model ${ollama.model} is not installed. Run: ollama pull ${ollama.model}` });
      return;
    }
    if (status.modelLoaded) {
      res.json({ ok: true, message: `${ollama.model} already loaded`, ollama: await buildOllamaPayload() });
      return;
    }
    ollamaSwitchState = {
      active: true,
      step: "loading",
      message: `Loading ${ollama.model} into VRAM…`,
      progress: 10,
      toModel: ollama.model,
    };
    emitOllamaSwitch({
      step: "loading",
      message: `Loading ${ollama.model} into VRAM…`,
      progress: 10,
      toModel: ollama.model,
    });
    await switchOllamaModel(ollama.model, emitOllamaSwitch);
    const payload = await buildOllamaPayload();
    ollamaSwitchState = { active: false };
    broadcastSystemEvent({
      type: "ollama_switch",
      step: "done",
      message: `${ollama.model} loaded into VRAM`,
      progress: 100,
      toModel: ollama.model,
      ollama: payload,
    });
    res.json({ ok: true, message: `${ollama.model} loaded into VRAM`, ollama: payload });
  } catch (err) {
    ollamaSwitchState = { active: false };
    res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
  }
});

app.post("/api/ollama/model", async (req, res) => {
  const model = typeof req.body?.model === "string" ? req.body.model.trim() : "";
  if (!model) {
    res.status(400).json({ error: "model is required" });
    return;
  }
  if (ollamaSwitchState.active) {
    res.status(409).json({ error: "An Ollama model switch is already in progress" });
    return;
  }
  try {
    await runOllamaModelSwitch(model);
    res.json({
      ok: true,
      message: `${model} is ready`,
      ollama: await buildOllamaPayload(),
    });
  } catch (err) {
    res.status(500).json({
      error: err instanceof Error ? err.message : String(err),
      ollama: await buildOllamaPayload(),
    });
  }
});

app.post("/api/ollama/pull", async (req, res) => {
  const model = typeof req.body?.model === "string" ? req.body.model.trim() : "";
  if (!model) {
    res.status(400).json({ error: "model is required" });
    return;
  }
  if (ollamaSwitchState.active) {
    res.status(409).json({ error: "An Ollama model switch is already in progress" });
    return;
  }
  try {
    const ollama = readOllamaConfig();
    const status = await fetchOllamaStatus(ollama);
    if (!status.reachable) {
      res.status(503).json({ error: "Ollama is not reachable at " + ollama.url });
      return;
    }
    ollamaSwitchState = {
      active: true,
      step: "downloading",
      message: `Downloading ${model}…`,
      progress: 0,
      toModel: model,
    };
    await pullOllamaModel(model, emitOllamaSwitch);
    const payload = await buildOllamaPayload();
    ollamaSwitchState = { active: false };
    broadcastSystemEvent({
      type: "ollama_switch",
      step: "done",
      message: `${model} downloaded`,
      progress: 100,
      toModel: model,
      ollama: payload,
    });
    res.json({
      ok: true,
      message: `${model} downloaded`,
      ollama: payload,
    });
  } catch (err) {
    ollamaSwitchState = { active: false };
    res.status(500).json({
      error: err instanceof Error ? err.message : String(err),
      ollama: await buildOllamaPayload(),
    });
  }
});

app.get("/api/state", (_req, res) => {
  res.json({
    ...runState,
    events: eventLog.slice(-300),
    logPaths: {
      engine: engineLogPath,
      project: projectRunLogPath,
    },
  });
});

app.get("/api/projects", (_req, res) => {
  res.json(loadRegistry());
});

app.post("/api/projects", (req, res) => {
  const { path: projectPath, name, settings } = req.body ?? {};
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path is required" });
    return;
  }
  if (!fs.existsSync(projectPath)) {
    res.status(400).json({ error: "Project path does not exist" });
    return;
  }
  const entry = upsertProject(projectPath, settings as ProjectSettings | undefined, name);
  res.json(entry);
});

app.post("/api/projects/active", (req, res) => {
  const { id } = req.body ?? {};
  if (!id || typeof id !== "string") {
    res.status(400).json({ error: "id is required" });
    return;
  }
  const project = setActiveProject(id);
  if (!project) {
    res.status(404).json({ error: "Project not found" });
    return;
  }
  res.json(project);
});

app.delete("/api/projects/:id", (req, res) => {
  const ok = removeProject(req.params.id);
  if (!ok) {
    res.status(404).json({ error: "Project not found" });
    return;
  }
  res.json({ ok: true });
});

app.get("/api/project/local-dev", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readLocalDevStatus(projectPath));
});

app.get("/api/project/run-history", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  const offset = req.query.offset;
  const limit = req.query.limit;
  res.json(
    listRunHistory(projectPath, {
      offset: typeof offset === "string" ? Number(offset) : undefined,
      limit: typeof limit === "string" ? Number(limit) : undefined,
    }),
  );
});

app.get("/api/project/run", (req, res) => {
  const projectPath = req.query.path;
  const runId = req.query.runId;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  if (!runId || typeof runId !== "string") {
    res.status(400).json({ error: "runId query param is required" });
    return;
  }
  const bundle = readRunBundle(projectPath, runId);
  const playwrightSession = sessionWithArtifactUrls(
    projectPath,
    runId,
    bundle.playwrightSession,
    bundle.sessionBase,
  );
  res.json({
    ...bundle,
    playwrightSession: playwrightSession
      ? { ...playwrightSession, source: bundle.sessionSource ?? "ui" }
      : null,
  });
});

app.get("/api/project/run-artifact", (req, res) => {
  const projectPath = req.query.path;
  const runId = req.query.runId;
  const file = req.query.file;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  if (!runId || typeof runId !== "string") {
    res.status(400).json({ error: "runId query param is required" });
    return;
  }
  if (!file || typeof file !== "string") {
    res.status(400).json({ error: "file query param is required" });
    return;
  }
  try {
    const artifactPath = resolveRunArtifact(projectPath, runId, file);
    res.setHeader("Content-Type", artifactContentType(artifactPath));
    fs.createReadStream(artifactPath).pipe(res);
  } catch (err) {
    res.status(404).json({ error: err instanceof Error ? err.message : String(err) });
  }
});

app.get("/api/project/run-report", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  const bundle = readRunReport(projectPath);
  const playwrightSession = sessionWithArtifactUrls(
    projectPath,
    "current",
    bundle.playwrightSession,
    bundle.sessionBase,
  );
  res.json({
    ...bundle,
    playwrightSession: playwrightSession
      ? { ...playwrightSession, source: bundle.sessionSource ?? "ui" }
      : null,
  });
});

app.get("/api/project/learnings", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readCheatsheetLearnings(projectPath));
});

app.get("/api/project/exploration", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readExploration(projectPath));
});

app.get("/api/project/web-research", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readWebResearch(projectPath));
});

app.get("/api/project/site-map", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readSiteMap(projectPath));
});

app.get("/api/project/nav-tree", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  res.json(readNavTree(projectPath));
});

app.get("/api/project/local-env", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  if (!fs.existsSync(projectPath)) {
    res.status(404).json({ error: "Project path does not exist" });
    return;
  }
  res.json(readLocalEnvStatus(projectPath));
});

app.get("/api/project/bundle", (req, res) => {
  const projectPath = req.query.path;
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path query param is required" });
    return;
  }
  if (!fs.existsSync(projectPath)) {
    res.status(404).json({ error: "Project path does not exist" });
    return;
  }
  res.json(readProjectBundle(projectPath));
});

app.put("/api/project/cheatsheet", (req, res) => {
  const { path: projectPath, content } = req.body ?? {};
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path is required" });
    return;
  }
  if (typeof content !== "string") {
    res.status(400).json({ error: "content is required" });
    return;
  }
  const saved = writeCheatsheet(projectPath, content);
  res.json({ ok: true, path: saved });
});

app.put("/api/project/profile", (req, res) => {
  const { path: projectPath, profile } = req.body ?? {};
  if (!projectPath || typeof projectPath !== "string") {
    res.status(400).json({ error: "path is required" });
    return;
  }
  if (!profile || typeof profile !== "object") {
    res.status(400).json({ error: "profile object is required" });
    return;
  }
  const saved = writeProfile(projectPath, profile as Record<string, unknown>);
  res.json({ ok: true, path: saved });
});

app.get("/api/project/spec", (req, res) => {
  const projectPath = req.query.path;
  const name = req.query.name;
  if (!projectPath || typeof projectPath !== "string" || !name || typeof name !== "string") {
    res.status(400).json({ error: "path and name query params are required" });
    return;
  }
  try {
    res.json({ name, content: readSpec(projectPath, name) });
  } catch (err) {
    res.status(404).json({ error: err instanceof Error ? err.message : String(err) });
  }
});

app.get("/api/events", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const send = (payload: StoredEvent) => {
    if (res.writableEnded) return;
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
  };

  send({ type: "connected", ts: new Date().toISOString() });

  for (const event of eventLog.slice(-50)) {
    send(event);
  }

  const onEvent = (event: StoredEvent) => send(event);
  systemEvents.on("event", onEvent);

  const heartbeat = setInterval(() => {
    if (res.writableEnded) return;
    res.write(": heartbeat\n\n");
  }, 15000);

  req.on("close", () => {
    clearInterval(heartbeat);
    systemEvents.off("event", onEvent);
  });
});

app.post("/api/run", (req, res) => {
  const blocked = runStartBlocked();
  if (blocked) {
    res.status(409).json({ error: blocked });
    return;
  }
  startCollaborationRun(req.body ?? {}, res);
});

app.post("/api/run/local", (req, res) => {
  const blocked = runStartBlocked();
  if (blocked) {
    res.status(409).json({ error: blocked });
    return;
  }
  const {
    project,
    task = "",
    skipStructure = false,
    skipUi = false,
    noOllama = false,
  } = req.body ?? {};
  const target = resolveRunTargetOptions(req.body?.testTarget);

  if (!project || typeof project !== "string") {
    res.status(400).json({ error: "project is required" });
    return;
  }

  const runKind = taskRunKind(task, noOllama);
  const archivedRunId = archivePreviousRun(project);
  eventLog.length = 0;
  resetRunStateForNewRun(project, runKind, task);
  initRunLogs(project);
  if (archivedRunId) {
    pushEvent({
      type: "log",
      message: `Archived previous run to .agent/history/${archivedRunId}`,
      level: "info",
    });
  }

  try {
    if (runKind === "web_research") {
      pushEvent({
        type: "log",
        message: "Task routed to open-web research",
        level: "info",
      });
      startWebResearchForTask(project, task, { noOllama });
      res.json({ started: true, runKind });
      return;
    }

    pythonRunner.start({
      project,
      task,
      push: target.push,
      skipDeploy: target.skipDeploy,
      testTarget: target.testTarget,
      skipStructure,
      skipUi,
      noOllama,
    });
    res.json({ started: true, runKind });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
  }
});

app.post("/api/run/stop", (_req, res) => {
  collaborationLoop.resetIfStale();
  const wasRunning =
    collaborationLoop.isActive ||
    pythonRunner.running ||
    webResearchRunner.running ||
    cursorRunner.isRunning ||
    Boolean(runState.running);

  if (!wasRunning) {
    res.status(404).json({ error: "No active run" });
    return;
  }

  const collabWasActive = collaborationLoop.isActive;
  collaborationLoop.forceStop();
  pythonRunner.stop();
  webResearchRunner.stop();
  cursorRunner.cancel();
  cursorRunner.forceReset();
  // forceStop already emits collaboration_done + run_state for collaboration runs.
  if (!collabWasActive) {
    pushEvent({ type: "run_state", running: false });
    pushEvent({ type: "done", overall_ok: false, error: "Cancelled by user" });
  }
  res.json({ stopped: true });
});

app.post("/api/run/cursor", async (req, res) => {
  if (cursorRunner.isRunning || collaborationLoop.isActive) {
    res.status(409).json({ error: "Cursor agent already running" });
    return;
  }

  const apiKey = process.env.CURSOR_API_KEY;
  if (!apiKey) {
    res.status(400).json({
      error: "CURSOR_API_KEY not set. Add it to ai-assistant/.env (see test-runner/.env.example).",
    });
    return;
  }

  const {
    project,
    prompt,
    runtime = "cloud",
    repoUrl = "",
    useReport = true,
  } = req.body ?? {};

  if (!project) {
    res.status(400).json({ error: "project is required" });
    return;
  }

  const reportRel = ".agent/current/REPORT.md";
  const reportPath = path.join(project, reportRel);
  let finalPrompt = typeof prompt === "string" ? prompt.trim() : "";
  if (!finalPrompt && useReport && fs.existsSync(reportPath)) {
    finalPrompt = buildReportPrompt(reportRel);
  }
  if (!finalPrompt) {
    res.status(400).json({ error: "prompt is required (or run local tests first to generate REPORT.md)" });
    return;
  }

  const cursorTarget = resolveCursorRuntime(runtime, repoUrl);
  if (cursorTarget.error) {
    res.status(400).json({ error: cursorTarget.error });
    return;
  }

  pushEvent({
    type: "phase",
    phase: "cursor",
    status: "running",
    message: `Cursor SDK agent starting (${cursorTarget.runtime})…`,
  });
  initRunLogs(project);

  void cursorRunner
    .run({
      prompt: finalPrompt,
      cwd: project,
      runtime: cursorTarget.runtime,
      repoUrl: cursorTarget.repoUrl,
      apiKey,
    })
    .then((result) => {
      pushEvent({
        type: "phase",
        phase: "cursor",
        status: result.ok ? "done" : "failed",
        message: result.error ?? "Cursor agent finished",
      });
    });

  res.json({ started: true, prompt: finalPrompt });
});

app.get("/api/collaboration/config", (_req, res) => {
  res.json(readCollaborationConfig());
});

app.put("/api/collaboration/config", (req, res) => {
  const { helperPrompt, helperModel, maxTestRetries, maxIterations, maxQuestionRounds, maxInfoRequests } =
    req.body ?? {};
  const current = readCollaborationConfig();
  const updated = writeCollaborationConfig({
    helperPrompt: typeof helperPrompt === "string" ? helperPrompt : current.helperPrompt,
    helperModel: typeof helperModel === "string" ? helperModel : current.helperModel,
    maxTestRetries: typeof maxTestRetries === "number" ? maxTestRetries : current.maxTestRetries,
    maxIterations: typeof maxIterations === "number" ? maxIterations : current.maxIterations,
    maxQuestionRounds: typeof maxQuestionRounds === "number" ? maxQuestionRounds : current.maxQuestionRounds,
    maxInfoRequests: typeof maxInfoRequests === "number" ? maxInfoRequests : current.maxInfoRequests,
  });
  res.json(updated);
});

app.post("/api/run/resume", async (req, res) => {
  const blocked = runStartBlocked();
  if (blocked) {
    res.status(409).json({ error: blocked });
    return;
  }

  const body = req.body ?? {};
  const project = typeof body.project === "string" ? body.project : "";
  const runId = typeof body.runId === "string" ? body.runId : "";
  if (!project || !runId) {
    res.status(400).json({ error: "project and runId are required" });
    return;
  }

  const transcript = readCollaborationTranscript(project, runId);
  if (!transcript || !canResumeTranscript(transcript)) {
    res.status(400).json({ error: "No resumable collaboration transcript for this run" });
    return;
  }

  eventLog.length = 0;
  resetRunStateForNewRun(project, "ui_test", transcript.task);
  pushEvent({
    type: "phase",
    phase: "collaboration",
    status: "running",
    message: "Resuming run…",
  });

  res.json({ started: true, resumedFrom: runId, task: transcript.task });

  const apiKey = process.env.CURSOR_API_KEY;
  const target = resolveRunTargetOptions(body.testTarget);

  void beginCollaborationRun({
    project,
    task: transcript.task,
    target,
    apiKey,
    cursorRuntime: body.cursorRuntime === "local" ? "local" : "cloud",
    repoUrl: typeof body.repoUrl === "string" ? body.repoUrl : undefined,
    skipStructure: Boolean(body.skipStructure),
    skipUi: Boolean(body.skipUi),
    noOllama: Boolean(body.noOllama),
    resumeFrom: transcript,
    userNote: typeof body.note === "string" ? body.note : undefined,
  });
});

app.post("/api/run/full", (req, res) => {
  const blocked = runStartBlocked();
  if (blocked) {
    res.status(409).json({ error: blocked });
    return;
  }
  startCollaborationRun(req.body ?? {}, res);
});

const distDir = path.join(__dirname, "../dist");
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir));
  app.get("*", (_req, res) => {
    res.sendFile(path.join(distDir, "index.html"));
  });
}

const server = app.listen(PORT, "127.0.0.1", () => {
  console.log(`Test runner API on http://127.0.0.1:${PORT}`);
  console.log(`Dev UI: http://127.0.0.1:5175 (pnpm dev)`);

  const ollama = readOllamaConfig();
  void (async () => {
    const status = await fetchOllamaStatus(ollama);
    if (!status.reachable) {
      console.log("Ollama: not reachable — start Ollama before running tests");
      return;
    }
    if (!status.modelAvailable) {
      console.log(`Ollama: model ${ollama.model} not installed — run: ollama pull ${ollama.model}`);
      return;
    }
    if (!status.modelLoaded) {
      console.log(`Ollama: preloading ${ollama.model} into VRAM…`);
      try {
        await switchOllamaModel(ollama.model, () => {});
        console.log(`Ollama: ${ollama.model} ready`);
      } catch (err) {
        console.log(`Ollama preload skipped: ${err instanceof Error ? err.message : String(err)}`);
      }
      return;
    }
    console.log(`Ollama: ${ollama.model} already loaded`);
  })();
});

server.on("error", (err: NodeJS.ErrnoException) => {
  if (err.code === "EADDRINUSE") {
    console.error(
      `Port ${PORT} is already in use. Stop the other test runner, or run: .\\scripts\\stop-test-runner-ports.ps1`,
    );
    process.exit(1);
  }
  throw err;
});

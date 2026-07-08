import cors from "cors";
import dotenv from "dotenv";
import express from "express";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildReportPrompt, CursorRunner } from "./cursor-agent.js";
import { fetchOllamaStatus, preloadOllamaModel, readOllamaConfig } from "./ollama.js";
import { defaultProjectPath, PythonRunner, REPO_ROOT } from "./python-runner.js";
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
  resolveRunArtifact,
  sessionWithArtifactUrls,
} from "./project-report.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(REPO_ROOT, ".env") });
dotenv.config({ path: path.join(REPO_ROOT, "test-runner", ".env") });

const PORT = Number(process.env.TEST_RUNNER_PORT || 8767);
const pythonRunner = new PythonRunner();
const cursorRunner = new CursorRunner();

type StoredEvent = Record<string, unknown>;
const eventLog: StoredEvent[] = [];
const engineLogPath = path.join(REPO_ROOT, "logs", "test-runner-last-run.log");
let projectRunLogPath: string | null = null;

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
  if (type === "process_exit") return `[process_exit] code=${String(event.code)}`;
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
};

function resetRunStateForNewRun(project: string) {
  runState = {
    running: true,
    phase: "idle",
    phases: {},
    project,
    structuredTask: null,
    runReport: null,
    browserState: null,
    testTarget: null,
    lastResult: null,
  };
  pushEvent({ type: "run_cleared" });
}

function pushEvent(event: StoredEvent) {
  eventLog.push({ ...event, ts: event.ts ?? new Date().toISOString() });
  if (eventLog.length > 2000) eventLog.splice(0, eventLog.length - 2000);
  appendRunLogs(event);

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
  if (event.type === "done") {
    runState.running = false;
    runState.lastResult = event;
  }
  if (event.type === "process_exit") {
    runState.running = false;
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
}

pythonRunner.on("event", pushEvent);
cursorRunner.on("event", pushEvent);

const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/config", async (_req, res) => {
  const ollama = readOllamaConfig();
  const ollamaStatus = await fetchOllamaStatus(ollama);
  res.json({
    defaultProject: defaultProjectPath(),
    hasCursorApiKey: Boolean(process.env.CURSOR_API_KEY),
    repoRoot: REPO_ROOT,
    ollama: {
      ...ollama,
      ...ollamaStatus,
    },
  });
});

app.post("/api/ollama/preload", async (_req, res) => {
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
      res.json({ ok: true, message: `${ollama.model} already loaded` });
      return;
    }
    await preloadOllamaModel(ollama);
    res.json({ ok: true, message: `${ollama.model} loaded into VRAM` });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
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
  res.json(listRunHistory(projectPath));
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
  res.json({
    ...bundle,
    playwrightSession: sessionWithArtifactUrls(projectPath, runId, bundle.playwrightSession),
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
  res.json({
    ...bundle,
    playwrightSession: sessionWithArtifactUrls(projectPath, "current", bundle.playwrightSession),
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
  pythonRunner.on("event", onEvent);
  cursorRunner.on("event", onEvent);

  const heartbeat = setInterval(() => {
    if (res.writableEnded) return;
    res.write(": heartbeat\n\n");
  }, 15000);

  req.on("close", () => {
    clearInterval(heartbeat);
    pythonRunner.off("event", onEvent);
    cursorRunner.off("event", onEvent);
  });
});

app.post("/api/run/local", (req, res) => {
  if (pythonRunner.running) {
    res.status(409).json({ error: "Local agent run already in progress" });
    return;
  }
  const {
    project,
    task = "",
    push = false,
    skipDeploy = false,
    skipStructure = false,
    skipUi = false,
    noOllama = false,
  } = req.body ?? {};

  if (!project || typeof project !== "string") {
    res.status(400).json({ error: "project is required" });
    return;
  }

  eventLog.length = 0;
  resetRunStateForNewRun(project);
  initRunLogs(project);

  try {
    pythonRunner.start({
      project,
      task,
      push,
      skipDeploy,
      testTarget: req.body?.testTarget === "deployed" ? "deployed" : "local",
      skipStructure,
      skipUi,
      noOllama,
    });
    res.json({ started: true });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
  }
});

app.post("/api/run/cursor", async (req, res) => {
  if (cursorRunner.isRunning) {
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

  pushEvent({
    type: "phase",
    phase: "cursor",
    status: "running",
    message: "Cursor SDK agent starting…",
  });
  initRunLogs(project);

  void cursorRunner
    .run({
      prompt: finalPrompt,
      cwd: project,
      runtime: runtime === "local" ? "local" : "cloud",
      repoUrl: repoUrl || undefined,
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

app.post("/api/run/full", async (req, res) => {
  if (pythonRunner.running || cursorRunner.isRunning) {
    res.status(409).json({ error: "A run is already in progress" });
    return;
  }

  const body = req.body ?? {};
  const project = typeof body.project === "string" ? body.project : "";
  eventLog.length = 0;
  resetRunStateForNewRun(project || "");
  initRunLogs(project || undefined);

  pythonRunner.once("event", (event) => {
    if (event.type !== "done" && event.type !== "process_exit") return;
    const apiKey = process.env.CURSOR_API_KEY;
    if (!apiKey || body.skipCursor) return;
    const project = body.project as string;
    const reportRel = ".agent/current/REPORT.md";
    void cursorRunner.run({
      prompt: buildReportPrompt(reportRel),
      cwd: project,
      runtime: body.cursorRuntime === "local" ? "local" : "cloud",
      repoUrl: body.repoUrl,
      apiKey,
    });
  });

  try {
    pythonRunner.start({
      project: body.project,
      task: body.task,
      push: body.push,
      skipDeploy: body.skipDeploy,
      testTarget: body.testTarget === "deployed" ? "deployed" : "local",
      skipStructure: body.skipStructure,
      skipUi: body.skipUi,
      noOllama: body.noOllama,
    });
    res.json({ started: true });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
  }
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
    if (status.modelLoaded) {
      console.log(`Ollama: ${ollama.model} already loaded`);
      return;
    }
    console.log(`Ollama: preloading ${ollama.model} into VRAM…`);
    try {
      await preloadOllamaModel(ollama);
      console.log(`Ollama: ${ollama.model} ready`);
    } catch (err) {
      console.log(`Ollama preload skipped: ${err instanceof Error ? err.message : String(err)}`);
    }
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

import cors from "cors";
import dotenv from "dotenv";
import express from "express";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildReportPrompt, CursorRunner } from "./cursor-agent.js";
import { defaultProjectPath, PythonRunner, REPO_ROOT } from "./python-runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(REPO_ROOT, ".env") });
dotenv.config({ path: path.join(REPO_ROOT, "test-runner", ".env") });

const PORT = Number(process.env.TEST_RUNNER_PORT || 8767);
const pythonRunner = new PythonRunner();
const cursorRunner = new CursorRunner();

type StoredEvent = Record<string, unknown>;
const eventLog: StoredEvent[] = [];
let runState: Record<string, unknown> = {
  running: false,
  phase: "idle",
  phases: {},
};

function pushEvent(event: StoredEvent) {
  eventLog.push({ ...event, ts: event.ts ?? new Date().toISOString() });
  if (eventLog.length > 2000) eventLog.splice(0, eventLog.length - 2000);

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
}

pythonRunner.on("event", pushEvent);
cursorRunner.on("event", pushEvent);

const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/config", (_req, res) => {
  res.json({
    defaultProject: defaultProjectPath(),
    hasCursorApiKey: Boolean(process.env.CURSOR_API_KEY),
    repoRoot: REPO_ROOT,
  });
});

app.get("/api/state", (_req, res) => {
  res.json({ ...runState, events: eventLog.slice(-300) });
});

app.get("/api/events", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (payload: StoredEvent) => {
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
  };

  for (const event of eventLog.slice(-50)) {
    send(event);
  }

  const onEvent = (event: StoredEvent) => send(event);
  pythonRunner.on("event", onEvent);
  cursorRunner.on("event", onEvent);

  req.on("close", () => {
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
  runState = { running: true, phase: "task_structure", phases: {} };

  try {
    pythonRunner.start({
      project,
      task,
      push,
      skipDeploy,
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
  eventLog.length = 0;
  runState = { running: true, phase: "task_structure", phases: {} };

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

app.listen(PORT, "127.0.0.1", () => {
  console.log(`Test runner API on http://127.0.0.1:${PORT}`);
  console.log(`Dev UI: http://127.0.0.1:5175 (pnpm dev)`);
});

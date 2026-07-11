import { spawn } from "node:child_process";
import process from "node:process";
import { REPO_ROOT, resolvePythonExecutable } from "./python-env.js";

export type RunKind = "web_research" | "ui_test";

const UI_SIGNALS = [
  "button",
  "modal",
  "login",
  "navbar",
  "home screen",
  "home page",
  "playwright",
  "deploy",
  "click the",
  "remove a",
  "add a",
  "verify",
  "test the",
  "page load",
  "settings page",
  "ui test",
  "explore the app",
  "open the app",
] as const;

const WEB_SIGNALS = [
  "research",
  "look up",
  "find out",
  "find me",
  "what is",
  "who is",
  "patch notes",
  "changelog",
  "release notes",
  "latest news",
  "latest version",
  "search the web",
  "search online",
  "compare prices",
  "competitors",
  "tell me about",
  "how does",
  "explain what",
  "find information",
  "gather data",
  "scrape",
  "from the internet",
  "on the web",
] as const;

function scoreSignals(signals: readonly string[], text: string): number {
  return signals.reduce((count, signal) => (text.includes(signal) ? count + 1 : count), 0);
}

/** Fast keyword routing — no subprocess or Ollama. */
export function classifyTaskRunKindHeuristic(task: string): RunKind | null {
  const text = task.trim().toLowerCase();
  if (!text) return "ui_test";

  const uiScore = scoreSignals(UI_SIGNALS, text);
  const webScore = scoreSignals(WEB_SIGNALS, text);

  if (webScore > 0 && uiScore === 0) return "web_research";
  if (uiScore > 0 && webScore === 0) return "ui_test";
  if (webScore >= 2 && webScore > uiScore) return "web_research";
  if (uiScore >= 2 && uiScore > webScore) return "ui_test";
  return null;
}

function spawnClassify(task: string, noOllama: boolean, timeoutMs = 45_000): Promise<RunKind> {
  const trimmed = task.trim();
  if (!trimmed) return Promise.resolve("ui_test");

  const python = resolvePythonExecutable();
  return new Promise((resolve) => {
    const child = spawn(
      python,
      ["-m", "web_surf", "classify", "--task", trimmed, ...(noOllama ? ["--no-ollama"] : [])],
      {
        cwd: REPO_ROOT,
        env: {
          ...process.env,
          PYTHONPATH: REPO_ROOT,
          PYTHONIOENCODING: "utf-8",
        },
        windowsHide: true,
      },
    );

    let stdout = "";
    let settled = false;
    const finish = (kind: RunKind) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(kind);
    };

    const timer = setTimeout(() => {
      child.kill();
      finish("ui_test");
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer | string) => {
      stdout += String(chunk);
    });
    child.on("error", () => finish("ui_test"));
    child.on("close", () => {
      const kind = stdout.trim();
      finish(kind === "web_research" ? "web_research" : "ui_test");
    });
  });
}

/** Non-blocking classification for the collaboration loop. */
export function classifyTaskRunKindAsync(task: string, noOllama = false): Promise<RunKind> {
  const heuristic = classifyTaskRunKindHeuristic(task);
  if (heuristic) return Promise.resolve(heuristic);
  if (noOllama) return Promise.resolve("ui_test");
  return spawnClassify(task, noOllama);
}

/** Blocking classification — prefer heuristic or async variants on request paths. */
export function classifyTaskRunKind(task: string, noOllama = false): RunKind {
  const heuristic = classifyTaskRunKindHeuristic(task);
  if (heuristic) return heuristic;
  if (noOllama) return "ui_test";
  // Ambiguous tasks without Ollama default to UI test on API paths.
  return "ui_test";
}

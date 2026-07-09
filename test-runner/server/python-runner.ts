import { EventEmitter } from "node:events";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import process from "node:process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");

export type RunOptions = {
  project: string;
  task?: string;
  push?: boolean;
  skipDeploy?: boolean;
  testTarget?: "local" | "deployed";
  skipStructure?: boolean;
  skipUi?: boolean;
  noOllama?: boolean;
};

export class PythonRunner extends EventEmitter {
  private proc: ChildProcessWithoutNullStreams | null = null;

  get running(): boolean {
    return this.proc !== null;
  }

  start(options: RunOptions): void {
    if (this.proc) {
      throw new Error("A run is already in progress");
    }

    const python = path.join(REPO_ROOT, "voice", ".venv", "Scripts", "python.exe");
    const args = [
      "-m",
      "ui_test",
      "--emit-events",
      "--project",
      options.project,
    ];
    if (options.task) args.push("--task", options.task);
    if (options.push) args.push("--push");
    if (options.skipDeploy) args.push("--skip-deploy");
    if (options.testTarget) args.push("--test-target", options.testTarget);
    if (options.skipStructure) args.push("--skip-structure");
    if (options.skipUi) args.push("--skip-ui");
    if (options.noOllama) args.push("--no-ollama");

    this.emit("event", { type: "run_state", running: true });

    this.proc = spawn(python, args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: REPO_ROOT,
        PYTHONIOENCODING: "utf-8",
      },
      windowsHide: true,
    });

    let stdoutBuffer = "";
    this.proc.stdout.on("data", (chunk: Buffer) => {
      stdoutBuffer += chunk.toString("utf8");
      const lines = stdoutBuffer.split("\n");
      stdoutBuffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event = JSON.parse(trimmed) as Record<string, unknown>;
          this.emit("event", event);
        } catch {
          this.emit("event", { type: "log", message: trimmed, level: "info" });
        }
      }
    });

    this.proc.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8").trim();
      if (text) {
        this.emit("event", { type: "log", message: text, level: "error" });
      }
    });

    this.proc.on("close", (code) => {
      this.proc = null;
      this.emit("event", {
        type: "process_exit",
        code,
        running: false,
      });
    });
  }

  stop(): void {
    const proc = this.proc;
    if (!proc) return;
    this.proc = null;
    const pid = proc.pid;
    if (process.platform === "win32" && pid) {
      spawn("taskkill", ["/T", "/F", "/PID", String(pid)], { windowsHide: true, stdio: "ignore" });
      return;
    }
    try {
      proc.kill("SIGTERM");
    } catch {
      proc.kill();
    }
  }
}

export function defaultProjectPath(): string {
  return path.resolve(REPO_ROOT, "../content-manager");
}

export { REPO_ROOT };

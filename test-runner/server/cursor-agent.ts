import { EventEmitter } from "node:events";
import { Agent, CursorAgentError, type Run, type SDKMessage } from "@cursor/sdk";
import { renderPrompt } from "./prompts.js";

export type CursorRuntime = "local" | "cloud";

export type CursorRunOptions = {
  prompt: string;
  cwd: string;
  runtime: CursorRuntime;
  repoUrl?: string;
  apiKey: string;
  modelId?: string;
};

const CONNECT_TIMEOUT_MS = 90_000;

function connectionTimeoutMessage(runtime: CursorRuntime): string {
  const base = `Cursor connection timed out after ${CONNECT_TIMEOUT_MS / 1000}s.`;
  if (runtime === "cloud") {
    return `${base} Check CURSOR_API_KEY and the GitHub repo URL, then cancel and re-run.`;
  }
  return `${base} For local runtime: (1) open the Cursor desktop app on this machine, (2) confirm CURSOR_API_KEY in ai-assistant/.env, (3) cancel and re-run.`;
}

function formatToolActivity(name: string, status: string): string {
  if (status === "running") return `Using ${name}…`;
  if (status === "completed") return `${name} done`;
  return `${name} failed`;
}

function previewText(text: string, max = 240): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max)}…`;
}

function handleSdkMessage(emitter: EventEmitter, event: SDKMessage): void {
  switch (event.type) {
    case "assistant":
      for (const block of event.message.content) {
        if (block.type === "text" && block.text) {
          emitter.emit("event", {
            type: "cursor_text",
            role: "assistant",
            text: block.text,
          });
        } else if (block.type === "tool_use") {
          // tool_call events cover live tool status
        }
      }
      break;
    case "thinking":
      // Omit thinking deltas from the UI — they arrive as tiny fragments and read poorly.
      break;
    case "tool_call":
      if (event.status === "running") {
        emitter.emit("event", {
          type: "cursor_activity",
          activity: formatToolActivity(event.name, event.status),
          kind: "tool",
        });
      } else if (event.status === "completed") {
        emitter.emit("event", {
          type: "cursor_activity",
          activity: formatToolActivity(event.name, event.status),
          kind: "tool",
        });
      }
      break;
    case "task":
      if (event.text?.trim()) {
        emitter.emit("event", {
          type: "cursor_activity",
          activity: previewText(event.text),
          kind: "task",
        });
      }
      break;
    case "status":
      if (event.message?.trim()) {
        emitter.emit("event", {
          type: "cursor_activity",
          activity: event.message.trim(),
          kind: "status",
        });
      }
      break;
    default:
      break;
  }
}

export class CursorRunner extends EventEmitter {
  private running = false;
  private abortRequested = false;
  private activeRun: Run | null = null;
  private runGeneration = 0;
  private createAbortReject: ((err: Error) => void) | null = null;

  get isRunning(): boolean {
    return this.running;
  }

  cancel(): void {
    this.abortRequested = true;
    this.createAbortReject?.(new Error("Cancelled by user"));
    if (this.activeRun?.supports("cancel")) {
      void this.activeRun.cancel();
    }
  }

  /** Clear a stuck isRunning flag after force-stop (e.g. hung Agent.create). */
  forceReset(): void {
    this.runGeneration += 1;
    this.abortRequested = true;
    this.createAbortReject?.(new Error("Cancelled by user"));
    this.activeRun = null;
    this.running = false;
    this.emit("event", {
      type: "cursor",
      status: "cancelled",
      message: "Cancelled by user",
    });
  }

  private emitConnecting(runtime: CursorRuntime, elapsedSec?: number): void {
    const suffix = elapsedSec && elapsedSec > 0 ? ` ${elapsedSec}s` : "";
    this.emit("event", {
      type: "cursor_activity",
      activity: `Connecting to Cursor (${runtime})…${suffix}`,
      kind: "status",
    });
  }

  private async createAgent(
    options: CursorRunOptions,
    modelId: string,
    generation: number,
  ): Promise<AsyncDisposable & { agentId: string; send: (message: string) => Promise<Run> }> {
    const start = Date.now();
    const heartbeat = setInterval(() => {
      if (generation !== this.runGeneration) return;
      const secs = Math.round((Date.now() - start) / 1000);
      this.emitConnecting(options.runtime, secs);
    }, 5000);

    const createPromise =
      options.runtime === "cloud"
        ? Agent.create({
            apiKey: options.apiKey,
            model: { id: modelId },
            cloud: {
              repos: [{ url: options.repoUrl! }],
            },
          })
        : Agent.create({
            apiKey: options.apiKey,
            model: { id: modelId },
            local: {
              cwd: options.cwd,
              settingSources: [],
            },
          });

    let timer: ReturnType<typeof setTimeout> | undefined;
    const abortPromise = new Promise<never>((_, reject) => {
      this.createAbortReject = (err: Error) => reject(err);
    });
    try {
      const agent = await Promise.race([
        createPromise,
        abortPromise,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            reject(new Error(connectionTimeoutMessage(options.runtime)));
          }, CONNECT_TIMEOUT_MS);
        }),
      ]);

      if (this.abortRequested || generation !== this.runGeneration) {
        await agent[Symbol.asyncDispose]();
        throw new Error("Cancelled by user");
      }

      return agent;
    } finally {
      this.createAbortReject = null;
      clearInterval(heartbeat);
      if (timer) clearTimeout(timer);
    }
  }

  async run(options: CursorRunOptions): Promise<{ ok: boolean; agentId?: string; runId?: string; error?: string; cancelled?: boolean }> {
    if (this.running) {
      throw new Error("Cursor agent already running");
    }
    const generation = this.runGeneration + 1;
    this.runGeneration = generation;
    this.running = true;
    this.abortRequested = false;
    this.emit("event", {
      type: "cursor",
      status: "starting",
      message: `Starting ${options.runtime} Cursor agent…`,
    });
    this.emitConnecting(options.runtime);

    const modelId = options.modelId || "composer-2.5";

    try {
      if (options.runtime === "cloud" && !options.repoUrl) {
        throw new Error("repoUrl is required for cloud runtime");
      }

      const agent = await this.createAgent(options, modelId, generation);
      if (generation !== this.runGeneration) {
        return { ok: false, cancelled: true, error: "Cancelled by user" };
      }

      return await this.runWithAgent(
        agent,
        options,
        generation,
        options.runtime === "cloud"
          ? {
              status: "agent_ready",
              message: "Cloud agent created — open Cursor → Agents sidebar to follow along",
              cursorUiHint: "cloud_agents_window",
            }
          : {
              status: "agent_ready",
              message:
                "Local agent started via SDK bridge. For full IDE visibility, prefer Cloud runtime (shows in Cursor Agents sidebar).",
              cursorUiHint: "local_bridge",
            },
      );
    } catch (err) {
      const cancelled = this.abortRequested || generation !== this.runGeneration;
      const message =
        cancelled && !(err instanceof Error && err.message.includes("timed out"))
          ? "Cancelled by user"
          : err instanceof CursorAgentError
            ? `${err.message} (retryable=${err.isRetryable})`
            : err instanceof Error
              ? err.message
              : String(err);
      this.emit("event", { type: "cursor", status: cancelled ? "cancelled" : "error", message });
      return { ok: false, cancelled, error: message };
    } finally {
      if (generation === this.runGeneration) {
        this.running = false;
        this.activeRun = null;
        this.abortRequested = false;
      }
    }
  }

  private async runWithAgent(
    agent: AsyncDisposable & { agentId: string; send: (message: string) => Promise<Run> },
    options: CursorRunOptions,
    generation: number,
    readyEvent: { status: string; message: string; cursorUiHint?: string },
  ): Promise<{ ok: boolean; agentId?: string; runId?: string; error?: string; cancelled?: boolean }> {
    await using _agent = agent;

    if (generation !== this.runGeneration) {
      return { ok: false, cancelled: true, error: "Cancelled by user" };
    }

    this.emit("event", {
      type: "cursor",
      status: readyEvent.status,
      agentId: agent.agentId,
      message: readyEvent.message,
      cursorUiHint: readyEvent.cursorUiHint,
    });

    const run = await agent.send(options.prompt);
    this.activeRun = run;

    this.emit("event", {
      type: "cursor",
      status: "running",
      agentId: agent.agentId,
      runId: run.id,
      message: "Agent run started",
    });

    const streamTask = (async () => {
      try {
        for await (const event of run.stream()) {
          if (this.abortRequested || generation !== this.runGeneration) break;
          handleSdkMessage(this, event);
        }
      } catch {
        /* stream may end abruptly on cancel */
      }
    })();

    const result = await run.wait();
    await streamTask.catch(() => {});

    const cancelled =
      result.status === "cancelled" || this.abortRequested || generation !== this.runGeneration;
    const ok = result.status === "finished";
    this.emit("event", {
      type: "cursor",
      status: cancelled ? "cancelled" : ok ? "done" : "failed",
      agentId: agent.agentId,
      runId: run.id,
      message: cancelled ? "cancelled" : result.status,
    });

    return {
      ok,
      cancelled,
      agentId: agent.agentId,
      runId: run.id,
      error: cancelled ? "Cancelled by user" : ok ? undefined : result.error?.message ?? result.status,
    };
  }
}

export function buildReportPrompt(reportPath: string): string {
  return renderPrompt("cursor.report_prompt", { report_path: reportPath });
}

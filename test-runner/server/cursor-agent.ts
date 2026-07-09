import { EventEmitter } from "node:events";
import { Agent, CursorAgentError, type Run, type SDKMessage } from "@cursor/sdk";

export type CursorRuntime = "local" | "cloud";

export type CursorRunOptions = {
  prompt: string;
  cwd: string;
  runtime: CursorRuntime;
  repoUrl?: string;
  apiKey: string;
  modelId?: string;
};

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

  get isRunning(): boolean {
    return this.running;
  }

  cancel(): void {
    this.abortRequested = true;
    if (this.activeRun?.supports("cancel")) {
      void this.activeRun.cancel();
    }
  }

  /** Clear a stuck isRunning flag after force-stop (e.g. hung Agent.create). */
  forceReset(): void {
    this.abortRequested = false;
    this.activeRun = null;
    this.running = false;
  }

  async run(options: CursorRunOptions): Promise<{ ok: boolean; agentId?: string; runId?: string; error?: string; cancelled?: boolean }> {
    if (this.running) {
      throw new Error("Cursor agent already running");
    }
    this.running = true;
    this.abortRequested = false;
    this.emit("event", {
      type: "cursor",
      status: "starting",
      message: `Starting ${options.runtime} Cursor agent…`,
    });
    this.emit("event", {
      type: "cursor_activity",
      activity: `Connecting to Cursor (${options.runtime})…`,
      kind: "status",
    });

    const modelId = options.modelId || "composer-2.5";

    try {
      if (options.runtime === "cloud") {
        if (!options.repoUrl) {
          throw new Error("repoUrl is required for cloud runtime");
        }
        return await this.runWithAgent(
          await Agent.create({
            apiKey: options.apiKey,
            model: { id: modelId },
            cloud: {
              repos: [{ url: options.repoUrl }],
            },
          }),
          options,
          {
            status: "agent_ready",
            message: "Cloud agent created — open Cursor → Agents sidebar to follow along",
            cursorUiHint: "cloud_agents_window",
          },
        );
      }

      return await this.runWithAgent(
        await Agent.create({
          apiKey: options.apiKey,
          model: { id: modelId },
          local: {
            cwd: options.cwd,
            settingSources: [],
          },
        }),
        options,
        {
          status: "agent_ready",
          message:
            "Local agent started via SDK bridge. For full IDE visibility, prefer Cloud runtime (shows in Cursor Agents sidebar).",
          cursorUiHint: "local_bridge",
        },
      );
    } catch (err) {
      const message =
        err instanceof CursorAgentError
          ? `${err.message} (retryable=${err.isRetryable})`
          : err instanceof Error
            ? err.message
            : String(err);
      this.emit("event", { type: "cursor", status: "error", message });
      return { ok: false, error: message };
    } finally {
      this.running = false;
      this.activeRun = null;
      this.abortRequested = false;
    }
  }

  private async runWithAgent(
    agent: AsyncDisposable & { agentId: string; send: (message: string) => Promise<Run> },
    options: CursorRunOptions,
    readyEvent: { status: string; message: string; cursorUiHint?: string },
  ): Promise<{ ok: boolean; agentId?: string; runId?: string; error?: string; cancelled?: boolean }> {
    await using _agent = agent;

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
          if (this.abortRequested) break;
          handleSdkMessage(this, event);
        }
      } catch {
        /* stream may end abruptly on cancel */
      }
    })();

    const result = await run.wait();
    await streamTask.catch(() => {});

    const cancelled = result.status === "cancelled" || this.abortRequested;
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
  return [
    `Read ${reportPath} and implement the fixes described there.`,
    "Add missing data-testid hooks listed under Structure.",
    "Keep changes minimal and match acceptance criteria.",
  ].join("\n");
}

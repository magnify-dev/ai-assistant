import { EventEmitter } from "node:events";
import { Agent, CursorAgentError } from "@cursor/sdk";

export type CursorRuntime = "local" | "cloud";

export type CursorRunOptions = {
  prompt: string;
  cwd: string;
  runtime: CursorRuntime;
  repoUrl?: string;
  apiKey: string;
  modelId?: string;
};

export class CursorRunner extends EventEmitter {
  private running = false;

  get isRunning(): boolean {
    return this.running;
  }

  async run(options: CursorRunOptions): Promise<{ ok: boolean; agentId?: string; runId?: string; error?: string }> {
    if (this.running) {
      throw new Error("Cursor agent already running");
    }
    this.running = true;
    this.emit("event", {
      type: "cursor",
      status: "starting",
      message: `Starting ${options.runtime} Cursor agent…`,
    });

    const modelId = options.modelId || "composer-2.5";

    try {
      if (options.runtime === "cloud") {
        if (!options.repoUrl) {
          throw new Error("repoUrl is required for cloud runtime");
        }
        await using agent = await Agent.create({
          apiKey: options.apiKey,
          model: { id: modelId },
          cloud: {
            repos: [{ url: options.repoUrl }],
          },
        });

        this.emit("event", {
          type: "cursor",
          status: "agent_ready",
          agentId: agent.agentId,
          message: "Cloud agent created — open Cursor → Agents sidebar to follow along",
          cursorUiHint: "cloud_agents_window",
        });

        const run = await agent.send(options.prompt);
        this.emit("event", {
          type: "cursor",
          status: "running",
          agentId: agent.agentId,
          runId: run.id,
          message: "Agent run started",
        });

        for await (const event of run.stream()) {
          if (event.type === "assistant") {
            for (const block of event.message.content) {
              if (block.type === "text" && block.text) {
                this.emit("event", {
                  type: "cursor_text",
                  role: "assistant",
                  text: block.text,
                });
              }
            }
          }
        }

        const result = await run.wait();
        const ok = result.status === "finished";
        this.emit("event", {
          type: "cursor",
          status: ok ? "done" : "failed",
          agentId: agent.agentId,
          runId: run.id,
          message: result.status,
        });
        return { ok, agentId: agent.agentId, runId: run.id };
      }

      await using agent = await Agent.create({
        apiKey: options.apiKey,
        model: { id: modelId },
        local: {
          cwd: options.cwd,
          settingSources: [],
        },
      });

      this.emit("event", {
        type: "cursor",
        status: "agent_ready",
        agentId: agent.agentId,
        message:
          "Local agent started via SDK bridge. For full IDE visibility, prefer Cloud runtime (shows in Cursor Agents sidebar).",
        cursorUiHint: "local_bridge",
      });

      const run = await agent.send(options.prompt);
      this.emit("event", {
        type: "cursor",
        status: "running",
        agentId: agent.agentId,
        runId: run.id,
      });

      for await (const event of run.stream()) {
        if (event.type === "assistant") {
          for (const block of event.message.content) {
            if (block.type === "text" && block.text) {
              this.emit("event", {
                type: "cursor_text",
                role: "assistant",
                text: block.text,
              });
            }
          }
        }
      }

      const result = await run.wait();
      const ok = result.status === "finished";
      this.emit("event", {
        type: "cursor",
        status: ok ? "done" : "failed",
        agentId: agent.agentId,
        runId: run.id,
        message: result.status,
      });
      return { ok, agentId: agent.agentId, runId: run.id };
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
    }
  }
}

export function buildReportPrompt(reportPath: string): string {
  return [
    `Read ${reportPath} and implement the fixes described there.`,
    "Add missing data-testid hooks listed under Structure.",
    "Keep changes minimal and match acceptance criteria.",
  ].join("\n");
}

import { EventEmitter } from "node:events";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import process from "node:process";
import { REPO_ROOT, resolvePythonExecutable } from "./python-env.js";
import { readOllamaConfig } from "./ollama.js";
import { stopChildProcess } from "./process-stop.js";

export type WebResearchRunOptions = {
  project: string;
  query: string;
  maxPages?: number;
  noOllama?: boolean;
  helpResponder?: (
    request: Record<string, unknown>,
  ) => Promise<string | { ok: boolean; content?: string; error?: string }>;
};

export class WebResearchRunner extends EventEmitter {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private sawResult = false;
  private helpRequests = new Set<string>();
  private helpControllers = new Set<AbortController>();

  get running(): boolean {
    return this.proc !== null;
  }

  start(options: WebResearchRunOptions): void {
    if (this.proc) {
      throw new Error("A web research run is already in progress");
    }

    this.sawResult = false;
    this.helpRequests.clear();
    const python = resolvePythonExecutable();
    const args = [
      "-m",
      "web_surf",
      "research",
      "--emit-events",
      "--query",
      options.query,
      "--project",
      options.project,
    ];
    if (options.maxPages) args.push("--max-pages", String(options.maxPages));
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
    let stderrTail = "";

    this.proc.stdout.on("data", (chunk: Buffer) => {
      stdoutBuffer += chunk.toString("utf8");
      const lines = stdoutBuffer.split("\n");
      stdoutBuffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event = JSON.parse(trimmed) as Record<string, unknown>;
          if (event.type === "web_research_result") {
            this.sawResult = true;
          }
          this.emit("event", event);
          if (event.type === "web_help_request") {
            void this.answerHelpRequest(
              event,
              this.proc,
              Boolean(options.noOllama),
              options.helpResponder,
            );
          }
        } catch {
          this.emit("event", { type: "log", message: trimmed, level: "info" });
        }
      }
    });

    this.proc.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8").trim();
      if (!text) return;
      stderrTail = `${stderrTail}\n${text}`.trim().slice(-4000);
      this.emit("event", { type: "log", message: text, level: "error" });
    });

    this.proc.on("close", (code) => {
      this.proc = null;
      for (const controller of this.helpControllers) controller.abort();
      this.helpControllers.clear();
      if (code !== 0 && !this.sawResult) {
        const message =
          stderrTail ||
          `Web research process exited with code ${code ?? "unknown"}. ` +
            `Install deps: "${python}" -m pip install -r web_surf/requirements.txt`;
        this.emit("event", {
          type: "phase",
          phase: "web_research",
          status: "failed",
          message,
        });
        this.emit("event", {
          type: "web_research_result",
          query: options.query,
          answer: "",
          pages_fetched: 0,
          facts_added: 0,
          errors: [message],
        });
        this.emit("event", { type: "done", overall_ok: false, error: message });
        this.emit("event", { type: "run_state", running: false });
      }
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
    for (const controller of this.helpControllers) controller.abort();
    this.helpControllers.clear();
    stopChildProcess(proc);
  }

  private async answerHelpRequest(
    request: Record<string, unknown>,
    proc: ChildProcessWithoutNullStreams | null,
    disabled: boolean,
    helpResponder?: WebResearchRunOptions["helpResponder"],
  ): Promise<void> {
    if (!proc || proc !== this.proc) return;
    const requestId = String(request.request_id ?? request.requestId ?? request.id ?? "").trim();
    if (requestId && this.helpRequests.has(requestId)) return;
    if (requestId) this.helpRequests.add(requestId);

    const response: Record<string, unknown> = {
      type: "web_help_response",
      request_id: requestId || undefined,
      ts: new Date().toISOString(),
    };

    if (helpResponder) {
      try {
        const result = await helpResponder(request);
        if (typeof result === "string") {
          response.ok = true;
          response.content = result;
          response.response = result;
        } else {
          response.ok = result.ok;
          response.content = result.content;
          response.response = result.content;
          response.error = result.error;
        }
      } catch (error) {
        response.ok = false;
        response.error = error instanceof Error ? error.message : String(error);
      }
      if (proc === this.proc) this.deliverHelpResponse(proc, response);
      return;
    }

    if (disabled) {
      response.ok = false;
      response.error = "Ollama collaboration is disabled for this run";
      this.deliverHelpResponse(proc, response);
      return;
    }

    const cfg = readOllamaConfig();
    const controller = new AbortController();
    this.helpControllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 180_000);
    try {
      const suppliedMessages = Array.isArray(request.messages)
        ? request.messages
            .filter((message): message is Record<string, unknown> => Boolean(message && typeof message === "object"))
            .map((message) => ({
              role: String(message.role ?? "user"),
              content: String(message.content ?? message.text ?? ""),
            }))
            .filter((message) => message.content)
        : [];
      const prompt = String(
        request.prompt ?? request.question ?? request.request ?? request.message ?? "",
      ).trim();
      const context = request.context
        ? typeof request.context === "string"
          ? request.context
          : JSON.stringify(request.context)
        : "";
      const messages =
        suppliedMessages.length > 0
          ? suppliedMessages
          : [
              {
                role: "system",
                content:
                  "You are the local reasoning helper for a stepwise web exploration controller. " +
                  "Return concise, actionable guidance grounded only in the supplied browser state. " +
                  "When the request asks for JSON, return valid JSON without markdown fences.",
              },
              {
                role: "user",
                content: [prompt || "Choose the best next web exploration action.", context].filter(Boolean).join(
                  "\n\nContext:\n",
                ),
              },
            ];

      const result = await fetch(`${cfg.url.replace(/\/$/, "")}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: cfg.model,
          messages,
          stream: false,
          format: request.response_format ?? request.format,
          keep_alive: -1,
        }),
        signal: controller.signal,
      });
      if (!result.ok) throw new Error(`Ollama helper failed: HTTP ${result.status}`);
      const body = (await result.json()) as {
        message?: { content?: string };
        response?: string;
        done_reason?: string;
      };
      const content = String(body.message?.content ?? body.response ?? "");
      response.ok = true;
      response.content = content;
      response.response = content;
      response.model = cfg.model;
      response.done_reason = body.done_reason;
      this.emit("event", {
        type: "web_llm_exchange",
        prompt_key: "web_research.help",
        label: "Browser helper",
        model: cfg.model,
        step_id: String(request.step_id ?? request.stepId ?? ""),
        session_id: String(request.session_id ?? request.sessionId ?? ""),
        system_prompt: messages.find((message) => message.role === "system")?.content ?? "",
        user_input: messages
          .filter((message) => message.role !== "system")
          .map((message) => `${message.role.toUpperCase()}\n${message.content}`)
          .join("\n\n"),
        response: content,
        ok: true,
        request_id: requestId || undefined,
      });
    } catch (error) {
      response.ok = false;
      response.error =
        error instanceof Error && error.name === "AbortError"
          ? "Ollama helper request timed out or the run stopped"
          : error instanceof Error
            ? error.message
            : String(error);
    } finally {
      clearTimeout(timeout);
      this.helpControllers.delete(controller);
    }

    if (proc === this.proc) this.deliverHelpResponse(proc, response);
  }

  private deliverHelpResponse(
    proc: ChildProcessWithoutNullStreams,
    response: Record<string, unknown>,
  ): void {
    if (proc !== this.proc || proc.stdin.destroyed || !proc.stdin.writable) return;
    proc.stdin.write(`${JSON.stringify(response)}\n`);
  }
}

export { REPO_ROOT };

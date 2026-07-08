import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { readCollaborationConfig } from "./collaboration-config.js";
import {
  buildHelperHandoffAfterPipelineFailure,
  expandPromptForHelper,
  isGitOrDeployBlockingFailure,
  pipelineFailureSummary,
  pipelineStopError,
  readRunPayload,
  triageTask,
  verifyAfterTest,
} from "./collaboration-eval.js";
import {
  buildConversationContext,
  determineResumePlan,
  saveCollaborationTranscript,
  type CollaborationTranscript,
  type ResumePlan,
} from "./collaboration-transcript.js";
import { CursorRunner } from "./cursor-agent.js";
import { PythonRunner, type RunOptions } from "./python-runner.js";

export type AgentCard = {
  id: string;
  agent: "local" | "helper";
  agentLabel: string;
  iteration: number;
  status: "running" | "done" | "failed";
  startedAt: string;
  completedAt?: string;
  summary?: string;
  outcomeType?: "answer" | "prompt" | "response";
  outcomeText?: string;
  messages?: Array<{ role: string; text: string; ts?: string }>;
  historical?: boolean;
};

export type CollaborationRunOptions = RunOptions & {
  cursorRuntime?: "cloud" | "local";
  repoUrl?: string;
  apiKey?: string;
  resumeFrom?: CollaborationTranscript;
};

export class CollaborationLoop extends EventEmitter {
  private pythonRunner: PythonRunner;
  private cursorRunner: CursorRunner;
  private active = false;
  private cards: AgentCard[] = [];

  constructor(pythonRunner: PythonRunner, cursorRunner: CursorRunner) {
    super();
    this.pythonRunner = pythonRunner;
    this.cursorRunner = cursorRunner;
  }

  get isActive(): boolean {
    return this.active;
  }

  get agentCards(): AgentCard[] {
    return [...this.cards];
  }

  private emitCard(card: AgentCard) {
    this.emit("event", { type: "agent_card", card: { ...card } });
  }

  private upsertCard(card: AgentCard) {
    const idx = this.cards.findIndex((c) => c.id === card.id);
    if (idx >= 0) {
      this.cards[idx] = card;
    } else {
      this.cards.push(card);
    }
    this.emitCard(card);
  }

  private startCard(agent: "local" | "helper", iteration: number, agentLabel: string): AgentCard {
    const card: AgentCard = {
      id: randomUUID(),
      agent,
      agentLabel,
      iteration,
      status: "running",
      startedAt: new Date().toISOString(),
      messages: [],
    };
    this.upsertCard(card);
    return card;
  }

  private finishCard(
    card: AgentCard,
    update: Partial<Pick<AgentCard, "status" | "summary" | "outcomeType" | "outcomeText" | "messages">>,
  ) {
    this.upsertCard({
      ...card,
      ...update,
      completedAt: new Date().toISOString(),
    });
  }

  private persistTranscript(
    project: string,
    task: string,
    result: { ok: boolean; answer?: string; error?: string; iterations?: number },
  ) {
    if (!this.cards.length) return;
    try {
      saveCollaborationTranscript(project, {
        task,
        agentCards: this.cards,
        collaborationResult: {
          ok: result.ok,
          answer: result.answer,
          error: result.error,
          iterations: result.iterations,
        },
      });
    } catch {
      /* ignore persistence errors */
    }
  }

  private finishCollaboration(
    project: string,
    task: string,
    result: { ok: boolean; answer?: string; error?: string; iterations?: number },
  ) {
    this.persistTranscript(project, task, result);
    this.emit("event", { type: "collaboration_done", ...result });
    return result;
  }

  async run(options: CollaborationRunOptions): Promise<{ ok: boolean; answer?: string; error?: string }> {
    if (this.active) {
      throw new Error("Collaboration loop already running");
    }
    if (this.pythonRunner.running || this.cursorRunner.isRunning) {
      throw new Error("Another run is already in progress");
    }

    this.active = true;
    const taskText = options.resumeFrom?.task ?? options.task ?? "";
    const config = readCollaborationConfig();
    let iteration = 0;
    let testFailures = 0;
    let helperContext = "";
    let conversationContext = "";
    let resumePlan: ResumePlan | null = null;

    if (options.resumeFrom) {
      this.cards = options.resumeFrom.agentCards.map((c) => ({ ...c, historical: true }));
      for (const card of this.cards) this.emitCard(card);
      resumePlan = determineResumePlan(options.resumeFrom);
      iteration = resumePlan.iteration - 1;
      helperContext = resumePlan.helperContext;
      conversationContext = resumePlan.conversationContext;
      this.emit("event", {
        type: "collaboration_start",
        task: taskText,
        resumed: true,
        resumeAction: resumePlan.nextAction,
      });
    } else {
      this.cards = [];
      this.emit("event", { type: "collaboration_start", task: taskText });
    }

    try {
      if (resumePlan?.nextAction === "helper") {
        const helperPrompt =
          resumePlan.retryHelper && resumePlan.pendingHelperPrompt
            ? resumePlan.pendingHelperPrompt
            : buildHelperPrompt(
                config.helperPrompt,
                resumePlan.pendingHelperPrompt ?? "",
                conversationContext,
              );
        const helperResult = await this.runHelperIteration(
          options,
          config,
          resumePlan.iteration,
          helperPrompt,
          conversationContext,
        );
        if (!helperResult.ok) {
          if (isNonRetryableHelperError(helperResult.error)) {
            return this.finishCollaboration(
              options.project,
              taskText,
              abortOnHelperError(
                options.project,
                taskText,
                resumePlan.iteration,
                helperResult.error ?? "Helper agent failed (non-retryable)",
              ),
            );
          }
        }
        helperContext = helperResult.responseText;
        conversationContext = buildFullConversationContext(this.cards);
        resumePlan = null;
      }

      while (iteration < config.maxIterations) {
        iteration++;
        resumePlan = null;

        const localCard = this.startCard("local", iteration, "Local agent (Ollama)");

        const triage = await triageTask(taskText, options.project, helperContext || undefined);
        localCard.messages = [
          ...(localCard.messages ?? []),
          { role: "triage", text: `${triage.action.toUpperCase()}: ${triage.reason}`, ts: new Date().toISOString() },
        ];
        this.upsertCard(localCard);

        // --- Handoff path: expand prompt, skip UI pipeline (nothing to verify yet) ---
        if (triage.action === "handoff") {
          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "running",
            message: "Expanding task and handing off…",
          });

          const expanded = await expandPromptForHelper(taskText, options.project, {
            mode: "initial",
            previousHelperResponse: helperContext || undefined,
          });

          this.finishCard(localCard, {
            status: "done",
            summary: triage.summary,
            outcomeType: "prompt",
            outcomeText: expanded.expandedPrompt,
            messages: localCard.messages,
          });

          if (!options.apiKey) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: "CURSOR_API_KEY not set — cannot delegate to helper agent",
              iterations: iteration,
            });
          }

          const helperPrompt = buildHelperPrompt(config.helperPrompt, expanded.expandedPrompt, conversationContext);
          const cursorResult = await this.runHelperIteration(
            options,
            config,
            iteration,
            helperPrompt,
            conversationContext,
          );

          helperContext = cursorResult.responseText;
          conversationContext = buildFullConversationContext(this.cards);

          if (!cursorResult.ok) {
            if (isNonRetryableHelperError(cursorResult.error)) {
              return this.finishCollaboration(
                options.project,
                taskText,
                abortOnHelperError(
                  options.project,
                  taskText,
                  iteration,
                  cursorResult.error ?? "Helper agent failed (non-retryable)",
                ),
              );
            }
          }
          continue;
        }

        // --- Test path: run UI pipeline then verify ---
        const localTask = buildLocalTask(taskText, helperContext, iteration, conversationContext);

        const verificationRequest = extractUiVerificationRequest(helperContext);
        this.emit("event", {
          type: "phase",
          phase: "collaboration",
          status: "running",
          message: verificationRequest
            ? "Running helper's UI verification request on live app…"
            : helperContext
              ? `Verifying UI (iteration ${iteration})…`
              : `Running UI tests (iteration ${iteration})…`,
        });

        const pythonRun = await this.runPythonOnce({ ...options, task: localTask }, localCard.id);
        if (!pythonRun.ok) {
          const pipelinePayload = readRunPayload(options.project);

          localCard.messages = [
            ...(localCard.messages ?? []),
            {
              role: "pipeline",
              text: pythonRun.failedPhases.join("\n") || "Pipeline failed",
              ts: new Date().toISOString(),
            },
          ];

          if (isGitOrDeployBlockingFailure(pythonRun.failedPhases)) {
            const stopMessage = pipelineStopError(pythonRun.failedPhases, pipelinePayload);
            this.finishCard(localCard, {
              status: "failed",
              summary: pipelineFailureSummary(pythonRun.failedPhases),
              outcomeType: "answer",
              outcomeText: stopMessage,
              messages: localCard.messages,
            });
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: stopMessage,
              iterations: iteration,
            });
          }

          const expanded = await buildHelperHandoffAfterPipelineFailure(
            options.project,
            taskText,
            pythonRun.failedPhases,
            helperContext || undefined,
          );

          this.finishCard(localCard, {
            status: "done",
            summary: pipelineFailureSummary(pythonRun.failedPhases),
            outcomeType: "prompt",
            outcomeText: expanded.expandedPrompt,
            messages: localCard.messages,
          });

          if (!options.apiKey) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: "CURSOR_API_KEY not set — cannot delegate to helper agent",
              iterations: iteration,
            });
          }

          const helperPrompt = buildHelperPrompt(config.helperPrompt, expanded.expandedPrompt, conversationContext);
          const cursorResult = await this.runHelperIteration(
            options,
            config,
            iteration,
            helperPrompt,
            conversationContext,
          );

          helperContext = cursorResult.responseText;
          conversationContext = buildFullConversationContext(this.cards);

          if (!cursorResult.ok) {
            if (isNonRetryableHelperError(cursorResult.error)) {
              return this.finishCollaboration(
                options.project,
                taskText,
                abortOnHelperError(
                  options.project,
                  taskText,
                  iteration,
                  cursorResult.error ?? "Helper agent failed (non-retryable)",
                ),
              );
            }
          }
          continue;
        }

        const verification = await verifyAfterTest(
          options.project,
          taskText,
          helperContext || undefined,
        );

        if (!verification.testsPassed) {
          testFailures++;
        }

        if (verification.outcome === "answer") {
          const finalAnswer = verification.answer ?? verification.summary;
          this.finishCard(localCard, {
            status: "done",
            summary: verification.summary,
            outcomeType: "answer",
            outcomeText: finalAnswer,
            messages: localCard.messages,
          });
          return this.finishCollaboration(options.project, taskText, {
            ok: true,
            answer: finalAnswer,
            iterations: iteration,
          });
        }

        const handoffPrompt =
          verification.prompt ||
          (
            await expandPromptForHelper(taskText, options.project, {
              mode: "verification_failed",
              previousHelperResponse: helperContext || undefined,
            })
          ).expandedPrompt;

        this.finishCard(localCard, {
          status: "done",
          summary: verification.summary,
          outcomeType: "prompt",
          outcomeText: handoffPrompt,
          messages: localCard.messages,
        });

        if (!options.apiKey) {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: "CURSOR_API_KEY not set — cannot delegate to helper agent",
            iterations: iteration,
          });
        }

        if (testFailures >= config.maxTestRetries) {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: `Stopped after ${config.maxTestRetries} failed verification attempts`,
            iterations: iteration,
          });
        }

        const helperPrompt = buildHelperPrompt(config.helperPrompt, handoffPrompt, conversationContext);

        const cursorResult = await this.runHelperIteration(
          options,
          config,
          iteration,
          helperPrompt,
          conversationContext,
        );

        helperContext = cursorResult.responseText;
        conversationContext = buildFullConversationContext(this.cards);

        if (extractUiVerificationRequest(helperContext)) {
          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "running",
            message: "Helper requested UI verification — local agent will test next",
          });
        }

        if (!cursorResult.ok) {
          if (isNonRetryableHelperError(cursorResult.error)) {
            return this.finishCollaboration(
              options.project,
              taskText,
              abortOnHelperError(
                options.project,
                taskText,
                iteration,
                cursorResult.error ?? "Helper agent failed (non-retryable)",
              ),
            );
          }
        }
      }

      return this.finishCollaboration(options.project, taskText, {
        ok: false,
        error: `Max iterations (${config.maxIterations}) reached`,
        iterations: iteration,
      });
    } finally {
      this.active = false;
      this.emit("event", { type: "run_state", running: false });
    }
  }

  private async runHelperIteration(
    options: CollaborationRunOptions,
    config: ReturnType<typeof readCollaborationConfig>,
    iteration: number,
    helperPrompt: string,
    _conversationContext: string,
  ): Promise<{ ok: boolean; responseText: string; error?: string }> {
    const helperCard = this.startCard("helper", iteration, `Helper (${config.helperModel})`);
    helperCard.messages = [{ role: "user", text: helperPrompt, ts: new Date().toISOString() }];
    this.upsertCard(helperCard);

    this.emit("event", {
      type: "phase",
      phase: "cursor",
      status: "running",
      message: `Helper agent iteration ${iteration}…`,
    });

    const cursorResult = await this.runCursorOnce(
      {
        prompt: helperPrompt,
        cwd: options.project,
        runtime: options.cursorRuntime ?? "local",
        repoUrl: options.repoUrl,
        apiKey: options.apiKey!,
        modelId: config.helperModel,
      },
      helperCard,
    );

    this.finishCard(helperCard, {
      status: cursorResult.ok ? "done" : "failed",
      summary: (() => {
        if (!cursorResult.ok) return cursorResult.error ?? "Helper agent failed";
        const request = extractUiVerificationRequest(cursorResult.responseText);
        if (!request) return "Implementation complete — no UI verification request section found";
        const checks = request.split("\n").filter((line) => /^\s*[-*]\s+/.test(line)).length;
        return checks > 0
          ? `Implementation complete — ${checks} UI check(s) requested for local agent`
          : "Implementation complete — UI verification request sent to local agent";
      })(),
      outcomeType: "response",
      outcomeText: cursorResult.responseText || cursorResult.error || "(no response)",
      messages: helperCard.messages,
    });

    this.emit("event", {
      type: "phase",
      phase: "cursor",
      status: cursorResult.ok ? "done" : "failed",
      message: cursorResult.ok ? "Helper finished" : cursorResult.error ?? "Helper failed",
    });

    return cursorResult;
  }

  private runPythonOnce(
    options: RunOptions,
    cardId: string,
  ): Promise<{ ok: boolean; failedPhases: string[] }> {
    return new Promise((resolve) => {
      let resolved = false;
      const failedPhases: string[] = [];
      const finish = (ok: boolean) => {
        if (resolved) return;
        resolved = true;
        this.pythonRunner.off("event", onEvent);
        resolve({ ok, failedPhases });
      };

      this.emit("event", { type: "phases_reset" });

      const onEvent = (event: Record<string, unknown>) => {
        const tagged = { ...event, cardId };
        this.emit("event", tagged);

        if (event.type === "phase" && event.status === "failed") {
          const phase = String(event.phase ?? "phase");
          const message = String(event.message ?? "failed").trim();
          failedPhases.push(message ? `${phase}: ${message}` : phase);
        }

        if (event.type === "agent_decision") {
          const card = this.cards.find((c) => c.id === cardId);
          if (card) {
            const messages = [...(card.messages ?? [])];
            messages.push({
              role: "agent",
              text: `${String(event.action ?? "")}: ${String(event.reason ?? "")}`,
              ts: String(event.ts ?? new Date().toISOString()),
            });
            this.upsertCard({ ...card, messages });
          }
        }

        if (event.type === "done") {
          const err = String(event.error ?? "").trim();
          if (err) failedPhases.push(`error: ${err}`);
          finish(Boolean(event.overall_ok));
        }
        if (event.type === "process_exit") {
          finish(Number(event.code) === 0);
        }
      };

      this.pythonRunner.on("event", onEvent);
      try {
        this.pythonRunner.start(options);
      } catch {
        this.pythonRunner.off("event", onEvent);
        resolve({ ok: false, failedPhases: ["python: failed to start test runner"] });
      }
    });
  }

  private runCursorOnce(
    options: {
      prompt: string;
      cwd: string;
      runtime: "cloud" | "local";
      repoUrl?: string;
      apiKey: string;
      modelId: string;
    },
    card: AgentCard,
  ): Promise<{ ok: boolean; responseText: string; error?: string }> {
    return new Promise((resolve) => {
      let responseText = "";
      const onEvent = (event: Record<string, unknown>) => {
        if (event.type === "cursor_text" && event.text) {
          responseText += String(event.text);
          this.upsertCard({ ...card, outcomeText: responseText });
        }
        if (event.type === "cursor" && (event.status === "done" || event.status === "failed" || event.status === "error")) {
          this.cursorRunner.off("event", onEvent);
          resolve({
            ok: event.status === "done",
            responseText: responseText.trim(),
            error: event.status !== "done" ? String(event.message ?? event.status) : undefined,
          });
        }
      };

      this.cursorRunner.on("event", onEvent);
      void this.cursorRunner
        .run({
          prompt: options.prompt,
          cwd: options.cwd,
          runtime: options.runtime,
          repoUrl: options.repoUrl,
          apiKey: options.apiKey,
          modelId: options.modelId,
        })
        .then((result) => {
          this.cursorRunner.off("event", onEvent);
          resolve({
            ok: result.ok,
            responseText: responseText.trim() || result.error || "",
            error: result.error,
          });
        })
        .catch((err) => {
          this.cursorRunner.off("event", onEvent);
          resolve({
            ok: false,
            responseText: responseText.trim(),
            error: err instanceof Error ? err.message : String(err),
          });
        });
    });
  }
}

function isNonRetryableHelperError(error?: string): boolean {
  if (!error?.trim()) return false;
  const lower = error.toLowerCase();
  return (
    lower.includes("cannot use this model") ||
    lower.includes("invalid model") ||
    lower.includes("model not found") ||
    lower.includes("unknown model") ||
    lower.includes("not available") ||
    lower.includes("cursor_api_key") ||
    lower.includes("api key") ||
    lower.includes("authentication") ||
    lower.includes("unauthorized") ||
    lower.includes("forbidden")
  );
}

function abortOnHelperError(
  project: string,
  task: string,
  iteration: number,
  error: string,
): { ok: false; error: string; iterations: number } {
  return { ok: false, error, iterations: iteration };
}

function buildFullConversationContext(cards: AgentCard[]): string {
  return buildConversationContext(cards.filter((c) => c.status !== "running"));
}

function extractUiVerificationRequest(helperResponse: string): string | null {
  if (!helperResponse.trim()) return null;

  const patterns = [
    /#{1,3}\s*UI verification request\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n```|\s*$)/i,
    /#{1,3}\s*Verification request\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n```|\s*$)/i,
  ];

  for (const pattern of patterns) {
    const match = helperResponse.match(pattern);
    if (match?.[1]?.trim()) return match[1].trim();
  }

  return null;
}

function buildLocalTask(
  task: string,
  helperContext: string,
  iteration: number,
  conversationContext: string,
): string {
  const verificationRequest = extractUiVerificationRequest(helperContext);

  const parts = [
    "You are the local UI testing agent. Explore the live app and return an answer plus a structured report.",
    "",
    "Original user task:",
    task,
  ];

  if (verificationRequest) {
    parts.push(
      "",
      "---",
      "UI verification request from the implementation agent (run these on the live UI):",
      verificationRequest,
      "",
      "Execute each check. For each item, note pass/fail and what you observed on screen.",
      "Return a clear answer summarizing whether the implementation agent's fix worked.",
    );
  } else if (helperContext && iteration > 1) {
    parts.push(
      "",
      "---",
      "The implementation agent made changes. Verify whether the user's task is satisfied on the live page.",
      "Helper response (no structured ### UI verification request — infer checks from this):",
      helperContext.slice(0, 2000),
    );
  }

  if (conversationContext) {
    parts.push(
      "",
      "---",
      "Prior collaboration (context only):",
      conversationContext.slice(0, 4000),
    );
  }

  return parts.join("\n");
}

function buildHelperPrompt(helperContext: string, localPrompt: string, priorConversation?: string): string {
  const parts = [helperContext];

  if (priorConversation) {
    parts.push("", "---", "Prior collaboration:", priorConversation.slice(0, 3000));
  }

  parts.push("", "---", localPrompt);

  return parts.join("\n");
}

export function readLatestReport(projectPath: string): Record<string, unknown> | null {
  const reportPath = path.join(projectPath, ".agent", "current", "run-report.json");
  if (!fs.existsSync(reportPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(reportPath, "utf8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

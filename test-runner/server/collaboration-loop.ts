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
  taskRequiresImplementation,
  triageTask,
  verifyAfterTest,
  extractUiVerificationRequest,
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
import { resolveCursorRuntime } from "./run-target.js";
import { HelperStreamAggregator } from "./helper-stream.js";
import { gitWorktreeChanged, gitWorktreeSnapshot } from "./helper-git.js";

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
  /** Live process text while running; cleared when the card finishes. */
  streamStatus?: string;
  streamText?: string;
  outcomeText?: string;
  messages?: Array<{ role: string; text: string; ts?: string }>;
  historical?: boolean;
};

class RunCancelledError extends Error {
  constructor() {
    super("Run cancelled by user");
    this.name = "RunCancelledError";
  }
}

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
  private cancelled = false;
  private finished = false;
  private runProject = "";
  private runTask = "";
  private cards: AgentCard[] = [];

  constructor(pythonRunner: PythonRunner, cursorRunner: CursorRunner) {
    super();
    this.pythonRunner = pythonRunner;
    this.cursorRunner = cursorRunner;
  }

  get isActive(): boolean {
    return this.active;
  }

  /** Clear stuck active flag when child runners have already exited. */
  resetIfStale(): boolean {
    if (!this.active) return false;
    if (this.pythonRunner.running || this.cursorRunner.isRunning) return false;
    this.active = false;
    this.cancelled = false;
    this.emit("event", { type: "run_state", running: false });
    return true;
  }

  /** Abort an in-flight collaboration loop (python pipeline and/or helper agent). */
  cancel(): void {
    this.cancelled = true;
    this.pythonRunner.stop();
    this.cursorRunner.cancel();
  }

  /** Cancel and immediately emit collaboration_done for the UI. */
  forceStop(): void {
    this.cancel();
    this.cursorRunner.forceReset();
    this.failRunningCards("Cancelled");
    if (this.finished) return;
    if (this.active) {
      this.finishCollaboration(this.runProject, this.runTask, {
        ok: false,
        error: "Cancelled by user",
      });
    }
    this.active = false;
    this.emit("event", { type: "run_state", running: false });
  }

  private checkCancelled(): void {
    if (this.cancelled || this.finished) {
      throw new RunCancelledError();
    }
  }

  private failRunningCards(reason: string): void {
    for (const card of this.cards) {
      if (card.status !== "running") continue;
      this.finishCard(card, {
        status: "failed",
        summary: reason,
        outcomeType: card.outcomeType ?? "response",
        outcomeText: card.outcomeText?.trim() || card.streamText?.trim() || reason,
        streamStatus: undefined,
        streamText: undefined,
        messages: card.messages,
      });
    }
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
    update: Partial<
      Pick<AgentCard, "status" | "summary" | "outcomeType" | "outcomeText" | "streamStatus" | "streamText" | "messages">
    >,
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
    if (this.finished) return result;
    this.finished = true;
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
    this.cancelled = false;
    this.finished = false;
    const taskText = options.resumeFrom?.task ?? options.task ?? "";
    this.runProject = options.project;
    this.runTask = taskText;
    const config = readCollaborationConfig();
    let iteration = 0;
    let testFailures = 0;
    let helperContext = "";
    let helperSucceeded = false;
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
    this.emit("event", { type: "run_state", running: true });

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
          taskText,
        );
        if (!helperResult.ok && isNonRetryableHelperError(helperResult.error)) {
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
        if (helperResult.succeeded) {
          helperContext = helperResult.responseText;
          helperSucceeded = true;
        }
        conversationContext = buildFullConversationContext(this.cards);
        resumePlan = null;
      }

      while (iteration < config.maxIterations) {
        iteration++;
        resumePlan = null;
        this.checkCancelled();

        const localCard = this.startCard("local", iteration, "Local agent (Ollama)");
        this.emit("event", {
          type: "phase",
          phase: "collaboration",
          status: "running",
          message: `Local agent iteration ${iteration} — triaging task…`,
        });

        const triage = await triageTask(taskText, options.project, {
          previousHelperResponse: helperContext || undefined,
          helperSucceeded,
        });
        this.checkCancelled();
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
          this.checkCancelled();

          this.finishCard(localCard, {
            status: "done",
            summary: triage.summary,
            outcomeType: "prompt",
            outcomeText: expanded.expandedPrompt,
            messages: localCard.messages,
          });

          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "done",
            message: "Handoff prompt ready — starting helper agent",
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
            taskText,
          );

          if (!cursorResult.ok && isNonRetryableHelperError(cursorResult.error)) {
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

          if (cursorResult.succeeded) {
            helperContext = cursorResult.responseText;
            helperSucceeded = true;
            conversationContext = buildFullConversationContext(this.cards);
            continue;
          }

          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: cursorResult.error ?? "Helper agent did not implement code changes",
            iterations: iteration,
          });
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

        this.emit("event", {
          type: "phase",
          phase: "cursor",
          status: "done",
          message: "Helper finished — running deploy & UI pipeline",
        });

        const pythonRun = await this.runPythonOnce({ ...options, task: localTask }, localCard.id);
        this.checkCancelled();
        if (pythonRun.failedPhases.some((p) => /cancelled/i.test(p))) {
          throw new RunCancelledError();
        }
        if (!pythonRun.ok) {
          this.checkCancelled();
          const pipelinePayload = readRunPayload(options.project);

          localCard.messages = [
            ...(localCard.messages ?? []),
            {
              role: "pipeline",
              text: pythonRun.failedPhases.join("\n") || "Pipeline failed",
              ts: new Date().toISOString(),
            },
          ];

          if (isGitOrDeployBlockingFailure(pythonRun.failedPhases, { skipDeploy: options.skipDeploy })) {
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
            taskText,
          );

          if (!cursorResult.ok && isNonRetryableHelperError(cursorResult.error)) {
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

          if (cursorResult.succeeded) {
            helperContext = cursorResult.responseText;
            helperSucceeded = true;
          }
          conversationContext = buildFullConversationContext(this.cards);
          continue;
        }

        const verification = await verifyAfterTest(
          options.project,
          taskText,
          helperContext || undefined,
        );
        this.checkCancelled();

        if (!verification.testsPassed) {
          testFailures++;
        }

        if (verification.outcome === "answer") {
          const finalAnswer = verification.answer ?? verification.summary;
          const passed = verification.testsPassed;
          this.finishCard(localCard, {
            status: passed ? "done" : "failed",
            summary: verification.summary,
            outcomeType: "answer",
            outcomeText: finalAnswer,
            messages: localCard.messages,
          });
          return this.finishCollaboration(options.project, taskText, {
            ok: passed,
            answer: passed ? finalAnswer : undefined,
            error: passed ? undefined : finalAnswer || verification.summary,
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
          taskText,
        );

        if (!cursorResult.ok && isNonRetryableHelperError(cursorResult.error)) {
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

        if (cursorResult.succeeded) {
          helperContext = cursorResult.responseText;
          helperSucceeded = true;
        }
        conversationContext = buildFullConversationContext(this.cards);

        if (extractUiVerificationRequest(helperContext)) {
          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "running",
            message: "Helper requested UI verification — local agent will test next",
          });
        }

        if (!cursorResult.succeeded) {
          testFailures++;
          if (testFailures >= config.maxTestRetries) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: cursorResult.error ?? `Stopped after ${config.maxTestRetries} failed helper attempts`,
              iterations: iteration,
            });
          }
        }
      }

      return this.finishCollaboration(options.project, taskText, {
        ok: false,
        error: `Max iterations (${config.maxIterations}) reached`,
        iterations: iteration,
      });
    } catch (err) {
      if (err instanceof RunCancelledError || this.cancelled) {
        this.failRunningCards("Cancelled");
        if (!this.finished) {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: "Cancelled by user",
            iterations: iteration,
          });
        }
        return { ok: false, error: "Cancelled by user" };
      }
      throw err;
    } finally {
      this.active = false;
      this.cancelled = false;
      this.emit("event", { type: "run_state", running: false });
    }
  }

  private async runHelperIteration(
    options: CollaborationRunOptions,
    config: ReturnType<typeof readCollaborationConfig>,
    iteration: number,
    helperPrompt: string,
    _conversationContext: string,
    taskText: string,
  ): Promise<{
    ok: boolean;
    succeeded: boolean;
    responseText: string;
    error?: string;
    cancelled?: boolean;
  }> {
    const needsCode = taskRequiresImplementation(taskText);
    const cursorTarget = resolveCursorRuntime(options.cursorRuntime, options.repoUrl);

    const execute = async (prompt: string, labelSuffix = "") => {
      const helperCard = this.startCard(
        "helper",
        iteration,
        `Helper (${config.helperModel})${labelSuffix}`,
      );
      helperCard.messages = [{ role: "user", text: prompt, ts: new Date().toISOString() }];
      this.upsertCard(helperCard);

      if (cursorTarget.fallbackReason && !labelSuffix) {
        this.emit("event", { type: "log", message: cursorTarget.fallbackReason, level: "warn" });
        helperCard.messages = [
          ...(helperCard.messages ?? []),
          { role: "system", text: cursorTarget.fallbackReason, ts: new Date().toISOString() },
        ];
        this.upsertCard(helperCard);
      }

      this.emit("event", {
        type: "phase",
        phase: "collaboration",
        status: "done",
        message: "Handed off to helper agent",
      });
      this.emit("event", {
        type: "phase",
        phase: "cursor",
        status: "running",
        message: `Helper agent iteration ${iteration}${labelSuffix} (${cursorTarget.runtime})…`,
      });

      this.checkCancelled();
      const gitBefore = gitWorktreeSnapshot(options.project);
      const cursorResult = await this.runCursorOnce(
        {
          prompt,
          cwd: options.project,
          runtime: cursorTarget.runtime,
          repoUrl: cursorTarget.repoUrl,
          apiKey: options.apiKey!,
          modelId: config.helperModel,
        },
        helperCard,
      );

      const codeChanged = !needsCode || gitWorktreeChanged(options.project, gitBefore);
      let ok = cursorResult.ok;
      let error = cursorResult.error;

      if (ok && needsCode && !codeChanged) {
        ok = false;
        error =
          "Helper finished without editing any project files. Use file tools to implement the fix in the codebase.";
      }

      this.finishCard(helperCard, {
        status: ok ? "done" : "failed",
        streamStatus: undefined,
        streamText: undefined,
        summary: (() => {
          if (cursorResult.cancelled) return "Cancelled by user";
          if (!ok) return error ?? "Helper agent failed";
          const request = extractUiVerificationRequest(cursorResult.responseText);
          if (!request) return "Implementation complete — no UI verification request section found";
          const checks = request.split("\n").filter((line) => /^\s*[-*]\s+/.test(line)).length;
          return checks > 0
            ? `Implementation complete — ${checks} UI check(s) requested for local agent`
            : "Implementation complete — UI verification request sent to local agent";
        })(),
        outcomeType: "response",
        outcomeText: cursorResult.responseText || error || "(no response)",
        messages: helperCard.messages,
      });

      this.emit("event", {
        type: "phase",
        phase: "cursor",
        status: ok ? "done" : "failed",
        message: cursorResult.cancelled
          ? "Helper cancelled"
          : ok
            ? "Helper finished"
            : error ?? "Helper failed",
      });

      if (cursorResult.cancelled || this.cancelled) {
        throw new RunCancelledError();
      }

      return { ...cursorResult, ok, error, codeChanged };
    };

    let result = await execute(helperPrompt);
    this.checkCancelled();
    if (!result.ok && needsCode && !result.codeChanged && !result.cancelled) {
      this.checkCancelled();
      const retryPrompt = [
        helperPrompt,
        "",
        "---",
        "Your previous attempt did not modify any files. Edit the project codebase directly (Read/Write/StrReplace tools), then reply with ### Summary and ### UI verification request.",
      ].join("\n");
      result = await execute(retryPrompt, " (retry)");
    }

    const succeeded = result.ok && (!needsCode || result.codeChanged);

    return {
      ok: result.ok,
      succeeded,
      responseText: result.responseText,
      error: result.error,
      cancelled: result.cancelled,
    };
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
          if (this.cancelled) {
            failedPhases.push("cancelled by user");
            finish(false);
            return;
          }
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
  ): Promise<{ ok: boolean; responseText: string; error?: string; cancelled?: boolean }> {
    return new Promise((resolve) => {
      let resolved = false;
      let responseText = "";
      const stream = new HelperStreamAggregator();

      const publishStream = () => {
        const snap = stream.snapshot();
        this.upsertCard({
          ...card,
          streamStatus: snap.streamStatus,
          streamText: snap.streamText,
          outcomeText: undefined,
        });
      };

      const finish = (result: { ok: boolean; responseText: string; error?: string; cancelled?: boolean }) => {
        if (resolved) return;
        resolved = true;
        this.cursorRunner.off("event", onEvent);
        resolve(result);
      };

      const onEvent = (event: Record<string, unknown>) => {
        stream.push(event);
        publishStream();

        if (event.type === "cursor_text" && event.text) {
          responseText += String(event.text);
        }
        if (
          event.type === "cursor" &&
          (event.status === "done" ||
            event.status === "failed" ||
            event.status === "error" ||
            event.status === "cancelled")
        ) {
          const cancelled = event.status === "cancelled";
          finish({
            ok: event.status === "done",
            cancelled,
            responseText: responseText.trim(),
            error:
              event.status === "done"
                ? undefined
                : cancelled
                  ? "Cancelled by user"
                  : String(event.message ?? event.status),
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
          finish({
            ok: result.ok,
            cancelled: result.cancelled,
            responseText: responseText.trim() || result.error || "",
            error: result.error,
          });
        })
        .catch((err) => {
          finish({
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
    lower.includes("repourl is required") ||
    lower.includes("repo url") ||
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

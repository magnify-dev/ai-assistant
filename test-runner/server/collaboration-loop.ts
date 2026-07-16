import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import {
  buildBestEffortSummary,
  buildEscalationPrompt,
  buildHelperHandoffAfterPipelineFailure,
  buildInfoReplyHandoff,
  buildQuestionPrompt,
  buildWebResearchQuestionPrompt,
  decideHelperIntervention,
  type InterventionSituation,
  expandPromptForHelper,
  isGitOrDeployBlockingFailure,
  pipelineFailureSummary,
  pipelineStopError,
  readRunPayload,
  taskRequiresImplementation,
  triageTask,
  verifyAfterTest,
  verifyAfterWebResearch,
  webResearchFindingsText,
  extractInfoRequest,
  extractUiVerificationRequest,
  extractAnswerSection,
} from "./collaboration-eval.js";
import {
  buildConversationContext,
  buildRecentConversationContext,
  determineResumePlan,
  saveCollaborationTranscript,
  type CollaborationTranscript,
  type ResumePlan,
} from "./collaboration-transcript.js";
import { readCollaborationConfig } from "./collaboration-config.js";
import { CursorRunner } from "./cursor-agent.js";
import { classifyTaskRunKind, classifyTaskRunKindAsync } from "./task-router.js";
import { WebResearchRunner } from "./web-research-runner.js";
import { getPrompt, renderPrompt } from "./prompts.js";
import { PythonRunner, type RunOptions } from "./python-runner.js";
import { resolveCursorRuntime } from "./run-target.js";
import { preflightCursorHelper } from "./cursor-preflight.js";
import { readOllamaConfig } from "./ollama.js";
import { HelperStreamAggregator } from "./helper-stream.js";
import { gitWorktreeChanged, gitWorktreeSnapshot } from "./helper-git.js";

export type AgentCard = {
  id: string;
  agent: "local" | "helper" | "user";
  agentLabel: string;
  iteration: number;
  status: "running" | "done" | "failed";
  startedAt: string;
  completedAt?: string;
  summary?: string;
  outcomeType?: "answer" | "prompt" | "response" | "note";
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
  /** Extra context the user typed when resuming a stopped/failed run. */
  userNote?: string;
};

export class CollaborationLoop extends EventEmitter {
  private pythonRunner: PythonRunner;
  private cursorRunner: CursorRunner;
  private webResearchRunner: WebResearchRunner;
  private active = false;
  private cancelled = false;
  private finished = false;
  private runProject = "";
  private runTask = "";
  private cards: AgentCard[] = [];

  constructor(
    pythonRunner: PythonRunner,
    cursorRunner: CursorRunner,
    webResearchRunner: WebResearchRunner,
  ) {
    super();
    this.pythonRunner = pythonRunner;
    this.cursorRunner = cursorRunner;
    this.webResearchRunner = webResearchRunner;
  }

  get isActive(): boolean {
    return this.active;
  }

  /** Clear stuck active flag when child runners have already exited. */
  resetIfStale(): boolean {
    if (!this.active) return false;
    if (this.pythonRunner.running || this.webResearchRunner.running || this.cursorRunner.isRunning) return false;
    this.active = false;
    this.cancelled = false;
    this.emit("event", { type: "run_state", running: false });
    return true;
  }

  /** Abort an in-flight collaboration loop (python pipeline and/or helper agent). */
  cancel(): void {
    this.cancelled = true;
    this.pythonRunner.stop();
    this.webResearchRunner.stop();
    this.cursorRunner.cancel();
  }

  /** Cancel and immediately emit collaboration_done for the UI. */
  forceStop(): void {
    this.cancel();
    this.cursorRunner.forceReset();
    this.failRunningCards("Cancelled");
    this.emit("event", {
      type: "phase",
      phase: "cursor",
      status: "failed",
      message: "Cancelled by user",
    });
    this.emit("event", {
      type: "cursor",
      status: "cancelled",
      message: "Cancelled by user",
    });
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
    if (this.pythonRunner.running || this.cursorRunner.isRunning || this.webResearchRunner.running) {
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
    let helperFailures = 0;
    let questionRounds = 0;
    let infoRounds = 0;
    let escalated = false;
    let lastFailureNote = "";
    let helperContext = "";
    let helperSucceeded = false;
    let conversationContext = "";
    let resumePlan: ResumePlan | null = null;
    const userNote = options.userNote?.trim() ?? "";
    this.emit("event", { type: "run_state", running: true });
    this.emit("event", {
      type: "phase",
      phase: "collaboration",
      status: "running",
      message: "Classifying task…",
    });
    const taskRunKind = await classifyTaskRunKindAsync(taskText, Boolean(options.noOllama));
    this.emit("event", {
      type: "log",
      message:
        taskRunKind === "web_research"
          ? "Task classified as web research — local agent will search the web and escalate to helper if needed"
          : "Task classified as UI test — local agent will run the app pipeline and escalate to helper if needed",
      level: "info",
    });

    if (options.resumeFrom) {
      this.cards = options.resumeFrom.agentCards.map((c) => ({ ...c, historical: true }));
      for (const card of this.cards) this.emitCard(card);
      resumePlan = determineResumePlan(options.resumeFrom);
      iteration = resumePlan.iteration - 1;
      helperContext = resumePlan.helperContext;
      conversationContext = resumePlan.conversationContext;
      if (userNote) {
        const noteCard: AgentCard = {
          id: randomUUID(),
          agent: "user",
          agentLabel: "You",
          iteration: resumePlan.iteration,
          status: "done",
          startedAt: new Date().toISOString(),
          completedAt: new Date().toISOString(),
          summary: "Context added on resume",
          outcomeType: "note",
          outcomeText: userNote,
        };
        this.cards.push(noteCard);
        this.emitCard(noteCard);
        conversationContext = buildFullConversationContext(this.cards);
      }
      this.emit("event", {
        type: "collaboration_start",
        task: taskText,
        resumed: true,
        resumeAction: resumePlan.nextAction,
      });
    } else {
      this.cards = [];
    }
    // run_state already emitted before classification

    try {
      if (resumePlan?.nextAction === "helper") {
        let helperPrompt =
          resumePlan.retryHelper && resumePlan.pendingHelperPrompt
            ? resumePlan.pendingHelperPrompt
            : buildHelperPrompt(
                config.helperPrompt,
                resumePlan.pendingHelperPrompt ?? "",
                conversationContext,
                resumePlan.iteration > 1,
              );
        if (userNote && resumePlan.retryHelper) {
          helperPrompt = `${helperPrompt}\n\n---\nUser update (added while resuming):\n${userNote}`;
        }
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

        const triage = triageTask(taskText, {
          previousHelperResponse: helperContext || undefined,
          helperSucceeded,
        });
        localCard.messages = [
          ...(localCard.messages ?? []),
          { role: "triage", text: `${triage.action.toUpperCase()}: ${triage.reason}`, ts: new Date().toISOString() },
        ];
        this.upsertCard(localCard);

        // --- Test path: run local work (web research or UI pipeline) then verify ---
        const pendingInfoRequest =
          infoRounds < config.maxInfoRequests ? extractInfoRequest(helperContext) : null;

        const verificationRequest = extractUiVerificationRequest(helperContext);
        const infoNeedsWebResearch =
          Boolean(pendingInfoRequest) &&
          classifyTaskRunKind(pendingInfoRequest ?? "", Boolean(options.noOllama)) === "web_research";
        const useWebResearch =
          !verificationRequest && (taskRunKind === "web_research" || infoNeedsWebResearch);
        const localTask = buildLocalTask(
          taskText,
          helperContext,
          iteration,
          buildRecentConversationContext(this.cards.filter((c) => c.status !== "running"), 2),
          useWebResearch,
        );
        this.emit("event", {
          type: "phase",
          phase: "collaboration",
          status: "running",
          message: pendingInfoRequest
            ? useWebResearch
              ? "Researching the web for info the helper asked for…"
              : "Gathering live-app info the helper asked for…"
            : verificationRequest
              ? "Running helper's UI verification request on live app…"
              : useWebResearch
                ? `Researching the web (iteration ${iteration})…`
                : helperContext
                  ? `Verifying UI (iteration ${iteration})…`
                  : `Running UI tests (iteration ${iteration})…`,
        });

        this.emit("event", {
          type: "phase",
          phase: "cursor",
          status: "done",
          message: useWebResearch ? "Running open-web research" : "Running local UI pipeline",
        });

        let pythonRun: { ok: boolean; failedPhases: string[] } | null = null;
        let webRun: {
          ok: boolean;
          failedPhases: string[];
          result: import("./collaboration-eval.js").WebResearchEvaluationInput;
        } | null = null;

        if (useWebResearch) {
          webRun = await this.runWebResearchOnce(
            options.project,
            localTask,
            localCard.id,
            Boolean(options.noOllama),
            async (request) => {
              const guidancePrompt = [
                "## Web exploration guidance request",
                "",
                "The local browser agent is blocked and needs one concise next-step instruction.",
                "Use only the supplied page state. Do not edit files or claim to browse.",
                "Return plain guidance naming one visible element/action (use controls[].id when possible),",
                "or explain that the site should be abandoned.",
                "",
                `Original task:\n${taskText}`,
                "",
                `Browser request:\n${JSON.stringify(request, null, 2).slice(0, 12000)}`,
              ].join("\n");
              const guidance = await answerWebHelpWithOllama(guidancePrompt, Boolean(options.noOllama));
              return guidance.ok
                ? { ok: true, content: guidance.content }
                : { ok: false, error: guidance.error ?? "Local helper did not return guidance" };
            },
          );
          this.checkCancelled();
          if (webRun.failedPhases.some((p) => /cancelled/i.test(p))) {
            throw new RunCancelledError();
          }
        } else {
          pythonRun = await this.runPythonOnce({ ...options, task: localTask }, localCard.id);
          this.checkCancelled();
          if (pythonRun.failedPhases.some((p) => /cancelled/i.test(p))) {
            throw new RunCancelledError();
          }
        }

        const localWorkOk = useWebResearch ? Boolean(webRun?.ok) : Boolean(pythonRun?.ok);

        const infoFindings = useWebResearch
          ? webResearchFindingsText(webRun?.result ?? {})
          : String(readLatestReport(options.project)?.task_answer ?? "").trim();

        // --- Info round-trip: hand the local agent's findings back to the helper ---
        if (
          pendingInfoRequest &&
          (localWorkOk ||
            Boolean(infoFindings.trim()) ||
            (pythonRun &&
              !isGitOrDeployBlockingFailure(pythonRun.failedPhases, { skipDeploy: options.skipDeploy })))
        ) {
          infoRounds++;
          const findings = infoFindings;
          const failureSummary =
            localWorkOk || useWebResearch
              ? undefined
              : pythonRun?.failedPhases.join("\n");
          const infoReply = buildInfoReplyHandoff(taskText, pendingInfoRequest, findings, failureSummary);

          this.finishCard(localCard, {
            status: "done",
            summary: useWebResearch
              ? `Gathered web research for helper (round ${infoRounds}/${config.maxInfoRequests})`
              : `Gathered live-app info requested by helper (round ${infoRounds}/${config.maxInfoRequests})`,
            outcomeType: "prompt",
            outcomeText: infoReply,
            messages: localCard.messages,
          });

          const infoPrompt = buildHelperPrompt(
            config.helperPrompt,
            infoReply,
            conversationContext,
            iteration > 1,
          );
          const infoResult = await this.runHelperIteration(
            options,
            config,
            iteration,
            infoPrompt,
            conversationContext,
            taskText,
          );
          if (!infoResult.ok && isNonRetryableHelperError(infoResult.error)) {
            return this.finishCollaboration(
              options.project,
              taskText,
              abortOnHelperError(
                options.project,
                taskText,
                iteration,
                infoResult.error ?? "Helper agent failed (non-retryable)",
              ),
            );
          }
          if (infoResult.succeeded) {
            helperContext = infoResult.responseText;
            helperSucceeded = true;
          } else {
            helperFailures++;
          }
          conversationContext = buildFullConversationContext(this.cards);
          if (!infoResult.succeeded && helperFailures >= config.maxTestRetries) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: buildBestEffortSummary(
                infoResult.error ?? `Stopped after ${config.maxTestRetries} failed helper attempts`,
                conversationContext,
              ),
              iterations: iteration,
            });
          }
          continue;
        }

        if (!useWebResearch && pythonRun && !pythonRun.ok) {
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

          const pipelineGate = await this.applyInterventionGate({
            options,
            config,
            iteration,
            localCard,
            taskText,
            situation: "pipeline_failed",
            findings: pythonRun.failedPhases.join("\n") || pipelineFailureSummary(pythonRun.failedPhases),
            suggestedHandoff: expanded.expandedPrompt,
            fallbackAnswer: pipelineFailureSummary(pythonRun.failedPhases),
          });
          if (pipelineGate.kind === "answer") {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              answer: pipelineGate.text,
              error: pipelineGate.text,
              iterations: iteration,
            });
          }
          if (pipelineGate.kind === "retry") continue;
          if (!options.apiKey) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: "CURSOR_API_KEY not set — cannot delegate to helper agent",
              iterations: iteration,
            });
          }
          const helperPrompt = buildHelperPrompt(
            config.helperPrompt,
            pipelineGate.handoffPrompt,
            conversationContext,
            iteration > 1,
          );
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

        const verification = useWebResearch
          ? verifyAfterWebResearch(webRun?.result ?? {}, taskText)
          : await verifyAfterTest(options.project, taskText, helperContext || undefined);
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

        if (verification.kind === "question") {
          const fallbackAnswer = verification.answer || verification.summary;
          const suggestedHandoff =
            verification.prompt ??
            (useWebResearch
              ? buildWebResearchQuestionPrompt(taskText, fallbackAnswer)
              : buildQuestionPrompt(taskText, fallbackAnswer));
          const questionGate = await this.applyInterventionGate({
            options,
            config,
            iteration,
            localCard,
            taskText,
            situation: useWebResearch ? "web_research_insufficient" : "ui_question_unanswered",
            findings: fallbackAnswer,
            suggestedHandoff,
            fallbackAnswer,
            webStats: useWebResearch
              ? {
                  pages_fetched: webRun?.result?.pages_fetched ?? 0,
                  facts_added: webRun?.result?.facts_added ?? 0,
                  goal_met: Boolean(webRun?.result?.goal_met),
                }
              : undefined,
          });
          if (questionGate.kind === "answer") {
            return this.finishCollaboration(options.project, taskText, {
              ok: Boolean(questionGate.text.trim()),
              answer: questionGate.text || undefined,
              error: questionGate.text ? undefined : fallbackAnswer,
              iterations: iteration,
            });
          }
          if (questionGate.kind === "retry") continue;
          if (!options.apiKey || questionRounds >= config.maxQuestionRounds) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: fallbackAnswer,
              iterations: iteration,
            });
          }
          questionRounds++;
          const questionPrompt = buildHelperPrompt(
            config.helperPrompt,
            questionGate.handoffPrompt,
            conversationContext,
            iteration > 1,
          );
          const questionResult = await this.runHelperIteration(
            options,
            config,
            iteration,
            questionPrompt,
            conversationContext,
            taskText,
          );
          if (!questionResult.ok) {
            if (isNonRetryableHelperError(questionResult.error)) {
              return this.finishCollaboration(options.project, taskText, {
                ok: false,
                error: questionResult.error ?? fallbackAnswer,
                iterations: iteration,
              });
            }
            conversationContext = buildFullConversationContext(this.cards);
            if (questionRounds < config.maxQuestionRounds) continue;
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: questionResult.error ?? fallbackAnswer,
              iterations: iteration,
            });
          }
          const helperAnswer =
            extractAnswerSection(questionResult.responseText) || questionResult.responseText.trim();
          if (!helperAnswer && questionRounds < config.maxQuestionRounds) {
            conversationContext = buildFullConversationContext(this.cards);
            continue;
          }
          return this.finishCollaboration(options.project, taskText, {
            ok: Boolean(helperAnswer),
            answer: helperAnswer || undefined,
            error: helperAnswer ? undefined : fallbackAnswer,
            iterations: iteration,
          });
        }

        const failureNote = verification.failureNote ?? verification.summary;
        const failureRepeated = sameFailure(lastFailureNote, failureNote);
        lastFailureNote = failureNote;

        let handoffPrompt =
          verification.prompt ||
          (
            await expandPromptForHelper(taskText, options.project, {
              mode: "verification_failed",
              previousHelperResponse: helperContext || undefined,
            })
          ).expandedPrompt;

        if (failureRepeated && !escalated) {
          handoffPrompt = [handoffPrompt, "", "---", getPrompt("collaboration.repeat_failure_note")].join("\n");
        }
        if (testFailures >= config.maxTestRetries && !escalated) {
          escalated = true;
          handoffPrompt = buildEscalationPrompt(taskText, conversationContext, failureNote);
        }

        const verifyGate = await this.applyInterventionGate({
          options,
          config,
          iteration,
          localCard,
          taskText,
          situation: "ui_verification_failed",
          findings: failureNote,
          suggestedHandoff: handoffPrompt,
          fallbackAnswer: failureNote,
        });
        if (verifyGate.kind === "answer") {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            answer: verifyGate.text,
            error: verifyGate.text,
            iterations: iteration,
          });
        }
        if (verifyGate.kind === "retry") continue;

        if (!options.apiKey) {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: "CURSOR_API_KEY not set — cannot delegate to helper agent",
            iterations: iteration,
          });
        }

        if (testFailures > config.maxTestRetries) {
          return this.finishCollaboration(options.project, taskText, {
            ok: false,
            error: buildBestEffortSummary(
              `Stopped after ${testFailures} failed verification attempts (including a final rethink round)`,
              conversationContext,
              failureNote,
            ),
            iterations: iteration,
          });
        }

        const helperPrompt = buildHelperPrompt(
          config.helperPrompt,
          verifyGate.handoffPrompt,
          conversationContext,
          iteration > 1,
        );

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

        if (extractInfoRequest(helperContext)) {
          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "running",
            message: "Helper asked for live-app info — local agent will gather it next",
          });
        } else if (extractUiVerificationRequest(helperContext)) {
          this.emit("event", {
            type: "phase",
            phase: "collaboration",
            status: "running",
            message: "Helper requested UI verification — local agent will test next",
          });
        }

        // Helper failures count separately from failed UI verifications.
        if (!cursorResult.succeeded) {
          helperFailures++;
          if (helperFailures >= config.maxTestRetries) {
            return this.finishCollaboration(options.project, taskText, {
              ok: false,
              error: buildBestEffortSummary(
                cursorResult.error ?? `Stopped after ${config.maxTestRetries} failed helper attempts`,
                conversationContext,
                lastFailureNote || undefined,
              ),
              iterations: iteration,
            });
          }
        }
      }

      return this.finishCollaboration(options.project, taskText, {
        ok: false,
        error: buildBestEffortSummary(
          `Max iterations (${config.maxIterations}) reached without confirmed success`,
          conversationContext,
          lastFailureNote || undefined,
        ),
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

  private async applyInterventionGate(params: {
    options: CollaborationRunOptions;
    config: ReturnType<typeof readCollaborationConfig>;
    iteration: number;
    localCard: AgentCard;
    taskText: string;
    situation: InterventionSituation;
    findings: string;
    suggestedHandoff?: string;
    fallbackAnswer?: string;
    webStats?: { pages_fetched: number; facts_added: number; goal_met: boolean };
  }): Promise<
    | { kind: "answer"; text: string }
    | { kind: "retry" }
    | { kind: "escalate"; handoffPrompt: string }
  > {
    const decision = await decideHelperIntervention(
      params.taskText,
      {
        situation: params.situation,
        findings: params.findings,
        suggestedHandoff: params.suggestedHandoff,
        iteration: params.iteration,
        maxIterations: params.config.maxIterations,
        webStats: params.webStats,
      },
      Boolean(params.options.noOllama),
    );

    params.localCard.messages = [
      ...(params.localCard.messages ?? []),
      {
        role: "intervention",
        text: `${decision.action.toUpperCase()}: ${decision.reason}`,
        ts: new Date().toISOString(),
      },
    ];
    this.upsertCard(params.localCard);

    this.emit("event", {
      type: "log",
      message: `Local agent intervention decision: ${decision.action} — ${decision.reason}`,
      level: "info",
    });

    if (decision.action === "answer") {
      const text = decision.answer?.trim() || params.fallbackAnswer?.trim() || params.findings.trim();
      this.finishCard(params.localCard, {
        status: "done",
        summary: decision.reason,
        outcomeType: "answer",
        outcomeText: text,
        messages: params.localCard.messages,
      });
      return { kind: "answer", text };
    }

    if (decision.action === "retry") {
      this.finishCard(params.localCard, {
        status: "done",
        summary: decision.reason,
        outcomeType: "note",
        outcomeText: decision.reason,
        messages: params.localCard.messages,
      });
      return { kind: "retry" };
    }

    const handoffPrompt = params.suggestedHandoff?.trim() || params.findings;
    this.finishCard(params.localCard, {
      status: "done",
      summary: decision.reason,
      outcomeType: "prompt",
      outcomeText: handoffPrompt,
      messages: params.localCard.messages,
    });
    return { kind: "escalate", handoffPrompt };
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

    if (!options.apiKey?.trim()) {
      return {
        ok: false,
        succeeded: false,
        responseText: "",
        error: "CURSOR_API_KEY not set in ai-assistant/.env — helper agent cannot connect",
      };
    }
    if (cursorTarget.error) {
      return { ok: false, succeeded: false, responseText: "", error: cursorTarget.error };
    }

    const preflight = preflightCursorHelper(cursorTarget.runtime, options.apiKey, options.project);
    for (const warning of preflight.warnings) {
      this.emit("event", { type: "log", message: warning, level: "warn" });
    }
    if (!preflight.ok) {
      const message = preflight.errors.join(" ");
      this.emit("event", { type: "log", message, level: "error" });
      return { ok: false, succeeded: false, responseText: "", error: message };
    }

    const execute = async (prompt: string, labelSuffix = "") => {
      const helperCard = this.startCard(
        "helper",
        iteration,
        `Helper (${config.helperModel})${labelSuffix}`,
      );
      helperCard.messages = [{ role: "user", text: prompt, ts: new Date().toISOString() }];
      this.upsertCard(helperCard);

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
      if (this.cursorRunner.isRunning) {
        this.emit("event", {
          type: "log",
          message: "Resetting stuck Cursor runner before helper handoff",
          level: "warn",
        });
        this.cursorRunner.forceReset();
      }
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
      // A deliberate "### Info needed" reply is collaboration, not a failure —
      // the helper is asking the local agent for facts before touching code.
      const infoRequested = Boolean(extractInfoRequest(cursorResult.responseText));
      let ok = cursorResult.ok;
      let error = cursorResult.error;

      if (ok && needsCode && !codeChanged && !infoRequested) {
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
          if (infoRequested && !codeChanged) {
            return "Helper needs live-app info — local agent will gather it";
          }
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

      return { ...cursorResult, ok, error, codeChanged, infoRequested };
    };

    let result = await execute(helperPrompt);
    this.checkCancelled();
    if (!result.ok && needsCode && !result.codeChanged && !result.infoRequested && !result.cancelled) {
      this.checkCancelled();
      const retryPrompt = [helperPrompt, "", "---", getPrompt("collaboration.no_edit_retry")].join("\n");
      result = await execute(retryPrompt, " (retry)");
    }

    const succeeded = result.ok && (!needsCode || result.codeChanged || result.infoRequested);

    return {
      ok: result.ok,
      succeeded,
      responseText: result.responseText,
      error: result.error,
      cancelled: result.cancelled,
    };
  }

  private runWebResearchOnce(
    project: string,
    query: string,
    cardId: string,
    noOllama = false,
    helpResponder?: (
      request: Record<string, unknown>,
    ) => Promise<string | { ok: boolean; content?: string; error?: string }>,
  ): Promise<{
    ok: boolean;
    failedPhases: string[];
    result: import("./collaboration-eval.js").WebResearchEvaluationInput;
  }> {
    return new Promise((resolve) => {
      let resolved = false;
      const failedPhases: string[] = [];
      let result: import("./collaboration-eval.js").WebResearchEvaluationInput = {};

      const finish = (ok: boolean) => {
        if (resolved) return;
        resolved = true;
        this.webResearchRunner.off("event", onEvent);
        resolve({ ok, failedPhases, result });
      };

      this.emit("event", { type: "phases_reset" });

      const onEvent = (event: Record<string, unknown>) => {
        if (event.type === "phase" && event.status === "failed") {
          const phase = String(event.phase ?? "phase");
          const message = String(event.message ?? "failed").trim();
          failedPhases.push(message ? `${phase}: ${message}` : phase);
        }

        if (event.type === "web_research_result") {
          result = {
            query: String(event.query ?? ""),
            answer: String(event.answer ?? ""),
            pages_fetched: Number(event.pages_fetched ?? 0),
            facts_added: Number(event.facts_added ?? 0),
            goal_met: Boolean(event.goal_met),
            errors: Array.isArray(event.errors) ? event.errors.map(String) : [],
            facts: Array.isArray(event.facts)
              ? (event.facts as import("./collaboration-eval.js").WebResearchEvaluationInput["facts"])
              : [],
          };
        }

        if (event.type === "log" && event.level === "error") {
          const message = String(event.message ?? "").trim();
          if (message) failedPhases.push(message);
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
          finish(Number(event.code) === 0 && Boolean(result.answer));
        }
      };

      this.webResearchRunner.on("event", onEvent);
      try {
        this.webResearchRunner.start({ project, query, noOllama, helpResponder });
      } catch (err) {
        this.webResearchRunner.off("event", onEvent);
        resolve({
          ok: false,
          failedPhases: [err instanceof Error ? err.message : "web research failed to start"],
          result: {},
        });
      }
    });
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

      // NOTE: python events already reach the SSE stream via the PythonRunner's own
      // "event" subscription in index.ts — do not re-emit them here (it duplicated
      // every pipeline event during collaboration runs).
      const onEvent = (event: Record<string, unknown>) => {
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
        if (resolved || this.cancelled) return;
        const current = this.cards.find((c) => c.id === card.id);
        if (!current || current.status !== "running") return;
        const snap = stream.snapshot();
        this.upsertCard({
          ...current,
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
        if (resolved || this.cancelled) return;
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

async function answerWebHelpWithOllama(
  prompt: string,
  noOllama: boolean,
): Promise<{ ok: boolean; content?: string; error?: string }> {
  if (noOllama) {
    return { ok: false, error: "Ollama collaboration is disabled for this run" };
  }
  const cfg = readOllamaConfig();
  try {
    const res = await fetch(`${cfg.url.replace(/\/$/, "")}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          {
            role: "system",
            content:
              "You guide a stepwise web exploration browser. Return concise, actionable guidance grounded only " +
              "in the supplied page state. Prefer official_routes and controls[].id when present.",
          },
          { role: "user", content: prompt },
        ],
        stream: false,
      }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!res.ok) {
      return { ok: false, error: `Ollama helper failed: HTTP ${res.status}` };
    }
    const body = (await res.json()) as { message?: { content?: string }; response?: string };
    const content = String(body.message?.content ?? body.response ?? "").trim();
    if (!content) {
      return { ok: false, error: "Ollama helper returned an empty response" };
    }
    return { ok: true, content };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
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
    // Deliberately narrow: a bare "not available" also matches transient
    // "service not available" errors, which ARE worth retrying.
    lower.includes("model not available") ||
    lower.includes("model is not available") ||
    lower.includes("repourl is required") ||
    lower.includes("repo url") ||
    lower.includes("cursor_api_key") ||
    lower.includes("api key") ||
    lower.includes("authentication") ||
    lower.includes("unauthorized") ||
    lower.includes("forbidden")
  );
}

/** Loose comparison of two verification failure notes to detect a stuck loop. */
function sameFailure(a: string, b: string): boolean {
  const normalize = (text: string) =>
    text
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  const na = normalize(a);
  const nb = normalize(b);
  if (!na || !nb) return false;
  return na === nb || na.includes(nb) || nb.includes(na);
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
  recentContext: string,
  forWebResearch = false,
): string {
  const infoRequest = extractInfoRequest(helperContext);
  const verificationRequest = extractUiVerificationRequest(helperContext);
  const followUp = iteration > 1;

  if (forWebResearch) {
    return task.trim();
  }

  const parts = [
    followUp
      ? renderPrompt("collaboration.local_task.intro_compact", { task })
      : renderPrompt("collaboration.local_task.intro", { task }),
  ];

  if (followUp) {
    parts.push("", renderPrompt("collaboration.local_task.followup_note", { iteration: String(iteration) }));
  }

  if (infoRequest) {
    parts.push("", renderPrompt("collaboration.local_task.info_section", { info_request: infoRequest }));
  } else if (verificationRequest) {
    parts.push(
      "",
      renderPrompt("collaboration.local_task.verification_section", {
        verification_request: verificationRequest,
      }),
    );
  } else if (helperContext && followUp) {
    parts.push(
      "",
      renderPrompt("collaboration.local_task.inferred_section", {
        helper_context: helperContext.slice(0, 1200),
      }),
    );
  }

  const needsPriorContext = !infoRequest && !verificationRequest && followUp && recentContext.trim();
  if (needsPriorContext) {
    parts.push(
      "",
      renderPrompt("collaboration.local_task.prior_context", {
        context: recentContext.trim().slice(0, 1500),
      }),
    );
  }

  return parts.join("\n");
}

function buildHelperPrompt(
  helperSystem: string,
  localPrompt: string,
  priorConversation?: string,
  followUp = false,
): string {
  const systemPrompt = followUp ? getPrompt("collaboration.helper_system_followup") : helperSystem;
  const parts = [systemPrompt];

  if (priorConversation?.trim()) {
    const context = followUp
      ? priorConversation.trim().slice(-1500)
      : priorConversation.trim().slice(0, 3000);
    parts.push("", "---", followUp ? "Last turn:" : "Prior collaboration:", context);
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

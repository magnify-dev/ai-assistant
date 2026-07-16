import fs from "node:fs";
import path from "node:path";
import { readProjectBundle } from "./project-store.js";
import { readOllamaConfig } from "./ollama.js";
import { getPrompt, renderPrompt } from "./prompts.js";

export type LocalEvaluation = {
  outcome: "answer" | "delegate";
  /** delegate kind: "implementation" (default) needs code changes; "question" asks the helper to answer from the codebase. */
  kind?: "implementation" | "question";
  answer?: string;
  prompt?: string;
  summary: string;
  testsPassed: boolean;
  /** The concrete reason verification failed — used to detect a stuck loop (same failure repeating). */
  failureNote?: string;
};

export type TriageResult = {
  action: "test" | "handoff";
  summary: string;
  reason: string;
};

export type ExpandedHandoff = {
  expandedPrompt: string;
  summary: string;
  successCriteria: string[];
};

type CriterionResult = { criterion?: string; met?: boolean; note?: string };

const UI_CHANGE_NOUNS =
  "button|btn|modal|dialog|popup|component|page|screen|view|tab|link|menu|field|form|input|feature|section|panel|card|banner|toast|notification|icon|tooltip|drawer|sidebar|header|footer|column|row|table|list|item|widget|toggle|switch|checkbox|dropdown|select|picker|editor|toolbar|navbar|layout|style|theme|hook|testid|data-testid";

const IMPLEMENTATION_PATTERNS = [
  /\bfix(ed|es|ing)?\b/i,
  /\bshould be fixed\b/i,
  /\bneeds? to be (fixed|changed|updated|implemented|moved|built|added|created|removed)\b/i,
  /\bimplement\b/i,
  /\brefactor\b/i,
  /\bchange (the|how|where|wherever|what)\b/i,
  /\bmove .+ (to|inside|elsewhere|within|out of)\b/i,
  /\bmake .+ (the )?same\b/i,
  /\balways the same\b/i,
  /\bsame (length|height|size|width)\b/i,
  new RegExp(`\\badd (a |an |the )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\badd .+ (to|on|into|at) (the |a |an )?(home|page|screen|ui|app|navbar|header|footer|modal)\\b`, "i"),
  new RegExp(`\\bcreate (a |an |the )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\bbuild (a |an |the )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\bremove (the |a |an )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\bdelete (the |a |an )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\binsert (a |an |the )?(${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\bnew (${UI_CHANGE_NOUNS})\\b`, "i"),
  new RegExp(`\\bopen(s)? (a |the )?(modal|dialog|popup|drawer|menu|panel)\\b`, "i"),
  new RegExp(`\\bclose(s)? (the |a )?(modal|dialog|popup|drawer|menu|panel)\\b`, "i"),
  /\badd (a |the )?(missing|data-testid|testid|hook)/i,
  /\bupdate (the )?(ui|component|code|layout|page|card|screen|app|frontend)\b/i,
  /\brearrange\b/i,
  /\bdisplay .+ (elsewhere|inside|within|below|above|under)\b/i,
  /\b(enable|disable|hide|show) (the |a )?(button|feature|modal|field|section|tab|menu)\b/i,
  /\bwire up\b/i,
  /\bhook up\b/i,
  /\bstyle (the |a )?(button|page|card|modal|component|layout)\b/i,
  /\bredesign\b/i,
  /\breplace (the |a )?(button|component|page|layout|modal)\b/i,
];

const INFORMATIONAL_PATTERNS = [
  /\bhow many\b/i,
  /\bwhat (is|are|was|were)\b/i,
  /\blist (all|the|every)\b/i,
  /\bcount (the|of)\b/i,
  /\btell me (about|what)\b/i,
  /\bshow me (the|what)\b/i,
];

const WORK_REMAINING_PATTERNS = [
  /\btask is to fix\b/i,
  /\bshould be fixed\b/i,
  /\bneeds? to be fixed\b/i,
  /\bneeds? to (be )?(changed|updated|moved|implemented)\b/i,
  /\bthis should be\b/i,
  /\bso that (the|all|each|every)\b/i,
  /\bstill (needs|need|requires|has)\b/i,
  /\bnot yet (fixed|implemented|done)\b/i,
  /\bdifferent lengths?\b/i,
  /\binconsistent\b/i,
  /\bdisplays different\b/i,
];

const TASK_NOT_DONE_ANSWER_PATTERNS = [
  /\bi apologize\b/i,
  /\bi'?m sorry\b/i,
  /\bcannot complete\b/i,
  /\bcan'?t complete\b/i,
  /\bwould need access\b/i,
  /\bneed access to\b/i,
  /\bdoes not include any information\b/i,
  /\bdoes not (show|contain|include|have)\b/i,
  /\bno (mention|information|evidence|sign) of\b/i,
  /\bnot (visible|present|found|shown|displayed|on the page|in the (data|json|report))\b/i,
  /\bno (button|modal|dialog|element|feature|component)s?\b/i,
  /\bdon'?t see (a |any |the )?(button|modal|change|feature)\b/i,
  /\bdo not see (a |any |the )?(button|modal|change|feature)\b/i,
  /\bbeyond what is currently visible\b/i,
  /\bnot (yet )?(implemented|built|added|created|deployed)\b/i,
  /\bunable to (verify|confirm|complete|find)\b/i,
];

function extractJson(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    const match = trimmed.match(/\{[\s\S]*\}/);
    if (!match) return null;
    try {
      return JSON.parse(match[0]) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
}

function textImpliesImplementation(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  return IMPLEMENTATION_PATTERNS.some((p) => p.test(trimmed));
}

export function taskRequiresImplementation(taskText: string, report: Record<string, unknown> = {}): boolean {
  const requested = (report.requested as Record<string, unknown> | undefined) ?? {};
  const notes = (requested.notes_for_cursor as string[] | undefined) ?? [];
  if (notes.length > 0) return true;

  const successCriteria = (requested.success_criteria as string[] | undefined) ?? [];
  const deliverables = (requested.deliverables as string[] | undefined) ?? [];

  const combined = [
    taskText,
    String(requested.summary ?? ""),
    String(requested.source_text ?? ""),
    ...successCriteria,
    ...deliverables,
  ].join("\n");

  const hasImplementation = textImpliesImplementation(combined);
  const clearlyInformational =
    INFORMATIONAL_PATTERNS.some((p) => p.test(combined)) &&
    !textImpliesImplementation(combined);

  if (clearlyInformational) return false;
  return hasImplementation;
}

function answerImpliesTaskNotDone(text: string): boolean {
  if (!text.trim()) return false;
  return TASK_NOT_DONE_ANSWER_PATTERNS.some((p) => p.test(text));
}

function answerImpliesWorkRemaining(text: string): boolean {
  if (!text.trim()) return false;
  return WORK_REMAINING_PATTERNS.some((p) => p.test(text));
}

export function extractAnswerSection(helperResponse: string): string | null {
  if (!helperResponse.trim()) return null;
  const match = helperResponse.match(/#{1,3}\s*Answer\s*\n([\s\S]*?)(?=\n#{1,3}\s|\s*$)/i);
  return match?.[1]?.trim() || null;
}

export function extractUiVerificationRequest(helperResponse: string): string | null {
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

/**
 * The helper can ask the local agent for facts from the live app instead of guessing
 * or giving up ("### Info needed"). Returns the question block, or null.
 */
export function extractInfoRequest(helperResponse: string): string | null {
  if (!helperResponse.trim()) return null;

  const patterns = [
    /#{1,3}\s*Info(?:rmation)?\s+(?:needed|request(?:ed)?)\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n```|\s*$)/i,
    /#{1,3}\s*Need(?:ed)?\s+info(?:rmation)?\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n```|\s*$)/i,
    /#{1,3}\s*Questions?\s+for\s+the\s+local\s+agent\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n```|\s*$)/i,
  ];

  for (const pattern of patterns) {
    const match = helperResponse.match(pattern);
    if (match?.[1]?.trim()) return match[1].trim();
  }

  return null;
}

function failedCriteriaList(report: Record<string, unknown>): string[] {
  const criteriaResults = (report.criteria_results as CriterionResult[]) ?? [];
  return criteriaResults.filter((c) => c.met === false).map((c) => `${c.criterion}: ${c.note ?? "not met"}`);
}

function readReportFiles(projectPath: string): { report: Record<string, unknown>; reportMd: string } {
  const reportPath = path.join(projectPath, ".agent", "current", "run-report.json");
  const reportMdPath = path.join(projectPath, ".agent", "current", "REPORT.md");

  let report: Record<string, unknown> = {};
  if (fs.existsSync(reportPath)) {
    try {
      report = JSON.parse(fs.readFileSync(reportPath, "utf8")) as Record<string, unknown>;
    } catch {
      report = {};
    }
  }

  let reportMd = "";
  if (fs.existsSync(reportMdPath)) {
    try {
      reportMd = fs.readFileSync(reportMdPath, "utf8").slice(0, 6000);
    } catch {
      reportMd = "";
    }
  }

  return { report, reportMd };
}

export function readRunPayload(projectPath: string): Record<string, unknown> | null {
  const taskPath = path.join(projectPath, ".agent", "current", "task.json");
  if (!fs.existsSync(taskPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(taskPath, "utf8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function summarizePipelineFailures(
  failedPhases: string[],
  payload: Record<string, unknown> | null,
  reportMd: string,
): string {
  const lines: string[] = [];

  if (failedPhases.length) {
    lines.push("Failed pipeline steps:");
    for (const phase of failedPhases) lines.push(`- ${phase}`);
  }

  if (payload) {
    const git = payload.git as Record<string, unknown> | undefined;
    if (git?.push_message && git.push_attempted && !git.push_ok) {
      lines.push(`Git push failed: ${String(git.push_message)}`);
    } else if (git?.has_uncommitted) {
      lines.push(
        "Git has uncommitted changes — auto-commit was attempted but changes remain.",
      );
    }
    const autoCommit = git?.auto_commit as Record<string, unknown> | undefined;
    if (autoCommit?.attempted && autoCommit.ok === false && autoCommit.error) {
      lines.push(`Auto-commit failed: ${String(autoCommit.error)}`);
    }

    const local = payload.local_server as Record<string, unknown> | undefined;
    if (local && local.skipped === false && local.ok === false && local.message) {
      lines.push(`Local dev / build failed: ${String(local.message)}`);
    }

    const deploy = payload.deploy as { results?: Array<{ service?: string; status?: string; message?: string; ok?: boolean }> } | undefined;
    for (const item of deploy?.results ?? []) {
      if (item.ok === false) {
        lines.push(
          `Railway deploy failed (${item.service ?? "service"}): ${item.status ?? "error"}${item.message ? ` — ${item.message}` : ""}`,
        );
      }
    }

    const health = payload.health as Array<{ service?: string; ok?: boolean; message?: string }> | undefined;
    for (const item of health ?? []) {
      if (item.ok === false) {
        lines.push(`Health check failed (${item.service ?? "service"}): ${item.message ?? "not reachable"}`);
      }
    }

    const ui = payload.ui_run as Record<string, unknown> | undefined;
    if (ui?.passed === false && ui.error) {
      lines.push(`UI test blocked: ${String(ui.error)}`);
    }

    const cursorSteps = payload.cursor_steps as string[] | undefined;
    if (cursorSteps?.length) {
      lines.push("", "Suggested fixes from test run:");
      for (const step of cursorSteps) lines.push(`- ${step}`);
    }
  }

  if (lines.length) return lines.join("\n");
  if (reportMd.trim()) return reportMd.slice(0, 2500);
  return "Pipeline failed before UI verification could complete.";
}

export async function buildHelperHandoffAfterPipelineFailure(
  projectPath: string,
  taskText: string,
  failedPhases: string[],
  previousHelperResponse?: string,
): Promise<ExpandedHandoff> {
  const { report, reportMd } = readReportFiles(projectPath);
  const payload = readRunPayload(projectPath);
  const failureSummary = summarizePipelineFailures(failedPhases, payload, reportMd);

  return expandPromptForHelper(taskText, projectPath, {
    mode: "pipeline_failed",
    verificationNote: failureSummary,
    report,
    reportMd,
    previousHelperResponse,
  });
}

export function pipelineFailureSummary(failedPhases: string[]): string {
  if (!failedPhases.length) return "Build/deploy pipeline failed";
  const first = failedPhases[0] ?? "";
  if (/^git:/i.test(first) || first.toLowerCase().includes("push")) return "Git push failed — collaboration stopped";
  if (/^deploy:/i.test(first) || first.toLowerCase().includes("railway")) return "Deploy failed — collaboration stopped";
  if (/^local_server:/i.test(first) || first.toLowerCase().includes("build") || first.toLowerCase().includes("setup")) {
    return "Build/local dev failed — handing off to helper";
  }
  return "Pipeline failed — handing off to helper";
}

/** Git/deploy failures block UI verification only when deploying. */
export function isGitOrDeployBlockingFailure(
  failedPhases: string[],
  opts?: { skipDeploy?: boolean },
): boolean {
  if (opts?.skipDeploy) {
    return failedPhases.some((line) => {
      const lower = line.toLowerCase();
      if (/^local_server:/i.test(line)) return true;
      if (lower.includes("build failed") || lower.includes("setup failed")) return true;
      return false;
    });
  }
  return failedPhases.some((line) => {
    const lower = line.toLowerCase();
    if (/^git:/i.test(line)) return true;
    if (/^deploy:/i.test(line)) return true;
    if (lower.includes("cannot push")) return true;
    if (lower.includes("uncommitted changes")) return true;
    if (lower.includes("git push failed")) return true;
    if (lower.includes("railway") && (lower.includes("failed") || lower.includes("error") || lower.includes("token"))) {
      return true;
    }
    return false;
  });
}

export function pipelineStopError(failedPhases: string[], payload: Record<string, unknown> | null): string {
  const summary = summarizePipelineFailures(failedPhases, payload, "");
  const headline = pipelineFailureSummary(failedPhases);
  return `${headline}. Fix git/deploy (token, remote access, commit hooks) then re-run.\n\n${summary}`;
}

function projectContextSnippet(projectPath: string): string {
  try {
    const bundle = readProjectBundle(projectPath);
    const parts: string[] = [];
    if (bundle.profile?.name) parts.push(`Project: ${String(bundle.profile.name)}`);
    if (bundle.profile?.description) parts.push(String(bundle.profile.description));
    if (bundle.cheatsheet) {
      const lines = bundle.cheatsheet.split("\n").slice(0, 40).join("\n");
      parts.push(`Cheatsheet excerpt:\n${lines}`);
    }
    return parts.join("\n").slice(0, 2000);
  } catch {
    return "";
  }
}

function fallbackExpand(taskText: string, projectPath: string, extra?: { verificationNote?: string; reportMd?: string }): ExpandedHandoff {
  const ctx = projectContextSnippet(projectPath);
  const parts = [
    "## Task",
    taskText,
    extra?.verificationNote ? `\n## Verification failed\n${extra.verificationNote}` : "",
    extra?.reportMd ? `\n## UI test findings\n${extra.reportMd.slice(0, 2500)}` : "",
    ctx ? `\n## Project context\n${ctx}` : "",
    "",
    getPrompt("collaboration.fallback_expand_footer"),
  ].filter(Boolean);

  return {
    expandedPrompt: parts.join("\n"),
    summary: "Expanded task brief for implementation agent",
    successCriteria: [],
  };
}

export type HelperInterventionDecision = {
  action: "answer" | "retry" | "escalate";
  reason: string;
  answer?: string;
};

export type InterventionSituation =
  | "web_research_insufficient"
  | "ui_question_unanswered"
  | "ui_verification_failed"
  | "pipeline_failed";

function fallbackInterventionDecision(
  taskText: string,
  input: {
    situation: InterventionSituation;
    findings: string;
    iteration: number;
    maxIterations: number;
    webStats?: { pages_fetched: number; facts_added: number; goal_met: boolean };
  },
): HelperInterventionDecision {
  const needsImpl = taskRequiresImplementation(taskText);
  const findings = input.findings.trim();

  if (input.situation === "web_research_insufficient") {
    const pages = input.webStats?.pages_fetched ?? 0;
    const facts = input.webStats?.facts_added ?? 0;
    const goalMet = input.webStats?.goal_met ?? false;
    if (!goalMet && pages === 0 && facts === 0 && input.iteration < input.maxIterations) {
      return { action: "retry", reason: "Browser research fetched no pages — retry exploration" };
    }
    if (!goalMet && input.iteration < input.maxIterations) {
      return { action: "retry", reason: "Research goal not met — retry with different navigation" };
    }
  }

  if (!needsImpl) {
    if (findings) {
      return { action: "answer", reason: "Return local research findings", answer: findings };
    }
    if (input.iteration < input.maxIterations) {
      return { action: "retry", reason: "Retry local web research" };
    }
    return {
      action: "answer",
      reason: "Local research exhausted without helper",
      answer: findings || "Could not complete research with local tools.",
    };
  }

  if (input.situation === "pipeline_failed" || input.situation === "ui_verification_failed") {
    if (input.iteration < 2) {
      return { action: "retry", reason: "Retry local pipeline before involving helper" };
    }
    return { action: "escalate", reason: "Implementation task needs helper after local failure" };
  }

  if (findings) {
    return { action: "answer", reason: "Best-effort local answer", answer: findings };
  }
  if (input.iteration < input.maxIterations) {
    return { action: "retry", reason: "Retry local work" };
  }
  return { action: "escalate", reason: "Local attempts exhausted for implementation task" };
}

export async function decideHelperIntervention(
  taskText: string,
  input: {
    situation: InterventionSituation;
    findings: string;
    suggestedHandoff?: string;
    iteration: number;
    maxIterations: number;
    webStats?: { pages_fetched: number; facts_added: number; goal_met: boolean };
  },
  noOllama = false,
): Promise<HelperInterventionDecision> {
  if (noOllama) {
    return fallbackInterventionDecision(taskText, input);
  }

  const cfg = readOllamaConfig();
  const userContent = [
    `User task:\n${taskText}`,
    `Situation: ${input.situation}`,
    `Iteration: ${input.iteration} of ${input.maxIterations}`,
    `Requires implementation (heuristic): ${taskRequiresImplementation(taskText)}`,
    input.webStats
      ? `Web research stats: pages_fetched=${input.webStats.pages_fetched}, facts_added=${input.webStats.facts_added}, goal_met=${input.webStats.goal_met}`
      : "",
    `\nLocal findings:\n${input.findings || "(none)"}`,
    input.suggestedHandoff ? `\nSuggested handoff if escalating:\n${input.suggestedHandoff.slice(0, 2000)}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  try {
    const res = await fetch(`${cfg.url.replace(/\/$/, "")}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: "system", content: getPrompt("collaboration.intervention_decide") },
          { role: "user", content: userContent },
        ],
        stream: false,
        format: "json",
      }),
      signal: AbortSignal.timeout(60_000),
    });
    if (!res.ok) {
      return fallbackInterventionDecision(taskText, input);
    }
    const data = (await res.json()) as { message?: { content?: string } };
    const parsed = extractJson(data.message?.content ?? "");
    const action = String(parsed?.action ?? "").toLowerCase();
    if (action === "answer" || action === "retry" || action === "escalate") {
      return {
        action,
        reason: String(parsed?.reason ?? action).trim() || action,
        answer: String(parsed?.answer ?? "").trim() || undefined,
      };
    }
  } catch {
    /* fall through */
  }
  return fallbackInterventionDecision(taskText, input);
}

export function triageTask(
  taskText: string,
  opts?: { previousHelperResponse?: string; helperSucceeded?: boolean },
): TriageResult {
  const needsImpl = taskRequiresImplementation(taskText);
  const previousHelperResponse = opts?.previousHelperResponse;
  const helperSucceeded = opts?.helperSucceeded ?? false;

  if (!needsImpl) {
    return {
      action: "test",
      summary: "Explore or research to answer your question",
      reason: "Informational task — local agent works first",
    };
  }

  if (!helperSucceeded || !previousHelperResponse?.trim()) {
    return {
      action: "test",
      summary: "Run local tests before involving the helper",
      reason: "Implementation task — local agent establishes baseline first",
    };
  }

  return {
    action: "test",
    summary: "Verify the fix on the live UI",
    reason: "Helper agent finished — run UI check",
  };
}

export async function expandPromptForHelper(
  taskText: string,
  projectPath: string,
  extra?: {
    mode?: "initial" | "verification_failed" | "pipeline_failed";
    verificationNote?: string;
    report?: Record<string, unknown>;
    reportMd?: string;
    previousHelperResponse?: string;
  },
): Promise<ExpandedHandoff> {
  const ctx = projectContextSnippet(projectPath);
  const report = extra?.report ?? {};
  const requested = (report.requested as Record<string, unknown> | undefined) ?? {};
  const taskAnswer = String(report.task_answer ?? "").trim();
  const failedCriteria = failedCriteriaList(report);

  const modeLabel =
    extra?.mode === "pipeline_failed"
      ? "Re-handoff after build/deploy/pipeline failure (fix before UI can be verified)"
      : extra?.mode === "verification_failed"
        ? "Re-handoff after failed UI verification"
        : "Initial handoff (no UI test yet)";

  const userContent = [
    `User task:\n${taskText}`,
    `\nMode: ${modeLabel}`,
    ctx ? `\nProject context:\n${ctx}` : "",
    extra?.previousHelperResponse ? `\nPrevious helper response:\n${extra.previousHelperResponse.slice(0, 1500)}` : "",
    extra?.verificationNote ? `\nVerification failure:\n${extra.verificationNote}` : "",
    taskAnswer ? `\nUI observations:\n${taskAnswer}` : "",
    failedCriteria.length ? `\nFailed checks:\n${failedCriteria.join("\n")}` : "",
    (requested.success_criteria as string[] | undefined)?.length
      ? `\nStructured success criteria:\n${(requested.success_criteria as string[]).map((c) => `- ${c}`).join("\n")}`
      : "",
    extra?.reportMd ? `\nTest report:\n${extra.reportMd.slice(0, 2500)}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  const cfg = readOllamaConfig();
  try {
    const res = await fetch(`${cfg.url.replace(/\/$/, "")}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: "system", content: getPrompt("collaboration.expand") },
          { role: "user", content: userContent },
        ],
        stream: false,
        format: "json",
      }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!res.ok) {
      return fallbackExpand(taskText, projectPath, {
        verificationNote: extra?.verificationNote,
        reportMd: extra?.reportMd,
      });
    }

    const data = (await res.json()) as { message?: { content?: string } };
    const parsed = extractJson(data.message?.content ?? "");
    if (!parsed?.expanded_prompt) {
      return fallbackExpand(taskText, projectPath, {
        verificationNote: extra?.verificationNote,
        reportMd: extra?.reportMd,
      });
    }

    return {
      expandedPrompt: String(parsed.expanded_prompt),
      summary: String(parsed.summary ?? "Expanded brief ready"),
      successCriteria: Array.isArray(parsed.success_criteria)
        ? (parsed.success_criteria as unknown[]).map(String)
        : [],
    };
  } catch {
    return fallbackExpand(taskText, projectPath, {
      verificationNote: extra?.verificationNote,
      reportMd: extra?.reportMd,
    });
  }
}

async function verifyWithOllama(
  taskText: string,
  report: Record<string, unknown>,
  reportMd: string,
): Promise<{ verified: boolean; summary: string } | null> {
  const requested = (report.requested as Record<string, unknown> | undefined) ?? {};
  const successCriteria = (requested.success_criteria as string[] | undefined) ?? [];
  const taskAnswer = String(report.task_answer ?? "").trim();
  const criteriaResults = (report.criteria_results as CriterionResult[]) ?? [];

  const userContent = [
    `User task:\n${taskText}`,
    successCriteria.length ? `\nSuccess criteria:\n${successCriteria.map((c) => `- ${c}`).join("\n")}` : "",
    `\nTest run passed (technical): ${Boolean(report.overall_ok)}`,
    taskAnswer ? `\nLocal agent observations:\n${taskAnswer}` : "",
    criteriaResults.length
      ? `\nCriteria checks:\n${criteriaResults.map((c) => `- ${c.criterion}: ${c.met ? "met" : "NOT met"}${c.note ? ` (${c.note})` : ""}`).join("\n")}`
      : "",
    reportMd ? `\nReport excerpt:\n${reportMd.slice(0, 2500)}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  const cfg = readOllamaConfig();
  try {
    const res = await fetch(`${cfg.url.replace(/\/$/, "")}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: "system", content: getPrompt("collaboration.verify") },
          { role: "user", content: userContent },
        ],
        stream: false,
        format: "json",
      }),
      signal: AbortSignal.timeout(90_000),
    });
    if (!res.ok) return null;

    const data = (await res.json()) as { message?: { content?: string } };
    const parsed = extractJson(data.message?.content ?? "");
    if (!parsed) return null;

    return {
      verified: parsed.verified === true,
      summary: String(parsed.summary ?? "").trim() || (parsed.verified ? "Task verified complete" : "Task not verified"),
    };
  } catch {
    return null;
  }
}

function deterministicVerificationFailed(report: Record<string, unknown>, taskText: string): string | null {
  const failedCriteria = failedCriteriaList(report);
  if (failedCriteria.length > 0) {
    return `Unmet criteria: ${failedCriteria.join("; ")}`;
  }

  const taskAnswer = String(report.task_answer ?? "").trim();
  if (answerImpliesWorkRemaining(taskAnswer)) {
    return "Page findings still describe work remaining, not a completed fix.";
  }

  if (!Boolean(report.overall_ok)) {
    return String(report.ui_error ?? "UI test run did not pass.");
  }

  if (taskRequiresImplementation(taskText, report) && taskAnswer) {
    const lower = taskAnswer.toLowerCase();
    if (
      lower.includes("different length") ||
      lower.includes("inconsistent") ||
      lower.includes("should be fixed") ||
      answerImpliesTaskNotDone(taskAnswer)
    ) {
      return answerImpliesTaskNotDone(taskAnswer)
        ? "Local agent could not confirm the requested UI change on the live page."
        : "Observed UI still matches the pre-fix problem description.";
    }
  }

  return null;
}

/** After a UI test run — verify pass/fail only. Never used before first handoff. */
export async function verifyAfterTest(
  projectPath: string,
  taskText: string,
  previousHelperResponse?: string,
): Promise<LocalEvaluation> {
  const { report, reportMd } = readReportFiles(projectPath);
  const overallOk = Boolean(report.overall_ok);
  const taskAnswer = String(report.task_answer ?? "").trim();
  const needsImplementation = taskRequiresImplementation(taskText, report);
  const failedCriteria = failedCriteriaList(report);
  const helperRan = Boolean(previousHelperResponse?.trim());

  // Safety net: implementation task reached verify without helper (mis-triage or explore-only run)
  if (needsImplementation && !helperRan) {
    const expanded = await expandPromptForHelper(taskText, projectPath, {
      mode: "initial",
      report,
      reportMd,
    });
    return {
      outcome: "delegate",
      prompt: expanded.expandedPrompt,
      summary: "Implementation task — hand off to helper agent",
      testsPassed: false,
    };
  }

  if (!needsImplementation) {
    if (failedCriteria.length > 0) {
      return {
        outcome: "answer",
        answer: taskAnswer || failedCriteria.join("; "),
        summary: "Some checks failed",
        testsPassed: false,
      };
    }
    if (answerImpliesTaskNotDone(taskAnswer)) {
      // The live UI did not contain the answer — ask the helper, which can read the codebase.
      return {
        outcome: "delegate",
        kind: "question",
        prompt: buildQuestionPrompt(taskText, taskAnswer),
        answer: taskAnswer,
        summary: "Could not answer from visible UI — asking helper agent",
        testsPassed: false,
      };
    }
    if (taskAnswer && overallOk) {
      return { outcome: "answer", answer: taskAnswer, summary: "Task complete", testsPassed: true };
    }
    return {
      outcome: "answer",
      answer: taskAnswer || String(report.ui_error ?? "Could not retrieve information from the app."),
      summary: overallOk ? "Task complete" : "UI test failed",
      testsPassed: overallOk && Boolean(taskAnswer),
    };
  }

  if (needsImplementation && previousHelperResponse) {
    if (!extractUiVerificationRequest(previousHelperResponse)) {
      const expanded = await expandPromptForHelper(taskText, projectPath, {
        mode: "verification_failed",
        verificationNote:
          "Helper response missing a ### UI verification request section — cannot verify without code changes and explicit checks.",
        report,
        reportMd,
        previousHelperResponse,
      });
      return {
        outcome: "delegate",
        prompt: expanded.expandedPrompt,
        summary: "Helper did not provide verifiable UI checks",
        testsPassed: false,
        failureNote: "Helper response missing a ### UI verification request section",
      };
    }
  }

  const failReason = deterministicVerificationFailed(report, taskText);
  if (failReason) {
    const expanded = await expandPromptForHelper(taskText, projectPath, {
      mode: "verification_failed",
      verificationNote: failReason,
      report,
      reportMd,
      previousHelperResponse,
    });
    return {
      outcome: "delegate",
      prompt: expanded.expandedPrompt,
      summary: "UI verification failed",
      testsPassed: false,
      failureNote: failReason,
    };
  }

  const ollamaVerify = await verifyWithOllama(taskText, report, reportMd);
  if (ollamaVerify) {
    if (ollamaVerify.verified) {
      return {
        outcome: "answer",
        answer: ollamaVerify.summary || taskAnswer || "Task verified complete.",
        summary: "UI verification passed",
        testsPassed: true,
      };
    }
    const expanded = await expandPromptForHelper(taskText, projectPath, {
      mode: "verification_failed",
      verificationNote: ollamaVerify.summary,
      report,
      reportMd,
      previousHelperResponse,
    });
    return {
      outcome: "delegate",
      prompt: expanded.expandedPrompt,
      summary: "UI verification failed",
      testsPassed: false,
      failureNote: ollamaVerify.summary,
    };
  }

  if (overallOk && taskAnswer && !answerImpliesWorkRemaining(taskAnswer) && failedCriteriaList(report).length === 0) {
    return {
      outcome: "answer",
      answer: taskAnswer,
      summary: "UI verification passed",
      testsPassed: true,
    };
  }

  const expanded = await expandPromptForHelper(taskText, projectPath, {
    mode: "verification_failed",
    verificationNote: "Re-test did not clearly confirm the fix.",
    report,
    reportMd,
    previousHelperResponse,
  });
  return {
    outcome: "delegate",
    prompt: expanded.expandedPrompt,
    summary: "UI verification inconclusive",
    testsPassed: false,
    failureNote: "Re-test did not clearly confirm the fix.",
  };
}

/**
 * Build a question handoff for the helper: the local agent explored the live UI but
 * could not answer from what is visible. The helper answers from the codebase — no edits.
 */
export function buildQuestionPrompt(
  taskText: string,
  localFindings: string,
): string {
  return renderPrompt("collaboration.question_handoff", {
    task: taskText,
    findings: localFindings || "(no useful findings on the page)",
  });
}

export type WebResearchEvaluationInput = {
  query?: string;
  answer?: string;
  pages_fetched?: number;
  facts_added?: number;
  errors?: string[];
  goal_met?: boolean;
  facts?: Array<{ field?: string; value?: string; source_url?: string; quote?: string }>;
};

export function webResearchFindingsText(result: WebResearchEvaluationInput): string {
  const lines: string[] = [];
  if (result.answer?.trim()) lines.push(`Answer: ${result.answer.trim()}`);
  if (result.errors?.length) lines.push(`Errors: ${result.errors.join("; ")}`);
  for (const fact of result.facts ?? []) {
    if (fact.field && fact.value) {
      lines.push(`- ${fact.field}: ${fact.value}${fact.source_url ? ` (${fact.source_url})` : ""}`);
    }
  }
  return lines.join("\n").trim();
}

export function buildWebResearchQuestionPrompt(taskText: string, findings: string): string {
  return renderPrompt("collaboration.web_research_question_handoff", {
    task: taskText,
    findings: findings || "(web research returned no usable data)",
  });
}

/** After a web research run — decide if we can answer or need the helper. */
export function verifyAfterWebResearch(
  result: WebResearchEvaluationInput,
  taskText: string,
): LocalEvaluation {
  const answer = String(result.answer ?? "").trim();
  const findings = webResearchFindingsText(result);
  const pagesFetched = Number(result.pages_fetched ?? 0);
  const factsAdded = Number(result.facts_added ?? 0);
  const errors = result.errors ?? [];
  const goalMet = Boolean(result.goal_met);

  if (goalMet && pagesFetched > 0) {
    return {
      outcome: "answer",
      answer: answer || findings,
      summary: `Research goal met via browser exploration (${pagesFetched} page(s))`,
      testsPassed: true,
    };
  }

  const insufficient =
    !answer ||
    pagesFetched === 0 ||
    factsAdded === 0 ||
    answerImpliesTaskNotDone(answer);

  if (!insufficient) {
    return {
      outcome: "answer",
      answer,
      summary: `Web research complete (${pagesFetched} page(s), ${factsAdded} fact(s))`,
      testsPassed: true,
    };
  }

  return {
    outcome: "delegate",
    kind: "question",
    prompt: buildWebResearchQuestionPrompt(taskText, findings || answer || errors.join("; ")),
    answer: answer || errors.join("; ") || "Web research did not produce a reliable answer.",
    summary:
      pagesFetched === 0 && factsAdded === 0
        ? "Web research stalled (0 pages) — will retry"
        : "Web research insufficient — asking helper agent",
    testsPassed: false,
    failureNote:
      pagesFetched === 0 && factsAdded === 0
        ? `Web research fetched 0 pages and 0 facts (goal_met=${goalMet})`
        : undefined,
  };
}

/**
 * The helper asked for information from the live app ("### Info needed") and the
 * local agent has now gathered it. Hand the findings back so the helper can implement.
 */
export function buildInfoReplyHandoff(
  taskText: string,
  infoRequest: string,
  localFindings: string,
  pipelineFailure?: string,
): string {
  return renderPrompt("collaboration.info_reply_handoff", {
    task: taskText,
    info_request: infoRequest,
    findings:
      localFindings ||
      "(the local agent could not gather useful findings — state your best assumption and implement)",
    pipeline_note: pipelineFailure
      ? `\nNote: parts of the pipeline failed while gathering this:\n${pipelineFailure}\n`
      : "",
  });
}

/**
 * Last-resort collaboration prompt before giving up: instead of repeating the same
 * delta, give the helper the full history of attempts and ask it to rethink.
 */
export function buildEscalationPrompt(
  taskText: string,
  attemptHistory: string,
  latestFailure: string,
): string {
  return renderPrompt("collaboration.escalation", {
    task: taskText,
    attempt_history: attemptHistory || "(no structured history available)",
    latest_failure: latestFailure || "(see history above)",
  });
}

/**
 * Build a best-effort outcome message when the loop hits an iteration/retry limit,
 * so the user gets the state of the collaboration instead of a bare error.
 */
export function buildBestEffortSummary(
  reason: string,
  conversationContext: string,
  latestFailure?: string,
): string {
  const parts = [reason.trim()];
  if (latestFailure?.trim()) {
    parts.push("", `Last verification result: ${latestFailure.trim()}`);
  }
  if (conversationContext.trim()) {
    const tail = conversationContext.trim();
    parts.push(
      "",
      "What the agents did (most recent last):",
      tail.length > 3000 ? `…${tail.slice(-3000)}` : tail,
    );
  }
  return parts.join("\n");
}

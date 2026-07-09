import fs from "node:fs";
import path from "node:path";
import { readProjectBundle } from "./project-store.js";
import { readOllamaConfig } from "./ollama.js";

export type LocalEvaluation = {
  outcome: "answer" | "delegate";
  answer?: string;
  prompt?: string;
  summary: string;
  testsPassed: boolean;
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

const TRIAGE_PROMPT = `You triage a user task for a two-agent workflow (local tester + implementation agent).

Return ONLY valid JSON:
{
  "action": "test" | "handoff",
  "reason": "short explanation",
  "summary": "one line for UI"
}

Rules:
- action=test when the user wants INFORMATION from the live app (counts, lists, what's on a page, verify login works) and no code change is requested.
- action=handoff when the user wants something FIXED, CHANGED, IMPLEMENTED, or UPDATED in the codebase — especially if nothing has been implemented yet.
- action=test when an implementation agent already made changes and we need to VERIFY on the live UI.
- When helper_has_responded=false and the task requires code/UI changes, prefer handoff (skip baseline testing).`;

const EXPAND_PROMPT = `You expand a user's task into a clear, rich brief for an implementation agent.

Return ONLY valid JSON:
{
  "expanded_prompt": "markdown brief for the coder",
  "summary": "one line",
  "success_criteria": ["testable UI outcomes"]
}

The expanded_prompt should include:
- Clear restatement of user intent
- Success criteria (what "done" looks like on the page)
- Any relevant project hints provided
- Scope boundaries and acceptance checks

The implementation agent will implement changes, then send back a "### UI verification request" for you to run on the live UI.
If build, git push, deploy, or local dev setup fails, include those errors clearly — the implementation agent must fix them before UI verification can run.
Do NOT write step-by-step code instructions or name specific files unless the user explicitly mentioned them.
The implementation agent decides HOW to build it. Your job is CONTEXT and CLARITY for both coding and later UI verification.`;

const VERIFY_PROMPT = `You verify whether a UI test run shows the user's task is COMPLETE on the live page.

Return ONLY valid JSON:
{ "verified": true | false, "summary": "one line for the user" }

Rules:
- verified=true ONLY when the requested change is clearly visible/working on the page.
- verified=false when the issue still exists or findings describe work remaining.
- "The task is to fix X" in findings means NOT verified.`;

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
    "Implement the task. The local testing agent will verify on the live UI when you finish.",
  ].filter(Boolean);

  return {
    expandedPrompt: parts.join("\n"),
    summary: "Expanded task brief for implementation agent",
    successCriteria: [],
  };
}

export async function triageTask(
  taskText: string,
  projectPath: string,
  opts?: { previousHelperResponse?: string; helperSucceeded?: boolean },
): Promise<TriageResult> {
  const needsImpl = taskRequiresImplementation(taskText);
  const previousHelperResponse = opts?.previousHelperResponse;
  const helperSucceeded = opts?.helperSucceeded ?? false;

  if (!needsImpl) {
    return {
      action: "test",
      summary: "Explore the app to answer your question",
      reason: "Informational task — run UI tests",
    };
  }

  if (!helperSucceeded || !previousHelperResponse?.trim()) {
    return {
      action: "handoff",
      summary: "Expand task and hand off to implementation agent",
      reason: "Implementation task with no successful helper run yet — skip baseline testing",
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
          { role: "system", content: EXPAND_PROMPT },
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
          { role: "system", content: VERIFY_PROMPT },
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
      return {
        outcome: "answer",
        answer: taskAnswer,
        summary: "Could not answer from visible UI",
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
  };
}

/** @deprecated use triageTask + verifyAfterTest */
export async function evaluateLocalOutcome(
  projectPath: string,
  taskText: string,
  _iteration: number,
  previousHelperResponse?: string,
): Promise<LocalEvaluation> {
  return verifyAfterTest(projectPath, taskText, previousHelperResponse ?? "placeholder");
}

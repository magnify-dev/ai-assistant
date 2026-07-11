import { useMemo, useState } from "react";
import type { RunReport, StructuredTask, TestTarget } from "@/lib/projectTypes";
import type { AgentRunCard, CollaborationResult } from "@/lib/collaborationTypes";
import type { WebResearchState } from "@/lib/webResearchTypes";
import { PHASES, type PhaseKey, type PhaseMap } from "@/types";
import { CollaborationPanel } from "@/components/CollaborationPanel";
import { ExplorationPanel } from "@/components/ExplorationPanel";
import { ProgressCard, phaseToStatus, type ProgressStatus } from "@/components/ProgressCard";
import {
  collaborationPipelineKeys,
  pipelineCardStatus,
  pipelineCardSummary,
  resolveActiveRunStep,
  stepLabel,
  type RunStepKey,
} from "@/lib/runProgress";
import { cn } from "@/lib/utils";

type Props = {
  phases: PhaseMap;
  structuredTask: StructuredTask | null;
  runReport: RunReport | null;
  testTarget: TestTarget | null;
  running: boolean;
  projectPath: string;
  lastResult?: { overall_ok?: boolean } | null;
  testTargetMode: "local" | "deployed";
  skipDeploy: boolean;
  hasTask: boolean;
  agentCards?: AgentRunCard[];
  collaborationResult?: CollaborationResult | null;
  hideCollaboration?: boolean;
  webResearch?: WebResearchState | null;
};

function isExplorationMode(
  hasTask: boolean,
  runReport: RunReport | null,
  phases: PhaseMap,
): boolean {
  return runReport?.mode === "exploration" || Boolean(phases.exploration) || (hasTask && !phases.ui_test);
}

function pipelineStepKeys(
  testTargetMode: "local" | "deployed",
  skipDeploy: boolean,
  hasTask: boolean,
  runReport: RunReport | null,
  phases: PhaseMap,
): PhaseKey[] {
  const exploration = isExplorationMode(hasTask, runReport, phases);
  const keys: PhaseKey[] = ["ollama"];
  if (hasTask) keys.push("task_structure");
  keys.push("git");
  if (testTargetMode === "local") {
    keys.push("local_server");
  } else if (!skipDeploy) {
    keys.push("deploy");
  }
  keys.push("health");
  if (!exploration) keys.push("structure");
  keys.push(exploration ? "exploration" : "ui_test");
  return keys;
}

const PIPELINE_PHASE_KEYS = new Set<PhaseKey>([
  "ollama",
  "task_structure",
  "git",
  "local_server",
  "deploy",
  "health",
  "structure",
  "exploration",
  "ui_test",
  "web_research",
]);

function hasPipelinePhaseActivity(phases: PhaseMap): boolean {
  return Object.keys(phases).some((key) => PIPELINE_PHASE_KEYS.has(key as PhaseKey));
}

function renderCollaborationPipeline(
  stepKeys: RunStepKey[],
  phases: PhaseMap,
  agentCards: AgentRunCard[],
  running: boolean,
  activeKey: RunStepKey | undefined,
) {
  return stepKeys.map((key) => {
    const status = pipelineCardStatus(key, phases, agentCards, activeKey);
    const summary = pipelineCardSummary(key, phases, agentCards);
    return (
      <ProgressCard
        key={key}
        title={stepLabel(key)}
        status={status}
        summary={
          summary ||
          (status === "idle" && running && key !== activeKey ? "Pending…" : undefined)
        }
        defaultOpen={status === "failed" || status === "running"}
        highlight={key === "deploy" || key === "git" || key === "cursor" || key === "exploration" || key === "ui_test"}
      />
    );
  });
}

function renderStepCards(
  stepKeys: PhaseKey[],
  phases: PhaseMap,
  running: boolean,
  structuredTask: StructuredTask | null,
  activeKey: PhaseKey | undefined,
) {
  return stepKeys.map((key) => {
    const phase = phases[key];
    const isTask = key === "task_structure";
    const status = stepStatus(key, phase, activeKey);
    const label = phaseLabel(key);
    const summary =
      phase?.message ||
      (isTask && structuredTask?.summary ? structuredTask.summary : undefined) ||
      (status === "idle" && running && key !== activeKey ? "Pending…" : undefined);

    return (
      <ProgressCard
        key={key}
        title={label}
        status={status}
        summary={summary}
        defaultOpen={status === "failed" || (isTask && Boolean(structuredTask))}
        highlight={isTask || key === "exploration" || key === "ui_test" || key === "deploy" || key === "git"}
      >
        {isTask && structuredTask ? (
          <div className="space-y-2">
            {structuredTask.summary ? <p className="text-white/85">{structuredTask.summary}</p> : null}
            {structuredTask.source_text ? (
              <p className="whitespace-pre-wrap text-white/65">{structuredTask.source_text}</p>
            ) : null}
            {(structuredTask.success_criteria?.length ?? 0) > 0 ? (
              <ul className="list-disc space-y-1 pl-4 text-white/70">
                {structuredTask.success_criteria!.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : null}
            {(structuredTask.intent_gaps?.length ?? 0) > 0 ? (
              <ul className="list-disc space-y-1 pl-4 text-amber-200/90">
                {structuredTask.intent_gaps!.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : undefined}
      </ProgressCard>
    );
  });
}

function extractAnswerSection(report: string): string {
  const match = report.match(/(?:^|\n)## Answer\s*\r?\n\r?\n([\s\S]*?)(?=\r?\n## |\r?\n# |$)/);
  return match?.[1]?.trim() ?? "";
}

function plainAnswer(text: string): string {
  return text.replace(/\*\*/g, "").trim();
}

function resolveTaskAnswer(runReport: RunReport | null): string {
  if (!runReport) return "";
  const direct = runReport.task_answer?.trim();
  if (direct) return direct;
  if (runReport.page_report) return extractAnswerSection(runReport.page_report);
  return "";
}

function stepStatus(
  key: PhaseKey,
  phase: PhaseMap[string] | undefined,
  activeKey: PhaseKey | undefined,
): ProgressStatus {
  if (key === activeKey) return "running";
  if (phase?.status === "skipped") return "done";
  if (phase?.status) return phaseToStatus(phase);
  return "idle";
}

function phaseLabel(key: PhaseKey): string {
  return PHASES.find((p) => p.key === key)?.label ?? key;
}

export function RunProgressPanel({
  phases,
  structuredTask,
  runReport,
  testTarget,
  running,
  projectPath,
  lastResult,
  testTargetMode,
  skipDeploy,
  hasTask,
  agentCards = [],
  collaborationResult = null,
  hideCollaboration = false,
  webResearch = null,
}: Props) {
  const [inspectExploration, setInspectExploration] = useState(false);

  const stepKeys = useMemo(
    () => pipelineStepKeys(testTargetMode, skipDeploy, hasTask, runReport, phases),
    [testTargetMode, skipDeploy, hasTask, runReport, phases],
  );

  const taskAnswer = useMemo(() => resolveTaskAnswer(runReport), [runReport]);
  const taskAnswerPlain = plainAnswer(taskAnswer);

  const exploration = isExplorationMode(hasTask, runReport, phases);
  const webResearchActive =
    Boolean(phases.web_research) ||
    Boolean(webResearch?.answer) ||
    Boolean(webResearch?.controller || webResearch?.currentUrl);

  const activeStep = useMemo(
    () => resolveActiveRunStep(phases, agentCards, running),
    [phases, agentCards, running],
  );
  const activeKey = activeStep?.key;

  const collabPipelineKeys = useMemo(
    () => collaborationPipelineKeys(testTargetMode, skipDeploy, exploration),
    [testTargetMode, skipDeploy, exploration],
  );

  const fullPipelineActiveKey = useMemo(() => {
    for (const key of stepKeys) {
      if (phases[key]?.status === "running") return key;
    }
    return undefined;
  }, [stepKeys, phases]);

  const showCursor = agentCards.length === 0;
  const showCollaboration = agentCards.length > 0 || Boolean(collaborationResult);
  const pipelineActive = running || lastResult !== null || hasPipelinePhaseActivity(phases);
  const showFullPipeline = pipelineActive && !showCollaboration;
  const showDeployPipeline =
    showCollaboration &&
    (running || hasPipelinePhaseActivity(phases) || Boolean(runReport) || Boolean(testTarget?.url));

  const overallStatus: ProgressStatus = running
    ? "running"
    : lastResult?.overall_ok
      ? "done"
      : lastResult
        ? "failed"
        : "idle";

  const siteChanges = runReport?.site_map_changes as
    | { new_pages?: string[]; updated_pages?: { path: string; new_elements: number }[]; new_elements?: number }
    | undefined;
  const cheatChanges = runReport?.cheatsheet_changes as
    | { added_learnings?: { insight?: string }[]; added_notes?: string[] }
    | undefined;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/50">Progress</h2>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[10px] font-medium",
            overallStatus === "running" && "bg-sky-500/20 text-sky-200",
            overallStatus === "done" && "bg-green-500/20 text-green-200",
            overallStatus === "failed" && "bg-red-500/20 text-red-200",
            overallStatus === "idle" && "bg-white/10 text-white/50",
          )}
        >
          {running ? "running" : lastResult?.overall_ok ? "pass" : lastResult ? "fail" : "…"}
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {showCollaboration && !hideCollaboration ? (
          <CollaborationPanel agentCards={agentCards} collaborationResult={collaborationResult} running={running} compact />
        ) : null}

        {testTarget?.url ? (
          <ProgressCard title="Target" status="done" summary={`${testTarget.source}: ${testTarget.url}`} />
        ) : null}

        {showDeployPipeline ? (
          <>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-white/40">Pipeline</p>
            {renderCollaborationPipeline(collabPipelineKeys, phases, agentCards, running, activeKey)}
          </>
        ) : null}

        {showFullPipeline ? renderStepCards(stepKeys, phases, running, structuredTask, fullPipelineActiveKey) : null}

        {webResearchActive ? (
          <ProgressCard
            title="Web research"
            status={
              phases.web_research?.status === "failed"
                ? "failed"
                : webResearch?.answer
                  ? "done"
                  : running
                    ? "running"
                    : "idle"
            }
            summary={
              webResearch?.progress?.step
                ? `${webResearch.progress.step}${webResearch.progress.url ? `: ${webResearch.progress.url}` : ""}${
                    webResearch.progress.index && webResearch.progress.total
                      ? ` (${webResearch.progress.index}/${webResearch.progress.total})`
                      : ""
                  }`
                : webResearch?.decision?.action
                  ? `${webResearch.decision.action}${webResearch.decision.target ? `: ${webResearch.decision.target}` : ""}`
                  : webResearch?.controller?.status || webResearch?.controller?.phase
                    ? String(webResearch.controller.status ?? webResearch.controller.phase)
                : phases.web_research?.message ||
                  (webResearch?.pages_fetched
                    ? `${webResearch.pages_fetched} page(s), ${webResearch.facts_added ?? 0} fact(s)`
                    : running
                      ? "Searching and extracting…"
                      : undefined)
            }
            defaultOpen
            highlight
          >
            {webResearch?.progress?.message ? (
              <p className="text-xs text-white/60">{webResearch.progress.message}</p>
            ) : null}
            {(webResearch?.indexPages?.length ?? 0) > 0 ? (
              <section className="mt-2">
                <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Pages indexed</p>
                <ul className="max-h-32 space-y-0.5 overflow-y-auto text-xs text-white/70">
                  {webResearch!.indexPages!.map((page, index) => (
                    <li key={`${page.url}-${index}`} className="truncate">
                      {page.title ? `${page.title} — ` : ""}
                      <span className="font-mono text-white/50">{page.url}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {webResearch?.answer ? (
              <p className="mt-2 text-sm leading-relaxed text-sky-100/95">{webResearch.answer}</p>
            ) : null}
            {((webResearch?.liveFacts?.length ?? 0) > 0 || (webResearch?.facts?.length ?? 0) > 0) ? (
              <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-xs text-white/75">
                {(webResearch!.liveFacts ?? webResearch!.facts)!.slice(0, 12).map((fact, index) => (
                  <li key={index}>
                    <span className="font-medium text-white/85">{fact.field}:</span> {fact.value}
                  </li>
                ))}
              </ul>
            ) : null}
            {(webResearch?.errors?.length ?? 0) > 0 ? (
              <ul className="mt-2 space-y-1 text-xs text-amber-200/80">
                {webResearch!.errors!.map((err, index) => (
                  <li key={index}>{err}</li>
                ))}
              </ul>
            ) : null}
          </ProgressCard>
        ) : null}

        <ProgressCard
          title="Report"
          status={runReport ? (runReport.overall_ok ? "done" : "failed") : running ? "running" : "idle"}
          summary={
            runReport
              ? runReport.overall_ok
                ? "All criteria met"
                : runReport.ui_error || "Some criteria failed"
              : running
                ? "Building…"
                : "Waiting for run"
          }
          defaultOpen={Boolean(runReport) && !running}
          highlight
        >
          {runReport ? (
              <div className="space-y-3">
                {(runReport.page_findings?.accounts?.length ?? 0) > 0 ? (
                  <section>
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Accounts on page</p>
                    <ul className="space-y-1">
                      {runReport.page_findings!.accounts!.map((acc) => (
                        <li
                          key={`${acc.platform}-${acc.name}`}
                          className="rounded border border-white/10 px-2 py-1 text-white/80"
                        >
                          <span className="font-medium">{acc.name}</span>
                          {acc.platform ? <span className="text-white/50"> ({acc.platform})</span> : null}
                          {acc.status ? <span className="text-white/45"> — {acc.status}</span> : null}
                          {acc.email ? <div className="font-mono text-[10px] text-white/45">{acc.email}</div> : null}
                          {acc.no_login ? <div className="text-[10px] text-white/45">No login stored</div> : null}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : runReport.page_findings?.empty_message ? (
                  <section>
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Accounts on page</p>
                    <p className="text-white/65">{runReport.page_findings.empty_message}</p>
                  </section>
                ) : null}

                {runReport.page_report ? (
                  <section>
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Page report</p>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-white/10 bg-black/30 p-2 font-mono text-[10px] leading-relaxed text-white/75">
                      {runReport.page_report}
                    </pre>
                  </section>
                ) : null}

                <section>
                  <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Executed</p>
                  <ul className="space-y-1">
                    {runReport.executed.slice(0, 12).map((step, i) => (
                      <li
                        key={`${step.action}-${i}`}
                        className={cn(
                          "rounded border px-2 py-1 font-mono text-[10px]",
                          step.ok ? "border-green-500/20 text-green-200/90" : "border-red-500/20 text-red-200/90",
                        )}
                      >
                        {step.ok ? "✓" : "✗"} {step.action} {step.target}
                      </li>
                    ))}
                  </ul>
                </section>

                <section>
                  <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Criteria</p>
                  <ul className="space-y-1">
                    {runReport.criteria_results.map((c) => (
                      <li
                        key={c.criterion}
                        className={cn(
                          "rounded border px-2 py-1",
                          c.met ? "border-green-500/20 text-green-200/90" : "border-red-500/20 text-red-200/90",
                        )}
                      >
                        {c.met ? "✓" : "✗"} {c.criterion}
                        {c.note ? <span className="text-white/45"> — {c.note}</span> : null}
                      </li>
                    ))}
                  </ul>
                </section>

                {siteChanges &&
                ((siteChanges.new_pages?.length ?? 0) > 0 || (siteChanges.updated_pages?.length ?? 0) > 0) ? (
                  <section>
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Site map changes</p>
                    {(siteChanges.new_pages?.length ?? 0) > 0 ? (
                      <p className="text-white/70">New pages: {siteChanges.new_pages!.join(", ")}</p>
                    ) : null}
                    {(siteChanges.updated_pages?.length ?? 0) > 0 ? (
                      <ul className="mt-1 list-disc pl-4 text-white/65">
                        {siteChanges.updated_pages!.map((u) => (
                          <li key={u.path}>
                            {u.path}: +{u.new_elements} element(s)
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </section>
                ) : null}

                {cheatChanges &&
                ((cheatChanges.added_learnings?.length ?? 0) > 0 || (cheatChanges.added_notes?.length ?? 0) > 0) ? (
                  <section>
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Cheatsheet changes</p>
                    {(cheatChanges.added_learnings?.length ?? 0) > 0 ? (
                      <ul className="list-disc space-y-1 pl-4 text-violet-200/90">
                        {cheatChanges.added_learnings!.map((e, i) => (
                          <li key={i}>+ {e.insight}</li>
                        ))}
                      </ul>
                    ) : null}
                    {(cheatChanges.added_notes?.length ?? 0) > 0 ? (
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-violet-200/80">
                        {cheatChanges.added_notes!.map((n) => (
                          <li key={n}>+ {n}</li>
                        ))}
                      </ul>
                    ) : null}
                  </section>
                ) : null}

                {runReport.final_url ? (
                  <p className="font-mono text-[10px] text-white/45">Final: {runReport.final_url}</p>
                ) : null}
              </div>
            ) : null}
        </ProgressCard>

        {taskAnswerPlain && !webResearch?.answer ? (
          <ProgressCard
            title="Answer"
            status={runReport?.overall_ok ? "done" : running ? "running" : "idle"}
            summary={taskAnswerPlain}
            defaultOpen
            highlight
          >
            <p className="text-sm leading-relaxed text-green-100/95">{taskAnswerPlain}</p>
          </ProgressCard>
        ) : null}

        {showCursor ? (
          <ProgressCard
            title="Cursor agent"
            status={phaseToStatus(phases.cursor)}
            summary={phases.cursor?.message || (running ? "After local agent…" : undefined)}
          />
        ) : null}
      </div>

      <div className="flex shrink-0 gap-2 border-t border-white/10 pt-3">
        <button
          type="button"
          onClick={() => setInspectExploration((value) => !value)}
          className={cn(
            "flex-1 rounded-md border px-2 py-1.5 text-xs",
            inspectExploration ? "border-emerald-500/40 bg-emerald-950/30 text-emerald-100" : "border-white/15 text-white/70",
          )}
        >
          Exploration map
        </button>
      </div>

      {inspectExploration ? (
        <div className="max-h-64 shrink-0 overflow-y-auto rounded-md border border-white/10 bg-black/30 p-2">
          <ExplorationPanel projectPath={projectPath} compact />
        </div>
      ) : null}
    </div>
  );
}

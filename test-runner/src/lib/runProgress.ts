import type { AgentRunCard } from "@/lib/collaborationTypes";
import { PHASES, type PhaseKey, type PhaseMap } from "@/types";
import type { ProgressStatus } from "@/components/ProgressCard";

export type RunStepKey = PhaseKey | "collaboration" | "cursor" | "local" | "helper" | "starting";

const EXTRA_LABELS: Record<string, string> = {
  collaboration: "Local agent",
  cursor: "Helper agent",
  helper: "Helper agent",
  local: "Local agent",
  starting: "Run",
};

export function stepLabel(key: RunStepKey): string {
  return EXTRA_LABELS[key] ?? PHASES.find((p) => p.key === key)?.label ?? key;
}

/** Collaboration verify pipeline: agent steps first, then deploy chain. */
export function collaborationPipelineKeys(
  testTargetMode: "local" | "deployed",
  skipDeploy: boolean,
  exploration: boolean,
): RunStepKey[] {
  const keys: RunStepKey[] = ["collaboration", "cursor", "git"];
  if (testTargetMode === "local") keys.push("local_server");
  else if (!skipDeploy) keys.push("deploy");
  keys.push("health");
  keys.push(exploration ? "exploration" : "ui_test");
  return keys;
}

export function stripPipelineKeys(
  testTargetMode: "local" | "deployed",
  skipDeploy: boolean,
  hasHelper: boolean,
): RunStepKey[] {
  const keys: RunStepKey[] = [];
  if (hasHelper) keys.push("cursor");
  keys.push("git");
  if (testTargetMode === "local") keys.push("local_server");
  else if (!skipDeploy) keys.push("deploy");
  keys.push("health", "exploration");
  return keys;
}

function helperCardState(agentCards: AgentRunCard[]) {
  return agentCards.find((c) => c.agent === "helper");
}

function localCardState(agentCards: AgentRunCard[]) {
  return agentCards.find((c) => c.agent === "local" && c.status === "running");
}

/** Exactly one active step for the status bar — never two at once. */
export function resolveActiveRunStep(
  phases: PhaseMap,
  agentCards: AgentRunCard[],
  running: boolean,
): { key: RunStepKey; label: string; message: string } | null {
  if (!running) return null;

  const helper = helperCardState(agentCards);
  if (helper?.status === "running" || phases.cursor?.status === "running") {
    const draft = helper?.streamText?.trim();
    const preview = draft
      ? draft.length > 120
        ? `${draft.slice(-120).trim()}…`
        : draft
      : undefined;
    return {
      key: "helper",
      label: "Helper agent",
      message:
        helper?.streamStatus?.trim() ||
        phases.cursor?.message?.trim() ||
        preview ||
        "Implementing changes…",
    };
  }

  if (phases.collaboration?.status === "running") {
    return {
      key: "collaboration",
      label: "Local agent",
      message: phases.collaboration.message?.trim() || "Working…",
    };
  }

  const pipelineOrder: PhaseKey[] = [
    "ollama",
    "task_structure",
    "git",
    "local_server",
    "deploy",
    "health",
    "structure",
    "exploration",
    "ui_test",
  ];
  for (const key of pipelineOrder) {
    if (phases[key]?.status === "running") {
      return {
        key,
        label: stepLabel(key),
        message: phases[key]?.message?.trim() || "In progress…",
      };
    }
  }

  const local = localCardState(agentCards);
  if (local) {
    return {
      key: "local",
      label: "Local agent",
      message: local.summary?.trim() || "Working…",
    };
  }

  return { key: "starting", label: "Run", message: "Starting…" };
}

export function phaseEntryStatus(status?: string): ProgressStatus {
  if (status === "running") return "running";
  if (status === "skipped") return "done";
  if (status === "done") return "done";
  if (status === "failed") return "failed";
  if (status === "warning") return "warning";
  return "idle";
}

/** Strip dot: only the active step may show running. */
export function stripStepStatus(
  key: RunStepKey,
  phases: PhaseMap,
  agentCards: AgentRunCard[],
  activeKey: RunStepKey | undefined,
): ProgressStatus {
  if (key === activeKey) return "running";

  if (key === "cursor" || key === "helper") {
    const helper = helperCardState(agentCards);
    if (helper?.status === "failed") return "failed";
    if (helper?.status === "done") return "done";
    const cursor = phases.cursor;
    if (cursor?.status === "failed") return "failed";
    if (cursor?.status === "skipped" || cursor?.status === "done") return "done";
    return "idle";
  }

  if (key === "collaboration" || key === "local") {
    const collab = phases.collaboration;
    if (collab?.status === "failed") return "failed";
    if (collab?.status === "skipped" || collab?.status === "done") return "done";
    const local = agentCards.find((c) => c.agent === "local");
    if (local?.status === "running" && key === "local") return activeKey === "local" ? "running" : "idle";
    if (local?.status === "failed") return "failed";
    if (local?.status === "done") return "done";
    return "idle";
  }

  return phaseEntryStatus(phases[key]?.status);
}

export function pipelineCardStatus(
  key: RunStepKey,
  phases: PhaseMap,
  agentCards: AgentRunCard[],
  activeKey: RunStepKey | undefined,
): ProgressStatus {
  if (key === activeKey) return "running";
  return stripStepStatus(key, phases, agentCards, undefined);
}

export function pipelineCardSummary(
  key: RunStepKey,
  phases: PhaseMap,
  agentCards: AgentRunCard[],
): string | undefined {
  if (key === "cursor" || key === "helper") {
    const helper = helperCardState(agentCards);
    return (
      helper?.streamStatus?.trim() ||
      phases.cursor?.message?.trim() ||
      helper?.summary?.trim() ||
      undefined
    );
  }
  if (key === "collaboration") {
    const local = agentCards.find((c) => c.agent === "local" && c.status === "running");
    return local?.summary?.trim() || phases.collaboration?.message?.trim() || undefined;
  }
  const phase = phases[key as PhaseKey];
  return phase?.message?.trim() || undefined;
}

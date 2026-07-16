import type { WebCaptureBuildStatus } from "@/lib/webCaptureTypes";
import type { WebResearchState } from "@/lib/webResearchTypes";

export type WebResearchWaitTone = "running" | "acting" | "waiting" | "failed" | "complete" | "blocked";

export type WebResearchWaitState = {
  label: string;
  message: string;
  tone: WebResearchWaitTone;
  url?: string;
  action?: string;
  targetId?: string;
  targetLabel?: string;
  step?: number;
  maxSteps?: number;
};

const BUILDING_PHASES = new Set(["geometry", "locators", "analyzing", "visual"]);

function text(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function targetLabelFromDecision(state: WebResearchState): string {
  const target = text(state.decision?.target);
  if (target) return target;
  const action = text(state.decision?.action);
  const reason = text(state.decision?.reason);
  if (action && reason) return `${action} — ${reason.slice(0, 80)}`;
  return action;
}

function lastStep(state: WebResearchState) {
  const steps = state.steps ?? [];
  return steps.length ? steps[steps.length - 1] : undefined;
}

function isRunFinished(state: WebResearchState): boolean {
  if (state.runFinished) return true;
  if (state.answer) return true;
  const status = text(state.controller?.status ?? state.progress?.step);
  return ["complete", "incomplete", "blocked"].includes(status);
}

export function resolveWebResearchWaitState(
  state: WebResearchState | null | undefined,
  captureBuild?: WebCaptureBuildStatus | null,
  running = false,
): WebResearchWaitState | null {
  if (!state && !running) return null;

  const url = text(state?.currentUrl ?? state?.snapshot?.url ?? state?.progress?.url);
  const controllerStatus = text(state?.controller?.status ?? state?.controller?.phase);
  const controllerReason = text(state?.controller?.reason ?? state?.progress?.message);
  const step = typeof state?.controller?.step === "number" ? state.controller.step : undefined;
  const maxSteps =
    typeof state?.controller?.max_steps === "number" ? state.controller.max_steps : undefined;
  const stepPrefix =
    step && maxSteps ? `Step ${step}/${maxSteps}` : step ? `Step ${step}` : undefined;

  if (state && isRunFinished(state)) {
    const last = lastStep(state);
    const failed = last && (last.ok === false || last.progress === false);
    return {
      label: failed ? "Run finished with issues" : "Run complete",
      message: text(state.answer) || controllerReason || text(last?.error) || "Exploration finished.",
      tone: failed ? "failed" : "complete",
      url,
      step,
      maxSteps,
    };
  }

  const capturePhase = captureBuild?.phase;
  if (capturePhase && BUILDING_PHASES.has(capturePhase)) {
    return {
      label: "Building page map",
      message: text(captureBuild?.message) || `Analyzing controls (${capturePhase})…`,
      tone: "waiting",
      url: text(captureBuild?.url) || url,
      step,
      maxSteps,
    };
  }

  const recent = lastStep(state ?? {});
  if (controllerStatus === "blocked") {
    return {
      label: "Blocked",
      message: controllerReason || "Too many failed actions on this page — try a different route.",
      tone: "blocked",
      url,
      step,
      maxSteps,
    };
  }

  if (controllerStatus === "waiting_for_helper") {
    return {
      label: "Waiting for helper",
      message: controllerReason || "Asking the helper agent for guidance…",
      tone: "waiting",
      url,
      step,
      maxSteps,
    };
  }

  if (controllerStatus === "acting") {
    const action = text(state?.controller?.action ?? state?.decision?.action);
    const targetId = text(state?.controller?.target_id ?? state?.decision?.target);
    const targetLabel = text(state?.controller?.target_label) || targetLabelFromDecision(state ?? {});
    const detail = targetLabel || targetId || text(state?.controller?.url);
    return {
      label: "Executing action",
      message: controllerReason || `${action}${detail ? ` → ${detail}` : ""}`,
      tone: "acting",
      url,
      action,
      targetId,
      targetLabel: detail,
      step,
      maxSteps,
    };
  }

  if (controllerStatus === "observing") {
    return {
      label: "Reading page",
      message: controllerReason || "Capturing page state and controls…",
      tone: "waiting",
      url,
      step,
      maxSteps,
    };
  }

  if (controllerStatus === "deciding") {
    return {
      label: "Waiting for AI decision",
      message: controllerReason || "Local model is choosing the next action…",
      tone: "waiting",
      url,
      step,
      maxSteps,
    };
  }

  if (controllerStatus === "extracting") {
    return {
      label: "Extracting content",
      message: controllerReason || "Collecting evidence from the page…",
      tone: "running",
      url,
      step,
      maxSteps,
    };
  }

  if (state?.decision && running) {
    const action = text(state.decision.action);
    const target = targetLabelFromDecision(state);
    return {
      label: "Preparing action",
      message: `${action}${target ? ` → ${target}` : ""}`,
      tone: "acting",
      url,
      action,
      targetId: text(state.decision.target),
      targetLabel: target,
      step,
      maxSteps,
    };
  }

  if (recent && (recent.ok === false || recent.progress === false)) {
    const err = text(recent.error ?? recent.message);
    return {
      label: "Last action failed",
      message: err || `${text(recent.action)} on ${text(recent.target_id)} had no effect`,
      tone: "failed",
      url,
      action: text(recent.action),
      targetId: text(recent.target_id),
      step,
      maxSteps,
    };
  }

  if (running || state) {
    return {
      label: stepPrefix ? "Web exploration" : "Web exploration",
      message:
        controllerReason ||
        text(state?.progress?.message) ||
        (url ? "Starting browser session…" : "Waiting for browser…"),
      tone: "running",
      url,
      step,
      maxSteps,
    };
  }

  return null;
}

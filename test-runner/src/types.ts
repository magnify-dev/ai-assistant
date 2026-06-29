export type PhaseStatus = "idle" | "running" | "done" | "failed";

export type PhaseKey =
  | "task_structure"
  | "git"
  | "deploy"
  | "health"
  | "structure"
  | "ui_test"
  | "cursor";

export const PHASES: { key: PhaseKey; label: string; group: "local" | "cursor" }[] = [
  { key: "task_structure", label: "Task", group: "local" },
  { key: "git", label: "Git", group: "local" },
  { key: "deploy", label: "Deploy", group: "local" },
  { key: "health", label: "Health", group: "local" },
  { key: "structure", label: "Structure", group: "local" },
  { key: "ui_test", label: "UI test", group: "local" },
  { key: "cursor", label: "Cursor agent", group: "cursor" },
];

export type RunEvent = {
  type: string;
  ts?: string;
  phase?: string;
  status?: string;
  message?: string;
  level?: string;
  mode?: string;
  action?: string;
  target?: string;
  ok?: boolean;
  text?: string;
  role?: string;
  agentId?: string;
  runId?: string;
  cursorUiHint?: string;
};

export type PhaseMap = Record<string, { status?: string; message?: string }>;

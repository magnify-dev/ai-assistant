export type PhaseStatus = "idle" | "running" | "done" | "failed" | "skipped" | "warning";

export type PhaseKey =
  | "ollama"
  | "task_structure"
  | "git"
  | "local_server"
  | "deploy"
  | "health"
  | "structure"
  | "ui_test"
  | "exploration"
  | "cursor";

export const PHASES: { key: PhaseKey; label: string; group: "local" | "cursor" }[] = [
  { key: "ollama", label: "Ollama", group: "local" },
  { key: "task_structure", label: "Task", group: "local" },
  { key: "git", label: "Git", group: "local" },
  { key: "local_server", label: "Local dev", group: "local" },
  { key: "deploy", label: "Deploy", group: "local" },
  { key: "health", label: "Health", group: "local" },
  { key: "structure", label: "Structure", group: "local" },
  { key: "exploration", label: "UI exploration", group: "local" },
  { key: "ui_test", label: "UI test (spec)", group: "local" },
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
  page_url?: string;
  text?: string;
  role?: string;
  agentId?: string;
  runId?: string;
  cursorUiHint?: string;
  url?: string;
  title?: string;
  context?: string;
  node_url?: string;
  interactables?: import("@/lib/projectTypes").InteractableElement[];
};

export type PhaseMap = Record<string, { status?: string; message?: string }>;

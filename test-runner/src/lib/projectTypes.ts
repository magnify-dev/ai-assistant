export type InteractableElement = {
  index: number;
  kind: string;
  test_id?: string | null;
  role?: string | null;
  text?: string | null;
  aria?: string | null;
  href?: string | null;
  input_type?: string | null;
  disabled?: boolean;
  name?: string | null;
  placeholder?: string | null;
};

export type BrowserState = {
  url: string;
  title?: string;
  interactables: InteractableElement[];
  context?: string;
  node_url?: string;
  ts?: string;
  screenshot_b64?: string;
  error?: string;
};

export type RegisteredProject = {
  id: string;
  name: string;
  path: string;
  lastUsed: string;
  settings: {
    task?: string;
    push?: boolean;
    skipDeploy?: boolean;
    testTarget?: "local" | "deployed";
    skipDeployWait?: boolean;
    skipCursor?: boolean;
    cursorRuntime?: "cloud" | "local";
    repoUrl?: string;
    cursorPrompt?: string;
  };
};

export type ProjectsRegistry = {
  version: number;
  activeProjectId: string | null;
  projects: RegisteredProject[];
};

export type ProjectBundle = {
  path: string;
  profile: Record<string, unknown> | null;
  cheatsheet: string;
  specs: { name: string }[];
};

export type LocalEnvStatus = {
  ready: boolean;
  missing: string[];
  required: string[];
  env_files: { path: string; exists: boolean }[];
  has_env?: boolean;
  has_env_example: boolean;
  has_env_local: boolean;
  env_path?: string;
  env_example_path: string;
  env_local_path: string;
  local_base_url?: string;
};

export type TestTarget = {
  url: string;
  source: "local" | "deployed_fallback" | "deployed" | string;
  local_url?: string;
  ts?: string;
};

export type StructuredTask = {
  summary?: string;
  source_text?: string;
  scope_urls?: string[];
  success_criteria?: string[];
  deliverables?: string[];
  suggested_steps?: { action?: string; description?: string; mode?: string }[];
  notes_for_cursor?: string[];
  preserves_intent?: boolean;
  intent_gaps?: string[];
  spec_runs?: string;
};

export type RunReport = {
  overall_ok: boolean;
  requested: {
    summary: string;
    source_text: string;
    success_criteria: string[];
    scope_urls: string[];
    deliverables?: string[];
    intent_gaps?: string[];
  };
  executed: {
    mode?: string;
    page_url?: string;
    action?: string;
    target?: string;
    ok?: boolean;
    message?: string;
    line?: string;
  }[];
  step_summary: Record<string, number>;
  criteria_results: { criterion: string; met: boolean | null; note: string }[];
  phases: { name: string; ok: boolean; detail: string }[];
  test_target: Record<string, unknown>;
  final_url: string;
  ui_error: string;
  mode?: string;
  site_map_changes?: {
    new_pages?: string[];
    updated_pages?: { path: string; new_elements: number }[];
    new_elements?: number;
    total_pages?: number;
  };
  cheatsheet_changes?: {
    added_learnings?: { insight?: string; source?: string }[];
    added_notes?: string[];
  };
  page_report?: string;
  task_answer?: string;
  page_findings?: {
    accounts?: { name?: string; platform?: string; status?: string; email?: string | null; no_login?: boolean }[];
    platform_counts?: Record<string, number>;
    attention_count?: number | null;
    empty_message?: string | null;
  };
  exploration_report_path?: string;
  playwright_session?: PlaywrightSession;
};

export type PlaywrightSessionFrame = {
  step?: number;
  label?: string;
  url?: string;
  context?: string;
  screenshot?: string;
  screenshotUrl?: string;
  ts?: string;
};

export type PlaywrightSession = {
  recorded_at?: string;
  frames?: PlaywrightSessionFrame[];
  trace?: string;
  traceUrl?: string;
  video?: string;
  videoUrl?: string;
  frame_count?: number;
};

export type RunHistoryEntry = {
  id: string;
  label: string;
  overallOk: boolean | null;
  summary: string;
  finalUrl: string;
  generatedAt: string;
  hasSession: boolean;
  frameCount: number;
};

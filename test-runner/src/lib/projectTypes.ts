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
  has_env_example: boolean;
  has_env_local: boolean;
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

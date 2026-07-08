import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { REPO_ROOT } from "./python-runner.js";

export type ProjectSettings = {
  task?: string;
  push?: boolean;
  skipDeploy?: boolean;
  skipCursor?: boolean;
  cursorRuntime?: "cloud" | "local";
  repoUrl?: string;
  cursorPrompt?: string;
};

export type RegisteredProject = {
  id: string;
  name: string;
  path: string;
  lastUsed: string;
  settings: ProjectSettings;
};

export type ProjectsRegistry = {
  version: number;
  activeProjectId: string | null;
  projects: RegisteredProject[];
};

const REGISTRY_PATH = path.join(REPO_ROOT, "test-runner", "data", "projects-registry.json");

function defaultRegistry(): ProjectsRegistry {
  return { version: 1, activeProjectId: null, projects: [] };
}

export function loadRegistry(): ProjectsRegistry {
  try {
    if (!fs.existsSync(REGISTRY_PATH)) {
      return defaultRegistry();
    }
    const raw = JSON.parse(fs.readFileSync(REGISTRY_PATH, "utf8")) as ProjectsRegistry;
    return {
      version: raw.version ?? 1,
      activeProjectId: raw.activeProjectId ?? null,
      projects: Array.isArray(raw.projects) ? raw.projects : [],
    };
  } catch {
    return defaultRegistry();
  }
}

export function saveRegistry(registry: ProjectsRegistry): void {
  fs.mkdirSync(path.dirname(REGISTRY_PATH), { recursive: true });
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2) + "\n", "utf8");
}

export function upsertProject(
  projectPath: string,
  settings?: ProjectSettings,
  name?: string,
): RegisteredProject {
  const resolved = path.resolve(projectPath);
  const registry = loadRegistry();
  const existing = registry.projects.find((p) => path.resolve(p.path) === resolved);
  const now = new Date().toISOString();

  if (existing) {
    existing.lastUsed = now;
    if (name) existing.name = name;
    if (settings) existing.settings = { ...existing.settings, ...settings };
    registry.activeProjectId = existing.id;
    saveRegistry(registry);
    return existing;
  }

  const entry: RegisteredProject = {
    id: randomUUID(),
    name: name || path.basename(resolved),
    path: resolved,
    lastUsed: now,
    settings: settings ?? {},
  };
  registry.projects.unshift(entry);
  registry.activeProjectId = entry.id;
  saveRegistry(registry);
  return entry;
}

export function setActiveProject(id: string): RegisteredProject | null {
  const registry = loadRegistry();
  const project = registry.projects.find((p) => p.id === id);
  if (!project) return null;
  registry.activeProjectId = id;
  project.lastUsed = new Date().toISOString();
  saveRegistry(registry);
  return project;
}

export function removeProject(id: string): boolean {
  const registry = loadRegistry();
  const before = registry.projects.length;
  registry.projects = registry.projects.filter((p) => p.id !== id);
  if (registry.activeProjectId === id) {
    registry.activeProjectId = registry.projects[0]?.id ?? null;
  }
  if (registry.projects.length === before) return false;
  saveRegistry(registry);
  return true;
}

function projectAgentDir(projectPath: string, ...parts: string[]): string {
  return path.join(path.resolve(projectPath), ".agent", ...parts);
}

export function readProjectBundle(projectPath: string) {
  const resolved = path.resolve(projectPath);
  const profilePath = projectAgentDir(resolved, "profile.json");
  const cheatsheetPath = projectAgentDir(resolved, "cheatsheet.yaml");
  const specsDir = projectAgentDir(resolved, "specs");

  let profile: Record<string, unknown> | null = null;
  if (fs.existsSync(profilePath)) {
    try {
      profile = JSON.parse(fs.readFileSync(profilePath, "utf8")) as Record<string, unknown>;
    } catch {
      profile = null;
    }
  }

  let cheatsheet = "";
  if (fs.existsSync(cheatsheetPath)) {
    cheatsheet = fs.readFileSync(cheatsheetPath, "utf8");
  }

  const specs: { name: string }[] = [];
  if (fs.existsSync(specsDir)) {
    for (const file of fs.readdirSync(specsDir).filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"))) {
      specs.push({ name: file });
    }
  }

  return { path: resolved, profile, cheatsheet, specs, profilePath, cheatsheetPath, specsDir };
}

export function writeCheatsheet(projectPath: string, content: string): string {
  const target = projectAgentDir(projectPath, "cheatsheet.yaml");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content.endsWith("\n") ? content : content + "\n", "utf8");
  return target;
}

export function writeProfile(projectPath: string, profile: Record<string, unknown>): string {
  const target = projectAgentDir(projectPath, "profile.json");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify({ ...profile, updated_at: new Date().toISOString() }, null, 2) + "\n", "utf8");
  return target;
}

export function readSpec(projectPath: string, name: string): string {
  const target = path.join(projectAgentDir(projectPath, "specs"), name);
  if (!fs.existsSync(target)) {
    throw new Error(`Spec not found: ${name}`);
  }
  return fs.readFileSync(target, "utf8");
}

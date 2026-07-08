import fs from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";

type ExplorationDoc = {
  version?: number;
  updated_at?: string;
  navigation?: Record<string, unknown>;
  pages?: Record<string, unknown>;
};

function agentPath(projectPath: string, ...parts: string[]) {
  return path.join(path.resolve(projectPath), ".agent", ...parts);
}

function runRoot(projectPath: string, runId: string) {
  if (runId === "current") return agentPath(projectPath, "current");
  return agentPath(projectPath, "history", runId);
}

function readJsonFile<T extends Record<string, unknown>>(file: string): T | null {
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as T;
  } catch {
    return null;
  }
}

function formatRunLabel(runId: string) {
  if (runId === "current") return "Latest run";
  const m = runId.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (!m) return runId;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]} UTC`;
}

function readSessionManifest(runRootDir: string) {
  const candidates = [
    path.join(runRootDir, "ui-artifacts", "playwright-session", "session.json"),
    path.join(runRootDir, "playwright-session", "session.json"),
  ];
  for (const file of candidates) {
    const data = readJsonFile<Record<string, unknown>>(file);
    if (data) return { manifest: data, base: path.relative(runRootDir, path.dirname(file)).replace(/\\/g, "/") };
  }
  return { manifest: null, base: "ui-artifacts/playwright-session" };
}

export type RunSummary = {
  id: string;
  label: string;
  overallOk: boolean | null;
  summary: string;
  finalUrl: string;
  generatedAt: string;
  hasSession: boolean;
  frameCount: number;
};

export function readRunBundle(projectPath: string, runId: string) {
  const root = runRoot(projectPath, runId);
  const report = readJsonFile<Record<string, unknown>>(path.join(root, "run-report.json"));
  const task = readJsonFile<Record<string, unknown>>(path.join(root, "task.json"));
  const explorationReportPath = path.join(root, "exploration-report.md");
  let pageReport = "";
  if (fs.existsSync(explorationReportPath)) {
    try {
      pageReport = fs.readFileSync(explorationReportPath, "utf8");
    } catch {
      pageReport = "";
    }
  }
  const { manifest: playwrightSession } = readSessionManifest(root);
  const structuredTask =
    (task?.structured_task as Record<string, unknown> | undefined) ??
    (report?.requested as Record<string, unknown> | undefined);
  const status = readJsonFile<Record<string, unknown>>(path.join(root, "status.json"));
  return {
    runId,
    root,
    report,
    task,
    pageReport,
    structuredTask,
    playwrightSession,
    status,
    hasRun: Boolean(report),
  };
}

function summarizeRun(runId: string, bundle: ReturnType<typeof readRunBundle>): RunSummary {
  const report = bundle.report;
  const requested = (report?.requested as Record<string, unknown> | undefined) ?? {};
  const session = bundle.playwrightSession as { frame_count?: number; frames?: unknown[] } | null;
  const frames = Array.isArray(session?.frames) ? session.frames.length : Number(session?.frame_count ?? 0);
  return {
    id: runId,
    label: formatRunLabel(runId),
    overallOk: typeof report?.overall_ok === "boolean" ? report.overall_ok : null,
    summary: String(requested.summary ?? report?.mode ?? "Run"),
    finalUrl: String(report?.final_url ?? ""),
    generatedAt: String(bundle.status?.generated_at ?? (report as { generated_at?: string } | null)?.generated_at ?? runId),
    hasSession: Boolean(session && (frames > 0 || session.frame_count)),
    frameCount: frames,
  };
}

export function listRunHistory(projectPath: string) {
  const runs: RunSummary[] = [];
  const current = readRunBundle(projectPath, "current");
  if (current.hasRun) runs.push(summarizeRun("current", current));

  const historyDir = agentPath(projectPath, "history");
  if (fs.existsSync(historyDir)) {
    const entries = fs
      .readdirSync(historyDir, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort((a, b) => b.localeCompare(a));
    for (const id of entries) {
      runs.push(summarizeRun(id, readRunBundle(projectPath, id)));
    }
  }

  return { runs };
}

export function resolveRunArtifact(projectPath: string, runId: string, fileRel: string) {
  const root = runRoot(projectPath, runId);
  const normalized = path.normalize(fileRel).replace(/^(\.\.(\/|\\|$))+/, "");
  if (normalized.startsWith("..") || path.isAbsolute(normalized)) {
    throw new Error("Invalid artifact path");
  }
  const full = path.join(root, normalized);
  if (!full.startsWith(root)) {
    throw new Error("Invalid artifact path");
  }
  if (!fs.existsSync(full) || !fs.statSync(full).isFile()) {
    throw new Error("Artifact not found");
  }
  return full;
}

export function readLocalDevStatus(projectPath: string) {
  const stateFile = agentPath(projectPath, "current", "local-dev-state.json");
  let state: Record<string, unknown> | null = null;
  if (fs.existsSync(stateFile)) {
    try {
      state = JSON.parse(fs.readFileSync(stateFile, "utf8")) as Record<string, unknown>;
    } catch {
      state = null;
    }
  }
  return { state, stateFile };
}

export function readRunReport(projectPath: string) {
  return readRunBundle(projectPath, "current");
}

export function readCheatsheetLearnings(projectPath: string) {
  const file = agentPath(projectPath, "cheatsheet-learnings.yaml");
  if (!fs.existsSync(file)) return { entries: [] as Record<string, unknown>[], file };
  const raw = fs.readFileSync(file, "utf8");
  const entries: Record<string, unknown>[] = [];
  const block = raw.match(/entries:\s*\n([\s\S]*)/);
  if (block) {
    for (const line of block[1].split("\n")) {
      const insight = line.match(/insight:\s*(.+)/);
      if (insight) entries.push({ insight: insight[1].trim() });
    }
  }
  return { entries, file, raw };
}

export function readExploration(projectPath: string): { exploration: ExplorationDoc | null; file: string } {
  const yamlFile = agentPath(projectPath, "exploration.yaml");
  if (fs.existsSync(yamlFile)) {
    try {
      const raw = fs.readFileSync(yamlFile, "utf8");
      const exploration = parseYaml(raw) as ExplorationDoc;
      return { exploration, file: yamlFile };
    } catch {
      /* fall through */
    }
  }

  const legacyNav = readLegacyNav(projectPath);
  const legacyPages = readLegacyPages(projectPath);
  if (!legacyNav && !legacyPages) {
    return { exploration: null, file: yamlFile };
  }

  const exploration: ExplorationDoc = {
    version: 1,
    navigation: legacyNav ?? { tree: [], routes: {}, edges: [], global_nav: [] },
    pages: legacyPages ?? {},
  };
  return { exploration, file: yamlFile };
}

function readLegacyNav(projectPath: string): Record<string, unknown> | null {
  const yamlFile = agentPath(projectPath, "cheatsheet-navigation.yaml");
  const jsonFile = agentPath(projectPath, "cheatsheet-navigation.json");
  if (fs.existsSync(yamlFile)) {
    try {
      const navTree = parseYaml(fs.readFileSync(yamlFile, "utf8")) as Record<string, unknown>;
      return {
        tree: navTree.tree ?? [],
        routes: navTree.routes ?? {},
        edges: navTree.edges ?? [],
        global_nav: navTree.global_nav ?? [],
      };
    } catch {
      /* fall through */
    }
  }
  if (fs.existsSync(jsonFile)) {
    try {
      const navTree = JSON.parse(fs.readFileSync(jsonFile, "utf8")) as Record<string, unknown>;
      return {
        tree: navTree.tree ?? [],
        routes: navTree.routes ?? {},
        edges: navTree.edges ?? [],
        global_nav: navTree.global_nav ?? [],
      };
    } catch {
      /* fall through */
    }
  }
  return null;
}

function readLegacyPages(projectPath: string): Record<string, unknown> | null {
  const yamlFile = agentPath(projectPath, "site-map.yaml");
  const jsonFile = agentPath(projectPath, "site-map.json");
  if (fs.existsSync(yamlFile)) {
    try {
      const siteMap = parseYaml(fs.readFileSync(yamlFile, "utf8")) as Record<string, unknown>;
      return (siteMap.pages as Record<string, unknown>) ?? {};
    } catch {
      /* fall through */
    }
  }
  if (fs.existsSync(jsonFile)) {
    try {
      const siteMap = JSON.parse(fs.readFileSync(jsonFile, "utf8")) as Record<string, unknown>;
      return (siteMap.pages as Record<string, unknown>) ?? {};
    } catch {
      /* fall through */
    }
  }
  return null;
}

export function readNavTree(projectPath: string) {
  const { exploration, file } = readExploration(projectPath);
  const navigation = exploration?.navigation as Record<string, unknown> | undefined;
  if (!navigation) {
    return { navTree: null, file };
  }
  return {
    navTree: {
      routes: navigation.routes ?? {},
      edges: navigation.edges ?? [],
      global_nav: navigation.global_nav ?? [],
      tree: navigation.tree ?? [],
      updated_at: exploration?.updated_at,
    },
    file,
  };
}

export function readSiteMap(projectPath: string) {
  const { exploration, file } = readExploration(projectPath);
  if (!exploration) {
    return { siteMap: null, file };
  }
  return {
    siteMap: {
      pages: exploration.pages ?? {},
      updated_at: exploration.updated_at,
    },
    file,
  };
}

export function artifactContentType(filePath: string) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".webm") return "video/webm";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".png") return "image/png";
  if (ext === ".zip") return "application/zip";
  if (ext === ".json") return "application/json";
  return "application/octet-stream";
}

export function buildArtifactUrl(projectPath: string, runId: string, fileRel: string) {
  return `/api/project/run-artifact?path=${encodeURIComponent(projectPath)}&runId=${encodeURIComponent(runId)}&file=${encodeURIComponent(fileRel)}`;
}

export function sessionWithArtifactUrls(
  projectPath: string,
  runId: string,
  manifest: Record<string, unknown> | null,
) {
  if (!manifest) return null;
  const base = "ui-artifacts/playwright-session";
  const toUrl = (fileRel: string) =>
    buildArtifactUrl(projectPath, runId, fileRel.startsWith("ui-artifacts/") ? fileRel : `${base}/${fileRel}`);
  const out = { ...manifest } as Record<string, unknown>;
  if (typeof out.video === "string") {
    out.videoUrl = toUrl(out.video);
  }
  if (typeof out.trace === "string") {
    out.traceUrl = toUrl(out.trace);
  }
  const frames = Array.isArray(out.frames) ? out.frames : [];
  out.frames = frames.map((frame) => {
    if (!frame || typeof frame !== "object") return frame;
    const item = { ...(frame as Record<string, unknown>) };
    if (typeof item.screenshot === "string") {
      item.screenshotUrl = toUrl(item.screenshot);
    }
    return item;
  });
  return out;
}

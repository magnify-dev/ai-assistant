import fs from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import { canResumeTranscript, readCollaborationTranscript } from "./collaboration-transcript.js";
import { classifyTaskRunKindHeuristic } from "./task-router.js";

type ExplorationDoc = {
  version?: number;
  updated_at?: string;
  navigation?: Record<string, unknown>;
  pages?: Record<string, unknown>;
};

type WebResearchDoc = {
  version?: number;
  updated_at?: string;
  pages?: Record<string, unknown>;
  facts?: Array<Record<string, unknown>>;
};

function readYamlFile(filePath: string): Record<string, unknown> | null {
  if (!fs.existsSync(filePath)) return null;
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    const data = parseYaml(raw);
    return data && typeof data === "object" ? (data as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

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
  const resolved = resolvePlaywrightSession("", runRootDir);
  return { manifest: resolved.manifest, base: resolved.base };
}

function synthesizeSessionFromScreenshots(
  runRootDir: string,
  baseRel: string,
): Record<string, unknown> | null {
  const screenshotsDir = path.join(runRootDir, baseRel, "screenshots");
  if (!fs.existsSync(screenshotsDir)) return null;
  let files: string[] = [];
  try {
    files = fs
      .readdirSync(screenshotsDir)
      .filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
      .sort();
  } catch {
    return null;
  }
  if (!files.length) return null;

  const frames = files.map((file, index) => {
    const match = file.match(/^(\d+)_(.+)\.(png|jpe?g|webp)$/i);
    const step = match ? Number(match[1]) : index + 1;
    const label = match?.[2] ?? file.replace(/\.[^.]+$/, "");
    let ts = new Date().toISOString();
    try {
      ts = fs.statSync(path.join(screenshotsDir, file)).mtime.toISOString();
    } catch {
      /* ignore */
    }
    return {
      step,
      label,
      url: "",
      context: "web_exploration",
      screenshot: `screenshots/${file}`,
      interactables: [],
      ts,
    };
  });

  return {
    recorded_at: frames[frames.length - 1]?.ts ?? new Date().toISOString(),
    frames,
    frame_count: frames.length,
  };
}

function readWebSessionStateForRun(
  projectPath: string,
  manifest: Record<string, unknown> | null,
  transcriptSavedAt?: string,
): Record<string, unknown> | null {
  const sessionsDir = agentPath(projectPath, "web", "sessions");
  if (!fs.existsSync(sessionsDir)) return null;

  const frameLabels = new Set(
    (Array.isArray(manifest?.frames) ? manifest.frames : [])
      .map((frame) =>
        frame && typeof frame === "object" ? String((frame as Record<string, unknown>).label ?? "") : "",
      )
      .filter(Boolean),
  );
  const targetTime = parseTimestamp(transcriptSavedAt);

  let best: { state: Record<string, unknown>; score: number; mtime: number } | null = null;
  for (const entry of fs.readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.ya?ml$/i.test(entry.name)) continue;
    const file = path.join(sessionsDir, entry.name);
    const state = readYamlFile(file);
    if (!state) continue;
    let mtime = 0;
    try {
      mtime = fs.statSync(file).mtimeMs;
    } catch {
      mtime = 0;
    }

    const sessionId = String(state.session_id ?? entry.name.replace(/\.ya?ml$/i, ""));
    if (manifest?.session_id && sessionId === String(manifest.session_id)) {
      return state;
    }

    const history = Array.isArray(state.history) ? state.history : [];
    const matchScore = history.reduce((count, item) => {
      if (!item || typeof item !== "object") return count;
      const stepId = String((item as Record<string, unknown>).step_id ?? "");
      return frameLabels.has(stepId) ? count + 1 : count;
    }, 0);

    const timeScore =
      targetTime > 0 ? -Math.abs(mtime - targetTime) : mtime;
    const score = matchScore * 1_000_000_000_000 + timeScore;
    if (!best || score > best.score) {
      best = { state, score, mtime };
    }
  }

  return best?.state ?? null;
}

function enrichSessionFromWebState(
  manifest: Record<string, unknown>,
  webState: Record<string, unknown> | null,
): Record<string, unknown> {
  if (!webState) return manifest;
  const history = Array.isArray(webState.history) ? webState.history : [];
  const snapshots = Array.isArray(webState.snapshots) ? webState.snapshots : [];
  if (!history.length && !snapshots.length) return manifest;

  const historyByStep = new Map<string, Record<string, unknown>>();
  for (const item of history) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const stepId = String(row.step_id ?? "");
    if (stepId) historyByStep.set(stepId, row);
  }

  const snapshotsByStep = new Map<string, Record<string, unknown>>();
  const snapshotsById = new Map<string, Record<string, unknown>>();
  for (const item of snapshots) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const stepId = String(row.step_id ?? "");
    const snapshotId = String(row.snapshot_id ?? "");
    if (stepId) snapshotsByStep.set(stepId, row);
    if (snapshotId) snapshotsById.set(snapshotId, row);
  }

  const frames = Array.isArray(manifest.frames) ? manifest.frames : [];
  const enrichedFrames = frames.map((frame, frameIndex) => {
    if (!frame || typeof frame !== "object") return frame;
    const item = { ...(frame as Record<string, unknown>) };
    const stepId = String(item.label ?? "");
    const hist =
      historyByStep.get(stepId) ??
      (Array.isArray(webState.history) ? (webState.history as Record<string, unknown>[])[frameIndex] : undefined);
    const snap =
      snapshotsByStep.get(stepId) ??
      (hist?.snapshot_id ? snapshotsById.get(String(hist.snapshot_id)) : undefined) ??
      (Array.isArray(webState.snapshots)
        ? (webState.snapshots as Record<string, unknown>[])[frameIndex]
        : undefined);

    if (snap?.url) item.url = snap.url;
    if (snap?.title) item.title = snap.title;
    if (Array.isArray(snap?.interactables) && snap.interactables.length) {
      item.interactables = snap.interactables;
    }

    if (hist) {
      const action = String(hist.action ?? "");
      const reason = String(hist.reason ?? "");
      const targetId = String(hist.target_id ?? "");
      if (action || reason || targetId) {
        item.decision = {
          action,
          reason,
          ...(targetId ? { target_id: targetId } : {}),
          ...(typeof item.decision === "object" && item.decision ? item.decision : {}),
        };
      }
      if (targetId) {
        const controls = Array.isArray(item.interactables) ? item.interactables : [];
        const selected = controls.find(
          (control) =>
            control &&
            typeof control === "object" &&
            String((control as Record<string, unknown>).id ?? "") === targetId,
        );
        if (selected && typeof selected === "object") {
          item.selected_interactable_id = targetId;
          item.selected_interactable = selected;
        } else {
          item.selected_interactable_id = targetId;
        }
      }
      if (typeof hist.ok === "boolean") item.action_ok = hist.ok;
      if (hist.error) item.error = String(hist.error);
      if (hist.ok === false && !item.error) {
        item.error = String(hist.error ?? "Action failed");
      }
    }

    return item;
  });

  return {
    ...manifest,
    frames: enrichedFrames,
    frame_count: enrichedFrames.length,
    session_id: webState.session_id,
    query: webState.query,
  };
}

type SessionResolution = {
  manifest: Record<string, unknown> | null;
  base: string;
  source: "web" | "ui";
};

function resolvePlaywrightSession(
  projectPath: string,
  runRootDir: string,
  hints?: {
    preferWeb?: boolean;
    runKind?: "ui_test" | "web_research" | "exploration";
    transcriptSavedAt?: string;
  },
): SessionResolution {
  const webBase = "web-artifacts/playwright-session";
  const uiBase = "ui-artifacts/playwright-session";
  const webManifestPath = path.join(runRootDir, webBase, "session.json");
  const uiManifestPath = path.join(runRootDir, uiBase, "session.json");

  const webManifest = readJsonFile<Record<string, unknown>>(webManifestPath);
  const uiManifest = readJsonFile<Record<string, unknown>>(uiManifestPath);
  const webScreenshots = countScreenshotFrames(runRootDir);
  const preferWeb =
    hints?.preferWeb === true ||
    hints?.runKind === "web_research" ||
    (webScreenshots.sessionSource === "web" && webScreenshots.hasSession);

  let webMtime = 0;
  let uiMtime = 0;
  if (fs.existsSync(webManifestPath)) {
    try {
      webMtime = fs.statSync(webManifestPath).mtimeMs;
    } catch {
      webMtime = 0;
    }
  }
  if (fs.existsSync(uiManifestPath)) {
    try {
      uiMtime = fs.statSync(uiManifestPath).mtimeMs;
    } catch {
      uiMtime = 0;
    }
  }
  const webShotDir = path.join(runRootDir, webBase, "screenshots");
  const uiShotDir = path.join(runRootDir, uiBase, "screenshots");
  if (fs.existsSync(webShotDir)) {
    try {
      webMtime = Math.max(webMtime, fs.statSync(webShotDir).mtimeMs);
    } catch {
      /* ignore */
    }
  }
  if (fs.existsSync(uiShotDir)) {
    try {
      uiMtime = Math.max(uiMtime, fs.statSync(uiShotDir).mtimeMs);
    } catch {
      /* ignore */
    }
  }

  let chosen: SessionResolution;
  if (preferWeb || (webMtime >= uiMtime && (webManifest || webScreenshots.hasSession))) {
    chosen = {
      manifest: webManifest ?? synthesizeSessionFromScreenshots(runRootDir, webBase),
      base: webBase,
      source: "web",
    };
  } else if (uiManifest || countScreenshotFrames(runRootDir).sessionSource === "ui") {
    const uiShots = countScreenshotFrames(runRootDir);
    chosen = {
      manifest: uiManifest ?? (uiShots.hasSession ? synthesizeSessionFromScreenshots(runRootDir, uiBase) : null),
      base: uiBase,
      source: "ui",
    };
  } else {
    chosen = { manifest: null, base: uiBase, source: "ui" };
  }

  if (chosen.source === "web" && chosen.manifest && projectPath) {
    chosen = {
      ...chosen,
      manifest: enrichSessionFromWebState(
        chosen.manifest,
        readWebSessionStateForRun(projectPath, chosen.manifest, hints?.transcriptSavedAt),
      ),
    };
  }

  return chosen;
}

function parseTimestamp(value: unknown): number {
  if (typeof value !== "string" || !value.trim()) return 0;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

function countScreenshotFrames(runRootDir: string): {
  frameCount: number;
  hasSession: boolean;
  sessionSource?: "web" | "ui";
} {
  const candidates: { rel: string; source: "web" | "ui" }[] = [
    { rel: "web-artifacts/playwright-session/screenshots", source: "web" },
    { rel: "ui-artifacts/playwright-session/screenshots", source: "ui" },
  ];
  let best: { frameCount: number; sessionSource: "web" | "ui"; mtime: number } | null = null;
  for (const { rel, source } of candidates) {
    const dir = path.join(runRootDir, rel);
    if (!fs.existsSync(dir)) continue;
    let files: string[] = [];
    try {
      files = fs.readdirSync(dir);
    } catch {
      continue;
    }
    const count = files.filter((name) => /\.(png|jpe?g|webp)$/i.test(name)).length;
    if (!count) continue;
    let mtime = 0;
    try {
      mtime = fs.statSync(dir).mtimeMs;
    } catch {
      mtime = 0;
    }
    if (!best || mtime >= best.mtime) {
      best = { frameCount: count, sessionSource: source, mtime };
    }
  }
  if (!best) return { frameCount: 0, hasSession: false };
  return {
    frameCount: best.frameCount,
    hasSession: true,
    sessionSource: best.sessionSource,
  };
}

function resolveSessionInfo(
  runRootDir: string,
  manifest: Record<string, unknown> | null,
): { frameCount: number; hasSession: boolean; sessionSource?: "web" | "ui" } {
  const screenshotInfo = countScreenshotFrames(runRootDir);
  const manifestFrames = Array.isArray(manifest?.frames)
    ? manifest.frames.length
    : Number(manifest?.frame_count ?? 0);

  if (manifest && manifestFrames > 0) {
    let manifestMtime = 0;
    const manifestPaths = [
      path.join(runRootDir, "web-artifacts", "playwright-session", "session.json"),
      path.join(runRootDir, "ui-artifacts", "playwright-session", "session.json"),
      path.join(runRootDir, "playwright-session", "session.json"),
    ];
    for (const file of manifestPaths) {
      if (!fs.existsSync(file)) continue;
      try {
        manifestMtime = Math.max(manifestMtime, fs.statSync(file).mtimeMs);
      } catch {
        /* ignore */
      }
    }
    const screenshotDir =
      screenshotInfo.sessionSource === "web"
        ? path.join(runRootDir, "web-artifacts", "playwright-session", "screenshots")
        : screenshotInfo.sessionSource === "ui"
          ? path.join(runRootDir, "ui-artifacts", "playwright-session", "screenshots")
          : "";
    let screenshotMtime = 0;
    if (screenshotDir && fs.existsSync(screenshotDir)) {
      try {
        screenshotMtime = fs.statSync(screenshotDir).mtimeMs;
      } catch {
        screenshotMtime = 0;
      }
    }
    if (screenshotInfo.hasSession && screenshotMtime > manifestMtime) {
      return screenshotInfo;
    }
    const base = String(manifest.video ?? manifest.trace ?? "");
    const sessionSource = base.includes("web-artifacts/") ? "web" : "ui";
    return { frameCount: manifestFrames, hasSession: true, sessionSource };
  }
  return screenshotInfo;
}

function inferRunKind(
  report: Record<string, unknown> | null,
  transcript: ReturnType<typeof readCollaborationTranscript>,
  root: string,
  transcriptPreferred: boolean,
): "ui_test" | "web_research" | "exploration" {
  if (transcriptPreferred && transcript?.task) {
    const fromTask = classifyTaskRunKindHeuristic(transcript.task);
    if (fromTask) return fromTask;
  }
  const webArtifacts = path.join(root, "web-artifacts");
  if (fs.existsSync(webArtifacts)) {
    try {
      const stat = fs.statSync(webArtifacts);
      const reportAt = parseTimestamp(
        (report as { generated_at?: string } | null)?.generated_at,
      );
      if (!report || stat.mtimeMs >= reportAt) return "web_research";
    } catch {
      return "web_research";
    }
  }
  if (report?.mode === "exploration") return "exploration";
  if (transcript?.task) {
    const fromTask = classifyTaskRunKindHeuristic(transcript.task);
    if (fromTask) return fromTask;
  }
  return "ui_test";
}

function lastAgentSummary(transcript: ReturnType<typeof readCollaborationTranscript>): string {
  if (!transcript?.agentCards?.length) return "";
  for (let i = transcript.agentCards.length - 1; i >= 0; i--) {
    const card = transcript.agentCards[i];
    if (card.summary?.trim()) return card.summary.trim();
  }
  return "";
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
  canResume: boolean;
  runKind: "ui_test" | "web_research" | "exploration";
  statusText: string;
  sessionSource?: "web" | "ui";
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
  const collaborationTranscript = readCollaborationTranscript(projectPath, runId);
  const reportAt = parseTimestamp(
    readJsonFile<Record<string, unknown>>(path.join(root, "status.json"))?.generated_at ??
      (report as { generated_at?: string } | null)?.generated_at,
  );
  const transcriptAt = parseTimestamp(collaborationTranscript?.savedAt);
  const transcriptPreferred = Boolean(collaborationTranscript && (!report || transcriptAt >= reportAt));
  const runKind = inferRunKind(report, collaborationTranscript, root, transcriptPreferred);
  const session = resolvePlaywrightSession(projectPath, root, {
    preferWeb: transcriptPreferred || runKind === "web_research",
    runKind,
    transcriptSavedAt: collaborationTranscript?.savedAt,
  });
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
    playwrightSession: session.manifest,
    sessionBase: session.base,
    sessionSource: session.source,
    status,
    hasRun: Boolean(report || collaborationTranscript || session.manifest),
    collaborationTranscript,
  };
}

function hasRunArtifacts(bundle: ReturnType<typeof readRunBundle>): boolean {
  if (bundle.report || bundle.collaborationTranscript || bundle.playwrightSession) return true;
  return resolveSessionInfo(bundle.root, null).hasSession;
}

function historyStamp(): string {
  const now = new Date();
  const y = now.getUTCFullYear();
  const mo = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  const h = String(now.getUTCHours()).padStart(2, "0");
  const mi = String(now.getUTCMinutes()).padStart(2, "0");
  const s = String(now.getUTCSeconds()).padStart(2, "0");
  return `${y}${mo}${d}T${h}${mi}${s}Z`;
}

/** Copy `.agent/current` to `.agent/history/<stamp>` when it contains run artifacts. */
export function archiveCurrentRun(projectPath: string): string | null {
  const currentDir = agentPath(projectPath, "current");
  if (!fs.existsSync(currentDir)) return null;
  let hasEntries = false;
  try {
    hasEntries = fs.readdirSync(currentDir).length > 0;
  } catch {
    return null;
  }
  if (!hasEntries) return null;

  const bundle = readRunBundle(projectPath, "current");
  if (!hasRunArtifacts(bundle)) return null;

  const historyDir = agentPath(projectPath, "history");
  fs.mkdirSync(historyDir, { recursive: true });
  const stamp = historyStamp();
  const target = path.join(historyDir, stamp);
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true });
  }
  fs.cpSync(currentDir, target, { recursive: true });
  return stamp;
}

/** Archive the latest run, then clear `.agent/current` before starting a new one. */
export function prepareCurrentForNewRun(projectPath: string): string | null {
  const stamp = archiveCurrentRun(projectPath);
  const currentDir = agentPath(projectPath, "current");
  fs.mkdirSync(currentDir, { recursive: true });
  for (const entry of fs.readdirSync(currentDir, { withFileTypes: true })) {
    fs.rmSync(path.join(currentDir, entry.name), { recursive: true, force: true });
  }
  return stamp;
}

function summarizeRun(runId: string, bundle: ReturnType<typeof readRunBundle>): RunSummary {
  const report = bundle.report;
  const transcript = bundle.collaborationTranscript;
  const requested = (report?.requested as Record<string, unknown> | undefined) ?? {};
  const reportAt = parseTimestamp(bundle.status?.generated_at ?? (report as { generated_at?: string } | null)?.generated_at);
  const transcriptAt = parseTimestamp(transcript?.savedAt);
  const transcriptPreferred = Boolean(transcript && (!report || transcriptAt >= reportAt));

  let summary = String(requested.summary ?? report?.mode ?? "Run");
  if (transcript?.task?.trim()) {
    if (transcriptPreferred || !requested.summary) summary = transcript.task.trim();
  }

  let overallOk: boolean | null = typeof report?.overall_ok === "boolean" ? report.overall_ok : null;
  if (transcript?.collaborationResult && (transcriptPreferred || overallOk === null)) {
    if (typeof transcript.collaborationResult.ok === "boolean") {
      overallOk = transcript.collaborationResult.ok;
    }
  }

  const generatedAt =
    transcriptPreferred && transcript?.savedAt
      ? transcript.savedAt
      : String(bundle.status?.generated_at ?? (report as { generated_at?: string } | null)?.generated_at ?? transcript?.savedAt ?? runId);

  const sessionInfo = bundle.sessionSource
    ? {
        hasSession: Boolean(bundle.playwrightSession && (bundle.playwrightSession.frame_count || (bundle.playwrightSession.frames as unknown[] | undefined)?.length)),
        frameCount: Array.isArray(bundle.playwrightSession?.frames)
          ? bundle.playwrightSession.frames.length
          : Number(bundle.playwrightSession?.frame_count ?? 0),
        sessionSource: bundle.sessionSource,
      }
    : resolveSessionInfo(bundle.root, bundle.playwrightSession);
  const runKind = inferRunKind(report, transcript, bundle.root, transcriptPreferred);

  const result = transcript?.collaborationResult;
  let statusText = "";
  if (result?.answer?.trim()) {
    statusText = result.answer.trim();
  } else if (result?.error?.trim()) {
    statusText = result.error.trim();
  } else {
    statusText = lastAgentSummary(transcript);
  }

  return {
    id: runId,
    label: formatRunLabel(runId),
    overallOk,
    summary,
    finalUrl: transcriptPreferred ? "" : String(report?.final_url ?? ""),
    generatedAt,
    hasSession: sessionInfo.hasSession,
    frameCount: sessionInfo.frameCount,
    canResume: canResumeTranscript(transcript),
    runKind,
    statusText,
    sessionSource: sessionInfo.sessionSource,
  };
}

export const RUN_HISTORY_PAGE_SIZE = 3;

function quickHasRunArtifacts(projectPath: string, runId: string): boolean {
  const root = runRoot(projectPath, runId);
  if (!fs.existsSync(root)) return false;
  const markers = [
    "run-report.json",
    "collaboration-transcript.json",
    path.join("web-artifacts", "playwright-session", "session.json"),
    path.join("ui-artifacts", "playwright-session", "session.json"),
  ];
  return markers.some((rel) => fs.existsSync(path.join(root, rel)));
}

function listRunIds(projectPath: string): string[] {
  const ids: string[] = [];
  if (quickHasRunArtifacts(projectPath, "current")) ids.push("current");

  const historyDir = agentPath(projectPath, "history");
  if (fs.existsSync(historyDir)) {
    const entries = fs
      .readdirSync(historyDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort((a, b) => b.localeCompare(a));
    for (const id of entries) {
      if (quickHasRunArtifacts(projectPath, id)) ids.push(id);
    }
  }
  return ids;
}

export function listRunHistory(
  projectPath: string,
  options?: { offset?: number; limit?: number },
) {
  const offset = Math.max(0, Number(options?.offset) || 0);
  const limit = Math.max(
    1,
    Math.min(50, Number(options?.limit) || RUN_HISTORY_PAGE_SIZE),
  );
  const allIds = listRunIds(projectPath);
  const total = allIds.length;
  const pageIds = allIds.slice(offset, offset + limit);
  const runs = pageIds.map((id) => summarizeRun(id, readRunBundle(projectPath, id)));

  return {
    runs,
    total,
    offset,
    limit,
    hasMore: offset + pageIds.length < total,
  };
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

export function readWebResearch(projectPath: string): {
  index: Record<string, unknown> | null;
  facts: Record<string, unknown> | null;
  webDir: string;
} {
  const webDir = agentPath(projectPath, "web");
  const indexFile = path.join(webDir, "index.yaml");
  const factsFile = path.join(webDir, "facts.yaml");
  const index = readYamlFile(indexFile);
  const facts = readYamlFile(factsFile);
  return { index, facts, webDir };
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
  baseHint?: string,
) {
  if (!manifest) return null;
  const probePaths = [
    manifest.video,
    manifest.trace,
    ...(Array.isArray(manifest.frames)
      ? manifest.frames.map((frame) =>
          frame && typeof frame === "object" ? (frame as Record<string, unknown>).screenshot : undefined,
        )
      : []),
  ].filter((value): value is string => typeof value === "string");
  const detectedBase = probePaths.find((value) => value.includes("/"))?.split("/").slice(0, 2).join("/");
  const base = baseHint || detectedBase || "ui-artifacts/playwright-session";
  const toUrl = (fileRel: string) => {
    if (fileRel.startsWith("web-artifacts/") || fileRel.startsWith("ui-artifacts/")) {
      return buildArtifactUrl(projectPath, runId, fileRel);
    }
    return buildArtifactUrl(projectPath, runId, `${base}/${fileRel}`);
  };
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

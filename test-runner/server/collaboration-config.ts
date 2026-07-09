import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { getPrompt } from "./prompts.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = path.join(__dirname, "../data/collaboration-config.json");

export type CollaborationConfig = {
  helperPrompt: string;
  helperModel: string;
  maxTestRetries: number;
  maxIterations: number;
  /** How many "Question from the local agent" round-trips are allowed per run. */
  maxQuestionRounds: number;
  /** How many "### Info needed" round-trips the helper may request per run. */
  maxInfoRequests: number;
};

const DEFAULT_CONFIG: CollaborationConfig = {
  helperPrompt: getPrompt("collaboration.helper_system"),
  helperModel: "composer-2.5",
  maxTestRetries: 3,
  maxIterations: 10,
  maxQuestionRounds: 2,
  maxInfoRequests: 2,
};

const MODEL_ALIASES: Record<string, string> = {
  "composer-2.5-fast": "composer-2.5",
  "composer-2-fast": "composer-2",
};

export function normalizeHelperModel(model?: string): string {
  const trimmed = model?.trim() || DEFAULT_CONFIG.helperModel;
  return MODEL_ALIASES[trimmed] ?? trimmed;
}

export function readCollaborationConfig(): CollaborationConfig {
  try {
    if (!fs.existsSync(CONFIG_PATH)) {
      writeCollaborationConfig(DEFAULT_CONFIG);
      return { ...DEFAULT_CONFIG };
    }
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8")) as Partial<CollaborationConfig>;
    return {
      // Empty stored prompt means "use the default from prompts.yaml".
      helperPrompt: raw.helperPrompt?.trim() ? raw.helperPrompt : DEFAULT_CONFIG.helperPrompt,
      helperModel: normalizeHelperModel(raw.helperModel),
      maxTestRetries: raw.maxTestRetries ?? DEFAULT_CONFIG.maxTestRetries,
      maxIterations: raw.maxIterations ?? DEFAULT_CONFIG.maxIterations,
      maxQuestionRounds: raw.maxQuestionRounds ?? DEFAULT_CONFIG.maxQuestionRounds,
      maxInfoRequests: raw.maxInfoRequests ?? DEFAULT_CONFIG.maxInfoRequests,
    };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function writeCollaborationConfig(config: CollaborationConfig): CollaborationConfig {
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  const normalized: CollaborationConfig = {
    helperPrompt: config.helperPrompt.trim() || DEFAULT_CONFIG.helperPrompt,
    helperModel: normalizeHelperModel(config.helperModel),
    maxTestRetries: Math.max(1, Math.min(10, config.maxTestRetries || DEFAULT_CONFIG.maxTestRetries)),
    maxIterations: Math.max(1, Math.min(20, config.maxIterations || DEFAULT_CONFIG.maxIterations)),
    maxQuestionRounds: Math.max(0, Math.min(5, config.maxQuestionRounds ?? DEFAULT_CONFIG.maxQuestionRounds)),
    maxInfoRequests: Math.max(0, Math.min(5, config.maxInfoRequests ?? DEFAULT_CONFIG.maxInfoRequests)),
  };
  // Persist an empty prompt when it matches the prompts.yaml default so the
  // YAML stays the single source of truth; only user customizations are stored.
  const stored = {
    ...normalized,
    helperPrompt: normalized.helperPrompt === DEFAULT_CONFIG.helperPrompt ? "" : normalized.helperPrompt,
  };
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(stored, null, 2) + "\n", "utf8");
  return normalized;
}

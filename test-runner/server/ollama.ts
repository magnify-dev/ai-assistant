import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./python-runner.js";

export type OllamaConfig = {
  url: string;
  model: string;
};

export type OllamaModelOption = {
  id: string;
  label: string;
  description: string;
};

/** Curated local models for UI test / web research runs. */
export const OLLAMA_MODEL_OPTIONS: OllamaModelOption[] = [
  {
    id: "qwen2.5-coder:14b",
    label: "Qwen2.5 Coder 14B",
    description: "Code-focused — best for UI tests and dev tasks",
  },
  {
    id: "qwen3:14b",
    label: "Qwen3 14B",
    description: "Fast everyday use — good balance of speed and quality",
  },
  {
    id: "qwen3:30b",
    label: "Qwen3 30B-A3B",
    description: "Higher-quality reasoning — uses more VRAM",
  },
];

const UI_TEST_CONFIG_PATH = path.join(REPO_ROOT, "ui_test", "config.yaml");

function ollamaExe(): string | null {
  const localAppData = process.env.LOCALAPPDATA;
  if (!localAppData) return null;
  const exe = path.join(localAppData, "Programs", "Ollama", "ollama.exe");
  return fs.existsSync(exe) ? exe : null;
}

export function isModelAvailable(model: string, availableModels: string[]): boolean {
  const baseName = model.split(":", 1)[0];
  return (
    availableModels.includes(model) ||
    availableModels.some((name) => name.startsWith(`${baseName}:`))
  );
}

function modelsMatch(a: string, b: string): boolean {
  if (a === b) return true;
  return a.split(":", 1)[0] === b.split(":", 1)[0];
}

export function readOllamaConfig(): OllamaConfig {
  const url = process.env.UI_TEST_OLLAMA_URL || "http://127.0.0.1:11434";
  const envModel = process.env.UI_TEST_OLLAMA_MODEL;
  if (envModel) {
    return { url, model: envModel };
  }

  try {
    const text = fs.readFileSync(UI_TEST_CONFIG_PATH, "utf8");
    const modelMatch = text.match(/^\s*model:\s*["']?([^"'\n#]+)/m);
    const urlMatch = text.match(/^\s*url:\s*["']?([^"'\n#]+)/m);
    return {
      url: urlMatch?.[1]?.trim() || url,
      model: modelMatch?.[1]?.trim() || "qwen2.5-coder:14b",
    };
  } catch {
    return { url, model: "qwen2.5-coder:14b" };
  }
}

export async function fetchOllamaStatus(cfg: OllamaConfig): Promise<{
  reachable: boolean;
  modelAvailable: boolean;
  modelLoaded: boolean;
  loadedModels: string[];
  availableModels: string[];
}> {
  try {
    const base = cfg.url.replace(/\/$/, "");
    const [tagsRes, psRes] = await Promise.all([
      fetch(`${base}/api/tags`, { signal: AbortSignal.timeout(5000) }),
      fetch(`${base}/api/ps`, { signal: AbortSignal.timeout(5000) }),
    ]);
    if (!tagsRes.ok || !psRes.ok) {
      return {
        reachable: false,
        modelAvailable: false,
        modelLoaded: false,
        loadedModels: [],
        availableModels: [],
      };
    }
    const tags = (await tagsRes.json()) as { models?: Array<{ name?: string; model?: string }> };
    const ps = (await psRes.json()) as { models?: Array<{ name?: string; model?: string }> };
    const availableModels = (tags.models ?? [])
      .map((m) => m.name || m.model || "")
      .filter(Boolean);
    const loadedModels = (ps.models ?? []).map((m) => m.name || m.model || "").filter(Boolean);
    const modelAvailable = isModelAvailable(cfg.model, availableModels);
    const modelLoaded = isModelAvailable(cfg.model, loadedModels);
    return { reachable: true, modelAvailable, modelLoaded, loadedModels, availableModels };
  } catch {
    return {
      reachable: false,
      modelAvailable: false,
      modelLoaded: false,
      loadedModels: [],
      availableModels: [],
    };
  }
}

export type OllamaSwitchProgress = {
  step: "checking" | "unloading" | "saving" | "loading" | "downloading" | "done" | "error";
  message: string;
  progress?: number;
  fromModel?: string;
  toModel?: string;
};

export async function unloadOllamaModel(url: string, model: string): Promise<void> {
  const base = url.replace(/\/$/, "");
  const res = await fetch(`${base}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      prompt: "",
      stream: false,
      keep_alive: 0,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Failed to unload ${model}: HTTP ${res.status}`);
  }
}

export async function preloadOllamaModel(cfg: OllamaConfig): Promise<void> {
  const base = cfg.url.replace(/\/$/, "");
  const res = await fetch(`${base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: cfg.model,
      messages: [{ role: "user", content: "ready" }],
      stream: false,
      keep_alive: -1,
    }),
    signal: AbortSignal.timeout(600_000),
  });
  if (!res.ok) {
    throw new Error(`Ollama preload failed: HTTP ${res.status}`);
  }
}

async function preloadWithProgress(
  cfg: OllamaConfig,
  onProgress: (progress: OllamaSwitchProgress) => void,
): Promise<void> {
  const started = Date.now();
  onProgress({
    step: "loading",
    message: `Loading ${cfg.model} into VRAM (first load can take 30–90s)…`,
    progress: 10,
    toModel: cfg.model,
  });

  const preloadPromise = preloadOllamaModel(cfg);
  let preloadError: Error | null = null;
  preloadPromise.catch((err: unknown) => {
    preloadError = err instanceof Error ? err : new Error(String(err));
  });

  while (true) {
    const status = await fetchOllamaStatus(cfg);
    if (status.modelLoaded) {
      onProgress({
        step: "loading",
        message: `${cfg.model} loaded into VRAM`,
        progress: 100,
        toModel: cfg.model,
      });
      return;
    }

    if (preloadError) {
      throw preloadError;
    }

    const elapsedSec = Math.round((Date.now() - started) / 1000);
    const pct = Math.min(95, 10 + Math.round((elapsedSec / 90) * 85));
    onProgress({
      step: "loading",
      message: `Loading ${cfg.model}… (${elapsedSec}s)`,
      progress: pct,
      toModel: cfg.model,
    });

    if (Date.now() - started > 600_000) {
      throw new Error(`Timed out loading ${cfg.model}`);
    }

    const settled = await Promise.race([
      preloadPromise.then(() => "done" as const).catch(() => "error" as const),
      new Promise<"tick">((resolve) => setTimeout(() => resolve("tick"), 2000)),
    ]);
    if (settled === "done") {
      const finalStatus = await fetchOllamaStatus(cfg);
      if (finalStatus.modelLoaded) return;
    }
    if (settled === "error" && preloadError) {
      throw preloadError;
    }
  }
}

export async function switchOllamaModel(
  newModel: string,
  onProgress: (progress: OllamaSwitchProgress) => void,
): Promise<OllamaConfig> {
  const current = readOllamaConfig();
  if (current.model === newModel) {
    const status = await fetchOllamaStatus(current);
    if (!status.reachable) {
      throw new Error("Ollama is not reachable");
    }
    if (!status.modelAvailable) {
      throw new Error(`Model ${newModel} is not installed`);
    }
    if (!status.modelLoaded) {
      await preloadWithProgress(current, onProgress);
    }
    onProgress({ step: "done", message: `${newModel} is already selected`, progress: 100, toModel: newModel });
    return current;
  }

  onProgress({
    step: "checking",
    message: "Checking Ollama status…",
    progress: 5,
    fromModel: current.model,
    toModel: newModel,
  });

  const initialStatus = await fetchOllamaStatus(current);
  if (!initialStatus.reachable) {
    throw new Error(`Ollama is not reachable at ${current.url}`);
  }
  if (!isModelAvailable(newModel, initialStatus.availableModels)) {
    throw new Error(`Model ${newModel} is not installed`);
  }

  const loadedToUnload = initialStatus.loadedModels.filter((name) => !modelsMatch(name, newModel));
  if (loadedToUnload.length > 0) {
    onProgress({
      step: "unloading",
      message: `Unloading ${loadedToUnload.join(", ")} from VRAM…`,
      progress: 20,
      fromModel: current.model,
      toModel: newModel,
    });
    for (const loaded of loadedToUnload) {
      await unloadOllamaModel(current.url, loaded);
    }
  }

  onProgress({
    step: "saving",
    message: `Switching config to ${newModel}…`,
    progress: 35,
    fromModel: current.model,
    toModel: newModel,
  });
  writeOllamaModel(newModel);

  const nextCfg = readOllamaConfig();
  await preloadWithProgress(nextCfg, onProgress);
  onProgress({
    step: "done",
    message: `${newModel} is ready`,
    progress: 100,
    fromModel: current.model,
    toModel: newModel,
  });
  return nextCfg;
}

export function writeOllamaModel(model: string): void {
  const allowed = OLLAMA_MODEL_OPTIONS.some((opt) => opt.id === model);
  if (!allowed) {
    throw new Error(`Unknown model: ${model}`);
  }
  if (process.env.UI_TEST_OLLAMA_MODEL) {
    throw new Error("UI_TEST_OLLAMA_MODEL env var overrides config — unset it to change model in UI");
  }

  const text = fs.readFileSync(UI_TEST_CONFIG_PATH, "utf8");
  const updated = text.replace(/(^\s*model:\s*)["']?[^"'\n#]+["']?/m, `$1"${model}"`);
  if (updated === text) {
    throw new Error("Could not find ollama.model in ui_test/config.yaml");
  }
  fs.writeFileSync(UI_TEST_CONFIG_PATH, updated, "utf8");
}

function parsePullProgress(line: string): number | null {
  const pctMatch = line.match(/(\d+(?:\.\d+)?)\s*%/);
  if (pctMatch) return Math.min(100, Math.round(Number(pctMatch[1])));
  if (/pulling manifest|verifying|writing manifest/i.test(line)) return 5;
  if (/downloading/i.test(line)) return 15;
  return null;
}

export function pullOllamaModel(
  model: string,
  onProgress?: (progress: OllamaSwitchProgress) => void,
): Promise<void> {
  const allowed = OLLAMA_MODEL_OPTIONS.some((opt) => opt.id === model);
  if (!allowed) {
    return Promise.reject(new Error(`Unknown model: ${model}`));
  }

  const exe = ollamaExe();
  if (!exe) {
    return Promise.reject(new Error("Ollama CLI not found — install from https://ollama.com"));
  }

  return new Promise((resolve, reject) => {
    const child = spawn(exe, ["pull", model], {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stderr = "";
    const handleLine = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const progress = parsePullProgress(trimmed);
      onProgress?.({
        step: "downloading",
        message: trimmed,
        progress: progress ?? undefined,
        toModel: model,
      });
    };
    const onChunk = (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      for (const line of text.split(/\r?\n/)) handleLine(line);
    };
    child.stdout?.on("data", onChunk);
    child.stderr?.on("data", onChunk);
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        onProgress?.({
          step: "done",
          message: `${model} downloaded`,
          progress: 100,
          toModel: model,
        });
        resolve();
        return;
      }
      reject(new Error(`ollama pull ${model} failed: ${stderr.trim() || `exit ${code}`}`));
    });
  });
}

export function buildOllamaModelCatalog(availableModels: string[]) {
  return OLLAMA_MODEL_OPTIONS.map((opt) => ({
    ...opt,
    installed: isModelAvailable(opt.id, availableModels),
  }));
}

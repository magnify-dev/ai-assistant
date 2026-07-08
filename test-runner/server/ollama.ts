import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./python-runner.js";

export type OllamaConfig = {
  url: string;
  model: string;
};

export function readOllamaConfig(): OllamaConfig {
  const url = process.env.UI_TEST_OLLAMA_URL || "http://127.0.0.1:11434";
  const envModel = process.env.UI_TEST_OLLAMA_MODEL;
  if (envModel) {
    return { url, model: envModel };
  }

  const cfgPath = path.join(REPO_ROOT, "ui_test", "config.yaml");
  try {
    const text = fs.readFileSync(cfgPath, "utf8");
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
    const baseName = cfg.model.split(":", 1)[0];
    const modelAvailable =
      availableModels.includes(cfg.model) ||
      availableModels.some((name) => name.startsWith(`${baseName}:`));
    const modelLoaded =
      loadedModels.includes(cfg.model) ||
      loadedModels.some((name) => name.startsWith(`${baseName}:`));
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

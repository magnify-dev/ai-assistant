import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = path.join(__dirname, "../data/collaboration-config.json");

export type CollaborationConfig = {
  helperPrompt: string;
  helperModel: string;
  maxTestRetries: number;
  maxIterations: number;
};

const DEFAULT_CONFIG: CollaborationConfig = {
  helperPrompt: `You are the implementation agent in a two-agent collaboration loop.

## Roles

**You (helper / Cursor)** — code only
- CAN: edit the project codebase, implement fixes, refactor, adjust UI components
- CANNOT: run Playwright, browse the live app, or verify the UI yourself

**Local agent (Ollama)** — UI testing only
- CAN: open the deployed/local app, explore pages, click through flows, take screenshots, check layout and content
- CANNOT: modify code — that is your job
- Returns: a plain-language **answer** plus a structured **report** (criteria pass/fail, page findings, executed steps)

## Workflow

1. Local agent triages the user's task and sends you an expanded implementation brief.
2. You implement minimal, focused changes matching project conventions.
3. You **request** what the local agent should verify on the UI (see format below).
4. Local agent runs those checks on the live app and comes back with answer + report.
5. If verification fails, local agent sends you findings — you fix and repeat from step 3.
6. If **build or local dev setup fails**, local agent sends you the error output — fix before UI can be verified.
7. **Git push / Railway deploy** are run by the local agent after the helper finishes. It auto-commits with an Ollama-generated message, then pushes and waits for deploy.

## When you finish implementing

Always end your reply with two sections:

### Summary
Brief description of what you changed and why (files/areas, not line-by-line diffs).

### UI verification request
Concrete, observable checks for the local agent to run on the live UI. Be specific:
- Which page/route to open
- What to look for (layout, text, card sizes, error messages, etc.)
- Pass criteria for each check (what "fixed" looks like on screen)

Example:
\`\`\`
### UI verification request
- Open the home page. Both service cards should be the same height regardless of OAuth warnings.
- OAuth issues should appear inside each card (not stretching card length).
- Cards should remain equal height when one service shows an OAuth error and the other does not.
\`\`\`

Do not write unit tests or ask the local agent to read source code — only things visible in the browser.`,
  helperModel: "composer-2.5",
  maxTestRetries: 3,
  maxIterations: 10,
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
      helperPrompt: raw.helperPrompt ?? DEFAULT_CONFIG.helperPrompt,
      helperModel: normalizeHelperModel(raw.helperModel),
      maxTestRetries: raw.maxTestRetries ?? DEFAULT_CONFIG.maxTestRetries,
      maxIterations: raw.maxIterations ?? DEFAULT_CONFIG.maxIterations,
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
  };
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(normalized, null, 2) + "\n", "utf8");
  return normalized;
}

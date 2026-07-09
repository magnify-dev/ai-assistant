/**
 * Central prompt registry loader.
 *
 * All LLM prompts live in prompts.yaml at the repo root. Code fetches them by
 * dotted key and substitutes {{name}} placeholders — never hardcode prompt text.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROMPTS_PATH = path.join(__dirname, "../../prompts.yaml");

let registry: Record<string, unknown> | null = null;

function loadRegistry(): Record<string, unknown> {
  if (registry) return registry;
  const raw = fs.readFileSync(PROMPTS_PATH, "utf8");
  const parsed = parseYaml(raw) as Record<string, unknown>;
  if (!parsed || typeof parsed !== "object") {
    throw new Error(`prompts.yaml did not parse to a mapping: ${PROMPTS_PATH}`);
  }
  registry = parsed;
  return registry;
}

/** Fetch a prompt by dotted key, e.g. "collaboration.helper_system". */
export function getPrompt(key: string): string {
  let node: unknown = loadRegistry();
  for (const part of key.split(".")) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      throw new Error(`Prompt '${key}' not found in ${PROMPTS_PATH}`);
    }
    node = (node as Record<string, unknown>)[part];
  }
  if (typeof node !== "string") {
    throw new Error(`Prompt '${key}' is not a string in ${PROMPTS_PATH}`);
  }
  return node.trim();
}

/** Fetch a prompt and substitute {{name}} placeholders. */
export function renderPrompt(key: string, variables: Record<string, string>): string {
  return getPrompt(key).replace(/\{\{(\w+)\}\}/g, (_match, name: string) => {
    if (!(name in variables)) {
      throw new Error(`Prompt '${key}' placeholder '{{${name}}}' has no value`);
    }
    return variables[name];
  });
}

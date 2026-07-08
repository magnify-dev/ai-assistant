import fs from "node:fs";
import path from "node:path";

function parseEnvFile(content: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of content.split("\n")) {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) continue;
    const eq = stripped.indexOf("=");
    if (eq <= 0) continue;
    const key = stripped.slice(0, eq).trim();
    let value = stripped.slice(eq + 1).trim();
    if (value.length >= 2 && value[0] === value.at(-1) && (value[0] === '"' || value[0] === "'")) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }
  return result;
}

function parseCheatsheetEnvFiles(cheatsheet: string): string[] {
  const files: string[] = [];
  const block = cheatsheet.match(/env_files:\s*\n((?:\s+-\s+.+\n?)+)/);
  if (block) {
    for (const line of block[1].split("\n")) {
      const m = line.match(/^\s+-\s+(.+)$/);
      if (m) files.push(m[1].trim());
    }
  }
  const single = cheatsheet.match(/^\s*env_file:\s*(.+)$/m);
  if (single) files.unshift(single[1].trim());
  if (!files.length) {
    files.push(".agent/.env", ".agent/.env.local");
  }
  return [...new Set(files)];
}

function parseRequiredEnv(cheatsheet: string): string[] {
  const block = cheatsheet.match(/required_env:\s*\n((?:\s+-\s+.+\n?)+)/);
  if (block) {
    const keys: string[] = [];
    for (const line of block[1].split("\n")) {
      const m = line.match(/^\s+-\s+(.+)$/);
      if (m) keys.push(m[1].trim());
    }
    if (keys.length) return keys;
  }
  return ["DATABASE_URL"];
}

export function readLocalEnvStatus(projectPath: string) {
  const resolved = path.resolve(projectPath);
  const agentDir = path.join(resolved, ".agent");
  const cheatsheetPath = path.join(agentDir, "cheatsheet.yaml");
  const cheatsheet = fs.existsSync(cheatsheetPath) ? fs.readFileSync(cheatsheetPath, "utf8") : "";
  const envFiles = parseCheatsheetEnvFiles(cheatsheet);
  const required = parseRequiredEnv(cheatsheet);

  const merged: Record<string, string> = {};
  const fileStatus: { path: string; exists: boolean }[] = [];
  for (const rel of envFiles) {
    const filePath = path.isAbsolute(rel) ? rel : path.join(resolved, rel);
    fileStatus.push({ path: rel, exists: fs.existsSync(filePath) });
    if (fs.existsSync(filePath)) {
      Object.assign(merged, parseEnvFile(fs.readFileSync(filePath, "utf8")));
    }
  }

  const missing = required.filter((key) => !String(merged[key] ?? "").trim());
  const envExample = path.join(agentDir, ".env.example");
  const envLocal = path.join(agentDir, ".env.local");

  return {
    ready: missing.length === 0,
    missing,
    required,
    env_files: fileStatus,
    has_env_example: fs.existsSync(envExample),
    has_env_local: fs.existsSync(envLocal),
    env_example_path: ".agent/.env.example",
    env_local_path: ".agent/.env.local",
    local_base_url: cheatsheet.match(/^\s*base_url:\s*(.+)$/m)?.[1]?.trim() ?? "",
  };
}

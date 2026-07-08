import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";

/** Must run before any `@cursor/sdk` import — seeds CURSOR_RIPGREP_PATH for local agents. */
export function bootstrapCursorSdk(): void {
  const existing = process.env.CURSOR_RIPGREP_PATH?.trim();
  if (existing && path.isAbsolute(existing) && fs.existsSync(existing)) return;

  for (const candidate of ripgrepCandidates()) {
    if (fs.existsSync(candidate)) {
      process.env.CURSOR_RIPGREP_PATH = candidate;
      return;
    }
  }
}

function ripgrepCandidates(): string[] {
  const exe = process.platform === "win32" ? "rg.exe" : "rg";
  const out: string[] = [];

  if (process.env.LOCALAPPDATA) {
    out.push(
      path.join(
        process.env.LOCALAPPDATA,
        "Programs",
        "cursor",
        "resources",
        "app",
        "node_modules",
        "@vscode",
        "ripgrep",
        "bin",
        exe,
      ),
    );
  }

  if (process.env.ProgramFiles) {
    out.push(
      path.join(
        process.env.ProgramFiles,
        "cursor",
        "resources",
        "app",
        "node_modules",
        "@vscode",
        "ripgrep",
        "bin",
        exe,
      ),
    );
    out.push(
      path.join(
        process.env.ProgramFiles,
        "Microsoft VS Code",
        "resources",
        "app",
        "node_modules",
        "@vscode",
        "ripgrep",
        "bin",
        exe,
      ),
    );
  }

  if (process.platform === "darwin") {
    out.push(
      "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    );
    out.push(
      "/Applications/Visual Studio Code.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    );
  }

  try {
    const found = execSync(process.platform === "win32" ? "where rg" : "which rg", {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    })
      .trim()
      .split(/\r?\n/)[0]
      ?.trim();
    if (found) out.push(found);
  } catch {
    /* rg not on PATH */
  }

  return out;
}

bootstrapCursorSdk();

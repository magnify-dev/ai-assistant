import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import type { CursorRuntime } from "./cursor-agent.js";

export type CursorPreflight = {
  ok: boolean;
  runtime: CursorRuntime;
  errors: string[];
  warnings: string[];
  cursorInstalled: boolean;
  cursorRunning: boolean;
  hasApiKey: boolean;
  projectPathOk: boolean;
};

function cursorInstallPaths(): string[] {
  const out: string[] = [];
  if (process.env.LOCALAPPDATA) {
    out.push(path.join(process.env.LOCALAPPDATA, "Programs", "cursor", "Cursor.exe"));
  }
  if (process.env.ProgramFiles) {
    out.push(path.join(process.env.ProgramFiles, "cursor", "Cursor.exe"));
  }
  if (process.platform === "darwin") {
    out.push("/Applications/Cursor.app/Contents/MacOS/Cursor");
  }
  return out;
}

function isCursorInstalled(): boolean {
  return cursorInstallPaths().some((p) => fs.existsSync(p));
}

function isCursorProcessRunning(): boolean {
  try {
    if (process.platform === "win32") {
      const out = execSync('tasklist /FI "IMAGENAME eq Cursor.exe" /NH', {
        encoding: "utf8",
        windowsHide: true,
        stdio: ["ignore", "pipe", "ignore"],
      });
      return /cursor\.exe/i.test(out);
    }
    if (process.platform === "darwin") {
      const out = execSync("pgrep -x Cursor", {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      return Boolean(out.trim());
    }
    const out = execSync("pgrep -f cursor", {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return Boolean(out.trim());
  } catch {
    return false;
  }
}

export function preflightCursorHelper(
  runtime: CursorRuntime,
  apiKey: string | undefined,
  projectPath: string,
): CursorPreflight {
  const errors: string[] = [];
  const warnings: string[] = [];
  const hasApiKey = Boolean(apiKey?.trim());
  const projectPathOk = Boolean(projectPath.trim()) && fs.existsSync(projectPath);
  const cursorInstalled = isCursorInstalled();
  const cursorRunning = isCursorProcessRunning();

  if (!hasApiKey) {
    errors.push(
      "CURSOR_API_KEY is not set in ai-assistant/.env — required for local helper too (create at cursor.com/dashboard/integrations)",
    );
  }

  if (!projectPathOk) {
    errors.push(`Helper project path does not exist: ${projectPath || "(empty)"}`);
  }

  if (runtime === "local") {
    if (!cursorInstalled) {
      errors.push(
        "Cursor desktop app not found — install Cursor from cursor.com. Local helper uses the SDK bridge inside the Cursor app.",
      );
    } else if (!cursorRunning) {
      errors.push(
        "Cursor desktop app is not running — open Cursor on this machine before starting a run with Local helper runtime.",
      );
    }

    const rg = process.env.CURSOR_RIPGREP_PATH?.trim();
    if (!rg || !fs.existsSync(rg)) {
      warnings.push(
        "Cursor ripgrep binary not detected — open Cursor once so the SDK can find bundled tools.",
      );
    }
  }

  if (runtime === "cloud" && !errors.length) {
    // cloud-specific checks handled by resolveCursorRuntime (repo URL)
  }

  return {
    ok: errors.length === 0,
    runtime,
    errors,
    warnings,
    cursorInstalled,
    cursorRunning,
    hasApiKey,
    projectPathOk,
  };
}

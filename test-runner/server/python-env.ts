import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(__dirname, "../..");

export function resolvePythonExecutable(): string {
  const fromEnv = process.env.UI_TEST_PYTHON?.trim();
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;

  const venvPython = path.join(REPO_ROOT, "voice", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPython)) return venvPython;

  return process.platform === "win32" ? "python" : "python3";
}

export function verifyWebSurfDeps(python = resolvePythonExecutable()): string | null {
  const result = spawnSync(
    python,
    ["-c", "import trafilatura, ddgs, httpx, yaml; from playwright.sync_api import sync_playwright"],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: REPO_ROOT,
      },
      encoding: "utf8",
      windowsHide: true,
    },
  );

  if (result.status === 0) return null;

  const detail = (result.stderr || result.stdout || "").trim();
  return (
    detail ||
    `Web research Python dependencies are missing in ${python}. ` +
      `Run: "${python}" -m pip install -r web_surf/requirements.txt`
  );
}

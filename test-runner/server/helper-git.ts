import { execSync } from "node:child_process";

export function isGitRepo(projectPath: string): boolean {
  try {
    execSync("git rev-parse --is-inside-work-tree", {
      cwd: projectPath,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return true;
  } catch {
    return false;
  }
}

/** Full porcelain output — compare before/after helper run. */
export function gitWorktreeSnapshot(projectPath: string): string {
  if (!isGitRepo(projectPath)) return "";
  try {
    return execSync("git status --porcelain", {
      cwd: projectPath,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

export function gitWorktreeChanged(projectPath: string, before: string): boolean {
  if (!isGitRepo(projectPath)) return true;
  const after = gitWorktreeSnapshot(projectPath);
  return after !== before;
}

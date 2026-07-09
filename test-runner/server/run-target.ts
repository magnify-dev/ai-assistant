export type TestTargetMode = "local" | "deployed";
export type CursorRuntime = "local" | "cloud";

/** Deployed = git push + Railway wait. Local = neither. */
export function resolveRunTargetOptions(testTarget?: string) {
  const mode: TestTargetMode = testTarget === "deployed" ? "deployed" : "local";
  return {
    testTarget: mode,
    push: mode === "deployed",
    skipDeploy: mode === "local",
  };
}

/**
 * Cloud runtime needs a repo URL. Fall back to local when cloud is selected without one
 * so the helper agent can still start against the project path on this machine.
 */
export function resolveCursorRuntime(
  cursorRuntime?: string,
  repoUrl?: string,
): { runtime: CursorRuntime; repoUrl?: string; fallbackReason?: string } {
  const wantsCloud = cursorRuntime === "cloud";
  const trimmedRepo = repoUrl?.trim();
  if (wantsCloud && trimmedRepo) {
    return { runtime: "cloud", repoUrl: trimmedRepo };
  }
  if (wantsCloud && !trimmedRepo) {
    return {
      runtime: "local",
      fallbackReason: "Cloud runtime requires a GitHub repo URL — using local runtime instead",
    };
  }
  return { runtime: "local" };
}

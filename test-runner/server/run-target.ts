export type TestTargetMode = "local" | "deployed";

/** Deployed = git push + Railway wait. Local = neither. */
export function resolveRunTargetOptions(testTarget?: string) {
  const mode: TestTargetMode = testTarget === "deployed" ? "deployed" : "local";
  return {
    testTarget: mode,
    push: mode === "deployed",
    skipDeploy: mode === "local",
  };
}

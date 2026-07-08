export type TestTargetMode = "local" | "deployed";

/** Deployed = git push + Railway wait. Local = neither. */
export function runTargetOptions(testTarget: TestTargetMode) {
  return {
    testTarget,
    push: testTarget === "deployed",
    skipDeploy: testTarget === "local",
  };
}

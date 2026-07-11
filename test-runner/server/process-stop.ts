import { spawn, type ChildProcess } from "node:child_process";
import process from "node:process";

/** Try a graceful stop first so Python finally blocks can persist session artifacts. */
export function stopChildProcess(proc: ChildProcess, graceMs = 3500): void {
  const pid = proc.pid;
  if (!pid) {
    try {
      proc.kill();
    } catch {
      /* ignore */
    }
    return;
  }

  if (process.platform === "win32") {
    spawn("taskkill", ["/T", "/PID", String(pid)], { windowsHide: true, stdio: "ignore" });
    setTimeout(() => {
      spawn("taskkill", ["/T", "/F", "/PID", String(pid)], { windowsHide: true, stdio: "ignore" });
    }, graceMs);
    return;
  }

  try {
    proc.kill("SIGTERM");
  } catch {
    try {
      proc.kill();
    } catch {
      /* ignore */
    }
  }
  setTimeout(() => {
    try {
      proc.kill("SIGKILL");
    } catch {
      /* ignore */
    }
  }, graceMs);
}

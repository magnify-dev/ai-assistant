import "../server/sdk-bootstrap.js";
import { Agent, CursorAgentError } from "@cursor/sdk";
import dotenv from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");

dotenv.config({ path: path.join(REPO_ROOT, ".env") });
dotenv.config({ path: path.join(REPO_ROOT, "test-runner", ".env") });

const key = process.env.CURSOR_API_KEY;
console.log("hasKey", Boolean(key));

async function main() {
  if (!key) {
    console.error("CURSOR_API_KEY missing");
    process.exit(1);
  }

  const cwd = path.resolve(REPO_ROOT, "../content-manager");
  console.log("cwd", cwd);

  try {
    await using agent = await Agent.create({
      apiKey: key,
      model: { id: "composer-2.5" },
      local: { cwd, settingSources: [] },
    });
    console.log("agentId", agent.agentId);
    const run = await agent.send("Reply with exactly: ok");
    console.log("runId", run.id);
    for await (const ev of run.stream()) {
      console.log("stream", ev.type);
    }
    const result = await run.wait();
    console.log("result", result.status, result.result?.slice(0, 80));
  } catch (err) {
    if (err instanceof CursorAgentError) {
      console.error("CursorAgentError", err.message, "retryable=", err.isRetryable);
    } else {
      console.error("ERR", err);
    }
    process.exit(2);
  }
}

void main();

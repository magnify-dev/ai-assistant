import "../server/sdk-bootstrap.js";
import dotenv from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CursorRunner } from "../server/cursor-agent.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
dotenv.config({ path: path.join(REPO_ROOT, ".env") });

const runner = new CursorRunner();
runner.on("event", (e) => console.log("event", e.type, e.status ?? "", e.activity ?? e.message ?? ""));

const result = await runner.run({
  prompt: "Reply with exactly: ok",
  cwd: path.resolve(REPO_ROOT, "../content-manager"),
  runtime: "local",
  apiKey: process.env.CURSOR_API_KEY!,
  modelId: "composer-2.5",
});

console.log("result", result);

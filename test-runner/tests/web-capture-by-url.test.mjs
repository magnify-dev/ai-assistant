import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

function normalizeCaptureUrlKey(url) {
  const raw = String(url ?? "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    parsed.hash = "";
    if (parsed.pathname.length > 1 && parsed.pathname.endsWith("/")) {
      parsed.pathname = parsed.pathname.slice(0, -1);
    }
    return parsed.toString();
  } catch {
    return raw.split("#")[0]?.replace(/\/$/, "") || raw;
  }
}

/** Mirror of the run-inspect catalog rule: every raw capture, not only latest.json. */
function loadCapturesByUrlFromRaw(rawDir, latestPath) {
  const byUrl = {};
  for (const name of fs.readdirSync(rawDir)) {
    if (!name.endsWith(".json")) continue;
    const capture = JSON.parse(fs.readFileSync(path.join(rawDir, name), "utf8"));
    const key = normalizeCaptureUrlKey(capture.url);
    if (!key || !Array.isArray(capture.elements) || capture.elements.length === 0) continue;
    byUrl[key] = capture;
  }
  if (fs.existsSync(latestPath)) {
    const latest = JSON.parse(fs.readFileSync(latestPath, "utf8"));
    const key = normalizeCaptureUrlKey(latest.url);
    if (key) byUrl[key] = latest;
  }
  return byUrl;
}

test("inspecting a run keeps every page map from raw/, not only latest.json", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "web-capture-by-url-"));
  const captureDir = path.join(root, "web-capture");
  const rawDir = path.join(captureDir, "raw");
  fs.mkdirSync(rawDir, { recursive: true });

  const first = {
    capture_id: "cap_first",
    created_at: "2026-07-19T10:00:00Z",
    url: "https://news.example/list",
    elements: [{ id: "a", kind: "link", text: "Story A", rect: { x: 0, y: 0, width: 10, height: 10 } }],
  };
  const second = {
    capture_id: "cap_second",
    created_at: "2026-07-19T10:05:00Z",
    url: "https://news.example/story/1",
    elements: [{ id: "b", kind: "link", text: "Story B", rect: { x: 0, y: 0, width: 10, height: 10 } }],
  };
  fs.writeFileSync(path.join(rawDir, "cap_first.json"), JSON.stringify(first));
  fs.writeFileSync(path.join(rawDir, "cap_second.json"), JSON.stringify(second));
  // latest.json only points at the second page — the old inspect-run bug.
  fs.writeFileSync(path.join(captureDir, "latest.json"), JSON.stringify(second));

  const byUrl = loadCapturesByUrlFromRaw(rawDir, path.join(captureDir, "latest.json"));
  assert.equal(Object.keys(byUrl).length, 2);
  assert.equal(byUrl["https://news.example/list"].capture_id, "cap_first");
  assert.equal(byUrl["https://news.example/story/1"].capture_id, "cap_second");

  fs.rmSync(root, { recursive: true, force: true });
});

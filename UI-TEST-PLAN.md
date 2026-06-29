# UI Test Loop — Implementation Plan

Track what is built vs still missing. **Engine** = Python `ui_test/` (runs without UI). **UI** = `test-runner/` (pnpm, React like admin).

---

## Phase 1 — Core engine ✅

Python CLI: Railway, Playwright, structure scan, Ollama task structuring, `REPORT.md` in target project. Auto-updates target `.gitignore` and scaffolds `ui-test/` on each run.

```powershell
.\run-ui-test.ps1 -Project C:\path\to\content-manager
```

---

## Phase 2 — Test Runner UI (admin-like stack) ✅

| Item | Status |
|------|--------|
| `test-runner/` pnpm package (Vite + React + Tailwind 4) | Done |
| Express API `:8767` + Vite dev `:5175` | Done |
| Phase stepper (local agent + Cursor agent) | Done |
| SSE live log + strict/fuzzy step lines | Done |
| Python `--emit-events` NDJSON protocol | Done |
| Engine runs standalone (no UI required) | Done |
| `run-test-runner.ps1` | Done |
| Jarvis panel → Open Test Runner | Done |

---

## Phase 3 — Cursor SDK ✅

| Item | Status |
|------|--------|
| `@cursor/sdk` in test-runner server | Done |
| Cloud runtime → **Cursor Agents sidebar** (IDE visibility) | Done |
| Local runtime → SDK bridge + stream to UI | Done |
| Full loop: local test → auto Cursor prompt from REPORT.md | Done |
| `CURSOR_API_KEY` in `ai-assistant/.env` | User setup |

---

## Phase 4 — Pending

| Item | Status |
|------|--------|
| FoxMCP fuzzy fallback | Pending |
| Jarvis voice “run tests” | Pending |
| Railway webhooks | Pending |
| `data-testid` in content-manager app | Pending (Cursor task) |
| Production `pnpm build` + single-port serve | Partial (`pnpm start` serves built UI) |

---

## How to start

### Test Runner UI (recommended)

```powershell
cd C:\Users\marce\Documents\Programming\ai-assistant
.\run-test-runner.ps1
```

Opens **http://127.0.0.1:5175** (UI) + API on **8767**.

Or: Jarvis Control Panel → **Open Test Runner**

### Engine only (no UI)

```powershell
.\run-ui-test.ps1 -Project C:\Users\marce\Documents\Programming\content-manager
```

### Secrets (`ai-assistant/.env`, gitignored)

```env
CURSOR_API_KEY=cursor_...
```

Cloud agents also need **Git repo URL** in the UI (connected to Cursor/GitHub).

---

## Cursor IDE visibility

| Runtime | Where you see the agent |
|---------|-------------------------|
| **Cloud** | Cursor desktop → **Agents** sidebar (same as cloud agents started in IDE) |
| **Local** | Stream in Test Runner UI; uses SDK bridge on your machine |

For prompts/responses in the **actual Cursor UI**, use **Cloud** runtime and watch the Agents panel.

---

## Update log

| Date | Change |
|------|--------|
| 2026-06-29 | Phase 1–3 initial |
| 2026-06-29 | Replaced FastAPI HTML UI with pnpm test-runner + Cursor SDK |

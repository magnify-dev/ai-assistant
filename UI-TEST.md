# UI Test Loop

Local agent UI testing: **Railway deploy → structure check → Playwright → REPORT.md** on the target project.

See **[UI-TEST-PLAN.md](UI-TEST-PLAN.md)** for implementation status (done vs pending).

## Quick start

### Test Runner UI (React + step visibility + Cursor SDK)

```powershell
cd C:\Users\marce\Documents\Programming\ai-assistant
.\run-test-runner.ps1
```

Open **http://127.0.0.1:5175**. Add `CURSOR_API_KEY` to `ai-assistant/.env` for Cursor handoff.

### Engine only (no UI)

```powershell
.\run-ui-test.ps1
```

## Target project setup

In **content-manager** (or any app repo):

```
ui-test/railway.yaml    # URLs + Railway IDs
ui-test/.env            # RAILWAY_TOKEN, login (gitignored)
ui-test/specs/*.yaml    # URL tree
ui-test/tasks/current.txt
.agent/current/REPORT.md  # generated after each run
```

## Cursor handoff

Open Cursor on the **target project** and paste:

```
Read .agent/current/REPORT.md and implement the fixes described there.
```

Re-run `.\run-ui-test.ps1` after changes deploy to Railway.

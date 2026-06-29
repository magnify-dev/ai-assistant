# Dev Loop (Phase 1)

This folder holds **generated reports** from the local dev loop. Do not edit these by hand — re-run `run-dev-loop.ps1` instead.

## Layout

```
.agent/
  current/
    REPORT.md        ← point Cursor at this file
    task.json        ← structured data (tests + Ollama analysis)
    test-output.txt  ← raw test output
    status.json      ← ready_for_cursor | ...
  history/
    YYYYMMDDTHHMMSSZ/  ← archived previous runs
```

## Workflow

1. Make code changes (or start from failing tests).
2. Run from repo root:
   ```powershell
   .\run-dev-loop.ps1
   ```
3. In **Cursor Agent**, paste:
   ```
   Read .agent/current/REPORT.md and implement the fixes described there.
   ```
4. After Cursor edits, run step 2 again to verify.

## Try the demo

```powershell
.\run-dev-loop.ps1 -Demo
```

Uses `dev_loop/demo/` — a tiny failing test Ollama can analyze.

## Options

```powershell
.\run-dev-loop.ps1 -Project C:\path\to\repo
.\run-dev-loop.ps1 -TestCmd "pytest -q tests/"
.\run-dev-loop.ps1 -Note "Focus on auth redirect only"
.\run-dev-loop.ps1 -SkipTests -Note "Review my uncommitted changes"
.\run-dev-loop.ps1 -NoOllama   # raw test output only, no Ollama
```

See [DEV-LOOP.md](../DEV-LOOP.md) for full details.

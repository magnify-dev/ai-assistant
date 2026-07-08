from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ui_test.project_paths import (
    AGENT_DIR,
    ENV_EXAMPLE,
    SPECS_DIR,
    TASKS_DIR,
    agent_dir,
    migrate_legacy_ui_test,
)

MARKER_START = "# UI test loop (ai-assistant)"
MARKER_END = "# end ui test loop"

GITIGNORE_LINES = (
    f"{AGENT_DIR}/",
    f"!{AGENT_DIR}/.env.example",
    f"!{AGENT_DIR}/README.md",
)

ENV_EXAMPLE_CONTENT = """# Assistant-only secrets (never commit .env)
#
# App runtime vars (DATABASE_URL, PORT, VITE_*) live in micro-services/admin/.env
# — copy micro-services/admin/.env.example → micro-services/admin/.env

# --- UI test login (must match a user in the DB) ---
UI_TEST_EMAIL=
UI_TEST_PASSWORD=

# --- Railway (optional — for deploy-wait runs) ---
RAILWAY_TOKEN=
"""

AGENT_README = """# AI assistant (this project)

Everything the [ai-assistant](https://github.com/) `ui_test` engine needs lives in this folder.

## Layout

| Path | Purpose |
|------|---------|
| `cheatsheet.yaml` | How to run locally before Railway deploy |
| `exploration.yaml` | Navigation tree + page catalog (how to move, what is where) |
| `cheatsheet.yaml` | Local dev, deploy, and run settings (not page exploration) |
| `profile.json` | Saved project settings |
| `railway.yaml` | Railway service URLs and IDs |
| `specs/*.yaml` | State-based UI traversal trees |
| `tasks/current.txt` | Free-text task for each run |
| `.env` | Secrets (gitignored) |
| `current/REPORT.md` | Latest run report for Cursor |
| `current/RUN-LOG.txt` | Live run log |
| `history/` | Archived past runs |

Run from ai-assistant:

```powershell
.\\run-test-runner.ps1
# or
.\\run-ui-test.ps1 -Project {project_path}
```
"""


@dataclass(frozen=True)
class ProjectSetupResult:
    gitignore_updated: bool
    created_paths: tuple[str, ...]
    migrated_paths: tuple[str, ...]


def _gitignore_block() -> str:
    lines = [MARKER_START, *GITIGNORE_LINES, MARKER_END, ""]
    return "\n".join(lines)


def _block_present(text: str) -> bool:
    return MARKER_START in text and MARKER_END in text


def _replace_block(text: str, block: str) -> str:
    start = text.index(MARKER_START)
    end = text.index(MARKER_END) + len(MARKER_END)
    return text[:start] + block.rstrip() + text[end:]


def ensure_gitignore(project: Path) -> bool:
    """Ensure target project .gitignore contains `.agent/` entries."""
    gitignore_path = project / ".gitignore"
    block = _gitignore_block()

    if gitignore_path.is_file():
        text = gitignore_path.read_text(encoding="utf-8")
        if _block_present(text):
            new_block = block.rstrip()
            existing_start = text.index(MARKER_START)
            existing_end = text.index(MARKER_END) + len(MARKER_END)
            existing_section = text[existing_start:existing_end]
            if existing_section == new_block:
                return False
            updated = _replace_block(text, block)
        else:
            suffix = "" if text.endswith("\n") else "\n"
            updated = text + suffix + "\n" + block
    else:
        updated = block

    gitignore_path.write_text(updated, encoding="utf-8")
    return True


def ensure_agent_tree(project: Path) -> list[str]:
    """Create `.agent/` scaffold if missing."""
    created: list[str] = []
    root = agent_dir(project)
    dirs = [
        root,
        root / SPECS_DIR,
        root / TASKS_DIR,
        root / "current",
        root / "history",
    ]
    for directory in dirs:
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(str(directory.relative_to(project)))

    example_env = root / ENV_EXAMPLE
    if not example_env.is_file():
        example_env.write_text(ENV_EXAMPLE_CONTENT, encoding="utf-8")
        created.append(str(example_env.relative_to(project)))

    readme = root / "README.md"
    if not readme.is_file():
        readme.write_text(AGENT_README.format(project_path=project), encoding="utf-8")
        created.append(str(readme.relative_to(project)))

    tasks_file = root / TASKS_DIR / "current.txt"
    if not tasks_file.is_file():
        tasks_file.write_text("Describe what you want verified on the deployed app.\n", encoding="utf-8")
        created.append(str(tasks_file.relative_to(project)))

    return created


def ensure_project_setup(project: Path) -> ProjectSetupResult:
    """Migrate legacy ui-test/, update .gitignore, scaffold `.agent/`."""
    migrated = migrate_legacy_ui_test(project)
    gitignore_updated = ensure_gitignore(project)
    created = ensure_agent_tree(project)
    return ProjectSetupResult(
        gitignore_updated=gitignore_updated,
        created_paths=tuple(created),
        migrated_paths=tuple(migrated),
    )

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitContext:
    is_repo: bool
    branch: str
    status: str
    diff_stat: str
    diff: str


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def collect_git_context(project: Path, *, max_diff_chars: int) -> GitContext:
    inside = _git(project, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        return GitContext(False, "", "", "", "")

    branch = _git(project, "branch", "--show-current").stdout.strip()
    status = _git(project, "status", "--short").stdout.strip()
    diff_stat = _git(project, "diff", "--stat").stdout.strip()
    diff = _git(project, "diff").stdout.strip()
    if len(diff) > max_diff_chars:
        diff = diff[: max_diff_chars - 80] + "\n\n... diff truncated ..."

    return GitContext(True, branch, status, diff_stat, diff)

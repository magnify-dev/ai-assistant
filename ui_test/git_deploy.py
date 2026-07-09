from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitDeployState:
    is_repo: bool
    branch: str
    status: str
    has_uncommitted: bool
    unpushed_commits: int
    changed_files: list[str]
    push_attempted: bool
    push_ok: bool
    push_message: str
    push_sent_commits: bool = False


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def collect_git_deploy_state(project: Path, *, do_push: bool = False) -> GitDeployState:
    inside = _git(project, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        return GitDeployState(
            is_repo=False,
            branch="",
            status="",
            has_uncommitted=False,
            unpushed_commits=0,
            changed_files=[],
            push_attempted=False,
            push_ok=False,
            push_message="Not a git repository",
        )

    branch = _git(project, "branch", "--show-current").stdout.strip()
    status = _git(project, "status", "--short").stdout.strip()
    has_uncommitted = bool(status)

    upstream = _git(project, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    unpushed = 0
    if upstream.returncode == 0:
        count = _git(project, "rev-list", "--count", f"{upstream.stdout.strip()}..HEAD")
        if count.returncode == 0 and count.stdout.strip().isdigit():
            unpushed = int(count.stdout.strip())

    diff_names = _git(project, "diff", "--name-only", "HEAD")
    status_names = _git(project, "diff", "--name-only")
    changed = sorted(
        {
            line.strip()
            for line in (diff_names.stdout + "\n" + status_names.stdout).splitlines()
            if line.strip()
        }
    )

    push_attempted = False
    push_ok = False
    push_message = ""
    push_sent_commits = False
    if do_push:
        push_attempted = True
        if has_uncommitted:
            push_ok = False
            push_message = "Cannot push: uncommitted changes present"
        else:
            unpushed_at_push = unpushed
            proc = _git(project, "push")
            push_ok = proc.returncode == 0
            push_message = (proc.stderr or proc.stdout or "").strip() or (
                "Push succeeded" if push_ok else "Push failed"
            )
            if push_ok and unpushed_at_push > 0:
                push_sent_commits = True
            elif push_ok:
                combined = push_message.lower()
                push_sent_commits = "everything up-to-date" not in combined and "up to date" not in combined

    return GitDeployState(
        is_repo=True,
        branch=branch,
        status=status,
        has_uncommitted=has_uncommitted,
        unpushed_commits=unpushed,
        changed_files=changed,
        push_attempted=push_attempted,
        push_ok=push_ok,
        push_message=push_message,
        push_sent_commits=push_sent_commits,
    )

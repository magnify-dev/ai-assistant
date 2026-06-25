"""Jarvis tools - git_ops.py"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from jarvis_tools.definitions import BLOCKED_GIT_ARGS
from jarvis_tools.constants import GH_EXE_CANDIDATES, GIT_EXE_CANDIDATES, GITHUB_ORG
from jarvis_tools.paths import _first_existing, _resolve_git_project


def _shell_env() -> dict[str, str]:
    env = os.environ.copy()
    extra = r"C:\Program Files\Git\cmd;C:\Program Files\GitHub CLI"
    path_key = "Path" if "Path" in env else "PATH"
    path = env.get(path_key, "")
    if "Git\\cmd" not in path:
        env[path_key] = path + ";" + extra
    return env


def _resolve_command(args: list[str]) -> list[str]:
    if not args:
        return args
    if args[0].lower() == "git":
        git_exe = _first_existing(GIT_EXE_CANDIDATES)
        if git_exe:
            return [git_exe, *args[1:]]
    if args[0].lower() == "gh":
        gh_exe = _first_existing(GH_EXE_CANDIDATES)
        if gh_exe:
            return [gh_exe, *args[1:]]
    return args

def _run_cmd(
    args: list[str],
    cwd: Path,
    timeout: int = 120,
) -> tuple[int, str]:
    joined = " ".join(args).lower()
    for blocked in BLOCKED_GIT_ARGS:
        if blocked in joined:
            return 1, f"Blocked dangerous git command: {blocked}"

    proc = subprocess.run(
        _resolve_command(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
        env=_shell_env(),
    )
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return proc.returncode, out or "(no output)"

def _git_status(project_path: str) -> str:
    root = _resolve_git_project(project_path)
    if not (root / ".git").exists():
        return f"Not a git repo yet: {root}"

    parts: list[str] = [f"Project: {root}"]
    code, branch = _run_cmd(["git", "branch", "--show-current"], root, timeout=30)
    if code == 0:
        parts.append(f"Branch: {branch}")
    else:
        parts.append(f"Branch check failed: {branch}")

    code, remote = _run_cmd(["git", "remote", "get-url", "origin"], root, timeout=30)
    if code == 0:
        parts.append(f"Remote: {remote}")
    else:
        parts.append(f"Remote check failed: {remote}")

    code, status = _run_cmd(["git", "status", "--short"], root, timeout=30)
    if code == 0:
        parts.append("Changes:\n" + (status if status else "(clean working tree)"))
    else:
        parts.append(f"Status check failed: {status}")

    return "\n".join(parts)

def _github_publish_project(
    project_path: str,
    repo_name: str,
    commit_message: str,
    visibility: str = "public",
    org: str | None = None,
) -> str:
    root = _resolve_git_project(project_path)
    if not root.is_dir():
        return f"Not a directory: {root}"

    org_name = (org or GITHUB_ORG).strip()
    repo_name = repo_name.strip().lower().replace(" ", "-")
    vis_flag = "--private" if visibility == "private" else "--public"
    full_name = f"{org_name}/{repo_name}"
    repo_url = f"https://github.com/{full_name}"

    if not (root / ".git").exists():
        code, out = _run_cmd(["git", "init", "-b", "main"], root)
        if code != 0:
            return f"git init failed:\n{out}"

    code, out = _run_cmd(["git", "add", "-A"], root)
    if code != 0:
        return f"git add failed:\n{out}"

    code, status = _run_cmd(["git", "status", "--porcelain"], root)
    if code != 0:
        return f"git status failed:\n{status}"

    if status.strip():
        code, out = _run_cmd(["git", "commit", "-m", commit_message], root)
        if code != 0:
            return f"git commit failed:\n{out}"

    code, remote = _run_cmd(["git", "remote", "get-url", "origin"], root)
    if code != 0:
        code, out = _run_cmd(
            [
                "gh",
                "repo",
                "create",
                full_name,
                vis_flag,
                "--source=.",
                "--remote=origin",
                "--push",
            ],
            root,
            timeout=180,
        )
        if code != 0:
            return f"github create/push failed:\n{out}"
        return f"Created and pushed {repo_url}"

    code, out = _run_cmd(["git", "push", "-u", "origin", "HEAD"], root, timeout=180)
    if code != 0:
        return f"git push failed:\n{out}"

    return f"Pushed latest code to {repo_url}"

def _git_command(project_path: str, args: list[str]) -> str:
    if not args:
        return "No git arguments provided"
    if any(not isinstance(arg, str) or not arg.strip() for arg in args):
        return "Git args must be non-empty strings"
    root = _resolve_git_project(project_path)
    code, out = _run_cmd(["git", *args], root, timeout=180)
    if code != 0:
        return f"Exit {code}\n{out}"
    return out


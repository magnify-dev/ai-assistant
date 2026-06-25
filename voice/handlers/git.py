from __future__ import annotations

import re
from pathlib import Path

from audit import audit_event
from tools import execute_tool
from voice_context import _last_git_project, _remember_git_project
def _project_for_voice_request(text: str, cfg: dict) -> str | None:
    lowered = text.lower()
    active_window = execute_tool("get_active_window", {}).lower()
    known_paths = cfg.get("tools", {}).get("known_paths") or {}

    for name, value in known_paths.items():
        path_name = Path(value).name.lower()
        if name.lower() in lowered or name.lower() in active_window or path_name in active_window:
            return value

    for root in cfg.get("tools", {}).get("git_roots") or []:
        path = Path(root)
        if path.name.lower() in lowered or path.name.lower() in active_window:
            return str(path)

    if "cursor" in lowered or "current" in lowered or "this" in lowered:
        for root in cfg.get("tools", {}).get("git_roots") or []:
            path = Path(root)
            if (path / ".git").exists():
                return str(path)

    if _last_git_project:
        return _last_git_project

    roots = [Path(root) for root in cfg.get("tools", {}).get("git_roots") or []]
    git_roots = [path for path in roots if (path / ".git").exists()]
    if len(git_roots) == 1:
        return str(git_roots[0])

    return None


def _changed_file_count(git_status_text: str) -> int:
    if "(clean working tree)" in git_status_text:
        return 0
    lines = git_status_text.splitlines()
    try:
        start = lines.index("Changes:") + 1
    except ValueError:
        return 0
    return sum(1 for line in lines[start:] if line.strip())


def _explicit_git_commit_message(text: str) -> str | None:
    lowered = text.lower()
    for marker in ("message is", "message:", "commit message is", "commit message"):
        idx = lowered.find(marker)
        if idx != -1:
            candidate = text[idx + len(marker) :].strip(" .:\"'")
            if candidate:
                return candidate[:120]
    return None


def _auto_git_commit_message(staged_files_text: str) -> str:
    files = [
        line.strip().replace("\\", "/")
        for line in staged_files_text.splitlines()
        if line.strip() and "(no output)" not in line
    ]
    file_set = set(files)

    if not files:
        return "Update local assistant changes"

    docs = [path for path in files if path.lower().endswith((".md", ".txt"))]
    voice = [path for path in files if path.startswith("voice/")]
    ui = [
        path
        for path in files
        if path.endswith("control_panel.py")
        or path.endswith("preview_voices.py")
        or path.endswith("jarvis-ui.bat")
    ]
    config = [path for path in files if path.endswith((".yaml", ".yml", ".json"))]
    tools = [path for path in files if path.endswith("tools.py")]
    assistant = [path for path in files if path.endswith("assistant.py")]

    if ui:
        return "Add Jarvis control panel and voice preview"
    if assistant and tools:
        return "Improve Jarvis voice assistant tooling"
    if assistant:
        return "Improve Jarvis voice command handling"
    if tools:
        return "Improve Jarvis tool execution"
    if voice and config:
        return "Update Jarvis voice configuration"
    if len(docs) == len(file_set):
        return "Update documentation"
    if voice:
        return "Update Jarvis voice assistant"
    return "Update local assistant changes"


def _wants_git_commit(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\b(commit|committing)\b", lowered))


def _wants_git_sync(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\b(sync|push|upload)\b", lowered))


def _git_stageable_paths(status_text: str) -> list[str]:
    ignored_prefixes = (
        "logs/",
        "voice/.assistant.lock",
    )
    ignored_suffixes = (
        ".pyc",
    )
    ignored_parts = (
        "/__pycache__/",
        "__pycache__/",
    )
    paths: list[str] = []

    for line in status_text.splitlines():
        if not line.strip() or line.strip() == "(no output)":
            continue
        if len(line) < 4:
            continue

        status_code = line[:2]
        raw_path = line[2:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        path = raw_path.strip('"').replace("\\", "/")

        if status_code == "!!":
            continue
        if path.startswith(ignored_prefixes):
            continue
        if path.endswith(ignored_suffixes):
            continue
        if any(part in path for part in ignored_parts):
            continue
        paths.append(path)

    return paths


def _run_git_tool(
    cfg: dict,
    command_id: str | None,
    project: str,
    args: list[str],
) -> str:
    result = execute_tool("git_command", {"project_path": project, "args": args})
    audit_event(
        cfg,
        "tool_result",
        command_id=command_id,
        name="git_command",
        arguments={"project_path": project, "args": args},
        result=result,
    )
    return result


def _git_commit_with_retries(
    text: str,
    cfg: dict,
    command_id: str | None,
    project: str,
) -> str:
    attempts = max(1, int(cfg.get("tools", {}).get("git_commit_attempts", 3)))
    explicit_message = _explicit_git_commit_message(text)
    last_error = ""

    for attempt in range(1, attempts + 1):
        audit_event(
            cfg,
            "git_commit_attempt",
            command_id=command_id,
            project=project,
            attempt=attempt,
            max_attempts=attempts,
        )

        status = _run_git_tool(cfg, command_id, project, ["status", "--short"])
        if status.startswith("Exit "):
            last_error = f"status failed: {status[:300]}"
            continue
        if "(no output)" in status:
            return "There are no uncommitted Git changes to commit."

        stageable_paths = _git_stageable_paths(status)
        audit_event(
            cfg,
            "git_stageable_paths",
            command_id=command_id,
            project=project,
            paths=stageable_paths,
        )
        if not stageable_paths:
            return "I only found ignored/generated files, so I did not commit."

        add_result = _run_git_tool(cfg, command_id, project, ["add", "-A", "--", *stageable_paths])
        if add_result.startswith("Exit "):
            last_error = f"stage failed: {add_result[:300]}"
            continue

        staged = _run_git_tool(cfg, command_id, project, ["diff", "--cached", "--name-only"])
        if staged.startswith("Exit "):
            last_error = f"staged check failed: {staged[:300]}"
            continue
        if "(no output)" in staged:
            last_error = "only ignored/generated files were found"
            break

        commit_message = explicit_message or _auto_git_commit_message(staged)
        audit_event(
            cfg,
            "git_commit_message_generated",
            command_id=command_id,
            explicit=bool(explicit_message),
            staged_files=staged,
            commit_message=commit_message,
        )
        commit = _run_git_tool(cfg, command_id, project, ["commit", "-m", commit_message])
        if commit.startswith("Exit "):
            last_error = f"commit failed: {commit[:300]}"
            if "nothing to commit" in commit.lower():
                break
            continue

        verify = _run_git_tool(cfg, command_id, project, ["status", "--short"])
        sync_result = _git_sync_with_retries(cfg, command_id, project)
        audit_event(
            cfg,
            "git_commit_success",
            command_id=command_id,
            project=project,
            attempt=attempt,
            commit_message=commit_message,
            verify_status=verify,
            sync_result=sync_result,
        )
        if sync_result.startswith("Synced"):
            return "Committed and synced."
        return f"Committed, but sync failed. {sync_result}"

    audit_event(
        cfg,
        "git_commit_failed",
        command_id=command_id,
        project=project,
        attempts=attempts,
        error=last_error,
    )
    return f"I couldn't commit after {attempts} attempts. {last_error}"


def _git_sync_with_retries(cfg: dict, command_id: str | None, project: str) -> str:
    attempts = max(1, int(cfg.get("tools", {}).get("git_sync_attempts", 3)))
    last_error = ""

    branch = _run_git_tool(cfg, command_id, project, ["branch", "--show-current"])
    if branch.startswith("Exit ") or not branch.strip() or "(no output)" in branch:
        branch = "HEAD"
    else:
        branch = branch.strip().splitlines()[0]

    upstream = _run_git_tool(cfg, command_id, project, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    has_upstream = not upstream.startswith("Exit ") and "(no output)" not in upstream

    for attempt in range(1, attempts + 1):
        audit_event(
            cfg,
            "git_sync_attempt",
            command_id=command_id,
            project=project,
            attempt=attempt,
            max_attempts=attempts,
            branch=branch,
            has_upstream=has_upstream,
        )

        args = ["push"] if has_upstream else ["push", "-u", "origin", branch]
        push = _run_git_tool(cfg, command_id, project, args)
        if push.startswith("Exit "):
            last_error = push[:300]
            if "no upstream branch" in push.lower():
                has_upstream = False
                continue
            if "fetch first" in push.lower() or "non-fast-forward" in push.lower():
                fetch = _run_git_tool(cfg, command_id, project, ["fetch", "origin"])
                last_error = f"push rejected; fetch result: {fetch[:220]}"
                continue
            continue

        audit_event(
            cfg,
            "git_sync_success",
            command_id=command_id,
            project=project,
            attempt=attempt,
            result=push,
        )
        return "Synced to GitHub."

    audit_event(
        cfg,
        "git_sync_failed",
        command_id=command_id,
        project=project,
        attempts=attempts,
        error=last_error,
    )
    return f"I couldn't sync after {attempts} attempts. {last_error}"



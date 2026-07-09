from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from ui_test.prompts import get_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoCommitResult:
    attempted: bool
    ok: bool
    subject: str
    error: str


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def fallback_commit_message(task_text: str, changed_files: list[str]) -> str:
    line = task_text.strip().split("\n")[0].strip()
    if line:
        subject = line[:72]
        if not subject.lower().startswith(("fix", "feat", "chore", "refactor", "style", "docs")):
            subject = f"fix: {subject}"[:72]
        return subject
    if changed_files:
        name = Path(changed_files[0]).name
        return f"chore: update {name}"
    return "chore: apply agent changes"


def _normalize_commit_message(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```[\w]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip().strip('"').strip("'")
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return ""
    subject = lines[0][:72]
    if len(lines) == 1:
        return subject
    body = "\n".join(lines[1:4])
    return f"{subject}\n\n{body}".strip()


def generate_commit_message_with_ollama(
    *,
    task_text: str,
    changed_files: list[str],
    status: str,
    ollama_url: str,
    model: str,
    timeout_sec: float = 60,
) -> str:
    files_block = "\n".join(f"- {f}" for f in changed_files[:30]) or "(see git status)"
    user = (
        f"User task:\n{task_text.strip() or '(UI fix from collaboration loop)'}\n\n"
        f"Changed files:\n{files_block}\n\n"
        f"Git status:\n{status[:1500] or '(none)'}"
    )
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(
                f"{ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": get_prompt("git.commit")},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content") or ""
        normalized = _normalize_commit_message(content)
        if normalized:
            return normalized
    except Exception as exc:
        logger.warning("Ollama commit message generation failed: %s", exc)
    return fallback_commit_message(task_text, changed_files)


def commit_all_changes(project: Path, message: str) -> tuple[bool, str]:
    if not message.strip():
        return False, "Empty commit message"

    add = _git(project, "add", "-A")
    if add.returncode != 0:
        return False, (add.stderr or add.stdout or "git add failed").strip()

    diff = _git(project, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return False, "Nothing to commit after staging"

    commit = _git(project, "commit", "-m", message.strip())
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout or "git commit failed").strip()
        return False, detail
    return True, (commit.stdout or "Committed").strip()


def auto_commit_if_needed(
    project: Path,
    *,
    task_text: str,
    changed_files: list[str],
    status: str,
    ollama_url: str = "",
    ollama_model: str = "",
    timeout_sec: float = 60,
) -> AutoCommitResult:
    if ollama_url and ollama_model:
        message = generate_commit_message_with_ollama(
            task_text=task_text,
            changed_files=changed_files,
            status=status,
            ollama_url=ollama_url,
            model=ollama_model,
            timeout_sec=timeout_sec,
        )
    else:
        message = fallback_commit_message(task_text, changed_files)

    ok, detail = commit_all_changes(project, message)
    subject = message.splitlines()[0].strip() if message else ""
    return AutoCommitResult(
        attempted=True,
        ok=ok,
        subject=subject,
        error="" if ok else detail,
    )

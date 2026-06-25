from __future__ import annotations

import re

from audit import audit_event
from handlers.git import (
    _changed_file_count,
    _git_commit_with_retries,
    _git_sync_with_retries,
    _project_for_voice_request,
    _wants_git_commit,
    _wants_git_sync,
)
from tools import execute_tool
from voice_context import _last_action, _remember_action, _remember_git_project
def _handle_local_command(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    lowered = text.lower()
    git_words = ("git", "uncommitted", "committed", "commit", "status", "changes", "sync", "push", "upload")
    if not any(word in lowered for word in git_words):
        return None
    if "commitment" in lowered:
        return None

    project = _project_for_voice_request(text, cfg)
    if not project:
        return "I need the project name or folder path before I can check Git."

    _remember_git_project("git_status", {"project_path": project})
    audit_event(cfg, "local_command", command_id=command_id, kind="git", project=project, text=text)
    status = execute_tool("git_status", {"project_path": project})
    audit_event(cfg, "tool_result", command_id=command_id, name="git_status", result=status)
    wants_commit = _wants_git_commit(text)
    wants_sync = _wants_git_sync(text)
    if wants_sync and not wants_commit:
        return _git_sync_with_retries(cfg, command_id, project)
    if not wants_commit:
        if "Status check failed:" in status:
            return "I couldn't read Git status."
        count = _changed_file_count(status)
        if count == 0:
            return "Git is clean."
        return f"I found {count} changed file{'s' if count != 1 else ''}. Say 'commit please' to commit."

    return _git_commit_with_retries(text, cfg, command_id, project)


def _handle_context_action(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    lowered = text.lower().strip(" .!?")
    if not lowered:
        return None
    git_words = ("git", "uncommitted", "committed", "commit", "sync", "push", "upload")
    if any(word in lowered for word in git_words):
        return None
    if re.match(r"^(what|which|where|who|when|how|is|are|am|do|does|did|has|have)\b", lowered):
        return None
    actionish = bool(
        re.search(r"\b(open|go|navigate|show|take|click|press|select|choose|play|start|launch|switch|focus)\b", lowered)
        or re.search(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\b.*\b(song|track|video|result|item)\b", lowered)
    )
    if not actionish:
        return None

    audit_event(cfg, "tool_call", command_id=command_id, name="act_on_context", arguments={"command": text})
    result = execute_tool("act_on_context", {"command": text})
    audit_event(cfg, "tool_result", command_id=command_id, name="act_on_context", result=result)
    if result == "OK":
        return "Done."
    if "couldn't confidently map" in result.lower():
        return None
    return result


def _is_followup_check(text: str) -> bool:
    lowered = text.lower().strip(" .!?")
    return lowered in {
        "did you check",
        "did you do it",
        "did it work",
        "what happened",
        "did anything happen",
    } or lowered.startswith(("did you check ", "did you do "))


def _handle_followup_check(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    if not _is_followup_check(text):
        return None

    tools = [str(tool) for tool in _last_action.get("tools", [])]
    actually_did_work = bool(_last_action.get("actually_did_work"))
    previous = str(_last_action.get("text", ""))

    audit_event(
        cfg,
        "followup_check",
        command_id=command_id,
        text=text,
        previous_command=previous,
        previous_tools=tools,
        previous_did_work=actually_did_work,
    )

    if tools or actually_did_work:
        return "Yes, I ran the check."
    if previous:
        return "No, I didn't run a tool for that yet."
    return "No, I don't have a previous action to check."


def _claims_action_without_tool(text: str) -> bool:
    lowered = text.lower()
    claim_phrases = (
        "i checked",
        "i tried",
        "i navigated",
        "navigated to",
        "using cursor",
        "using cursor's",
        "i opened",
        "i searched",
        "i found",
        "i clicked",
        "i ran",
        "i used",
        "done",
        "got it",
    )
    return any(phrase in lowered for phrase in claim_phrases)


def _sanitize_for_speech(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "Done."

    if stripped.startswith("{") or stripped.startswith("```"):
        return "I tried to run that, but need another moment. Please ask again."

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if any(line.startswith(("-", "*", "•")) for line in lines) or len(lines) > 3:
        return lines[0][:180].rstrip(".") + "."

    sentence_parts = stripped.replace("\n", " ").split(". ")
    brief = ". ".join(sentence_parts[:2]).strip()
    if len(brief) > 220:
        brief = brief[:220].rsplit(" ", 1)[0]
    return brief.rstrip() + ("." if brief and brief[-1].isalnum() else "")



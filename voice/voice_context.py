from __future__ import annotations

_last_git_project: str | None = None
_last_action: dict[str, object] = {
    "text": "",
    "reply": "",
    "tools": [],
    "source": "",
    "actually_did_work": False,
}


def _remember_action(text: str, reply: str, source: str, tools: list[str] | None = None) -> None:
    _last_action.update(
        {
            "text": text,
            "reply": reply,
            "source": source,
            "tools": tools or [],
            "actually_did_work": bool(tools) or source == "local",
        }
    )


def _remember_git_project(tool_name: str, args: dict) -> None:
    global _last_git_project
    if tool_name in {"git_status", "git_command", "github_publish_project"}:
        project = args.get("project_path")
        if isinstance(project, str) and project.strip():
            _last_git_project = project.strip()



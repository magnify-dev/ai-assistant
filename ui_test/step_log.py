from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SelectorMode(str, Enum):
    STRICT = "strict"
    FUZZY = "fuzzy"


@dataclass
class StepLogEntry:
    timestamp: str
    mode: SelectorMode
    ephemeral: bool
    page_url: str
    action: str
    target: str
    ok: bool
    message: str

    def format_line(self) -> str:
        tag = self.mode.value
        if self.ephemeral:
            tag += ", ephemeral"
        status = "✓" if self.ok else "✗"
        return f"[{tag}] {self.page_url} → {self.action} {self.target} {status} {self.message}".strip()


@dataclass
class StepLogger:
    entries: list[StepLogEntry] = field(default_factory=list)
    enabled: bool = True

    def log(
        self,
        *,
        mode: SelectorMode,
        ephemeral: bool,
        page_url: str,
        action: str,
        target: str,
        ok: bool,
        message: str = "",
    ) -> None:
        entry = StepLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode,
            ephemeral=ephemeral,
            page_url=page_url,
            action=action,
            target=target,
            ok=ok,
            message=message,
        )
        self.entries.append(entry)
        if self.enabled:
            try:
                from ui_test.events import step_event

                step_event(
                    mode=mode.value,
                    ephemeral=ephemeral,
                    page_url=page_url,
                    action=action,
                    target=target,
                    ok=ok,
                    message=message,
                )
            except ImportError:
                pass

    def lines(self) -> list[str]:
        return [entry.format_line() for entry in self.entries]

    def summary(self) -> dict[str, int]:
        strict = sum(1 for e in self.entries if e.mode == SelectorMode.STRICT)
        fuzzy = sum(1 for e in self.entries if e.mode == SelectorMode.FUZZY)
        passed = sum(1 for e in self.entries if e.ok)
        failed = sum(1 for e in self.entries if not e.ok)
        return {
            "strict_steps": strict,
            "fuzzy_steps": fuzzy,
            "passed": passed,
            "failed": failed,
            "total": len(self.entries),
        }


def parse_mode(step: dict[str, Any], default: str = "strict") -> SelectorMode:
    raw = str(step.get("mode") or default).strip().lower()
    if raw == "fuzzy":
        return SelectorMode.FUZZY
    return SelectorMode.STRICT


def collect_test_ids(spec: dict[str, Any]) -> set[str]:
    ids: set[str] = set()

    def walk_steps(steps: list[Any]) -> None:
        for step in steps:
            if not isinstance(step, dict):
                continue
            target = step.get("target")
            if isinstance(target, dict):
                tid = target.get("test_id")
                if tid:
                    ids.add(str(tid))
            expect = step.get("expect")
            if isinstance(expect, dict):
                for key in ("test_id_visible", "dialog_visible", "element_visible"):
                    val = expect.get(key)
                    if isinstance(val, str) and val:
                        ids.add(val)
            if step.get("test_id"):
                ids.add(str(step["test_id"]))

    auth = spec.get("auth")
    if isinstance(auth, dict):
        walk_steps(auth.get("steps") or [])

    for node in spec.get("tree") or []:
        if not isinstance(node, dict):
            continue
        if node.get("test_id"):
            ids.add(str(node["test_id"]))
        walk_steps(node.get("interactions") or [])

    return ids


def substitute_env(text: str, env: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return env.get(match.group(1), match.group(0))

    return re.sub(r"\$\{([^}]+)\}", repl, text)

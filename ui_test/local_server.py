from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from ui_test.env_loader import _parse_env_file
from ui_test.local_env import (
    cheatsheet_env_files,
    cheatsheet_required_env,
    format_local_env_hint,
    load_merged_local_env,
)
from ui_test.env_loader import require_keys
from ui_test.project_paths import agent_dir
from ui_test.project_profile import load_cheatsheet

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LogFn = Callable[[str], None]


@dataclass(frozen=True)
class LocalRunConfig:
    base_url: str
    setup_commands: tuple[str, ...]
    start_commands: tuple[str, ...]
    start_cwd: str
    health_url: str
    env_files: tuple[str, ...]
    required_env: tuple[str, ...]
    startup_timeout_sec: float
    auto_start: bool
    fallback_to_deployed: bool


@dataclass
class LocalServerResult:
    ok: bool
    message: str
    started_by_us: bool = False
    already_running: bool = False
    used_fallback: bool = False
    fallback_url: str = ""


@dataclass
class _ManagedProcess:
    command: str
    proc: subprocess.Popen[str]
    log_path: Path


def local_run_config(project: Path) -> LocalRunConfig | None:
    sheet = load_cheatsheet(project)
    local = sheet.get("local") if isinstance(sheet.get("local"), dict) else {}
    base_url = str(local.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return None
    health = str(local.get("health_url") or base_url).strip().rstrip("/")

    setup_commands: list[str] = []
    raw_setup = local.get("setup_commands")
    if isinstance(raw_setup, list):
        setup_commands = [str(c).strip() for c in raw_setup if str(c).strip()]

    start_commands: list[str] = []
    raw_list = local.get("start_commands")
    if isinstance(raw_list, list):
        start_commands = [str(c).strip() for c in raw_list if str(c).strip()]
    single = str(local.get("start_command") or "").strip()
    if single:
        start_commands.insert(0, single)

    env_files = list(cheatsheet_env_files(project))
    required_env = list(cheatsheet_required_env(project))

    deploy = sheet.get("deploy") if isinstance(sheet.get("deploy"), dict) else {}
    fallback = local.get("fallback_to_deployed")
    if fallback is None:
        fallback = deploy.get("fallback_to_deployed", True)

    return LocalRunConfig(
        base_url=base_url,
        setup_commands=tuple(dict.fromkeys(setup_commands)),
        start_commands=tuple(dict.fromkeys(start_commands)),
        start_cwd=str(local.get("start_cwd") or ".").strip() or ".",
        health_url=health,
        env_files=tuple(env_files),
        required_env=tuple(required_env),
        startup_timeout_sec=float(local.get("startup_timeout_sec") or 120),
        auto_start=local.get("auto_start", True) is not False,
        fallback_to_deployed=bool(fallback),
    )


def url_is_up(url: str, *, timeout_sec: float = 3.0) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            response = client.get(url)
        return True, f"HTTP {response.status_code}"
    except httpx.ConnectError:
        return False, "connection refused"
    except Exception as exc:
        return False, str(exc)


def _load_merged_env(project: Path, env_files: tuple[str, ...]) -> dict[str, str]:
    if env_files == cheatsheet_env_files(project):
        return load_merged_local_env(project)
    merged: dict[str, str] = {}
    for rel in env_files:
        path = project / rel if not Path(rel).is_absolute() else Path(rel)
        merged.update(_parse_env_file(path))
    return merged


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _tail_file(path: Path, limit: int = 800) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:].strip()
    except OSError:
        return ""


def _log_indicates_failure(path: Path) -> str | None:
    text = _ANSI_ESCAPE.sub("", _tail_file(path, 3000))
    if not text:
        return None
    for marker in (
        "DATABASE_URL is required",
        "Environment validation failed",
        "Cannot find module",
        "EADDRINUSE",
        "Error: Cannot find",
        "ERR!",
        "ELIFECYCLE",
        "Command failed",
    ):
        if marker in text:
            return text[-400:]
    if "throw new Error" in text and "is required" in text:
        return text[-400:]
    return None


def _run_setup(command: str, *, cwd: Path, env: dict[str, str], on_log: LogFn) -> tuple[bool, str]:
    on_log(f"Setup: {command}")
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        shell=True,
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        tail = output.strip()[-500:] or f"exit code {completed.returncode}"
        return False, tail
    return True, "ok"


def _stream_log_to_file(proc: subprocess.Popen[str], log_path: Path) -> None:
    if not proc.stdout:
        return
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            for line in proc.stdout:
                handle.write(line)
    except OSError:
        pass


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class LocalServerSession:
    def __init__(self, project: Path, config: LocalRunConfig, *, on_log: LogFn | None = None) -> None:
        self.project = project.resolve()
        self.config = config
        self.on_log = on_log or (lambda _msg: None)
        self._managed: list[_ManagedProcess] = []
        self._log_dir = agent_dir(self.project) / "current" / "local-server-logs"
        self.started_by_us = False
        self.result = LocalServerResult(ok=False, message="not started")

    @property
    def start_command(self) -> str:
        parts = list(self.config.setup_commands) + list(self.config.start_commands)
        return " && ".join(parts)

    def _workdir(self) -> Path:
        if self.config.start_cwd and self.config.start_cwd != ".":
            return (self.project / self.config.start_cwd).resolve()
        return self.project

    def ensure(self) -> LocalServerResult:
        check_url = self.config.health_url or self.config.base_url
        up, msg = url_is_up(check_url)
        if up:
            self.on_log(f"Local server already running at {check_url} ({msg})")
            self.result = LocalServerResult(
                ok=True,
                message=f"already running ({msg})",
                already_running=True,
            )
            return self.result

        if not self.config.auto_start:
            self.result = LocalServerResult(
                ok=False,
                message=f"Local server not running at {check_url} and auto_start is disabled",
            )
            return self.result

        if not self.config.start_commands:
            self.result = LocalServerResult(
                ok=False,
                message=f"No start_commands in cheatsheet — cannot start {check_url}",
            )
            return self.result

        cwd = self._workdir()
        merged_env = load_merged_local_env(self.project)
        missing = require_keys(merged_env, list(self.config.required_env))
        if missing:
            hint = format_local_env_hint(self.project, missing)
            self.on_log(hint)
            self.result = LocalServerResult(ok=False, message=hint)
            return self.result

        env = {**os.environ, **merged_env}
        self._log_dir.mkdir(parents=True, exist_ok=True)

        for command in self.config.setup_commands:
            ok, detail = _run_setup(command, cwd=cwd, env=env, on_log=self.on_log)
            if not ok:
                self.result = LocalServerResult(
                    ok=False,
                    message=f"Setup failed ({command}): {detail}",
                )
                return self.result

        for index, command in enumerate(self.config.start_commands):
            log_path = self._log_dir / f"proc-{index}.log"
            log_path.write_text(f"# {command}\n", encoding="utf-8")
            self.on_log(f"Starting: {command} (cwd={cwd})")
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(
                target=_stream_log_to_file,
                args=(proc, log_path),
                daemon=True,
            ).start()
            self._managed.append(_ManagedProcess(command=command, proc=proc, log_path=log_path))
        self.started_by_us = True

        deadline = time.time() + self.config.startup_timeout_sec
        last_msg = msg
        while time.time() < deadline:
            for item in self._managed:
                failure = _log_indicates_failure(item.log_path)
                if failure:
                    self.result = LocalServerResult(
                        ok=False,
                        message=f"Startup error ({item.command}): {failure}",
                        started_by_us=True,
                    )
                    self.stop()
                    return self.result
                if item.proc.poll() is not None:
                    tail = _ANSI_ESCAPE.sub("", _tail_file(item.log_path))
                    self.result = LocalServerResult(
                        ok=False,
                        message=f"Process exited ({item.command}): {tail or 'no output'}",
                        started_by_us=True,
                    )
                    self.stop()
                    return self.result

            up, last_msg = url_is_up(check_url)
            if up:
                self.on_log(f"Local server ready at {check_url} ({last_msg})")
                self.result = LocalServerResult(
                    ok=True,
                    message=f"started ({last_msg})",
                    started_by_us=True,
                )
                return self.result
            time.sleep(0.5)

        tails = " | ".join(_tail_file(item.log_path, 200) for item in self._managed)
        self.result = LocalServerResult(
            ok=False,
            message=f"Timed out after {self.config.startup_timeout_sec}s waiting for {check_url}. {last_msg}. {tails}",
            started_by_us=True,
        )
        self.stop()
        return self.result

    def stop(self) -> None:
        if self._managed and self.started_by_us:
            self.on_log("Stopping local server(s) started by test runner")
            for item in self._managed:
                _stop_process(item.proc)
            self._managed = []
            self.started_by_us = False

    def __enter__(self) -> LocalServerSession:
        self.ensure()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


def ensure_local_server(
    project: Path,
    *,
    on_log: LogFn | None = None,
) -> LocalServerSession | None:
    config = local_run_config(project)
    if not config:
        return None
    return LocalServerSession(project, config, on_log=on_log)

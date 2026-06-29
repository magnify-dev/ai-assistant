from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestRunResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    skipped: bool = False

    @property
    def combined_output(self) -> str:
        parts: list[str] = []
        if self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.stderr.strip():
            parts.append("--- stderr ---")
            parts.append(self.stderr.rstrip())
        return "\n".join(parts).strip()

    @property
    def passed(self) -> bool:
        return not self.skipped and self.exit_code == 0


def _pytest_cmd(*args: str) -> str:
    parts = [sys.executable, "-m", "pytest", *args]
    return subprocess.list2cmdline(parts)


def detect_test_command(project: Path) -> str | None:
    try:
        import pytest  # noqa: F401
    except ImportError:
        return None

    if (
        (project / "pyproject.toml").exists()
        or (project / "pytest.ini").exists()
        or (project / "tests").is_dir()
        or list(project.glob("test_*.py"))
        or list(project.glob("**/test_*.py"))
    ):
        return _pytest_cmd("-q")
    if (project / "package.json").exists() and shutil.which("npm"):
        return "npm test"
    return None


def run_tests(project: Path, test_cmd: str | None) -> TestRunResult:
    if not test_cmd:
        return TestRunResult(
            command="",
            exit_code=0,
            stdout="",
            stderr="",
            skipped=True,
        )

    completed = subprocess.run(
        test_cmd,
        cwd=project,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return TestRunResult(
        command=test_cmd,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )

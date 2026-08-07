from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class CLIResult:
    exit_code: int
    stdout: str
    stderr: str

class CLITimeout(Exception):
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        super().__init__(f"CLI timed out after {timeout} seconds")

class Runner(Protocol):
    def run(self, argv: list[str], timeout: int) -> CLIResult: ...

class SubprocessRunner:
    """Run an argv directly, never through a shell, and kill it on timeout."""
    def run(self, argv: list[str], timeout: int) -> CLIResult:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise CLITimeout(timeout) from exc
        return CLIResult(process.returncode, stdout, stderr)

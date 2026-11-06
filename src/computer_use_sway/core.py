"""Errors, results, subprocess execution and session-environment recovery.

Why one bottom module: every expected failure must surface as a ToolError with
a clear message (never a raw OSError), and MCP hosts often launch the server
with a stripped environment that has to be rebuilt before anything else runs.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


COMMAND_NAME = "computer-use-sway"
COMMAND_ENV = "COMPUTER_USE_SWAY_COMMAND"


DEFAULT_TIMEOUT = 5.0
TEXT_LIMIT = 10_000
CLIPBOARD_LIMIT = 100_000


class ToolError(Exception):
    """Expected tool failure reported as MCP tool content.

    `code` is an optional machine-readable reason (``stale_ref``,
    ``session_required``, ...); the message stays human-readable.
    """

    def __init__(self, message: str = "", code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Result:
    """Tool content plus curated, timeline-safe metadata for the stream."""

    content: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


def text_result(value: Any, meta: dict[str, Any] | None = None) -> Result:
    return Result(json_text(value), dict(meta or {}))


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_command(
    args: list[str],
    timeout: float = DEFAULT_TIMEOUT,
    input_text: str | None = None,
    input_bytes: bytes | None = None,
    binary: bool = False,
    capture: bool = True,
) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            input=input_bytes if input_bytes is not None else input_text,
            text=(input_bytes is None and not binary),
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {timeout:.1f}s: {args[0]}") from exc

    stdout = completed.stdout if completed.stdout is not None else b""
    stderr = completed.stderr if completed.stderr is not None else b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")

    if completed.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-500:].strip()
        detail = f": {tail}" if tail else ""
        raise ToolError(f"command failed ({completed.returncode}): {args[0]}{detail}")

    return CommandResult(stdout=stdout, stderr=stderr, returncode=completed.returncode)


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def require_binaries(names: list[str]) -> None:
    missing = [name for name in names if not command_available(name)]
    if missing:
        raise ToolError(f"missing required command(s): {', '.join(missing)}")


def command_argv() -> list[str]:
    configured = os.environ.get(COMMAND_ENV)
    if configured:
        parts = shlex.split(configured)
        if not parts:
            raise ToolError(f"{COMMAND_ENV} is set but empty")
        return parts

    invoked = Path(sys.argv[0])
    if invoked.name == COMMAND_NAME:
        if invoked.exists():
            return [str(invoked.resolve())]
        found = shutil.which(sys.argv[0])
        if found:
            return [found]

    installed = shutil.which(COMMAND_NAME)
    if installed:
        return [installed]

    return [sys.executable, "-m", "computer_use_sway"]


def newest_socket(pattern: str) -> str | None:
    candidates = [Path(path) for path in glob.glob(pattern) if Path(path).is_socket()]
    if not candidates:
        return None
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def ensure_session_environment() -> None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        candidate = f"/run/user/{os.getuid()}"
        if Path(candidate).is_dir():
            runtime_dir = candidate
            os.environ["XDG_RUNTIME_DIR"] = candidate

    if runtime_dir and not os.environ.get("SWAYSOCK"):
        socket = newest_socket(f"{runtime_dir}/sway-ipc.{os.getuid()}.*.sock")
        if socket:
            os.environ["SWAYSOCK"] = socket

    if runtime_dir and not os.environ.get("WAYLAND_DISPLAY"):
        displays = sorted(Path(runtime_dir).glob("wayland-*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for display in displays:
            if display.is_socket():
                os.environ["WAYLAND_DISPLAY"] = display.name
                break


def require_session() -> None:
    ensure_session_environment()
    missing = [
        name
        for name in ("SWAYSOCK", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
        if not os.environ.get(name)
    ]
    if missing:
        raise ToolError(
            "not running inside a usable Sway session; missing "
            + ", ".join(missing)
        )
    from . import swayipc

    swayipc.connect().outputs()


def strict_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be an integer")
    return value


def strict_number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ToolError(f"{name} must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        raise ToolError(f"{name} must be at most {maximum:g}")
    return number


def json_text(value: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(value, indent=2, sort_keys=True)}]

"""The agent seat's anchor: a detached process that keeps one idle virtual
pointer and keyboard on the agent seat for the life of the sway session.

Why: WezTerm binds a single keyboard and pointer across all seats and drops
them when ANY seat loses that capability (window/src/os/wayland/seat.rs,
`remove_capability`). Without an anchor, the agent seat lost its devices every
time the MCP server exited, and the user's WezTerm windows stopped accepting
the user's own keyboard (measured 3/3, FINDINGS I7). The anchor never sends an
event; it only keeps the seat's capabilities constant. After a sway `reload`
(which moves virtual devices to seat0) it puts its devices back.

Run with `python -m computer_use_sway.anchor`; one instance per runtime
directory, guarded by an flock on `anchor.pid`. It exits with sway.
"""

from __future__ import annotations

import fcntl
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import core, install, swayipc

PID_NAME = "anchor.pid"


def pid_path() -> Path:
    return install.runtime_root() / PID_NAME


def running() -> bool:
    """Whether an anchor holds the lock for this runtime directory."""
    path = pid_path()
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def ensure() -> bool:
    """Start the anchor unless one runs. Returns whether one was started."""
    if running():
        return False
    package_parent = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        subprocess.Popen([sys.executable, "-m", "computer_use_sway.anchor"], env=env, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise core.ToolError(f"could not start the agent seat anchor: {exc}") from exc
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if running():
            return True
        time.sleep(0.05)
    raise core.ToolError("the agent seat anchor did not start")


def _hold(lock_fd: int) -> int:
    from . import vinput, wire

    conn = wire.Connection()
    devices: Any = vinput.SeatDevices(conn, install.SEAT, extent=lambda: (1, 1))
    ipc = swayipc.connect()
    sub = swayipc.Ipc(ipc.path, timeout=5.0)
    sub.call(swayipc.SUBSCRIBE, '["workspace", "shutdown"]')
    assert sub._sock is not None
    sub._sock.settimeout(None)
    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, str(os.getpid()).encode())
    while True:
        ready, _, _ = select.select([conn.sock, sub._sock], [], [])
        if conn.sock in ready:
            try:
                conn.dispatch(0.01)
            except Exception:  # sway closed the Wayland connection
                return 0
        if sub._sock in ready:
            try:
                kind, body = sub.read_message()
            except Exception:  # sway exited
                return 0
            change = body.get("change") if isinstance(body, dict) else None
            if change == "exit":
                return 0
            if change == "reload":
                time.sleep(0.2)
                devices.recreate()


def main() -> int:
    core.ensure_session_environment()
    path = pid_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another anchor holds the seat
    try:
        return _hold(fd)
    except core.ToolError:
        return 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())

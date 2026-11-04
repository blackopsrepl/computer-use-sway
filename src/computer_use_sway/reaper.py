#!/usr/bin/env python3
"""Launch wrapper: keep the launch pid alive as a child subreaper.

Sway places a new window on the workspace its launch came from by walking
the window's pid ancestry up to the pid it exec'd. Apps that fork and let
their launcher exit (Electron) get re-parented to init and fall off that
chain, so their windows map on the user's workspace. As a subreaper, this
process becomes the parent of every orphaned descendant, keeping the chain
intact. It exits when its last descendant does, with the first child's status.

Standalone on purpose (run by path from the launcher script): stdlib only,
no package imports.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys

PR_SET_CHILD_SUBREAPER = 36


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: reaper.py PROGRAM [ARGS...]", file=sys.stderr)
        return 2
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (OSError, AttributeError):
        pass  # placement degrades to sway's own pid match; the app still runs
    try:
        child = subprocess.Popen(argv)
    except OSError as exc:
        print(f"reaper: cannot start {argv[0]}: {exc}", file=sys.stderr)
        return 127

    def forward(signum: int, _frame: object) -> None:
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    status = None
    while True:
        try:
            pid, raw = os.wait()
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        if pid == child.pid:
            status = os.waitstatus_to_exitcode(raw)
    return status if status is not None and status >= 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

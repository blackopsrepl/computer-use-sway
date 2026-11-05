#!/usr/bin/env python3
"""Fail when a sandbox root outlived the process that created it.

Sandbox roots are `$XDG_RUNTIME_DIR/computer-use-sway/sandbox-<pid>-<n>`. A
root whose creating process is gone was not torn down: a leak of sway, D-Bus
and AT-SPI processes or of files. Roots of live processes (a run in progress)
are not leftovers.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PATTERN = re.compile(r"^sandbox-(\d+)-\d+$")


def runtime_root() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "computer-use-sway"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def leftovers(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for entry in sorted(root.iterdir()):
        match = PATTERN.match(entry.name)
        if match and not alive(int(match.group(1))):
            found.append(entry)
    return found


def main() -> int:
    found = leftovers(runtime_root())
    if found:
        print("sandbox roots left behind by finished processes:")
        for path in found:
            print(f"  {path}")
        return 1
    print("no sandbox leftovers")
    return 0


if __name__ == "__main__":
    sys.exit(main())

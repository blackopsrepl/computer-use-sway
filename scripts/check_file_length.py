#!/usr/bin/env python3
"""Fail when any tracked text file reaches the 500-line ceiling.

Generated and binary files are exempt; everything else must be split into
focused modules before it grows to 500 lines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIMIT = 500
EXEMPT = {"CHANGELOG.md"}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def looks_binary(path: Path) -> bool:
    try:
        return b"\x00" in path.read_bytes()[:8192]
    except OSError:
        return True


def line_count(path: Path) -> int | None:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (UnicodeDecodeError, OSError):
        return None


def main() -> int:
    violations: list[tuple[str, int]] = []
    for name in tracked_files():
        if name in EXEMPT:
            continue
        path = Path(name)
        if not path.is_file() or looks_binary(path):
            continue
        count = line_count(path)
        if count is not None and count >= LIMIT:
            violations.append((name, count))

    if violations:
        print(f"files must stay under {LIMIT} lines; split these:", file=sys.stderr)
        for name, count in sorted(violations, key=lambda item: -item[1]):
            print(f"  {count:5d}  {name}", file=sys.stderr)
        return 1

    print(f"file length OK (< {LIMIT} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

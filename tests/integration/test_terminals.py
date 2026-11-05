"""kitty, the supported terminal: exact text on the user's seat, and nothing
from the agent seat, which kitty ignores (FINDINGS I5, I6). kitty runs `cat`
into a file, so the file holds exactly what kitty received."""

from __future__ import annotations

import os
import shutil
import time
import unittest
from pathlib import Path

from computer_use_sway import desktop, session

from harness import Box, needs_sway

TEXT = "Ab9 !@#$%^&*()_+{}|:\"<>? é ü ß 日本 € ñ Ω"


def argv(name: str, command: str) -> list[str]:
    return {
        "kitty": ["kitty", "sh", "-c", command],
    }[name]


# Measured: kitty ignores input from a non-default seat.
AGENT_SEAT = {"kitty": False}


def received(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


@needs_sway
class TerminalTests(unittest.TestCase):
    def check(self, name: str, seat: str, expect: str) -> str:
        with Box() as b:
            out = Path(b.assets) / f"{name}-{seat}.txt"
            win = b.call("launch", {"argv": argv(name, f"stty raw -echo; cat > {out}"),
                                    "timeout_ms": 30000})["window"]
            time.sleep(1.0)
            if seat == "agent":
                session.current().agent().focus(int(win["id"]))
            else:
                desktop.ipc().command(f"[con_id={int(win['id'])}] focus")
            time.sleep(0.3)
            b.call("type_text", {"text": TEXT, "seat": seat})
            if expect:
                b.wait(lambda: received(out) == expect, 5)
            else:
                time.sleep(2.0)
            return received(out)

    def test_i6_each_terminal_receives_exact_text_on_its_seats(self) -> None:
        for name, agent_ok in AGENT_SEAT.items():
            for seat in ("user", "agent"):
                with self.subTest(terminal=name, seat=seat):
                    if shutil.which(name) is None:
                        self.skipTest(f"{name} is not installed")
                    expect = "" if seat == "agent" and not agent_ok else TEXT
                    self.assertEqual(self.check(name, seat, expect), expect)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    unittest.main()

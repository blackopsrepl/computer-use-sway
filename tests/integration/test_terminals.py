"""Terminals: which seats each accepts, and exact text through them
(FINDINGS I5, I6). Each terminal runs `cat` into a file, so the file holds
exactly what the terminal received from the keyboard."""

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
        "foot": ["foot", "sh", "-c", command],
        "alacritty": ["alacritty", "-e", "sh", "-c", command],
        "wezterm": ["wezterm", "start", "--always-new-process", "--", "sh", "-c", command],
        "ghostty": ["ghostty", "-e", "sh", "-c", command],
    }[name]


# Measured: kitty ignores input from a non-default seat; WezTerm binds one
# keyboard across seats, the user's, which exists first; the others accept it.
AGENT_SEAT = {"kitty": False, "foot": True, "alacritty": True, "wezterm": False, "ghostty": True}


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
            if shutil.which(name) is None:
                continue
            for seat in ("user", "agent"):
                with self.subTest(terminal=name, seat=seat):
                    expect = "" if seat == "agent" and not agent_ok else TEXT
                    self.assertEqual(self.check(name, seat, expect), expect)

    def test_i7_wezterm_keeps_the_users_keyboard_when_the_agent_session_ends(self) -> None:
        if shutil.which("wezterm") is None:
            self.skipTest("needs wezterm")
        with Box() as b:
            out = Path(b.assets) / "wez.txt"
            b.sess.sandbox().spawn(argv("wezterm", f"stty raw -echo; cat > {out}"))
            win = b.wait(lambda: next((w for w in desktop.all_windows()
                                       if "wezterm" in (w.get("app_id") or "")), None), 20)
            time.sleep(1.0)
            desktop.ipc().command(f"[con_id={int(win['id'])}] focus")
            b.user_keyboard().type_text("u1")
            b.wait(lambda: received(out) == "u1", 5)
            b.sess._agent.stop()  # the MCP server exiting: its devices are destroyed
            b.sess._agent = None
            time.sleep(0.5)
            desktop.ipc().command(f"[con_id={int(win['id'])}] focus")
            b.user_keyboard().type_text("u2")
            b.wait(lambda: received(out) == "u1u2", 5)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    unittest.main()

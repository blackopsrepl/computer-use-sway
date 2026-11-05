"""I8, I9: the agent seat's cursor is never left drawn on a user output.

A seat with a pointer draws its own cursor, and sway puts it on a user output
whenever the agent output is absent. The agent seat must therefore have a
pointer only while the agent output exists, with its cursor parked there, and
every tool call that moved it onto a user output (a click on a visible
window) must end with it back home.

Method: the sandbox renders cursors in software (WLR_NO_HARDWARE_CURSORS),
so a drawn cursor is part of the captured frame. An output that shows no
window is one flat colour unless a cursor is drawn on it. Positive controls:
the agent's own cursor shows on the agent output, and seat0's shows on the
user output once seat0 gets a pointer at the end.
"""

from __future__ import annotations

import subprocess
import time
import unittest
from typing import Any

from computer_use_sway import desktop

from harness import Box, needs_sway


def cursor_drawn(env: dict[str, str], output: str) -> bool:
    time.sleep(0.3)  # let sway render the last change
    frame = subprocess.run(["grim", "-t", "ppm", "-o", output, "-"], env=env,
                           capture_output=True, check=True, timeout=10).stdout
    pixels = frame.split(b"\n", 3)[3]  # after the P6 header lines
    first = pixels[:3]
    return pixels != first * (len(pixels) // 3)


@needs_sway
class CursorTests(unittest.TestCase):
    def test_i8_no_agent_cursor_on_a_user_output(self) -> None:
        with Box() as b:
            env = b.sess.sandbox().env
            agent: Any = b.sess.agent()
            user = next(o["name"] for o in desktop.get_outputs() if o["name"] != agent.output)
            rect = next(o["rect"] for o in desktop.get_outputs() if o["name"] == agent.output)

            with self.subTest("session running"):
                self.assertFalse(cursor_drawn(env, user))
            with self.subTest("the agent's cursor is on the agent output"):
                agent.devices.pointer.move_to(rect["x"] + 300, rect["y"] + 300)
                self.assertTrue(cursor_drawn(env, agent.output))
                self.assertFalse(cursor_drawn(env, user))
            with self.subTest("session stopped"):
                agent.stop()
                b.sess._agent = None
                self.assertFalse(cursor_drawn(env, user))
            with self.subTest("session restarted"):
                agent = b.sess.agent()
                self.assertFalse(cursor_drawn(env, user))
            with self.subTest("after a reload and recovery"):
                desktop.ipc().command("reload")
                b.wait(lambda: agent.stale == "reload", 3)
                b.sess.agent()  # ensure_ready recovers
                self.assertFalse(cursor_drawn(env, user))
            for tool, args in (("move_pointer", {"x": 300, "y": 300}),
                               ("click", {"x": 300, "y": 300}),
                               ("drag", {"from": {"x": 200, "y": 200}, "to": {"x": 400, "y": 300}}),
                               ("scroll", {"x": 300, "y": 300, "direction": "down", "clicks": 1})):
                with self.subTest(f"after an agent-seat {tool} on the user output [I9]"):
                    b.call(tool, dict(args, seat="agent"))
                    self.assertFalse(cursor_drawn(env, user))
            with self.subTest("after an act on a window on the user output [I9]"):
                win = b.launch("gtk3")
                desktop.ipc().command(f"[con_id={win['id']}] move container to workspace 1")
                time.sleep(0.5)
                out = b.call("act", {"target": {"query": {"name": "Go", "window": {"con_id": win["id"]}}},
                                     "action": "press", "delivery": "agent"})
                self.assertIn(out["effect"], ("confirmed", "changed"), out)
                desktop.ipc().command(f"[con_id={win['id']}] kill")
                b.wait(lambda: b.window(win["id"]) is None, 5)
                self.assertFalse(cursor_drawn(env, user))
            with self.subTest("positive control: seat0's own cursor is detected"):
                b.user_pointer().move_to(200, 200)
                self.assertTrue(cursor_drawn(env, user))


if __name__ == "__main__":
    unittest.main()

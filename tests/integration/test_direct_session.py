"""Exercise real direct user input, never the human's live compositor."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from computer_use_sway import core, desktop, session, swayipc
from harness import Box, needs_sway


@needs_sway
class DirectSessionTests(unittest.TestCase):
    def test_direct_user_pointer_keyboard_and_no_agent_objects(self) -> None:
        with Box() as b:
            ipc = desktop.ipc()
            seats = {s["name"] for s in ipc.seats()}
            outputs = [(o["name"], o["rect"]) for o in ipc.outputs()]
            workspaces = {w["name"] for w in ipc.workspaces()}
            self.assertEqual(seats, {"seat0"})
            self.assertEqual(len(outputs), 1)
            commands: list[str] = []
            original = swayipc.Ipc.command

            def tracked(connection, command):
                commands.append(command)
                return original(connection, command)

            with patch.object(swayipc.Ipc, "command", tracked):
                win = b.oracle("direct")
                b.call("focus_window", {"con_id": win["id"]})
                x, y = b.center_of(win, "direct", "entry")
                b.call("click", {"x": x, "y": y, "verify": False})
                b.call("type_text", {"text": "Direct üser", "verify": False})
                b.wait(lambda: b.text_of("direct") == "Direct üser", 5)
                self.assertEqual(b.seat_focus(), win["id"])
                self.assertEqual(b.sess.user_devices().seat_name, "seat0")
                b.call("key", {"key": "a", "modifiers": ["ctrl"], "verify": False})
                b.call("type_text", {"text": "replacement", "verify": False})
                b.wait(lambda: b.text_of("direct") == "replacement", 5)
                b.call("screenshot", {"output": "image"})
                b.call("observe", {"scope": "visible"})
            self.assertEqual({s["name"] for s in ipc.seats()}, seats)
            self.assertEqual([(o["name"], o["rect"]) for o in ipc.outputs()], outputs)
            self.assertEqual({w["name"] for w in ipc.workspaces()}, workspaces)
            for command in commands:
                self.assertFalse(any(word in command for word in
                                     ("cus-agent", "create_output", "assign ", "move container to workspace",
                                      "output ", "bindsym ")), command)
            self.assertNotIn("agent", b.sess.status())
            self.assertFalse((b.root / "computer-use-sway/session.json").exists())
            self.assertFalse((b.root / "computer-use-sway/launch").exists())

    def test_explicit_default_start_in_private_existing_session(self) -> None:
        # Stop only the driver, not the outer test compositor; default start must
        # control that compositor directly and never create another seat/output.
        with Box() as b:
            env = dict(b.sess.sandbox().env)
            driver = session.Session()
            with patch.dict(session.os.environ, env):
                try:
                    result = driver.start()
                    self.assertEqual(result["mode"], "user")
                    self.assertEqual(result["user_seat"], "seat0")
                    self.assertEqual(len(driver.ipc().outputs()), 1)
                    self.assertEqual({s["name"] for s in driver.ipc().seats()}, {"seat0"})
                finally:
                    driver.stop()

    def test_nested_run_cannot_silently_accept_old_placement(self) -> None:
        with Box() as b:
            win = b.oracle("nested")
            out = b.call("run", {"steps": [{"tool": "focus_window", "args": {
                "con_id": win["id"], "move_to_agent": True}}]})
            self.assertFalse(out["ok"])
            self.assertIn("move_to_agent", out["steps"][0]["summary"])


if __name__ == "__main__":
    unittest.main()

"""Direct user-seat transports in a private compositor."""

from __future__ import annotations

import time
import unittest

from computer_use_sway import core, desktop

from harness import Box, needs_sway, require


@needs_sway
class TransportTests(unittest.TestCase):
    def test_t1_1_ipc_requests_and_subscription(self) -> None:
        with Box() as b:
            ipc = desktop.ipc()
            self.assertEqual(ipc.tree()["type"], "root")
            self.assertGreaterEqual(len(ipc.outputs()), 1)
            t0 = time.monotonic()
            ipc.command("workspace 2")
            event = b.sess.log.wait_for(lambda e: e[1] == "workspace" and e[2].get("change") == "init", t0, 2.0)
            self.assertIsNotNone(event)
            with self.assertRaises(core.ToolError):
                ipc.command("definitely-not-a-command")

    def test_t1_2_drag_and_scroll_through_the_virtual_pointer(self) -> None:
        with Box() as b:
            win = b.oracle("A")
            x, y = b.center_of(win, "A", "area")
            t0 = time.monotonic()  # the oracle logs CLOCK_MONOTONIC too
            out = b.call("drag", {"from": {"x": x - 100, "y": y}, "to": {"x": x + 100, "y": y + 40}, "steps": 20})
            self.assertTrue(out["dragged"])
            time.sleep(0.3)
            rows = [r for r in b.events("A") if r["t"] >= t0]
            held = [r for r in rows if r["ev"] == "motion" and r["held"]]
            ends = [(round(r["x"]), round(r["y"])) for r in rows if r["ev"] in ("press", "release")]
            self.assertGreaterEqual(len(held), 15)
            self.assertEqual(len(ends), 2)
            self.assertNotEqual(ends[0], ends[1])
            before = len(b.events("A", "scroll"))
            b.call("scroll", {"x": x, "y": y, "direction": "down", "clicks": 3})
            time.sleep(0.3)
            self.assertGreaterEqual(len(b.events("A", "scroll")) - before, 3)

    def test_t1_2_unicode_shift_and_chords_on_seat0(self) -> None:
        # 8 distinct non-ASCII characters: more than the six character slots,
        # so the keymap is rebound mid-text [I3, I6].
        text = "Ab9 !@#$%^&*()_+{}|:\"<>? é ü ß 日本 € ñ Ω"
        for name in ("gtk3", "gtk4", "qt6", "firefox", "chromium", "electron"):
            with self.subTest(name):
                require(self, name)
                with Box() as b:
                    win = b.launch(name)
                    ref = b.ref(win["id"], "Field")
                    bounds = b.call("read", {"ref": ref})["bounds"]
                    desktop.ipc().command(f"[con_id={win['id']}] focus")
                    b.call("click", {"x": bounds[0] + 10, "y": bounds[1] + bounds[3] // 2, "seat": "user"})
                    b.call("type_text", {"text": text, "seat": "user"})
                    b.wait(lambda: b.title(win["id"]).startswith("T:" + text), 5)
                    b.call("key", {"key": "a", "modifiers": ["ctrl"], "seat": "user"})
                    b.call("key", {"key": "BackSpace", "seat": "user"})
                    b.wait(lambda: b.title(win["id"]).split(" — ")[0] == "T:", 5)

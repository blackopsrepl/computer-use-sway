"""T3 perception: the measured geometry matrix, the OCR ensemble, ref lifetimes
(ports of spikes 4, 4 matrix and 6 onto observe + act)."""

from __future__ import annotations

import time
import unittest

from computer_use_sway import core, desktop

from harness import Box, needs_sway, require

SINGLE_SEAT = {"chromium", "electron"}
FOREGROUND = {"foreground_pointer": True, "idle_ms": 200, "idle_max_wait_ms": 5000}
# (target, scale, floating) — the 12 cases of spikes/spike4_matrix.py
MATRIX = [("gtk3", 1, False), ("gtk3-csd", 1, True), ("gtk4", 1, False), ("gtk4", 2, False), ("qt6", 1, False),
          ("qt6", 2, False), ("firefox", 1, False), ("firefox", 2, False), ("chromium", 1, False),
          ("chromium", 2, False), ("electron", 1, False), ("electron", 2, False)]
OCR = [(n, s) for n in ("gtk3", "gtk4", "qt6", "firefox", "chromium") for s in (1, 2)]


def place(b: Box, win: dict, scale: int, floating: bool) -> None:
    ipc = desktop.ipc()
    if scale != 1:  # the agent output is scale 1; the scaled case is the user's output
        ipc.command(f"[con_id={win['id']}] move container to workspace 1")
    if floating:
        ipc.command(f"[con_id={win['id']}] floating enable, resize set 900 700, move position 150 50")
    time.sleep(0.8)


def delivery(name: str) -> str:
    return "foreground" if name in SINGLE_SEAT else "agent"


@needs_sway
class GeometryMatrixTests(unittest.TestCase):
    def test_t3_1_click_hit_from_a11y_bounds(self) -> None:
        for name, scale, floating in MATRIX:
            with self.subTest(f"{name} s{scale}{' floating' if floating else ''}"):
                require(self, name)
                with Box(scale, FOREGROUND) as b:
                    win = b.launch(name)
                    place(b, win, scale, floating)
                    ref = b.ref(win["id"], "Hit")
                    out = b.call("act", {"target": {"ref": ref}, "action": "click", "delivery": delivery(name)})
                    self.assertIn(out["effect"], ("confirmed", "changed"), out)
                    b.wait(lambda: "T:hit" in b.title(win["id"]), 5)


@needs_sway
class OcrTests(unittest.TestCase):
    def test_t3_2_ocr_ensemble_grounds_clicks(self) -> None:
        """Clicks grounded on OCR boxes alone (the a11y twin is ignored)."""
        for name, scale in OCR:
            with self.subTest(f"{name} s{scale}"):
                require(self, name)
                with Box(scale, FOREGROUND) as b:
                    win = b.launch(name)
                    place(b, win, scale, False)
                    ref = b.ref(win["id"], "Hit", ocr="always")
                    self.assertEqual(b.call("read", {"ref": ref})["source"], "ocr")
                    out = b.call("act", {"target": {"ref": ref}, "action": "click", "delivery": delivery(name)})
                    self.assertIn(out["effect"], ("confirmed", "changed"), out)
                    b.wait(lambda: "T:hit" in b.title(win["id"]), 5)


@needs_sway
class RefTests(unittest.TestCase):
    def test_t3_3_refs_follow_moves_and_go_stale(self) -> None:
        with Box() as b:
            win = b.launch("gtk3")
            place(b, win, 1, True)
            ref = b.ref(win["id"], "Hit")
            desktop.ipc().command(f"[con_id={win['id']}] move position 400 200")
            time.sleep(0.5)
            out = b.call("act", {"target": {"ref": ref}, "action": "click", "delivery": "agent"})
            self.assertIn(out["effect"], ("confirmed", "changed"), out)
            b.wait(lambda: "T:hit" in b.title(win["id"]), 5)

            entry = {"target": {"query": {"role": "entry", "name": "Field"}}, "action": "set_text"}
            b.call("act", {**entry, "text": "alpha"})
            time.sleep(0.5)
            text_ref = b.ref(win["id"], "alpha", ocr="always")
            b.call("read", {"ref": text_ref})  # still valid: nothing changed (the caret blinks)
            b.call("act", {**entry, "text": "omega"})
            time.sleep(0.5)
            with self.assertRaises(core.ToolError) as caught:
                b.call("read", {"ref": text_ref})
            self.assertEqual(caught.exception.code, "stale_ref")

            ref = b.ref(win["id"], "Hit")
            desktop.ipc().command(f"[con_id={win['id']}] kill")
            b.wait(lambda: b.window(win["id"]) is None, 5)
            with self.assertRaises(core.ToolError) as caught:
                b.call("read", {"ref": ref})
            self.assertEqual(caught.exception.code, "stale_ref")


@needs_sway
class ScreenshotTests(unittest.TestCase):
    def test_p3_the_default_screenshot_is_the_users_outputs_only(self) -> None:
        """Every agent output is left out, another server's included [P3]."""
        import struct

        with Box() as b:
            b.sess.agent()
            ipc = desktop.ipc()
            before = {o["name"] for o in desktop.get_outputs()}
            ipc.command("create_output")  # another server's agent output
            other = next(o["name"] for o in desktop.get_outputs() if o["name"] not in before)
            ipc.command(f"output {other} position 200000 0")
            try:
                path = f"{b.assets}/default.png"
                b.call("screenshot", {"save_path": path})
                with open(path, "rb") as f:
                    head = f.read(24)
                self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", head[16:24]), (1280, 800))
            finally:
                ipc.command(f"output {other} unplug")


if __name__ == "__main__":
    unittest.main()

"""Verified semantic/direct-user actions, launch flags and batches in private sandboxes."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from computer_use_sway import capture, core, desktop

from harness import Box, available, needs_sway, require

FOREGROUND = {}


def field(win: dict) -> dict:
    return {"query": {"role": "entry", "name": "Field", "window": {"con_id": win["id"]}}}


@needs_sway
class LadderTests(unittest.TestCase):
    def test_t4_1_gtk3_set_text_is_semantic_and_confirmed(self) -> None:
        with Box() as b:
            win = b.launch("gtk3")
            out = b.call("act", {"target": field(win), "action": "set_text", "text": "hello"})
            self.assertEqual((out["delivery"], out["effect"]), ("semantic", "confirmed"), out)
            self.assertEqual(b.title(win["id"]), "T:hello")

    @unittest.skipUnless(available("firefox"), "needs firefox")
    def test_t4_1_firefox_set_text_climbs_past_the_lying_semantic_call(self) -> None:
        with Box() as b:
            win = b.launch("firefox")
            out = b.call("act", {"target": field(win), "action": "set_text", "text": "hello"})
            self.assertEqual((out["delivery"], out["effect"]), ("user", "confirmed"), out)
            self.assertTrue(out["escalations"][0].get("readback_failed"), out)
            b.wait(lambda: b.title(win["id"]).startswith("T:hello"), 5)

    @unittest.skipUnless(available("chromium"), "needs chromium")
    def test_t4_1_chromium_set_text_directly_controls_user_focus(self) -> None:
        with Box(options=FOREGROUND) as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            win = b.launch("chromium")
            out = b.call("act", {"target": field(win), "action": "set_text", "text": "hello"})
            self.assertEqual((out["delivery"], out["effect"]), ("user", "confirmed"), out)
            self.assertEqual(out["delivery"], "user")
            self.assertEqual(b.seat_focus("seat0"), win["id"])
            b.wait(lambda: b.title(win["id"]) == "T:hello", 5)


@needs_sway
class LaunchTests(unittest.TestCase):
    @unittest.skipUnless(available("chromium"), "needs chromium")
    def test_t4_2_launch_adds_the_chromium_a11y_flag(self) -> None:
        with Box() as b:
            win = b.launch("chromium-noforce")
            self.assertTrue(b.ref(win["id"], "Field").startswith("s"))


@needs_sway
class VerdictTests(unittest.TestCase):
    def test_t4_3_verdicts(self) -> None:
        with Box() as b:
            inert = b.oracle("A")  # logs clicks, shows nothing for a click on its background
            c = b.window(inert["id"])["content"]
            empty = {"x": c["x"] + c["width"] - 40, "y": c["y"] + c["height"] - 40}
            b.call("act", {"target": empty, "action": "click", "delivery": "user"})  # settle focus/backdrop
            out = b.call("act", {"target": empty, "action": "click", "delivery": "user"})
            self.assertEqual(out["effect"], "no_change", out)
            win = b.launch("gtk3")
            out = b.call("act", {"target": {"query": {"role": "button", "name": "Go"}}, "action": "press"})
            self.assertIn(out["effect"], ("confirmed", "changed"), out)

    def test_t4_3_without_readback_events_or_pixels_is_unverifiable(self) -> None:
        # Keep this oracle isolated: opening a second tiled window changes
        # its geometry, so coordinates from before that launch are not inert.
        with Box() as b:
            inert = b.oracle("A")
            c = b.window(inert["id"])["content"]
            empty = {"x": c["x"] + c["width"] - 40, "y": c["y"] + c["height"] - 40}
            b.call("act", {"target": empty, "action": "click", "delivery": "user"})
            with patch.object(capture, "frame", side_effect=core.ToolError("no capture")):
                out = b.call("act", {"target": empty, "action": "click", "delivery": "user"})
            self.assertEqual(out["effect"], "unverifiable", out)


def plan(win_name: str = "T:") -> list[dict]:
    q = {"role": "entry", "name": "Field"}
    return [
        {"tool": "launch", "args": {"argv": [], "timeout_ms": 60000}},
        # Launch reports whether accessibility is ready; mapping a window
        # does not guarantee that the app has published the target field.
        {"tool": "wait_for", "args": {"condition": {"element": q}, "timeout_ms": 10000}},
        {"tool": "act", "args": {"target": {"query": q}, "action": "set_text", "text": "hello"}},
        {"tool": "act", "args": {"target": {"query": {"role": "button", "name": "Go"}}, "action": "press"}},
        {"tool": "wait_for", "args": {"condition": {"window": {"title": "T:clicked"}}, "timeout_ms": 5000}},
        {"tool": "assert", "args": {"condition": {"element": {"role": "button", "name": "Hit"}}}},
    ]


@needs_sway
class RunTests(unittest.TestCase):
    def run_form(self, b: Box) -> dict:
        import harness

        steps = plan()
        argv, env = harness.targets.argv("gtk4", b.assets)
        steps[0]["args"].update(argv=argv, env=env)
        return b.call("run", {"steps": steps})

    def test_t4_4_one_call_runs_a_whole_task(self) -> None:
        with Box() as b:
            out = self.run_form(b)
            self.assertTrue(out["ok"], out)
            self.assertEqual(len(out["steps"]), 6)

@needs_sway
class DirectTypingTests(unittest.TestCase):
    def test_t4_5_typing_keeps_the_target_focused(self) -> None:
        for name in ("chromium", "electron"):
            with self.subTest(name):
                require(self, name)
                with Box(options=FOREGROUND) as b:
                    a = b.oracle("A")
                    b.user_pointer().click(*b.center_of(a, "A", "entry"))
                    b.user_keyboard().type_text("before")
                    win = b.launch(name)
                    out = b.call("act", {"target": field(win), "action": "type", "text": "wörld"})
                    self.assertEqual((out["delivery"], out["effect"]), ("user", "confirmed"), out)
                    self.assertEqual(b.seat_focus("seat0"), win["id"])
                    b.user_keyboard().type_text("after")
                    b.wait(lambda: b.title(win["id"]).startswith("T:wörldafter"), 5)
                    self.assertEqual(b.text_of("A"), "before")


if __name__ == "__main__":
    unittest.main()

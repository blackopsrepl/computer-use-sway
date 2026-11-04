"""T4 action: the delivery ladder, launch flags, verdicts, run, foreground steps,
and the user-untouched guarantee (ports of spikes 4, 4b, 5, 10, 14)."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from computer_use_sway import capture, core, desktop

from harness import Box, available, needs_sway

FOREGROUND = {"idle_ms": 300, "idle_max_wait_ms": 8000}


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
            self.assertEqual((out["delivery"], out["effect"]), ("agent", "confirmed"), out)
            self.assertTrue(out["escalations"][0].get("readback_failed"), out)
            b.wait(lambda: b.title(win["id"]).startswith("T:hello"), 5)

    @unittest.skipUnless(available("chromium"), "needs chromium")
    def test_t4_1_chromium_set_text_goes_foreground_and_restores_the_user(self) -> None:
        with Box(options=FOREGROUND) as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            win = b.launch("chromium")
            out = b.call("act", {"target": field(win), "action": "set_text", "text": "hello"})
            self.assertEqual((out["delivery"], out["effect"]), ("foreground", "confirmed"), out)
            self.assertNotIn("agent", [e["rung"] for e in out["escalations"]], "single-seat profile skips rung 2")
            self.assertEqual(b.seat_focus("seat0"), a["id"])
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
            b.call("act", {"target": empty, "action": "click", "delivery": "agent"})  # settle focus/backdrop
            out = b.call("act", {"target": empty, "action": "click", "delivery": "agent"})
            self.assertEqual(out["effect"], "no_change", out)
            win = b.launch("gtk3")
            out = b.call("act", {"target": {"query": {"role": "button", "name": "Go"}}, "action": "press"})
            self.assertIn(out["effect"], ("confirmed", "changed"), out)
            with patch.object(capture, "frame", side_effect=core.ToolError("no capture")):
                out = b.call("act", {"target": empty, "action": "click", "delivery": "agent"})
            self.assertEqual(out["effect"], "unverifiable", out)


def plan(win_name: str = "T:") -> list[dict]:
    q = {"role": "entry", "name": "Field"}
    return [
        {"tool": "launch", "args": {"argv": [], "timeout_ms": 60000}},
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
            self.assertEqual(len(out["steps"]), 5)

    def test_t4_6_user_untouched_while_the_agent_works(self) -> None:
        from computer_use_sway import swayipc, vinput, wire

        for attempt in range(5):  # definition of done: 5 consecutive passes
            with self.subTest(attempt=attempt), Box() as b:
                a = b.oracle("A")
                b.user_pointer().click(*b.center_of(a, "A", "entry"))
                typed: list[str] = []
                focus_left: list[int] = []
                stop = threading.Event()

                def user() -> None:
                    # A separate keyboard on seat0 with its own connection,
                    # as a real user's keyboard is.
                    conn = wire.Connection()
                    devices = vinput.SeatDevices(conn, "seat0", extent=lambda: (1, 1))
                    i = 0
                    while not stop.is_set():
                        ch = "abcdefghij"[i % 10]
                        devices.keyboard.type_text(ch)
                        typed.append(ch)
                        i += 1
                        stop.wait(0.2)
                    devices.destroy()
                    conn.close()

                def watcher() -> None:
                    ipc = swayipc.Ipc(desktop.ipc().path)
                    while not stop.is_set():
                        focus = next(s["focus"] for s in ipc.seats() if s["name"] == "seat0")
                        if focus != a["id"]:
                            focus_left.append(focus)
                        stop.wait(0.05)
                    ipc.close()

                threads = [threading.Thread(target=user), threading.Thread(target=watcher)]
                for thread in threads:
                    thread.start()
                try:
                    out = self.run_form(b)
                finally:
                    time.sleep(0.4)
                    stop.set()
                    for thread in threads:
                        thread.join()
                self.assertTrue(out["ok"], out)
                self.assertGreater(len(typed), 5)
                b.wait(lambda: b.text_of("A") == "".join(typed), 5)
                self.assertEqual(focus_left, [])


@needs_sway
class ForegroundTests(unittest.TestCase):
    def test_t4_5_single_seat_typing_borrows_and_returns_the_user_seat(self) -> None:
        for name in ("chromium", "electron"):
            if not available(name):
                continue
            with self.subTest(name), Box(options=FOREGROUND) as b:
                a = b.oracle("A")
                b.user_pointer().click(*b.center_of(a, "A", "entry"))
                b.user_keyboard().type_text("before")
                win = b.launch(name)
                out = b.call("act", {"target": field(win), "action": "type", "text": "wörld"})
                self.assertEqual((out["delivery"], out["effect"]), ("foreground", "confirmed"), out)
                self.assertEqual(b.seat_focus("seat0"), a["id"])
                b.user_keyboard().type_text("after")
                b.wait(lambda: b.text_of("A") == "beforeafter", 5)


if __name__ == "__main__":
    unittest.main()

"""T1 transports and T2 agent seat (ports of spikes 1b, 3, 7c, 8, 11, 12, 13, 14)."""

from __future__ import annotations

import time
import unittest

from computer_use_sway import core, desktop, install, vinput

from harness import Box, available, needs_sway


@needs_sway
class TransportTests(unittest.TestCase):
    def test_t1_1_ipc_requests_and_subscription(self) -> None:
        with Box() as b:
            ipc = desktop.ipc()
            self.assertEqual(ipc.tree()["type"], "root")
            self.assertGreaterEqual(len(ipc.outputs()), 2)  # user output + agent output
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
        text = "Ab9 !@#$%^&*()_+{}|:\"<>? é ü ß 日本 €"
        for name in ("gtk3", "gtk4", "qt6", "firefox"):
            if not available(name):
                continue
            with self.subTest(name), Box() as b:
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


@needs_sway
class SeatTests(unittest.TestCase):
    def test_t2_1_seat_isolation(self) -> None:
        with Box() as b:
            a, bw = b.oracle("A"), b.oracle("B")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            b.call("focus_window", {"con_id": bw["id"], "seat": "agent"})
            b.call("click", {**dict(zip("xy", b.center_of(bw, "B", "entry"))), "seat": "agent"})
            for user_ch, agent_ch in zip("usera", "agent"):
                b.user_keyboard().type_text(user_ch)
                b.call("type_text", {"text": agent_ch, "seat": "agent"})
            b.wait(lambda: b.text_of("A") == "usera" and b.text_of("B") == "agent", 5)
            self.assertEqual(b.seat_focus("seat0"), a["id"])
            self.assertEqual(b.seat_focus(install.SEAT), bw["id"])
            desktop.ipc().command(f"[con_id={bw['id']}] focus")  # IPC acts on seat0 only
            self.assertEqual((b.seat_focus("seat0"), b.seat_focus(install.SEAT)), (bw["id"], bw["id"]))
            desktop.ipc().command(f"[con_id={a['id']}] focus")
            b.sess.agent().focus(a["id"])  # the binding moves the agent only
            b.sess.agent().focus(bw["id"])
            self.assertEqual((b.seat_focus("seat0"), b.seat_focus(install.SEAT)), (a["id"], bw["id"]))

    def test_t2_2_bringup_and_launch_leave_the_user_alone(self) -> None:
        with Box() as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            b.user_keyboard().type_text("u1")
            win = b.launch("gtk3")
            self.assertEqual(win["workspace"], install.SEAT)
            self.assertEqual(b.seat_focus("seat0"), a["id"])
            agent_output = b.sess.agent().output
            spaces = desktop.ipc().workspaces()
            self.assertIn((install.SEAT, agent_output), [(w["name"], w["output"]) for w in spaces])
            self.assertFalse([w for w in spaces if w["name"] == install.SEAT and w["output"] != agent_output])
            b.user_keyboard().type_text("u2")
            b.wait(lambda: b.text_of("A") == "u1u2", 5)

    def test_t2_3_reload_recovery(self) -> None:
        with Box() as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            apps = [b.launch(n) for n in ("gtk3", "gtk4", "qt6") if available(n)]
            user_outputs = {o["name"]: (o["rect"]["x"], o["rect"]["y"]) for o in desktop.get_outputs()
                            if o["name"] != b.sess.agent().output}
            seat0_devices = len(next(s for s in desktop.get_seats() if s["name"] == "seat0")["devices"])
            t0 = time.monotonic()
            desktop.ipc().command("reload")
            self.assertIsNotNone(b.sess.log.wait_for(
                lambda e: e[1] == "workspace" and e[2].get("change") == "reload", t0, 3.0))
            agent = b.sess._agent
            b.wait(lambda: agent.stale == "reload", 2)
            with self.assertRaises(vinput.AgentSeatLost):
                agent.gate()
            b.sess.agent()  # ensure_ready recovers
            seats = {s["name"]: s for s in desktop.get_seats()}
            self.assertEqual(len(seats[install.SEAT]["devices"]), 2)
            self.assertEqual(len(seats["seat0"]["devices"]), seat0_devices)
            outs = {o["name"]: o["rect"] for o in desktop.get_outputs()}
            self.assertEqual(outs[agent.output]["x"], 100000)
            for name, (x, y) in user_outputs.items():
                self.assertEqual((outs[name]["x"], outs[name]["y"]), (x, y))
            for win in apps:
                out = b.call("act", {"target": {"query": {"role": "entry", "name": "Field",
                                                          "window": {"con_id": win["id"]}}},
                                     "action": "type", "text": "R"})
                self.assertIn(out["effect"], ("confirmed", "changed"), out)
                self.assertTrue(b.title(win["id"]).endswith("R"))
            b.user_keyboard().type_text("u")
            b.wait(lambda: b.text_of("A") == "u", 5)
            self.assertEqual(b.seat_focus("seat0"), a["id"])

    def test_t2_4_idle_gate_is_per_seat(self) -> None:
        with Box() as b:
            b.oracle("A")
            sentinel = b.sess.sentinel()
            self.assertTrue(sentinel.wait_user_idle(400, 3000))
            b.call("type_text", {"text": "x", "seat": "agent"})
            for _ in range(6):
                sentinel.conn.dispatch(0.05)
            self.assertTrue(sentinel.idle, "agent input must not count as user activity")
            b.user_keyboard().type_text("y")
            for _ in range(6):
                sentinel.conn.dispatch(0.05)
            self.assertFalse(sentinel.idle)


if __name__ == "__main__":
    unittest.main()

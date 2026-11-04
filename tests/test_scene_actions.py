from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from computer_use_sway import actions, core, query, refs, scene, swayipc, toolkits, waits
from computer_use_sway.actions_delivery import Delivery


class FakeAgent:
    output = "HEADLESS-2"


class FakeSession:
    def __init__(self, mode: str = "seat", **options) -> None:
        self.mode = mode
        self.options = {"foreground": "idle", "ocr": "auto", "settle_ms": 0, **options}
        self._agent = FakeAgent()
        self.log = swayipc.EventLog()

    def ensure_started(self) -> None:
        pass

    def agent(self):
        return self._agent

    def a11y_or_none(self):
        return None


def element(**kw) -> refs.Element:
    base = dict(ref="s1:1", source="a11y", con_id=7, role="push button", name="Go", actions=["click"],
                bounds=(10, 20, 30, 40), profile="gtk3", app_id="t", ifaces=[])
    base.update(kw)
    return refs.Element(**base)


class RenderTests(unittest.TestCase):
    def test_rendering_is_byte_exact(self) -> None:
        win = {"id": 41, "name": "Settings", "app_id": "org.gnome.Settings", "workspace": "cus-agent",
               "content": {"x": 100002, "y": 26, "width": 1916, "height": 1052}}
        ws = scene.WindowScene(win=win, profile=toolkits.PROFILES["gtk4"], a11y=True)
        ws.elements = [
            element(ref="s7:1", name="Back", states=["focusable"], actions=["press"], bounds=(100014, 38, 34, 34)),
            element(ref="s7:2", role="entry", name="Search", states=["editable", "focused"], actions=[],
                    bounds=(100060, 38, 300, 34), preview=""),
            element(ref="s7:3", source="ocr", role="text", name="Wi-Fi", actions=[], bounds=(100020, 120, 60, 18)),
        ]
        text = scene.render(FakeSession(), {"snapshot": 7, "windows": [ws], "truncated": False, "scope": "agent"}, None)
        self.assertEqual(text, "\n".join([
            "snapshot s7 · mode seat · agent workspace cus-agent (HEADLESS-2)",
            'window 41 "Settings" app=org.gnome.Settings toolkit=gtk4 seats=agent+user ws=cus-agent '
            "@100002,26 1916x1052 a11y=on",
            '  s7:1 push button "Back" [focusable] @100014,38 34x34 actions=press',
            '  s7:2 entry "Search" [editable,focused] @100060,38 300x34 value=""',
            '  s7:3 text "Wi-Fi" @100020,120 60x18 (ocr)',
        ]))

    def test_query_filter_keeps_window_headers_and_notes(self) -> None:
        win = {"id": 3, "name": "x", "app_id": "chromium", "workspace": "1",
               "content": {"x": 0, "y": 0, "width": 10, "height": 10}}
        ws = scene.WindowScene(win=win, profile=toolkits.PROFILES["chromium"], a11y=False, notes=["a11y: off"])
        ws.elements = [element(name="Go"), element(ref="s1:2", name="Stop")]
        text = scene.render(FakeSession(), {"snapshot": 1, "windows": [ws], "truncated": True, "scope": "visible"},
                            query.parse({"name": "Stop", "match": "exact"}))
        self.assertIn("window 3", text)
        self.assertIn("seats=user only", text)
        self.assertIn("note: a11y: off", text)
        self.assertNotIn('"Go"', text)
        self.assertIn('"Stop"', text)
        self.assertIn("(truncated", text)

    def test_menu_contents_are_marked_popup(self) -> None:
        class N:
            def __init__(self, ref, role, parent):
                self.ref, self.role, self.parent = ref, role, parent

        nodes = [N("f", "frame", None), N("b", "menu bar", "f"), N("m", "menu", "b"), N("i", "menu item", "m"),
                 N("s", "menu", "m"), N("j", "menu item", "s")]
        by_ref = {n.ref: n for n in nodes}
        self.assertEqual([scene.in_popup(n, by_ref) for n in nodes], [False, False, False, True, True, True])

    def test_offscreen_elements_only_with_a_query(self) -> None:
        win = {"id": 3, "name": "x", "app_id": "a", "workspace": "1",
               "content": {"x": 0, "y": 0, "width": 10, "height": 10}}
        ws = scene.WindowScene(win=win, profile=toolkits.PROFILES["gtk3"], a11y=True)
        ws.elements = [element(name="Item 1"), element(ref="s1:2", name="Item 29", states=["offscreen"])]
        result = {"snapshot": 1, "windows": [ws], "truncated": False, "scope": "agent"}
        plain = scene.render(FakeSession(), result, None)
        self.assertNotIn("Item 29", plain)
        self.assertIn("(+1 scrolled out of view", plain)
        self.assertIn('"Item 29" [offscreen]', scene.render(FakeSession(), result, query.parse({"name": "Item 29"})))

    def test_pointer_input_refuses_offscreen_targets(self) -> None:
        from computer_use_sway import actions_delivery

        with self.assertRaises(core.ToolError) as caught:
            actions_delivery.device_form(None, "click", element(states=["offscreen"]), (1, 1), Delivery())
        self.assertEqual(caught.exception.code, "offscreen")

    def test_a_label_shows_its_text_when_it_differs_from_its_name(self) -> None:
        line = scene.render_element(element(role="label", name="Code", actions=[], preview="Code: 4821"))
        self.assertTrue(line.endswith('text="Code: 4821"'), line)

    def test_long_names_are_clipped(self) -> None:
        line = scene.render_element(element(name="x" * 300))
        self.assertLessEqual(len(line), scene.LINE_LIMIT)
        self.assertIn("…", line)


class LadderTests(unittest.TestCase):
    def test_rungs_per_profile_and_mode(self) -> None:
        gtk, chrome = toolkits.PROFILES["gtk3"], toolkits.PROFILES["chromium"]
        s = FakeSession()
        self.assertEqual(actions.ladder(s, "set_text", element(), gtk, "auto"), ["semantic", "agent", "foreground"])
        # gtk3 runs a pressed button's handler inside DoAction: pointer first [A10]
        self.assertEqual(actions.ladder(s, "press", element(), gtk, "auto"), ["agent", "semantic", "foreground"])
        self.assertEqual(actions.ladder(s, "press", element(states=["offscreen"]), gtk, "auto"),
                         ["semantic", "agent", "foreground"])
        popup_item = element(role="menu item", states=["popup"])
        self.assertEqual(actions.ladder(s, "click", popup_item, gtk, "auto"), ["semantic"])
        with self.assertRaises(core.ToolError):
            actions.ladder(s, "double_click", popup_item, gtk, "auto")
        qt = toolkits.PROFILES["qt"]
        self.assertEqual(actions.ladder(s, "press", element(), qt, "auto"), ["semantic", "agent", "foreground"])
        self.assertEqual(actions.ladder(s, "click", element(profile="chromium"), chrome, "auto"),
                         ["foreground", "semantic"])
        self.assertEqual(actions.ladder(s, "set_text", element(), chrome, "auto"), ["foreground"])
        self.assertEqual(actions.ladder(FakeSession("user"), "press", element(), gtk, "auto"), ["semantic", "user"])
        self.assertEqual(actions.ladder(FakeSession("user"), "focus", element(), gtk, "auto"), ["semantic", "user"])
        self.assertEqual(actions.ladder(FakeSession(foreground="never"), "type", element(), gtk, "auto"), ["agent"])
        self.assertEqual(actions.ladder(s, "click", None, gtk, "agent"), ["agent"])
        with self.assertRaises(core.ToolError):
            actions.ladder(s, "click", None, gtk, "semantic")

    def test_query_matches_are_ranked_for_the_action(self) -> None:
        label = element(ref="s1:1", role="label", name="Name", actions=[])
        entry = element(ref="s1:2", role="text", name="Name", actions=["activate"], states=["editable"])
        self.assertEqual(actions.rank_for("set_text", [label, entry])[0].ref, "s1:2")
        self.assertEqual(actions.rank_for("press", [label, entry])[0].ref, "s1:2")
        self.assertEqual(actions.rank_for("focus", [label, entry])[0].ref, "s1:1")

    def run_act(self, outcomes: dict, action: str = "activate", text: str | None = None) -> dict:
        calls: list[str] = []

        def fake_rung(session, rung, *args):
            calls.append(rung)
            result = outcomes[rung]
            if isinstance(result, Exception):
                raise result
            return dict(result, evidence=[], elapsed_ms=0)

        with patch.object(actions, "resolve_target", return_value=(element(), 7, (25, 40))), \
                patch.object(actions, "run_rung", side_effect=fake_rung):
            result = actions.act(FakeSession(), {"ref": "s1:1"}, action, Delivery(text=text))
        out = json.loads(result.content[0]["text"])
        out["_calls"], out["_meta"] = calls, result.meta
        return out

    def test_escalates_on_no_change_and_stops_on_change(self) -> None:
        out = self.run_act({"semantic": {"effect": "no_change"}, "agent": {"effect": "changed"}})
        self.assertEqual((out["delivery"], out["effect"], out["_calls"]), ("agent", "changed", ["semantic", "agent"]))
        self.assertEqual(out["escalations"], [{"rung": "semantic", "effect": "no_change"}])

    def test_failed_text_readback_escalates_even_when_events_fired(self) -> None:
        out = self.run_act({"semantic": {"effect": "changed", "readback_failed": True},
                            "agent": {"effect": "confirmed"}}, action="set_text", text="pw")
        self.assertEqual(out["delivery"], "agent")
        self.assertTrue(out["escalations"][0]["readback_failed"])
        self.assertNotIn("pw", json.dumps(out["_meta"]))
        self.assertEqual(out["_meta"]["characters"], 2)

    def test_errors_escalate_and_all_failing_raises(self) -> None:
        out = self.run_act({"semantic": core.ToolError("no action", code="no_action"), "agent": {"effect": "changed"}})
        self.assertEqual(out["escalations"][0]["code"], "no_action")
        with self.assertRaises(core.ToolError):
            self.run_act({r: core.ToolError("x") for r in ("semantic", "agent", "foreground")})

    def test_blocked_rung_falls_through_to_the_last(self) -> None:
        out = self.run_act({"semantic": {"effect": "no_change"}, "agent": {"effect": "no_change"},
                            "foreground": {"effect": "no_change", "blocked": "user_active"}})
        self.assertEqual(out["blocked"], "user_active")


class WaitTests(unittest.TestCase):
    def test_condition_validation(self) -> None:
        s = FakeSession()
        for bad in ({}, {"window": {}, "text": "x"}, "nope"):
            with self.subTest(bad), self.assertRaises(core.ToolError):
                waits.wait_for(s, bad)
        with self.assertRaises(core.ToolError):
            waits.wait_for(s, {"user_idle_ms": 1}, timeout_ms=999999)

    def test_window_event_wait_wakes_on_the_event(self) -> None:
        s = FakeSession()
        container = {"id": 5, "name": "T:clicked", "app_id": "t"}
        threading.Timer(0.05, lambda: s.log.append("window", {"change": "title", "container": container})).start()
        out = json.loads(waits.wait_for(s, {"window": {"title": "clicked"}, "event": "title"}, 2000).content[0]["text"])
        self.assertTrue(out["satisfied"])
        self.assertLess(out["waited_ms"], 1500)

    def test_unsatisfied_is_reported_not_raised(self) -> None:
        s = FakeSession()
        out = json.loads(waits.wait_for(s, {"window": {"title": "never"}, "event": "new"}, 50).content[0]["text"])
        self.assertFalse(out["satisfied"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from computer_use_sway import core, launch, plans, timeline, verify


def ok(data: dict) -> core.Result:
    return core.text_result(data, {})


class PlanTests(unittest.TestCase):
    def tools(self, effects: list[str]) -> dict:
        calls = iter(effects)
        return {
            "act": lambda a: ok({"effect": next(calls)}),
            "wait_for": lambda a: ok({"satisfied": a.get("timeout_ms") != 0}),
            "observe": lambda a: core.Result([{"type": "text", "text": "snapshot s1"}], {}),
        }

    def report(self, result: core.Result) -> dict:
        return json.loads(result.content[0]["text"])

    def test_stops_on_first_failure(self) -> None:
        steps = [{"tool": "act", "args": {}}] * 3
        report = self.report(plans.run(self.tools(["changed", "no_change", "changed"]), steps))
        self.assertEqual((report["ok"], report["failed_step"], len(report["steps"])), (False, 1, 2))

    def test_never_continues(self) -> None:
        steps = [{"tool": "act", "args": {}}] * 3
        report = self.report(plans.run(self.tools(["no_change", "changed", "changed"]), steps, stop_on="never"))
        self.assertEqual(len(report["steps"]), 3)
        self.assertEqual(report["failed_step"], 0)

    def test_require_any_accepts_no_change(self) -> None:
        report = self.report(plans.run(self.tools(["no_change"]), [{"tool": "act", "args": {}, "require": "any"}]))
        self.assertTrue(report["ok"])

    def test_assert_is_an_immediate_wait(self) -> None:
        report = self.report(plans.run(self.tools([]), [{"tool": "assert", "args": {"condition": {}}}]))
        self.assertFalse(report["ok"])

    def test_validation_happens_before_any_step(self) -> None:
        for steps in ([], [{"tool": "run", "args": {}}], [{"tool": "rm", "args": {}}],
                      [{"tool": "act", "args": {}}] * 51):
            with self.subTest(len(steps)), self.assertRaises(core.ToolError):
                plans.run(self.tools(["changed"] * 60), steps)

    def test_each_step_is_published(self) -> None:
        seen = []
        plans.run(self.tools(["changed"]), [{"tool": "act", "args": {"text": "pw"}}],
                  publish=lambda *a: seen.append(a[0]))
        self.assertEqual(seen, ["act"])

    def test_observe_after_is_appended(self) -> None:
        result = plans.run(self.tools(["changed"]), [{"tool": "act", "args": {}}], observe_after={})
        self.assertEqual(result.content[-1]["text"], "snapshot s1")


class LaunchTests(unittest.TestCase):
    def test_script_quotes_and_keeps_the_activation_token(self) -> None:
        text = launch.script(["/usr/bin/app", "a b", "it's"], "/tmp/x y", {"FOO": "1 2"})
        self.assertIn("cd '/tmp/x y' || exit 1", text)
        self.assertIn("export FOO='1 2'", text)
        self.assertIn("reaper.py /usr/bin/app 'a b' 'it'\"'\"'s'", text)
        self.assertTrue(text.splitlines()[-1].startswith("exec "))
        # sway's activation token must reach the app: it places Qt/Chromium windows
        self.assertNotIn("XDG_ACTIVATION_TOKEN", text)

    def test_a11y_flag_added_once(self) -> None:
        argv, profile = launch.with_a11y_flags(["chromium", "--force-renderer-accessibility", "x"], True)
        self.assertEqual(argv.count("--force-renderer-accessibility"), 1)
        argv, _ = launch.with_a11y_flags(["chromium", "x"], True)
        self.assertEqual(argv[1], "--force-renderer-accessibility")
        argv, _ = launch.with_a11y_flags(["chromium", "x"], False)
        self.assertNotIn("--force-renderer-accessibility", argv)

    def test_validation(self) -> None:
        for bad in ([], ["no-such-binary-xyz"], [1]):
            with self.subTest(bad), self.assertRaises(core.ToolError):
                launch.validate(bad, None, None, 20000)
        with self.assertRaises(core.ToolError):
            launch.validate(["sh"], None, {"bad-key": "x"}, 20000)


class FakeSession:
    options = {"settle_ms": 0}

    class log:
        @staticmethod
        def since(t, name=None):
            return []

    def a11y_or_none(self):
        return None


class VerdictTests(unittest.TestCase):
    def base(self, region: bool) -> verify.Baseline:
        return verify.Baseline(0.0, 1, "t", {"x": 0, "y": 0, "width": 1, "height": 1} if region else None,
                               "h0" if region else None, None)

    def test_pixels_changed_and_unchanged(self) -> None:
        with patch.object(verify.capture, "settle", return_value=("h1", True)):
            self.assertEqual(verify.verdict(FakeSession(), self.base(True))["effect"], "changed")
        with patch.object(verify.capture, "settle", return_value=("h0", True)):
            self.assertEqual(verify.verdict(FakeSession(), self.base(True))["effect"], "no_change")

    def test_nothing_observable_is_unverifiable(self) -> None:
        self.assertEqual(verify.verdict(FakeSession(), self.base(False))["effect"], "unverifiable")

    def test_sway_title_event_is_change_but_focus_is_not(self) -> None:
        sess = FakeSession()
        events = [(1.0, "window", {"change": "focus", "container": {"id": 1}})]
        sess.log = type("L", (), {"since": staticmethod(lambda t, name=None: events)})
        with patch.object(verify.capture, "settle", return_value=("h0", True)):
            self.assertEqual(verify.verdict(sess, self.base(True))["effect"], "no_change")
        events.append((1.1, "window", {"change": "title", "container": {"id": 1}}))
        self.assertEqual(verify.verdict(sess, self.base(False))["evidence"], ["sway:title"])


class TimelineSecretTests(unittest.TestCase):
    def test_secrets_never_reach_the_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            timeline.append_event("act", {"target": {"query": {"role": "entry", "name": "Password"}},
                                          "action": "set_text", "text": "hunter2"}, 1.0, True,
                                  {"text": "hunter2", "effect": "confirmed"})
            timeline.append_event("launch", {"argv": ["/usr/bin/app", "--token=s3cret"], "env": {"K": "s3cret"}},
                                  2.0, True, {"argv": ["s3cret"]})
            timeline.append_event("run", {"steps": [{"tool": "type_text", "args": {"text": "hunter2"}}]}, 3.0, True)
            path = timeline.stream_path()
            assert path is not None
            raw = path.read_text(encoding="utf-8")
        for secret in ("hunter2", "s3cret", "Password"):
            self.assertNotIn(secret, raw)
        records = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(records[0]["payload"]["characters"], 7)
        self.assertEqual(records[1]["payload"]["program"], "app")
        self.assertEqual(records[2]["payload"]["tools"], ["type_text"])


if __name__ == "__main__":
    unittest.main()

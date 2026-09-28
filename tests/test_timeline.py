from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import server, timeline


class StreamPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def stream(self) -> Path:
        path = timeline.stream_path()
        assert path is not None
        return path

    def records(self) -> list[dict]:
        path = self.stream()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_writes_one_contract_object_per_action(self) -> None:
        timeline.append_event("click", {"x": 640, "y": 360, "button": "left"}, 12345.678, True)
        record = self.records()[0]
        self.assertEqual(record["at_monotonic"], 12345.678)
        self.assertEqual(record["tool"], "click")
        self.assertIs(record["ok"], True)
        self.assertEqual(record["source"], "computer-use-sway")
        self.assertEqual(record["payload"], {"x": 640, "y": 360, "button": "left"})

    def test_failed_actions_are_published_too(self) -> None:
        timeline.append_event("key", {"key": "Return", "modifiers": []}, 100.0, False)
        self.assertIs(self.records()[0]["ok"], False)

    def test_appends_across_calls(self) -> None:
        timeline.append_event("click", {}, 100.0, True)
        timeline.append_event("scroll", {"direction": "down"}, 101.0, True)
        self.assertEqual([record["tool"] for record in self.records()], ["click", "scroll"])

    def test_typed_text_never_leaves_the_server(self) -> None:
        timeline.append_event("type_text", {"text": "hunter2", "delay_ms": 10}, 100.0, True)
        raw = self.stream().read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)
        self.assertEqual(self.records()[0]["payload"], {"delay_ms": 10, "characters": 7})

    def test_clipboard_content_is_published_as_a_size(self) -> None:
        timeline.append_event("clipboard_set", {"text": "secret"}, 100.0, True)
        raw = self.stream().read_text(encoding="utf-8")
        self.assertNotIn("secret", raw)
        self.assertEqual(self.records()[0]["payload"], {"bytes": 6})

    def test_non_action_tools_are_not_published(self) -> None:
        for name in ("recording_status", "recording_start", "unknown_tool"):
            timeline.append_event(name, {}, 100.0, True)
        self.assertEqual(self.records(), [])

    def test_stream_directory_and_file_are_private(self) -> None:
        timeline.append_event("click", {}, 100.0, True)
        path = self.stream()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_publishing_never_raises(self) -> None:
        blocked = Path(self.tmp.name) / "seshat"
        blocked.write_text("not a directory", encoding="utf-8")
        timeline.append_event("click", {}, 100.0, True)  # must not raise

    def test_without_a_runtime_dir_nothing_is_written(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            self.assertIsNone(timeline.stream_path())
            timeline.append_event("click", {}, 100.0, True)

    def test_reset_stream_truncates_previous_sessions(self) -> None:
        timeline.append_event("click", {}, 100.0, True)
        timeline.reset_stream()
        self.assertEqual(self.records(), [])
        timeline.reset_stream()  # idempotent


class DispatchPublishTests(unittest.TestCase):
    """The hook that keeps a take narratable lives in the tools/call dispatch."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def call(self, name: str, arguments: dict | None = None) -> dict:
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        assert response is not None
        return response

    def published(self) -> list[dict]:
        path = timeline.stream_path()
        assert path is not None
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_a_dispatched_action_is_published_with_its_result(self) -> None:
        def fake(_: dict) -> list[dict[str, str]]:
            return [{"type": "text", "text": "{}"}]

        with patch.dict(server.TOOLS, {"screen_info": fake}):
            response = self.call("screen_info")
        self.assertIs(response["result"]["isError"], False)
        record = self.published()[0]
        self.assertEqual(record["tool"], "screen_info")
        self.assertIs(record["ok"], True)
        self.assertGreater(record["at_monotonic"], 0.0)

    def test_a_failing_action_is_published_as_not_ok(self) -> None:
        def boom(_: dict) -> list[dict[str, str]]:
            raise server.ToolError("no such window")

        with patch.dict(server.TOOLS, {"focus_window": boom}):
            response = self.call("focus_window", {"title": "x"})
        self.assertIs(response["result"]["isError"], True)
        self.assertEqual(self.published()[0]["ok"], False)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_use_sway import cli, core, server


class ProtocolTests(unittest.TestCase):
    def test_initialize_response_names_server(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )

        self.assertIsNotNone(response)
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "computer-use-sway")
        self.assertEqual(result["capabilities"], {"tools": {}})

    def test_initialize_carries_operating_instructions(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {}}
        )

        instructions = response["result"]["instructions"]
        self.assertIsInstance(instructions, str)
        self.assertIn("observe", instructions)
        self.assertIn("no_change", instructions)
        self.assertIn("never paste", instructions)
        self.assertIn("neither records nor narrates", instructions)
        self.assertIn("seshat", instructions)
        self.assertNotIn("recording_status", instructions)

    def test_tools_list_exposes_expected_tools(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "session",
                "screen_info",
                "observe",
                "read",
                "act",
                "run",
                "wait_for",
                "launch",
                "screenshot",
                "window_tree",
                "focus_window",
                "move_pointer",
                "click",
                "drag",
                "scroll",
                "type_text",
                "key",
                "clipboard_set",
                "clipboard_get",
            },
        )

    def test_the_recording_surface_is_gone(self) -> None:
        """Recording and narration moved to seshat; this server must not offer them."""
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}
        )

        specs = {tool["name"]: tool for tool in response["result"]["tools"]}
        for name, schema in specs.items():
            self.assertIs(schema["inputSchema"].get("additionalProperties"), False, name)
            self.assertNotIn("audio", json.dumps(schema), name)
        self.assertFalse([name for name in specs if name.startswith("recording_")])
        for module in ("manager", "narration", "recording", "scenes", "tts", "media"):
            with self.assertRaises(AttributeError, msg=module):
                getattr(server, module)

    def test_every_tool_has_annotations_and_a_strict_schema(self) -> None:
        response = server.handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 19)
        self.assertEqual(tools[0]["name"], "session")
        for tool in tools:
            self.assertIn("readOnlyHint", tool["annotations"], tool["name"])
            self.assertIs(tool["inputSchema"]["additionalProperties"], False, tool["name"])
        no_wtype = json.dumps(tools)
        self.assertNotIn("wtype", no_wtype)

    def test_tool_error_codes_are_published_not_shown(self) -> None:
        def boom(_: dict) -> list:
            raise core.ToolError("stale_ref s1:1: element changed", code="stale_ref")

        with patch.dict(server.TOOLS, {"read": boom}), patch.object(server, "append_event") as publish:
            response = server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                              "params": {"name": "read", "arguments": {"ref": "s1:1"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(publish.call_args.args[4], {"error": "stale_ref"})

    def test_non_object_arguments_are_rejected(self) -> None:
        response = server.handle_message({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                          "params": {"name": "observe", "arguments": [1]}})
        self.assertTrue(response["result"]["isError"])

    def test_unknown_tool_returns_tool_error_result(self) -> None:
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            }
        )

        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIn("unknown tool", result["content"][0]["text"])

    def test_notification_returns_no_response(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        self.assertIsNone(response)

    def test_command_argv_uses_environment_override(self) -> None:
        with patch.dict(os.environ, {core.COMMAND_ENV: "/tmp/cus --flag"}, clear=False):
            self.assertEqual(cli.command_argv(), ["/tmp/cus", "--flag"])

    def test_command_argv_prefers_invoked_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "computer-use-sway"
            script.write_text("#!/bin/sh\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(core.COMMAND_ENV, None)
                with patch.object(core.sys, "argv", [str(script)]):
                    self.assertEqual(cli.command_argv(), [str(script)])


class ParkingTests(unittest.TestCase):
    def test_every_tool_call_parks_the_agent_pointer_even_when_it_fails(self) -> None:
        from types import SimpleNamespace

        from computer_use_sway import session

        parked: list[int] = []
        fake = SimpleNamespace(busy=0)
        fake.park_pointer = lambda: parked.append(fake.busy) or True

        def boom(_arguments: dict) -> list:
            raise core.ToolError("no", code="x")

        sess = session.Session()
        sess._agent = fake
        session.reset_for_tests(sess)
        try:
            with patch.dict(server.TOOLS, {"ok": lambda _a: [], "boom": boom}):
                server.call_tool("ok", {})
                with self.assertRaises(core.ToolError):
                    server.call_tool("boom", {})
        finally:
            session.reset_for_tests(None)
        self.assertEqual(parked, [0, 0])  # after each call, with no call running


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_use_sway import server


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
        self.assertIn("never invent", instructions)
        self.assertIn("verify the visible result", instructions)
        self.assertIn("recording_status", instructions)

    def test_tools_list_exposes_expected_tools(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("screen_info", names)
        self.assertIn("screenshot", names)
        self.assertIn("clipboard_get", names)
        self.assertIn("recording_start", names)
        self.assertIn("recording_status", names)
        self.assertIn("recording_stop", names)

    def test_recording_tools_are_silent_and_closed(self) -> None:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}
        )

        specs = {tool["name"]: tool for tool in response["result"]["tools"]}
        for name in ("recording_start", "recording_status", "recording_stop"):
            schema = specs[name]["inputSchema"]
            self.assertIs(schema.get("additionalProperties"), False, name)
            self.assertNotIn("audio", json.dumps(schema), name)

        start_schema = specs["recording_start"]["inputSchema"]
        self.assertEqual(start_schema["properties"]["format"]["enum"], ["webm", "gif"])
        region = start_schema["properties"]["region"]
        self.assertEqual(region["required"], ["x", "y", "width", "height"])

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
        with patch.dict(os.environ, {server.COMMAND_ENV: "/tmp/cus --flag"}, clear=False):
            self.assertEqual(server.command_argv(), ["/tmp/cus", "--flag"])

    def test_command_argv_prefers_invoked_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "computer-use-sway"
            script.write_text("#!/bin/sh\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(server.COMMAND_ENV, None)
                with patch.object(server.sys, "argv", [str(script)]):
                    self.assertEqual(server.command_argv(), [str(script)])


if __name__ == "__main__":
    unittest.main()

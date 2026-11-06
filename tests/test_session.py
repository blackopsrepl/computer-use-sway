"""Explicit-start and retired-route regression pins; no live desktop."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from computer_use_sway import core, server, session, specs, validation


class ExplicitSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sess = session.Session()
        session.reset_for_tests(self.sess)
        self.addCleanup(session.reset_for_tests)

    def test_no_desktop_tool_implicitly_starts_or_connects(self) -> None:
        with patch.object(self.sess, "start", side_effect=AssertionError("implicit start")), \
                patch.object(self.sess, "ipc", side_effect=AssertionError("desktop connection")):
            for spec in specs.tool_specs():
                if spec["name"] == "session":
                    continue
                with self.subTest(tool=spec["name"]), self.assertRaises(core.ToolError) as caught:
                    server.call_tool(spec["name"], {})
                self.assertEqual(caught.exception.code, "session_required")
        self.assertIsNone(self.sess.mode)
        self.assertIsNone(self.sess._conn)

    def test_status_and_stop_do_not_start_or_connect(self) -> None:
        with patch.object(self.sess, "ipc", side_effect=AssertionError("connection")):
            for action in ("status", "stop", "status"):
                content, _ = server.call_tool("session", {"action": action})
                self.assertIsNone(json.loads(content[0]["text"])["mode"])

    def test_old_mode_is_rejected_without_connecting(self) -> None:
        with patch.object(self.sess, "ipc", side_effect=AssertionError("connection")):
            with self.assertRaises(core.ToolError):
                self.sess.start("seat")
        self.assertIsNone(self.sess.mode)

    def test_old_routes_are_rejected_by_advertised_schema(self) -> None:
        cases = [("session", {"action": "start", "mode": "seat"}),
                 ("session", {"action": "start", "options": {"foreground": "always"}}),
                 ("observe", {"scope": "agent"}),
                 ("act", {"target": {"x": 1, "y": 1}, "action": "click", "delivery": "agent"}),
                 ("act", {"target": {"x": 1, "y": 1}, "action": "click", "delivery": "foreground"}),
                 ("focus_window", {"con_id": 1, "move_to_agent": True})]
        cases += [(s["name"], {"seat": "agent"}) for s in specs.tool_specs()
                  if "seat" in s["inputSchema"].get("properties", {})]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(core.ToolError):
                validation.arguments(name, args)
        schema = next(s for s in specs.tool_specs() if s["name"] == "session")
        self.assertEqual(schema["inputSchema"]["properties"]["mode"]["default"], "user")

    def test_stdio_refuses_implicit_desktop_with_sdk_import_blocked(self) -> None:
        runner = '''import builtins, runpy
original = builtins.__import__
def checked(name, *a, **k):
    if name == "mcp" or name.startswith("mcp."):
        raise ImportError("MCP SDK forbidden")
    return original(name, *a, **k)
builtins.__import__ = checked
runpy.run_module("computer_use_sway", run_name="__main__")
'''
        frames = [{"id": 1, "method": "initialize"},
                  {"method": "notifications/initialized"},
                  {"id": 2, "method": "tools/call", "params": {"name": "screenshot", "arguments": {}}},
                  {"id": 3, "method": "tools/call", "params": {"name": "session", "arguments": {"action": "status"}}}]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, XDG_RUNTIME_DIR=tmp)
            proc = subprocess.run([sys.executable, "-c", runner], input="\n".join(map(json.dumps, frames)),
                                  capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual([r["id"] for r in rows], [1, 2, 3])
            self.assertTrue(rows[1]["result"]["isError"])
            status = json.loads(rows[2]["result"]["content"][0]["text"])
            self.assertIsNone(status["mode"])
            self.assertNotIn("agent", status)
            self.assertFalse((Path(tmp) / "computer-use-sway").exists())
            events = [json.loads(l) for l in (Path(tmp) / "seshat/streams/computer-use-sway.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["payload"]["error"], "session_required")


if __name__ == "__main__":
    unittest.main()

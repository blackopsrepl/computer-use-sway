"""T5.1: the real MCP stdio server against a sandbox sway."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from computer_use_sway import sandbox

from harness import needs_sway, targets

SRC = str(Path(__file__).resolve().parents[2] / "src")


@needs_sway
class McpStdioTests(unittest.TestCase):
    def test_t5_1_tools_list_observe_act_and_the_timeline(self) -> None:
        with sandbox.Sandbox(width=1280, height=800) as box:
            assets = str(box.root / "assets")
            targets.write_assets(assets)
            env = dict(box.env, PYTHONPATH=SRC)
            server = subprocess.Popen([sys.executable, "-m", "computer_use_sway"], env=env, text=True,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            next_id = iter(range(1, 100))

            def rpc(method: str, params: dict | None = None) -> dict:
                assert server.stdin is not None and server.stdout is not None
                rid = next(next_id)
                server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                               "params": params or {}}) + "\n")
                server.stdin.flush()
                reply = json.loads(server.stdout.readline())
                self.assertEqual(reply["id"], rid)
                return reply["result"]

            def tool(name: str, args: dict) -> str:
                result = rpc("tools/call", {"name": name, "arguments": args})
                self.assertFalse(result["isError"], result)
                return result["content"][0]["text"]

            try:
                self.assertEqual(rpc("initialize")["serverInfo"]["name"], "computer-use-sway")
                self.assertEqual(len(rpc("tools/list")["tools"]), 19)
                argv, app_env = targets.argv("gtk3", assets)
                json.loads(tool("launch", {"argv": argv, "env": app_env}))
                listing = tool("observe", {"query": {"role": "entry", "name": "Field"}})
                ref = next(line.split()[0] for line in listing.splitlines() if '"Field"' in line)
                out = json.loads(tool("act", {"target": {"ref": ref}, "action": "set_text", "text": "s3cret-text"}))
                self.assertEqual(out["effect"], "confirmed")
            finally:
                assert server.stdin is not None
                server.stdin.close()
                server.wait(timeout=30)
            stream = box.root / "seshat" / "streams" / "computer-use-sway.jsonl"
            raw = stream.read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertGreaterEqual(len(records), 3)
            act = next(r for r in records if r["tool"] == "act")
            self.assertEqual(act["payload"]["effect"], "confirmed")
            self.assertEqual(act["payload"]["characters"], len("s3cret-text"))
            self.assertNotIn("s3cret-text", raw)
            self.assertEqual(os.stat(stream).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

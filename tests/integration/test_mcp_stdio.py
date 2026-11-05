"""T5.1: the real MCP stdio server against a sandbox sway."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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

    def test_t5_1_the_hermes_mcp_client_lists_all_tools(self) -> None:
        # The annotations are keys the 2024-11-05 protocol does not define;
        # the client must still list every tool.
        hermes = shutil.which("hermes")
        if hermes is None:
            self.skipTest("needs the hermes CLI")
        with sandbox.Sandbox(width=1280, height=800) as box, tempfile.TemporaryDirectory() as home:
            env_keys = ("XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "SWAYSOCK", "DBUS_SESSION_BUS_ADDRESS")
            server_env = {key: box.env[key] for key in env_keys if key in box.env}
            server_env["PYTHONPATH"] = SRC
            config = {"mcp_servers": {"cus": {"command": sys.executable, "args": ["-m", "computer_use_sway"],
                                              "env": server_env}}}
            # JSON is YAML: no YAML writer needed.
            Path(home, "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            proc = subprocess.Popen([hermes, "mcp", "test", "cus"], env=dict(os.environ, HERMES_HOME=home),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                # Name what hung instead of leaving a bare timeout.
                tree = subprocess.run(["ps", "-o", "pid,stat,wchan:20,args", "--ppid", str(proc.pid), "-p",
                                       str(proc.pid)], capture_output=True, text=True).stdout
                os.killpg(proc.pid, 9)
                stdout, stderr = proc.communicate()
                self.fail(f"hermes mcp test hung for 120 s\nprocesses:\n{tree}\nstdout:\n{stdout[-2000:]}"
                          f"\nstderr:\n{stderr[-2000:]}")
            out = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("Tools discovered: 19", out.stdout)
            for name in ("observe", "act", "run", "clipboard_get"):
                self.assertIn(name, out.stdout)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_use_sway import agent, core, install, sandbox, sentinel, session, swayipc, vinput


class FakeIpc:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.out = [{"name": "DP-1", "active": True, "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
        self.ws = [{"name": "1", "output": "DP-1"}]
        self.seat_list = [{"name": "seat0", "focus": 5, "devices": [1, 2, 3]},
                          {"name": "cus-agent", "focus": 0, "devices": [1, 2]}]

    def command(self, cmd: str) -> list:
        self.commands.append(cmd)
        if cmd == "create_output":
            self.out.append({"name": "HEADLESS-1", "active": True,
                             "rect": {"x": 1920, "y": 0, "width": 1920, "height": 1080}})
            self.ws.append({"name": "2", "output": "HEADLESS-1"})
        if cmd.startswith("rename workspace"):
            self.ws[-1]["name"] = "cus-agent"
        return [{"success": True}]

    def outputs(self) -> list:
        return self.out

    def workspaces(self) -> list:
        return self.ws

    def seats(self) -> list:
        return self.seat_list

    def tree(self) -> dict:
        return {"type": "root", "nodes": []}

    def config(self) -> str:
        return "include ~/.config/sway/config.d/*\n"


class FakeKeyboard:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def chord(self, key: str, mods=None) -> None:
        self.log.append(f"key:{key}")


class FakeDevices:
    def __init__(self, log: list[str]) -> None:
        self.keyboard = FakeKeyboard(log)
        self.recreated = 0

    def recreate(self) -> None:
        self.recreated += 1

    def destroy(self) -> None:
        pass


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ipc = FakeIpc()
        self.devices = FakeDevices(self.ipc.commands)
        self.agent = agent.Agent(self.ipc, None, swayipc.EventLog(), agent.StateFile(Path(tmp.name) / "s.json"),
                                 devices_factory=lambda: self.devices)

    def test_pins_precede_create_output_and_workspace_is_renamed(self) -> None:
        self.agent.start()
        cmds = self.ipc.commands
        self.assertLess(cmds.index('output "DP-1" position 0 0'), cmds.index("create_output"))
        self.assertIn('output "HEADLESS-1" position 100000 0 resolution 1920x1080', cmds)
        self.assertIn('rename workspace "2" to cus-agent', cmds)
        self.assertFalse([c for c in cmds if c.startswith("bindsym")], "never bind at runtime")

    def test_refuses_without_configured_seat(self) -> None:
        self.ipc.seat_list = self.ipc.seat_list[:1]
        with self.assertRaises(core.ToolError) as caught:
            self.agent.start()
        self.assertEqual(caught.exception.code, "agent_seat_missing")
        self.assertNotIn("create_output", self.ipc.commands)

    def test_nop_follows_every_tap(self) -> None:
        self.agent.start()
        self.agent.tap(install.LAUNCH_KEY)
        cmds = self.ipc.commands
        self.assertEqual(cmds[cmds.index(f"key:{install.LAUNCH_KEY}") + 1], "nop")

    def test_reload_gates_input_then_recovers(self) -> None:
        self.agent.start()
        self.agent._on_event("workspace", {"change": "reload"})
        with self.assertRaises(vinput.AgentSeatLost):
            self.agent.gate()
        self.ipc.commands.clear()
        self.agent.ensure_ready()
        self.assertEqual(self.devices.recreated, 1)
        self.assertIn('output "DP-1" position 0 0', self.ipc.commands)
        self.assertIn('output "HEADLESS-1" position 100000 0 resolution 1920x1080', self.ipc.commands)
        self.agent.gate()

    def test_recovery_refuses_when_devices_land_on_seat0(self) -> None:
        self.agent.start()
        self.agent._on_event("workspace", {"change": "reload"})
        seats = self.ipc.seat_list

        def recreate_onto_seat0() -> None:  # the new devices end up on seat0
            seats[0]["devices"] = seats[0]["devices"] + [9, 10]
            seats[1]["devices"] = []

        self.devices.recreate = recreate_onto_seat0
        with self.assertRaises(core.ToolError):
            self.agent.ensure_ready()

    def test_adopts_a_saved_live_output(self) -> None:
        self.agent.start()
        self.ipc.commands.clear()
        again = agent.Agent(self.ipc, None, swayipc.EventLog(), self.agent.state, devices_factory=lambda: self.devices)
        again.start()
        self.assertNotIn("create_output", self.ipc.commands)
        self.assertEqual(again.output, "HEADLESS-1")


class InstallTests(unittest.TestCase):
    def test_render_has_seat_and_bindings_but_no_workspace_binding(self) -> None:
        text = install.render(Path("/run/user/1/computer-use-sway/launch/next.sh"))
        self.assertIn("seat cus-agent fallback false", text)
        self.assertIn('[con_mark="_cus_focus"] focus', text)
        self.assertNotIn("workspace ", "\n".join(l for l in text.splitlines() if l.startswith("bindsym")))

    def test_included(self) -> None:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/u/.config", "HOME": "/home/u"}):
            path = install.config_path()
            self.assertTrue(install.included("include ~/.config/sway/config.d/*\n", path))
            self.assertTrue(install.included("include /home/u/.config/sway/config.d/*.conf", path))
            self.assertFalse(install.included("include ~/.config/sway/other/*", path))
            self.assertFalse(install.included("", path))

    def test_conflicts(self) -> None:
        self.assertEqual(install.conflicts("bindsym XF86Macro29 exec foo\nbindsym a exec b"),
                         ["bindsym XF86Macro29 exec foo"])


class SentinelTests(unittest.TestCase):
    class Conn:
        def __init__(self) -> None:
            self.sent: list = []
            self.handlers: dict = {}
            self.next = 10

        def has_global(self, name: str) -> bool:
            return True

        def bind(self, name: str, version: int) -> tuple:
            return 3, 1

        def seats(self) -> dict:
            return {"seat0": 4}

        def new_id(self) -> int:
            self.next += 1
            return self.next

        def send(self, obj, opcode, payload=b"") -> None:
            self.sent.append((obj, opcode, payload))

        def set_handler(self, obj, fn) -> None:
            self.handlers[obj] = fn

        def roundtrip(self) -> None:
            pass

        def dispatch(self, timeout: float) -> None:
            for fn in self.handlers.values():
                fn(0, b"")  # idled

    def test_idle_notification_encoding_and_event(self) -> None:
        conn = self.Conn()
        s = sentinel.Sentinel(FakeIpc(), conn)
        self.assertTrue(s.wait_user_idle(1500, 100))
        obj, opcode, payload = conn.sent[0]
        self.assertEqual((obj, opcode), (3, 1))
        self.assertEqual(struct.unpack("=III", payload), (11, 1500, 4))

    def test_restore_only_when_focus_moved(self) -> None:
        ipc = FakeIpc()
        s = sentinel.Sentinel(ipc, self.Conn())
        snap = s.snapshot()
        self.assertFalse(s.restore(snap))
        ipc.seat_list[0]["focus"] = 9
        self.assertTrue(s.restore(snap))
        self.assertEqual(ipc.commands[-1], "[con_id=5] focus")


class SessionTests(unittest.TestCase):
    def test_option_validation(self) -> None:
        self.assertEqual(session.validate_options({"idle_ms": 10})["idle_ms"], 10)
        for bad in ({"bogus": 1}, {"foreground": "sometimes"}, {"idle_ms": -1}, {"idle_ms": True},
                    {"foreground_pointer": "yes"}):
            with self.subTest(bad), self.assertRaises(core.ToolError):
                session.validate_options(bad)

    def test_user_mode_refuses_the_agent(self) -> None:
        s = session.Session()
        s.mode = "user"
        with self.assertRaises(core.ToolError):
            s.agent()
        self.assertEqual(s.default_seat(), "user")


class SandboxTests(unittest.TestCase):
    def test_config_disables_xwayland_and_declares_the_seat(self) -> None:
        text = sandbox.render_config(Path("/r/sandbox-1-1"), 800, 600, 2.0)
        self.assertIn("xwayland disable", text)
        self.assertIn("seat cus-agent fallback false", text)
        self.assertIn("scale 2", text)

    def test_stop_never_removes_outside_its_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "computer-use-sway"
            prefix.mkdir()
            outside = Path(tmp) / "precious"
            outside.mkdir()
            box = sandbox.Sandbox(prefix=prefix)
            box.root, box._created = outside, True
            box.stop()
            self.assertTrue(outside.exists())
            box.root, box._created = prefix / "sandbox-x", False
            box.root.mkdir()
            box.stop()  # not created by this instance
            self.assertTrue(box.root.exists())


class AnchorTests(unittest.TestCase):
    def test_running_follows_the_lock(self) -> None:
        import fcntl
        import os
        import tempfile

        from computer_use_sway import anchor

        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(anchor.install, "runtime_root", return_value=Path(tmp)):
            self.assertFalse(anchor.running())
            fd = os.open(Path(tmp) / anchor.PID_NAME, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                self.assertFalse(anchor.running())  # file without a holder
                fcntl.flock(fd, fcntl.LOCK_EX)
                pid = os.fork()
                if pid == 0:  # another process sees the lock held
                    os._exit(0 if anchor.running() else 1)
                self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), 0)
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()

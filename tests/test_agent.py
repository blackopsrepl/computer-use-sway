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


class FakePointer:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.position: tuple[int, int] | None = None

    def move_to(self, x: int, y: int) -> None:
        self.position = (x, y)
        self.log.append(f"pointer:{x},{y}")


class FakeDevices:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.keyboard = FakeKeyboard(log)
        self.pointer = FakePointer(log)
        self.recreated = 0

    def recreate(self) -> None:
        self.recreated += 1
        self.log.append("devices:recreate")

    def destroy(self) -> None:
        self.log.append("devices:destroy")


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ipc = FakeIpc()
        self.devices = FakeDevices(self.ipc.commands)
        self.agent = agent.Agent(self.ipc, None, swayipc.EventLog(), agent.StateFile(Path(tmp.name) / "s.json"),
                                 devices_factory=self.make_devices)

    def make_devices(self) -> FakeDevices:
        self.ipc.commands.append("devices:create")
        return self.devices

    def test_the_agent_pointer_exists_only_with_its_cursor_on_the_agent_output(self) -> None:
        # A seat with a pointer draws a cursor; with no agent output sway puts
        # it on the user's screen [I8].
        self.agent.start()
        cmds = self.ipc.commands
        park = "seat cus-agent cursor set 2880 540"  # the centre of HEADLESS-1
        self.assertLess(cmds.index("create_output"), cmds.index(park))
        self.assertLess(cmds.index(park), cmds.index("devices:create"))
        cmds.clear()
        self.agent.stop()
        self.assertLess(cmds.index("devices:destroy"), cmds.index('output "HEADLESS-1" unplug'))

    def test_the_cursor_goes_home_by_a_seat_warp_wherever_it_was(self) -> None:
        # sway warps the cursor to the seat's focus without telling the pointer,
        # so parking does not trust the recorded position [I9].
        self.agent.start()
        self.devices.pointer.move_to(2000, 600)  # recorded on the agent output
        self.ipc.commands.clear()
        self.assertTrue(self.agent.park_pointer())
        self.assertEqual(self.devices.pointer.position, (2880, 540))
        # a seat warp, never pointer motion, which would move the agent's focus
        self.assertEqual(self.ipc.commands, ["seat cus-agent cursor set 2880 540"])
        self.agent._on_event("workspace", {"change": "reload"})
        self.assertFalse(self.agent.park_pointer())  # stale: recovery parks it instead

    def test_between_calls_a_focus_change_parks_the_cursor(self) -> None:
        self.agent.start()
        park = "seat cus-agent cursor set 2880 540"
        self.ipc.commands.clear()
        self.agent.busy = 1
        self.agent._on_event("window", {"change": "close"})
        self.assertNotIn(park, self.ipc.commands)  # a call is running: its cursor is its own
        self.agent.busy = 0
        self.agent._on_event("window", {"change": "title"})
        self.assertNotIn(park, self.ipc.commands)
        self.agent._on_event("window", {"change": "close"})
        self.assertIn(park, self.ipc.commands)

    def test_reload_parks_the_cursor_before_the_devices_return(self) -> None:
        self.agent.start()
        self.agent._on_event("workspace", {"change": "reload"})
        self.ipc.commands.clear()
        self.agent.ensure_ready()
        cmds = self.ipc.commands
        self.assertLess(cmds.index("seat cus-agent cursor set 2880 540"), cmds.index("devices:recreate"))

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

    def test_a_seat_change_that_moves_the_devices_gates_input_and_recovers(self) -> None:
        # Any runtime `seat ...` command re-sorts devices and the agent's fall
        # back to seat0 with no event [S12]: input must stop before it reaches
        # the user's seat, and the next call recovers.
        self.agent.start()
        seats = self.ipc.seat_list
        seats[1]["devices"], seats[0]["devices"] = [], seats[0]["devices"] + [8, 9]
        with self.assertRaises(vinput.AgentSeatLost):
            self.agent.gate()

        def recreate_on_agent_seat() -> None:
            self.devices.recreated += 1
            seats[0]["devices"], seats[1]["devices"] = seats[0]["devices"][:-2], [10, 11]

        self.devices.recreate = recreate_on_agent_seat
        self.agent.ensure_ready()
        self.assertEqual(self.devices.recreated, 1)
        self.assertIsNone(self.agent.stale)
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
        again = agent.Agent(self.ipc, None, swayipc.EventLog(), self.agent.state, devices_factory=self.make_devices)
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


class SigtermTests(unittest.TestCase):
    def test_sigterm_becomes_system_exit_unless_someone_else_handles_it(self) -> None:
        import signal

        previous = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            sandbox.exit_on_sigterm()
            with self.assertRaises(SystemExit) as raised:
                os.kill(os.getpid(), signal.SIGTERM)  # the handler runs before kill returns
            self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)

            def mine(*_args: object) -> None:
                pass

            signal.signal(signal.SIGTERM, mine)
            sandbox.exit_on_sigterm()
            self.assertIs(signal.getsignal(signal.SIGTERM), mine)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    unittest.main()

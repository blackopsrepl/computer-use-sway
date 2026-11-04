from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from computer_use_sway import placement, swayipc


class FakeIpc:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, cmd: str) -> list[dict]:
        self.commands.append(cmd)
        return [{"success": True}]


class CriterionTests(unittest.TestCase):
    def test_app_ids_become_quote_safe_exact_patterns(self) -> None:
        self.assertEqual(placement.criterion(42, "org.gnome.Settings"),
                         '[pid=42 app_id="^org[.]gnome[.]Settings$"]')
        self.assertEqual(placement.criterion(7, "bench-dialogs"), '[pid=7 app_id="^bench-dialogs$"]')

    def test_inexpressible_app_ids_fall_back_to_pid(self) -> None:
        self.assertEqual(placement.criterion(7, 'a"b'), "[pid=7]")
        self.assertEqual(placement.criterion(7, None), "[pid=7]")


class PlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ipc, self.log = FakeIpc(), swayipc.EventLog()
        self.p = placement.Placement(self.ipc, self.log, "cus-agent")

    def tearDown(self) -> None:
        self.p.close()

    def test_adopt_assigns_once_and_records_the_launch_root(self) -> None:
        with patch.object(placement.desktop, "ancestors", return_value=[100, 90, 80]), \
                patch.object(placement, "is_reaper", side_effect=lambda pid: pid == 90):
            self.p.adopt({"pid": 100, "app_id": "x"})
            self.p.adopt({"pid": 100, "app_id": "x"})
        self.assertEqual(self.ipc.commands, ['assign [pid=100 app_id="^x$"] workspace cus-agent'])
        self.assertEqual(self.p.roots, {90})

    def test_reapply_after_reload_keeps_only_live_processes(self) -> None:
        self.p.keys = {(os.getpid(), "live"), (2 ** 22 + 1, "gone")}
        self.p.reapply()
        self.assertEqual(self.ipc.commands, ['assign [pid=%d app_id="^live$"] workspace cus-agent' % os.getpid()])

    def test_an_escaped_window_of_an_agent_app_is_moved_back(self) -> None:
        self.p.roots = {90}
        body = {"change": "new", "container": {"id": 9, "pid": 101, "app_id": "x"}}
        with patch.object(placement.desktop, "ancestors", return_value=[101, 90]), \
                patch.object(placement, "is_reaper", return_value=False), \
                patch.object(placement.desktop, "window_by_id", return_value={"id": 9, "workspace": "1"}):
            self.log.append("window", body)
        self.assertEqual(self.ipc.commands[-1], "[con_id=9] move container to workspace cus-agent")
        self.assertEqual(self.p.escaped, [9])

    def test_user_windows_are_left_alone(self) -> None:
        self.p.roots = {90}
        with patch.object(placement.desktop, "ancestors", return_value=[300, 1]), \
                patch.object(placement, "is_reaper", return_value=False), \
                patch.object(placement.desktop, "window_by_id", return_value={"id": 3, "workspace": "1"}):
            self.log.append("window", {"change": "new", "container": {"id": 3, "pid": 300, "app_id": "y"}})
        self.assertEqual(self.ipc.commands, [])

    def test_a_user_window_caught_by_a_stale_criterion_is_handed_back(self) -> None:
        with patch.object(placement.desktop, "ancestors", return_value=[300, 1]), \
                patch.object(placement, "is_reaper", return_value=False), \
                patch.object(placement.desktop, "seat_workspace", return_value="2: web"), \
                patch.object(placement.desktop, "window_by_id", return_value={"id": 3, "workspace": "cus-agent"}):
            self.log.append("window", {"change": "new", "container": {"id": 3, "pid": 300, "app_id": "y"}})
        self.assertEqual(self.ipc.commands, ['[con_id=3] move container to workspace "2: web"'])
        self.assertEqual(self.p.returned, [3])

    def test_expect_registers_the_child_before_it_maps(self) -> None:
        with patch.object(placement.desktop, "parent_pid", return_value=90), \
                patch.object(placement, "is_reaper", return_value=True):
            self.p.expect(100)
        self.assertEqual(self.ipc.commands, ["assign [pid=100] workspace cus-agent"])
        self.assertEqual(self.p.roots, {90})


if __name__ == "__main__":
    unittest.main()

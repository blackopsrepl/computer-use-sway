from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import core, pointer


class AvailabilityTests(unittest.TestCase):
    def test_available_follows_write_access(self) -> None:
        with patch.object(os, "access", return_value=True):
            self.assertTrue(pointer.available())
        with patch.object(os, "access", return_value=False):
            self.assertFalse(pointer.available())

    def test_require_explains_what_to_fix(self) -> None:
        """A missing device must name the remedy, not raise a bare OSError.

        The alternative a caller would otherwise see is `PermissionError:
        [Errno 13]`, which says nothing about the udev rule that fixes it.
        """
        with patch.object(os, "access", return_value=False):
            with self.assertRaises(core.ToolError) as caught:
                pointer.require()
        message = str(caught.exception)
        self.assertIn("/dev/uinput", message)
        self.assertIn("not writable", message)


class MoveTests(unittest.TestCase):
    """`move` must split long hops: one report beyond the acceleration curve's
    linear region moves less than it asks for, so the pointer drifts short."""

    def device(self) -> tuple[pointer.VirtualPointer, list[tuple[int, int, int]]]:
        emitted: list[tuple[int, int, int]] = []

        class Fake(pointer.VirtualPointer):
            def __init__(self) -> None:  # noqa: D107 - test double
                self._closed = False

            def _emit(self, etype: int, code: int, value: int) -> None:
                emitted.append((etype, code, value))

        return Fake(), emitted

    def test_short_move_is_one_report(self) -> None:
        dev, emitted = self.device()
        dev.move(10, -5, pause_s=0)
        rel = [e for e in emitted if e[0] == pointer.EV_REL]
        self.assertEqual(
            rel, [(pointer.EV_REL, pointer.REL_X, 10), (pointer.EV_REL, pointer.REL_Y, -5)]
        )

    def test_long_move_is_split_and_adds_up(self) -> None:
        dev, emitted = self.device()
        dev.move(300, -1000, pause_s=0)
        xs = [e[2] for e in emitted if e[1] == pointer.REL_X]
        ys = [e[2] for e in emitted if e[1] == pointer.REL_Y]
        self.assertEqual(sum(xs), 300)
        self.assertEqual(sum(ys), -1000)
        self.assertTrue(all(abs(v) <= pointer._MAX_STEP for v in xs + ys))
        self.assertGreater(len(xs), 1)

    def test_a_sync_follows_every_report(self) -> None:
        """An unsynced report is not delivered until the next one arrives."""
        dev, emitted = self.device()
        dev.move(300, 0, pause_s=0)
        syns = [e for e in emitted if e[0] == pointer.EV_SYN]
        reports = [e for e in emitted if e[0] == pointer.EV_REL and e[1] == pointer.REL_X]
        self.assertEqual(len(syns), len(reports))


class PressReleaseTests(unittest.TestCase):
    def test_press_and_release_carry_the_button_state(self) -> None:
        emitted: list[tuple[int, int, int]] = []

        class Fake(pointer.VirtualPointer):
            def __init__(self) -> None:
                self._closed = False

            def _emit(self, etype: int, code: int, value: int) -> None:
                emitted.append((etype, code, value))

        dev = Fake()
        dev.press()
        dev.release()
        keys = [e for e in emitted if e[0] == pointer.EV_KEY]
        self.assertEqual(keys, [(pointer.EV_KEY, pointer.BTN_LEFT, 1), (pointer.EV_KEY, pointer.BTN_LEFT, 0)])


class DragTests(unittest.TestCase):
    """The sequence is the whole point: the button must be down *before* the
    path is walked, and up only afterwards."""

    def test_button_is_held_across_the_whole_path(self) -> None:
        events: list[str] = []
        warps: list[tuple[str, ...]] = []

        class Fake(pointer.VirtualPointer):
            def __init__(self, name: str = "x") -> None:
                self._closed = False

            def press(self, button: int = pointer.BTN_LEFT) -> None:
                events.append("press")

            def release(self, button: int = pointer.BTN_LEFT) -> None:
                events.append("release")

            def move(self, dx: int, dy: int, pause_s: float = 0.0) -> None:
                events.append(f"move {dx},{dy}")

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> None:
                self.close()

        with patch.object(pointer, "VirtualPointer", Fake):
            pointer.drag((100, 100), (200, 40), steps=4, duration_ms=0,
                         sway_cursor=lambda *parts: warps.append(parts))

        # The press must come before any step of the path, and the release after
        # all of them: that is what makes it a drag rather than a click.
        first_press = events.index("press")
        release = events.index("release")
        steps = [i for i, e in enumerate(events) if e.startswith("move")]
        self.assertTrue(all(i < first_press for i in steps[:2]))
        self.assertTrue(all(first_press < i < release for i in steps[2:]))

        # The whole offset, delivered in steps of the requested size.
        path = [e for e in events[first_press:release] if e.startswith("move")]
        self.assertEqual(len(path), 4)
        self.assertEqual(sum(int(m.split()[1].split(",")[0]) for m in path), 100)
        self.assertEqual(sum(int(m.split()[1].split(",")[1]) for m in path), -60)
        # Absolute placement happens once, before anything moves.
        self.assertEqual(warps, [("set", "100", "100")])

    def test_the_device_moves_before_it_presses(self) -> None:
        """A press is delivered where the compositor thinks the pointer is, and
        a new device has no position, so a press sent first goes nowhere."""
        events: list[str] = []

        class Fake(pointer.VirtualPointer):
            def __init__(self, name: str = "x") -> None:
                self._closed = False

            def press(self, button: int = pointer.BTN_LEFT) -> None:
                events.append("press")

            def release(self, button: int = pointer.BTN_LEFT) -> None:
                events.append("release")

            def move(self, dx: int, dy: int, pause_s: float = 0.0) -> None:
                events.append(f"move {dx},{dy}")

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> None:
                self.close()

        with patch.object(pointer, "VirtualPointer", Fake):
            pointer.drag((10, 10), (20, 20), steps=2, duration_ms=0,
                         sway_cursor=lambda *parts: None)

        self.assertEqual(events[0], "move 1,0")
        self.assertEqual(events[1], "move -1,0")
        self.assertEqual(events[2], "press")


if __name__ == "__main__":
    unittest.main()

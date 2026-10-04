"""Pointer motion through a virtual input device.

Sway's IPC cursor warp cannot drag. `swaymsg seat <seat> cursor set x y` reports
success for every step and the compositor does not deliver the motion between a
press and its release: the events arrive only after the button comes up, so a
drag renders as a press and a release at the same point, with the whole path
flushed afterwards as a plain hover. That is measured, not assumed — an app that
logs its own mouse events receives

    move (pressed=false) → press → release → move (pressed=false)

and no movement at all while the button is down.

Relative motion through a virtual device does not have that problem, because the
compositor sees an ordinary pointer moving with a button held. This module emits
those events directly through `/dev/uinput` using only the standard library,
which keeps the server's `dependencies = []` contract intact.

Absolute positioning still goes through Sway: a warp sets the pointer exactly,
and the relative device then continues from there. Neither half alone is enough
— the warp is precise but cannot move under a press, and the device is smooth but
has no notion of where the pointer started.
"""

from __future__ import annotations

import fcntl
import os
import struct
import time

from . import core

# ioctl numbers from <linux/uinput.h>. These are stable ABI, not per-kernel.
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
REL_X = 0x00
REL_Y = 0x01
BTN_LEFT = 0x110
SYN_REPORT = 0

_INPUT_EVENT = "llHHi"
_UINPUT_SETUP = "HHHH80sI"

# A single relative report is clamped by the pointer acceleration curve, so long
# hops are split. 120 is comfortably inside the curve's linear region.
_MAX_STEP = 120

DEVICE_PATH = "/dev/uinput"

DEVICE_HINT = (
    f"{DEVICE_PATH} is not writable. Dragging needs a virtual pointer, which "
    "the kernel only exposes to a process that may write that node. Add a udev "
    'rule ("KERNEL==\\"uinput\\", MODE=\\"0666\\"") or run the server as a user '
    "in a group that owns it."
)


__all__ = ["VirtualPointer", "available", "require", "drag", "DEVICE_PATH", "DEVICE_HINT"]


def available() -> bool:
    """Whether a virtual pointer can be created right now."""
    return os.access(DEVICE_PATH, os.W_OK)


def require() -> None:
    """Raise a ToolError explaining what to fix, rather than a bare OSError."""
    if not available():
        raise core.ToolError(DEVICE_HINT)


class VirtualPointer:
    """A relative pointer device, valid only for the life of the context."""

    def __init__(self, name: str = "computer-use-sway-pointer") -> None:
        require()
        try:
            self._fd = os.open(DEVICE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:  # pragma: no cover - depends on host policy
            raise core.ToolError(DEVICE_HINT) from exc
        try:
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self._fd, UI_SET_KEYBIT, BTN_LEFT)
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_REL)
            fcntl.ioctl(self._fd, UI_SET_RELBIT, REL_X)
            fcntl.ioctl(self._fd, UI_SET_RELBIT, REL_Y)
            # bus 3 is USB; vendor, product and version are arbitrary but must
            # be present, and the struct ends with an unused effect count.
            fcntl.ioctl(
                self._fd,
                UI_DEV_SETUP,
                struct.pack(_UINPUT_SETUP, 3, 1, 1, 1, name.encode(), 0),
            )
            fcntl.ioctl(self._fd, UI_DEV_CREATE)
        except OSError as exc:  # pragma: no cover - depends on kernel support
            os.close(self._fd)
            raise core.ToolError(f"could not create a virtual pointer: {exc}") from exc
        # The compositor has to notice and open the device before its events are
        # meaningful. Without this pause the first reports are dropped and a
        # drag loses its opening steps.
        time.sleep(0.35)
        self._closed = False

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack(_INPUT_EVENT, 0, 0, etype, code, value))

    def _sync(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    def press(self, button: int = BTN_LEFT) -> None:
        self._emit(EV_KEY, button, 1)
        self._sync()

    def release(self, button: int = BTN_LEFT) -> None:
        self._emit(EV_KEY, button, 0)
        self._sync()

    def move(self, dx: int, dy: int, pause_s: float = 0.004) -> None:
        """Move by a relative offset, split into reports the curve accepts."""
        while dx or dy:
            step_x = max(-_MAX_STEP, min(_MAX_STEP, dx))
            step_y = max(-_MAX_STEP, min(_MAX_STEP, dy))
            if step_x:
                self._emit(EV_REL, REL_X, step_x)
            if step_y:
                self._emit(EV_REL, REL_Y, step_y)
            self._sync()
            dx -= step_x
            dy -= step_y
            if pause_s:
                time.sleep(pause_s)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        finally:
            os.close(self._fd)

    def __enter__(self) -> "VirtualPointer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def drag(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int,
    duration_ms: int,
    sway_cursor,
) -> None:
    """Drag from `start` to `end`, holding the button across the whole path.

    `sway_cursor` is the Sway warp, injected so this module does not reach back
    into the desktop layer: it is used once, to place the pointer exactly, after
    which the path is walked by the virtual device.
    """
    start_x, start_y = start
    end_x, end_y = end
    sway_cursor("set", str(start_x), str(start_y))
    time.sleep(0.08)  # the warp is asynchronous; let it land

    with VirtualPointer() as pointer:
        # Establish the device's position before pressing. A press is delivered
        # to whatever surface the compositor believes the pointer is over, and a
        # freshly created device has no position yet — so a press sent as the
        # device's very first event goes nowhere, and the drag then looks like
        # motion that never grabbed anything. Two one-pixel reports put the
        # pointer over the target without moving it.
        pointer.move(1, 0, pause_s=0)
        pointer.move(-1, 0, pause_s=0)
        time.sleep(0.05)
        pointer.press()
        time.sleep(0.05)
        pause_s = min(0.02, duration_ms / 1000.0 / max(steps, 1))
        sent_x = sent_y = 0
        for step in range(1, steps + 1):
            want_x = round((end_x - start_x) * step / steps)
            want_y = round((end_y - start_y) * step / steps)
            pointer.move(want_x - sent_x, want_y - sent_y, pause_s=pause_s)
            sent_x, sent_y = want_x, want_y
        # The release is deliberately not instant: a compositor that sees press
        # and release in the same report can treat a drag as a click.
        time.sleep(0.1)
        pointer.release()
        time.sleep(0.1)

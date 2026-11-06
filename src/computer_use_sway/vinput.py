"""Virtual pointer and keyboard attached to the existing user seat."""

from __future__ import annotations

import os
import struct
import time
from typing import Callable

from . import core, keymap
from .wire import Connection, wl_fixed

VPTR_MGR_CREATE = 0
VPTR_MOTION, VPTR_MOTION_ABS, VPTR_BUTTON, VPTR_AXIS, VPTR_FRAME = 0, 1, 2, 3, 4
VPTR_AXIS_SOURCE, VPTR_AXIS_STOP, VPTR_AXIS_DISCRETE, VPTR_DESTROY = 5, 6, 7, 8
VKBD_MGR_CREATE = 0
VKBD_KEYMAP, VKBD_KEY, VKBD_MODIFIERS, VKBD_DESTROY = 0, 1, 2, 3
KEYMAP_FORMAT_XKB_V1 = 1

BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}
SCROLL = {"up": (0, -1), "down": (0, 1), "left": (1, -1), "right": (1, 1)}
DEVICE_SETTLE_S = 0.3
KEYMAP_SETTLE_S = 0.1  # clients apply a new keymap asynchronously

Gate = Callable[[], None] | None
Extent = Callable[[], tuple[int, int]]


def _now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def _default_extent() -> tuple[int, int]:
    from . import desktop

    return desktop.layout_extent()


class VirtualPointer:
    def __init__(self, conn: Connection, seat_obj: int, gate: Gate = None, extent: Extent | None = None) -> None:
        self.conn = conn
        self.gate = gate
        self.extent = extent or _default_extent
        manager, _ = conn.bind("zwlr_virtual_pointer_manager_v1", 2)
        self.id = conn.new_id()
        conn.send(manager, VPTR_MGR_CREATE, struct.pack("=II", seat_obj, self.id))
        conn.roundtrip()
        self.position: tuple[int, int] | None = None
        self.sent = 0

    def _send(self, opcode: int, payload: bytes = b"") -> None:
        if self.gate is not None:
            self.gate()
        self.conn.send(self.id, opcode, payload)
        self.sent += 1

    def move_to(self, x: int, y: int) -> None:
        width, height = self.extent()
        if not (0 <= x < width and 0 <= y < height):
            raise core.ToolError(f"point {x},{y} is outside the output layout")
        self._send(VPTR_MOTION_ABS, struct.pack("=IIIII", _now_ms(), x, y, width, height))
        self._send(VPTR_FRAME)
        self.conn.roundtrip()
        self.position = (x, y)

    def button(self, name: str, pressed: bool) -> None:
        code = BTN.get(name)
        if code is None:
            raise core.ToolError("button must be left, middle, or right")
        self._send(VPTR_BUTTON, struct.pack("=III", _now_ms(), code, 1 if pressed else 0))
        self._send(VPTR_FRAME)
        self.conn.roundtrip()

    def click(self, x: int, y: int, button: str = "left", count: int = 1, interval_ms: int = 80) -> None:
        self.move_to(x, y)
        time.sleep(0.05)
        for index in range(count):
            self.button(button, True)
            time.sleep(0.03)
            self.button(button, False)
            if index + 1 < count:
                time.sleep(interval_ms / 1000.0)

    def drag(self, start: tuple[int, int], end: tuple[int, int], button: str = "left",
             steps: int = 12, duration_ms: int = 350) -> None:
        self.move_to(*start)
        time.sleep(0.05)
        self.button(button, True)
        time.sleep(0.05)
        pause = min(0.02, duration_ms / 1000.0 / max(steps, 1))
        for step in range(1, steps + 1):
            x = round(start[0] + (end[0] - start[0]) * step / steps)
            y = round(start[1] + (end[1] - start[1]) * step / steps)
            self.move_to(x, y)
            if pause:
                time.sleep(pause)
        time.sleep(0.1)
        self.button(button, False)
        time.sleep(0.1)

    def scroll(self, direction: str, clicks: int) -> None:
        if direction not in SCROLL:
            raise core.ToolError("direction must be up, down, left, or right")
        axis, sign = SCROLL[direction]
        for _ in range(clicks):
            self._send(VPTR_AXIS_SOURCE, struct.pack("=I", 0))
            self._send(VPTR_AXIS_DISCRETE, struct.pack("=IIii", _now_ms(), axis, wl_fixed(15.0 * sign), sign))
            self._send(VPTR_FRAME)
            self.conn.roundtrip()
            time.sleep(0.01)

    def destroy(self) -> None:
        """Idempotent: a second destroy request on a dead id is a protocol
        error that would end the whole Wayland connection."""
        if getattr(self, "destroyed", False):
            return
        self.destroyed = True
        try:
            self.conn.send(self.id, VPTR_DESTROY)
        except core.ToolError:
            pass


class VirtualKeyboard:
    def __init__(self, conn: Connection, seat_obj: int, gate: Gate = None) -> None:
        self.conn = conn
        self.gate = gate
        manager, _ = conn.bind("zwp_virtual_keyboard_manager_v1", 1)
        self.id = conn.new_id()
        conn.send(manager, VKBD_MGR_CREATE, struct.pack("=II", seat_obj, self.id))
        self.slots: list[str | None] = [None] * len(keymap.SLOTS)
        self.pinned: set[str] = set()
        self._used: dict[str, float] = {}
        self._upload()
        self.sent = 0

    @property
    def private(self) -> list[str]:
        return [sym for sym in self.slots if sym is not None]

    def _assign(self, wanted: list[str], settle: bool = True) -> None:
        """Bind keysyms to slots of their pool (evicting least recently used
        ones) and upload."""
        changed = False
        for sym in wanted:
            if sym in self.slots:
                continue
            pool = keymap.TEXT_POOL if keymap.is_text_keysym(sym) else keymap.KEY_POOL
            free = [i for i in pool if self.slots[i] is None]
            if free:
                index = free[0]
            else:
                protect = self.pinned | set(wanted)
                victims = [i for i in pool if self.slots[i] not in protect]
                if not victims:
                    raise core.ToolError("too many distinct special characters at once for the keymap")
                index = min(victims, key=lambda i: self._used.get(self.slots[i] or "", 0.0))
            self.slots[index] = sym
            changed = True
        if changed:
            self._upload()
            if settle:
                time.sleep(KEYMAP_SETTLE_S)

    def _upload(self) -> None:
        text = keymap.build_keymap(self.slots)
        fd = os.memfd_create("cus-keymap", os.MFD_CLOEXEC)
        try:
            os.write(fd, text)
            self.conn.send(self.id, VKBD_KEYMAP, struct.pack("=II", KEYMAP_FORMAT_XKB_V1, len(text)), fds=[fd])
        finally:
            os.close(fd)
        self.conn.roundtrip()

    def _send(self, opcode: int, payload: bytes) -> None:
        if self.gate is not None:
            self.gate()
        self.conn.send(self.id, opcode, payload)
        self.sent += 1

    def _raw(self, code: int, pressed: bool) -> None:
        self._send(VKBD_KEY, struct.pack("=III", _now_ms(), code, 1 if pressed else 0))

    def _mods(self, mods: list[str]) -> None:
        mask = 0
        for name in mods:
            mask |= keymap.MOD_MASK[name]
        self._send(VKBD_MODIFIERS, struct.pack("=IIII", mask, 0, 0, 0))

    def code_for(self, keysym: str) -> int:
        if keysym in keymap.NAMED:
            return keymap.NAMED[keysym]
        if len(keysym) == 1 and keysym in keymap.US and not keymap.US[keysym][1]:
            return keymap.US[keysym][0]
        self._assign([keysym])
        self._used[keysym] = time.monotonic()
        return keymap.SLOTS[self.slots.index(keysym)][0]

    def chord_code(self, code: int, mods: list[str], hold: float = 0.012) -> None:
        for name in mods:
            if name not in keymap.MOD_KEYS:
                raise core.ToolError("modifiers may only contain ctrl, shift, alt, or logo")
        for name in mods:
            self._raw(keymap.MOD_KEYS[name], True)
        if mods:
            self._mods(mods)
        self._raw(code, True)
        self.conn.roundtrip()
        time.sleep(hold)
        self._raw(code, False)
        for name in reversed(mods):
            self._raw(keymap.MOD_KEYS[name], False)
        if mods:
            self._mods([])
        self.conn.roundtrip()

    def chord(self, key: str, modifiers: list[str] | None = None) -> None:
        keysym = keymap.normalize_key(key)
        mods = list(modifiers or [])
        if len(keysym) == 1 and keysym in keymap.US and keymap.US[keysym][1] and "shift" not in mods:
            mods.append("shift")
        if len(keysym) == 1 and keysym in keymap.US:
            self.chord_code(keymap.US[keysym][0], mods)
        else:
            self.chord_code(self.code_for(keysym), mods)

    def type_text(self, text: str, delay_ms: int = 0) -> int:
        """Type text; returns the number of characters delivered."""
        if "\x00" in text:
            raise core.ToolError("text must not contain NUL bytes")
        if len(text) > core.TEXT_LIMIT:
            raise core.ToolError(f"text exceeds limit of {core.TEXT_LIMIT} characters")
        # Bind the text's special characters up front (as many as fit) so the
        # keymap is not swapped between keystrokes in the common case.
        special: list[str] = []
        for ch in text:
            if ch not in keymap.US:
                sym = keymap.keysym_for_char(ch)
                if sym not in keymap.NAMED and sym not in special:
                    special.append(sym)
        text_special = [sym for sym in special if keymap.is_text_keysym(sym)]
        if text_special:
            self._assign(text_special[:len(keymap.TEXT_POOL)])
        typed = 0
        for ch in text:
            if ch in keymap.US:
                code, shifted = keymap.US[ch]
                self.chord_code(code, ["shift"] if shifted else [], hold=0.008)
            else:
                self.chord_code(self.code_for(keymap.keysym_for_char(ch)), [], hold=0.008)
            typed += 1
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
        return typed

    def destroy(self) -> None:
        """Idempotent, as VirtualPointer.destroy."""
        if getattr(self, "destroyed", False):
            return
        self.destroyed = True
        try:
            self.conn.send(self.id, VKBD_DESTROY)
        except core.ToolError:
            pass


class SeatDevices:
    """One pointer and one keyboard on a named seat, recreatable."""

    def __init__(self, conn: Connection, seat_name: str, gate: Gate = None, extent: Extent | None = None) -> None:
        self.conn = conn
        self.seat_name = seat_name
        self.gate = gate
        self.extent = extent
        self.pointer: VirtualPointer
        self.keyboard: VirtualKeyboard
        self._create()

    def _create(self) -> None:
        seat_obj = self.conn.wait_seat(self.seat_name)
        self.pointer = VirtualPointer(self.conn, seat_obj, self.gate, self.extent)
        self.keyboard = VirtualKeyboard(self.conn, seat_obj, self.gate)
        time.sleep(DEVICE_SETTLE_S)

    def recreate(self) -> None:
        self.destroy()
        self.conn.roundtrip()
        self._create()

    def destroy(self) -> None:
        self.pointer.destroy()
        self.keyboard.destroy()

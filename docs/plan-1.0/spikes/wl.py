"""Minimal stdlib Wayland wire client (spike, reference implementation).

Only what the agent needs: registry, wl_seat names, wl_output names,
zwlr_virtual_pointer_v1 and zwp_virtual_keyboard_v1 bound to a chosen seat.
Opcodes come from the protocol XML files in ./protocols (request order).
"""
from __future__ import annotations

import array
import os
import socket
import struct
import time

# request opcodes
WL_DISPLAY_SYNC, WL_DISPLAY_GET_REGISTRY = 0, 1
WL_REGISTRY_BIND = 0
VPTR_MGR_CREATE, VPTR_MGR_DESTROY, VPTR_MGR_CREATE_WITH_OUTPUT = 0, 1, 2
VPTR_MOTION, VPTR_MOTION_ABS, VPTR_BUTTON, VPTR_AXIS, VPTR_FRAME = 0, 1, 2, 3, 4
VPTR_AXIS_SOURCE, VPTR_AXIS_STOP, VPTR_AXIS_DISCRETE, VPTR_DESTROY = 5, 6, 7, 8
VKBD_MGR_CREATE = 0
VKBD_KEYMAP, VKBD_KEY, VKBD_MODIFIERS, VKBD_DESTROY = 0, 1, 2, 3
# event opcodes
WL_DISPLAY_ERROR, WL_DISPLAY_DELETE_ID = 0, 1
WL_REGISTRY_GLOBAL, WL_REGISTRY_GLOBAL_REMOVE = 0, 1
WL_CALLBACK_DONE = 0
WL_SEAT_CAPABILITIES, WL_SEAT_NAME = 0, 1
WL_OUTPUT_NAME = 4  # since v4: geometry0 mode1 done2 scale3 name4 description5

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
KEYMAP_FORMAT_XKB_V1 = 1


class WaylandError(Exception):
    pass


def _string(value: str) -> bytes:
    raw = value.encode() + b"\0"
    return struct.pack("=I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _fixed(value: float) -> int:
    return int(round(value * 256.0))


class Connection:
    def __init__(self, display: str | None = None, runtime_dir: str | None = None):
        display = display or os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        runtime_dir = runtime_dir or os.environ["XDG_RUNTIME_DIR"]
        path = display if display.startswith("/") else os.path.join(runtime_dir, display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.next_id = 2  # 1 is wl_display
        self.buf = b""
        self.handlers: dict[int, object] = {}
        self.globals: dict[int, tuple[str, int]] = {}
        self.registry = self.new_id()
        self.send(1, WL_DISPLAY_GET_REGISTRY, struct.pack("=I", self.registry))
        self.handlers[self.registry] = self._on_registry
        self.handlers[1] = self._on_display
        self.roundtrip()

    # -- plumbing -------------------------------------------------------
    def new_id(self) -> int:
        oid = self.next_id
        self.next_id += 1
        return oid

    def send(self, obj: int, opcode: int, payload: bytes = b"", fds: list[int] | None = None) -> None:
        msg = struct.pack("=II", obj, ((8 + len(payload)) << 16) | opcode) + payload
        if fds:
            self.sock.sendmsg([msg], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
        else:
            self.sock.sendall(msg)

    def _on_display(self, opcode: int, body: bytes) -> None:
        if opcode == WL_DISPLAY_ERROR:
            obj, code, length = struct.unpack_from("=III", body)
            text = body[12:12 + length - 1].decode(errors="replace")
            raise WaylandError(f"protocol error on object {obj} code {code}: {text}")

    def _on_registry(self, opcode: int, body: bytes) -> None:
        if opcode == WL_REGISTRY_GLOBAL:
            name, length = struct.unpack_from("=II", body)
            iface = body[8:8 + length - 1].decode()
            (version,) = struct.unpack_from("=I", body, 8 + length + (-length % 4))
            self.globals[name] = (iface, version)
        elif opcode == WL_REGISTRY_GLOBAL_REMOVE:
            self.globals.pop(struct.unpack_from("=I", body)[0], None)

    def dispatch(self, timeout: float = 1.0) -> None:
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(65536)
        except socket.timeout:
            return
        if not data:
            raise WaylandError("compositor closed the connection")
        self.buf += data
        while len(self.buf) >= 8:
            obj, word = struct.unpack_from("=II", self.buf)
            size, opcode = word >> 16, word & 0xFFFF
            if len(self.buf) < size:
                break
            body, self.buf = self.buf[8:size], self.buf[size:]
            handler = self.handlers.get(obj)
            if handler:
                handler(opcode, body)

    def roundtrip(self, timeout: float = 2.0) -> None:
        done = []
        cb = self.new_id()
        self.handlers[cb] = lambda op, body: done.append(op)
        self.send(1, WL_DISPLAY_SYNC, struct.pack("=I", cb))
        deadline = time.monotonic() + timeout
        while not done:
            if time.monotonic() > deadline:
                raise WaylandError("roundtrip timed out")
            self.dispatch(0.2)

    def bind(self, iface: str, max_version: int, name: int | None = None) -> tuple[int, int]:
        for gname, (giface, gversion) in self.globals.items():
            if giface == iface and (name is None or gname == name):
                version = min(max_version, gversion)
                oid = self.new_id()
                self.send(self.registry, WL_REGISTRY_BIND,
                          struct.pack("=I", gname) + _string(iface) + struct.pack("=II", version, oid))
                return oid, version
        raise WaylandError(f"compositor does not advertise {iface}")

    # -- seats ----------------------------------------------------------
    def seats(self) -> dict[str, int]:
        """Bind every wl_seat and return {seat name: object id}."""
        found: dict[str, int] = {}
        for gname, (iface, _v) in list(self.globals.items()):
            if iface != "wl_seat":
                continue
            oid, _ = self.bind("wl_seat", 2, name=gname)

            def on_seat(op, body, oid=oid):
                if op == WL_SEAT_NAME:
                    (length,) = struct.unpack_from("=I", body)
                    found[body[4:4 + length - 1].decode()] = oid
            self.handlers[oid] = on_seat
        self.roundtrip()
        return found


class VirtualPointer:
    """zwlr_virtual_pointer_v1 bound to one seat. Absolute motion needs an extent."""

    def __init__(self, conn: Connection, seat_id: int):
        self.c = conn
        mgr, self.mgr_version = conn.bind("zwlr_virtual_pointer_manager_v1", 2)
        self.id = conn.new_id()
        conn.send(mgr, VPTR_MGR_CREATE, struct.pack("=II", seat_id, self.id))
        conn.roundtrip()

    @staticmethod
    def _t() -> int:
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def move_abs(self, x: int, y: int, width: int, height: int) -> None:
        self.c.send(self.id, VPTR_MOTION_ABS, struct.pack("=IIIII", self._t(), x, y, width, height))
        self.c.send(self.id, VPTR_FRAME)

    def move_rel(self, dx: float, dy: float) -> None:
        self.c.send(self.id, VPTR_MOTION, struct.pack("=Iii", self._t(), _fixed(dx), _fixed(dy)))
        self.c.send(self.id, VPTR_FRAME)

    def button(self, button: int, pressed: bool) -> None:
        self.c.send(self.id, VPTR_BUTTON, struct.pack("=III", self._t(), button, 1 if pressed else 0))
        self.c.send(self.id, VPTR_FRAME)

    def scroll(self, axis: int, discrete: int) -> None:
        # axis 0 vertical, 1 horizontal; 15 units per wheel detent like libinput
        self.c.send(self.id, VPTR_AXIS_SOURCE, struct.pack("=I", 0))
        self.c.send(self.id, VPTR_AXIS_DISCRETE,
                    struct.pack("=IIii", self._t(), axis, _fixed(15.0 * discrete), discrete))
        self.c.send(self.id, VPTR_FRAME)

    def flush(self) -> None:
        self.c.roundtrip()


# Standard evdev keycodes + US symbols, plus private keycodes for any other
# keysym. Apps that look at physical key codes (Chromium, games, terminals
# with the kitty keyboard protocol) then see what a real keyboard sends.
US_ROWS = [
    ("1234567890-=", "!@#$%^&*()_+", 2),
    ("qwertyuiop[]", "QWERTYUIOP{}", 16),
    ("asdfghjkl;'`", 'ASDFGHJKL:"~', 30),
    ("\\zxcvbnm,./", "|ZXCVBNM<>?", 43),
]
US = {}
for plain, shifted, first in US_ROWS:
    for i, (a, b) in enumerate(zip(plain, shifted)):
        US[a] = (first + i, False)
        US[b] = (first + i, True)
US[" "] = (57, False)
NAMED = {
    "Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "space": 57,
    "Control_L": 29, "Shift_L": 42, "Alt_L": 56, "Super_L": 125, "Shift_R": 54,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66,
    "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "Home": 102, "Up": 103, "Prior": 104, "Left": 105, "Right": 106, "End": 107,
    "Down": 108, "Next": 109, "Insert": 110, "Delete": 111, "Menu": 127,
}
MOD_KEYS = {"shift": 42, "ctrl": 29, "alt": 56, "logo": 125}
PRIVATE_BASE = 600  # evdev codes >= 600 are unassigned; xkb code = evdev + 8

KEYMAP = """xkb_keymap {
xkb_keycodes "cus" { include "evdev" %s };
xkb_types "cus" { include "complete" };
xkb_compatibility "cus" { include "complete" };
xkb_symbols "cus" { include "pc+us+inet(evdev)" %s };
};
"""


class VirtualKeyboard:
    """zwp_virtual_keyboard_v1 bound to one seat.

    Keymap = evdev keycodes + US layout, so ASCII goes out on its real
    physical key (with a real Shift press for shifted characters). Any other
    keysym (non-ASCII text, F13-F35, XF86 keys) gets a private keycode; the
    keymap is re-uploaded when the private set changes.
    """

    def __init__(self, conn: Connection, seat_id: int, extra_keysyms: list[str] | None = None):
        self.c = conn
        mgr, _ = conn.bind("zwp_virtual_keyboard_manager_v1", 1)
        self.id = conn.new_id()
        conn.send(mgr, VKBD_MGR_CREATE, struct.pack("=II", seat_id, self.id))
        self.private: list[str] = []
        self.ensure(extra_keysyms or [])

    def ensure(self, keysyms: list[str]) -> None:
        missing = [k for k in keysyms if k not in NAMED and k not in self.private]
        if not missing and self.private:
            return
        self.private += missing
        codes = " ".join(f"<P{i}> = {PRIVATE_BASE + i + 8};" for i in range(len(self.private)))
        syms = " ".join(f"key <P{i}> {{[ {k} ]}};" for i, k in enumerate(self.private))
        text = (KEYMAP % (codes, syms)).encode() + b"\0"
        fd = os.memfd_create("cus-keymap", os.MFD_CLOEXEC)
        os.write(fd, text)
        self.c.send(self.id, VKBD_KEYMAP, struct.pack("=II", KEYMAP_FORMAT_XKB_V1, len(text)), fds=[fd])
        os.close(fd)
        self.c.roundtrip()

    @staticmethod
    def _t() -> int:
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def raw(self, evdev: int, pressed: bool) -> None:
        self.c.send(self.id, VKBD_KEY, struct.pack("=III", self._t(), evdev, 1 if pressed else 0))

    def code_for(self, keysym: str) -> int:
        if keysym in NAMED:
            return NAMED[keysym]
        if len(keysym) == 1 and keysym in US and not US[keysym][1]:
            return US[keysym][0]
        self.ensure([keysym])
        return PRIVATE_BASE + self.private.index(keysym)

    def chord(self, keysym: str, mods: list[str] | None = None, hold: float = 0.012) -> None:
        self.chord_code(self.code_for(keysym), mods or [], hold)

    # back-compat with spike1
    def tap(self, keysym: str, mods: list[str] | None = None, hold: float = 0.012) -> None:
        if len(keysym) == 1 and keysym in US:
            code, shift = US[keysym]
            return self.chord_code(code, ["shift"] if shift else [], hold)
        self.chord(keysym, mods, hold)

    # xkb real-modifier bits in the evdev/US keymap: Shift=1 Control=4 Mod1=8 Mod4=64
    MOD_MASK = {"shift": 1, "ctrl": 4, "alt": 8, "logo": 64}

    def set_mods(self, mods: list[str]) -> None:
        # wlroots does not derive modifier state from a virtual keyboard's key
        # events (update_state=false); the client must send it explicitly.
        mask = 0
        for m in mods:
            mask |= self.MOD_MASK[m]
        self.c.send(self.id, VKBD_MODIFIERS, struct.pack("=IIII", mask, 0, 0, 0))

    def chord_code(self, code: int, mods: list[str], hold: float) -> None:
        for m in mods:
            self.raw(MOD_KEYS[m], True)
        if mods:
            self.set_mods(mods)
        self.raw(code, True)
        self.c.roundtrip()
        time.sleep(hold)
        self.raw(code, False)
        for m in reversed(mods):
            self.raw(MOD_KEYS[m], False)
        if mods:
            self.set_mods([])
        self.c.roundtrip()

    def type(self, text: str, delay: float = 0.0) -> None:
        for ch in text:
            if ch in US:
                code, shift = US[ch]
                self.chord_code(code, ["shift"] if shift else [], 0.008)
            elif ch == "\n":
                self.chord("Return")
            elif ch == "\t":
                self.chord("Tab")
            else:
                self.chord("U%04X" % ord(ch))
            if delay:
                time.sleep(delay)


def keysym_for(char: str) -> str:
    named = {" ": "space", "\n": "Return", "\t": "Tab"}
    if char in named:
        return named[char]
    if char.isascii() and char.isprintable():
        return char
    return "U%04X" % ord(char)

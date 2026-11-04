"""A minimal Wayland wire client, standard library only.

Only what the agent needs: the registry, seat names, and binding the
virtual-pointer, virtual-keyboard and idle-notifier globals. Messages are
encoded by hand (header: object id, then size<<16 | opcode, native endian);
file descriptors travel with SCM_RIGHTS. The connection is owned by the main
thread and is not thread-safe by design.
"""

from __future__ import annotations

import array
import os
import socket
import struct
import time
from typing import Callable

from . import core

WL_DISPLAY_SYNC, WL_DISPLAY_GET_REGISTRY = 0, 1
WL_DISPLAY_ERROR, WL_DISPLAY_DELETE_ID = 0, 1
WL_REGISTRY_BIND = 0
WL_REGISTRY_GLOBAL, WL_REGISTRY_GLOBAL_REMOVE = 0, 1
WL_SEAT_NAME = 1

Handler = Callable[[int, bytes], None]


class WaylandError(core.ToolError):
    """The compositor rejected a request or the connection broke."""


def wl_string(value: str) -> bytes:
    raw = value.encode("utf-8") + b"\0"
    return struct.pack("=I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def wl_fixed(value: float) -> int:
    return int(round(value * 256.0))


def read_string(body: bytes, offset: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("=I", body, offset)
    text = body[offset + 4 : offset + 4 + max(0, length - 1)].decode("utf-8", errors="replace")
    return text, offset + 4 + length + (-length % 4)


class Connection:
    def __init__(self, display: str | None = None, runtime_dir: str | None = None) -> None:
        core.ensure_session_environment()
        display = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR") or ""
        path = display if display.startswith("/") else os.path.join(runtime_dir, display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(path)
        except OSError as exc:
            self.sock.close()
            raise WaylandError(f"cannot connect to the Wayland display {path}: {exc}") from exc
        self._next_id = 2
        self._buf = b""
        self._handlers: dict[int, Handler] = {1: self._on_display}
        self.globals: dict[int, tuple[str, int]] = {}
        self._seat_objects: dict[int, int] = {}  # global name -> bound object id
        self._seat_names: dict[int, str] = {}  # bound object id -> seat name
        self.registry = self.new_id()
        self.send(1, WL_DISPLAY_GET_REGISTRY, struct.pack("=I", self.registry))
        self._handlers[self.registry] = self._on_registry
        self.roundtrip()

    # -- plumbing ----------------------------------------------------------
    def new_id(self) -> int:
        oid = self._next_id
        self._next_id += 1
        return oid

    def set_handler(self, obj: int, fn: Handler) -> None:
        self._handlers[obj] = fn

    def send(self, obj: int, opcode: int, payload: bytes = b"", fds: list[int] | None = None) -> None:
        message = struct.pack("=II", obj, ((8 + len(payload)) << 16) | opcode) + payload
        try:
            if fds:
                sent = self.sock.sendmsg(
                    [message], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))]
                )
                if sent < len(message):
                    self.sock.sendall(message[sent:])
            else:
                self.sock.sendall(message)
        except OSError as exc:
            raise WaylandError(f"lost the Wayland connection: {exc}") from exc

    def _on_display(self, opcode: int, body: bytes) -> None:
        if opcode == WL_DISPLAY_ERROR:
            obj, code = struct.unpack_from("=II", body)
            text, _ = read_string(body, 8)
            raise WaylandError(f"Wayland protocol error on object {obj} (code {code}): {text}")
        if opcode == WL_DISPLAY_DELETE_ID:
            (oid,) = struct.unpack_from("=I", body)
            self._handlers.pop(oid, None)

    def _on_registry(self, opcode: int, body: bytes) -> None:
        if opcode == WL_REGISTRY_GLOBAL:
            (name,) = struct.unpack_from("=I", body)
            iface, offset = read_string(body, 4)
            (version,) = struct.unpack_from("=I", body, offset)
            self.globals[name] = (iface, version)
        elif opcode == WL_REGISTRY_GLOBAL_REMOVE:
            (name,) = struct.unpack_from("=I", body)
            self.globals.pop(name, None)
            oid = self._seat_objects.pop(name, None)
            if oid is not None:
                self._seat_names.pop(oid, None)

    def dispatch(self, timeout: float = 0.0) -> None:
        """Read and handle whatever arrives within `timeout` seconds."""
        self.sock.settimeout(max(timeout, 0.0001))
        try:
            data = self.sock.recv(65536)
        except (socket.timeout, BlockingIOError):
            return
        except OSError as exc:
            raise WaylandError(f"lost the Wayland connection: {exc}") from exc
        if not data:
            raise WaylandError("the compositor closed the Wayland connection")
        self._buf += data
        while len(self._buf) >= 8:
            obj, word = struct.unpack_from("=II", self._buf)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8 or len(self._buf) < size:
                break
            body, self._buf = self._buf[8:size], self._buf[size:]
            handler = self._handlers.get(obj)
            if handler is not None:
                handler(opcode, body)

    def roundtrip(self, timeout: float = 2.0) -> None:
        done: list[int] = []
        callback = self.new_id()
        self._handlers[callback] = lambda opcode, body: done.append(opcode)
        self.send(1, WL_DISPLAY_SYNC, struct.pack("=I", callback))
        deadline = time.monotonic() + timeout
        while not done:
            if time.monotonic() > deadline:
                raise WaylandError("the compositor did not answer a roundtrip")
            self.dispatch(0.1)
        self._handlers.pop(callback, None)

    def has_global(self, iface: str) -> bool:
        return any(i == iface for i, _ in self.globals.values())

    def bind(self, iface: str, max_version: int, name: int | None = None) -> tuple[int, int]:
        for gname, (giface, gversion) in self.globals.items():
            if giface == iface and (name is None or gname == name):
                version = min(max_version, gversion)
                oid = self.new_id()
                self.send(
                    self.registry,
                    WL_REGISTRY_BIND,
                    struct.pack("=I", gname) + wl_string(iface) + struct.pack("=II", version, oid),
                )
                return oid, version
        raise WaylandError(f"the compositor does not offer {iface}")

    # -- seats ---------------------------------------------------------------
    def seats(self) -> dict[str, int]:
        """Seat name -> bound wl_seat object id. Binds new seat globals once."""
        fresh = False
        for gname, (iface, _version) in list(self.globals.items()):
            if iface != "wl_seat" or gname in self._seat_objects:
                continue
            oid, _ = self.bind("wl_seat", 2, name=gname)
            self._seat_objects[gname] = oid

            def on_seat(opcode: int, body: bytes, oid: int = oid) -> None:
                if opcode == WL_SEAT_NAME:
                    self._seat_names[oid], _ = read_string(body, 0)

            self._handlers[oid] = on_seat
            fresh = True
        if fresh:
            self.roundtrip()
        return {name: oid for oid, name in self._seat_names.items()}

    def wait_seat(self, name: str, timeout: float = 2.0) -> int:
        """The wl_seat global of a new seat arrives after Sway's IPC reply."""
        deadline = time.monotonic() + timeout
        while True:
            self.roundtrip()
            found = self.seats().get(name)
            if found is not None:
                return found
            if time.monotonic() > deadline:
                raise WaylandError(f"Wayland seat {name!r} does not exist")
            time.sleep(0.02)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

"""Sway IPC over `$SWAYSOCK`, standard library only.

The server used to shell out to `swaymsg` for every query. Talking to the
socket directly removes a binary dependency and, more importantly, gives us an
event subscription: reloads, new windows and focus changes arrive as events
the instant they happen, which is what the agent seat's recovery and the
action verdicts are built on.

Wire format (i3 IPC): b"i3-ipc" + uint32 length + uint32 type, native endian,
followed by a JSON payload. Event replies carry the high bit in their type.
"""

from __future__ import annotations

import collections
import json
import os
import socket
import struct
import threading
import time
from typing import Any, Callable

from . import core

MAGIC = b"i3-ipc"
RUN_COMMAND, GET_WORKSPACES, SUBSCRIBE, GET_OUTPUTS, GET_TREE = 0, 1, 2, 3, 4
GET_MARKS, GET_VERSION, GET_CONFIG, SEND_TICK = 5, 7, 9, 10
GET_INPUTS, GET_SEATS = 100, 101
EVENT_NAMES = {
    0: "workspace",
    1: "output",
    2: "mode",
    3: "window",
    4: "barconfig_update",
    5: "binding",
    6: "shutdown",
    7: "tick",
    0x14: "bar_state_update",
    0x15: "input",
}
_EVENT_BIT = 0x80000000

Event = tuple[float, str, dict]


def socket_path() -> str:
    core.ensure_session_environment()
    path = os.environ.get("SWAYSOCK")
    if not path:
        raise core.ToolError("not running inside a Sway session: SWAYSOCK is not set")
    return path


class Ipc:
    """One request/response connection. Used from the main thread."""

    def __init__(self, path: str | None = None, timeout: float = 5.0) -> None:
        self.path = path or socket_path()
        self._lock = threading.Lock()
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._connect()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise core.ToolError(f"cannot connect to the Sway IPC socket {self.path}: {exc}") from exc
        self._sock = sock

    def _recv_exact(self, count: int) -> bytes:
        if self._sock is None:
            raise ConnectionError("IPC socket closed")
        chunks = bytearray()
        while len(chunks) < count:
            chunk = self._sock.recv(count - len(chunks))
            if not chunk:
                raise ConnectionError("sway closed the IPC socket")
            chunks += chunk
        return bytes(chunks)

    def read_message(self) -> tuple[int, Any]:
        head = self._recv_exact(14)
        if head[:6] != MAGIC:
            raise ConnectionError("bad IPC magic")
        length, kind = struct.unpack("=II", head[6:])
        body = self._recv_exact(length) if length else b""
        return kind, (json.loads(body) if body else None)

    def _roundtrip(self, kind: int, payload: str) -> Any:
        raw = payload.encode("utf-8")
        assert self._sock is not None
        self._sock.sendall(MAGIC + struct.pack("=II", len(raw), kind) + raw)
        _, reply = self.read_message()
        return reply

    def call(self, kind: int, payload: str = "") -> Any:
        with self._lock:
            try:
                return self._roundtrip(kind, payload)
            except (OSError, ConnectionError):
                self.close()
                self._connect()
                try:
                    return self._roundtrip(kind, payload)
                except (OSError, ConnectionError) as exc:
                    raise core.ToolError(f"lost the Sway IPC socket: {exc}") from exc

    def command(self, cmd: str) -> list[dict]:
        reply = self.call(RUN_COMMAND, cmd)
        if not isinstance(reply, list):
            raise core.ToolError(f"sway: unexpected reply to {cmd!r}")
        for element in reply:
            if isinstance(element, dict) and not element.get("success", False):
                raise core.ToolError(f"sway: {element.get('error', 'command failed')} ({cmd})")
        return reply

    def tree(self) -> dict:
        return self.call(GET_TREE)

    def outputs(self) -> list[dict]:
        return self.call(GET_OUTPUTS)

    def workspaces(self) -> list[dict]:
        return self.call(GET_WORKSPACES)

    def seats(self) -> list[dict]:
        return self.call(GET_SEATS)

    def config(self) -> str:
        reply = self.call(GET_CONFIG) or {}
        return str(reply.get("config", ""))

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class EventLog:
    """Bounded, timestamped event history with condition-variable waits."""

    def __init__(self, maxlen: int = 4096) -> None:
        self._events: collections.deque[Event] = collections.deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self._listeners: list[Callable[[str, dict], None]] = []

    def append(self, name: str, body: dict) -> None:
        with self._cond:
            self._events.append((time.monotonic(), name, body))
            self._cond.notify_all()
        for listener in list(self._listeners):
            try:
                listener(name, body)
            except Exception as exc:  # a listener must never kill the reader
                core.eprint(f"event listener failed: {exc}")

    def add_listener(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, dict], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def since(self, t: float, name: str | None = None) -> list[Event]:
        with self._cond:
            return [e for e in self._events if e[0] >= t and (name is None or e[1] == name)]

    def wait_for(self, predicate: Callable[[Event], bool], since: float, timeout: float) -> Event | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                for event in self._events:
                    if event[0] >= since and predicate(event):
                        return event
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)


class Subscription(threading.Thread):
    """Event reader on its own socket, feeding an EventLog."""

    def __init__(self, path: str, kinds: list[str], log: EventLog) -> None:
        super().__init__(daemon=True, name="sway-ipc-events")
        self.log = log
        self.dead = False
        self._ipc = Ipc(path, timeout=5.0)
        reply = self._ipc.call(SUBSCRIBE, json.dumps(kinds))
        if not isinstance(reply, dict) or not reply.get("success"):
            raise core.ToolError(f"sway refused the event subscription: {reply}")
        assert self._ipc._sock is not None
        self._ipc._sock.settimeout(None)

    def run(self) -> None:
        while True:
            try:
                kind, body = self._ipc.read_message()
            except Exception:  # closed by stop(), sway exited, or garbage
                self.dead = True
                self.log.append("ipc_lost", {})
                return
            name = EVENT_NAMES.get(kind & ~_EVENT_BIT, str(kind & ~_EVENT_BIT))
            self.log.append(name, body if isinstance(body, dict) else {"value": body})

    def stop(self) -> None:
        self._ipc.close()


_shared: Ipc | None = None
_shared_path: str | None = None


def connect() -> Ipc:
    """Process-wide request connection, re-created when SWAYSOCK changes."""
    global _shared, _shared_path
    path = socket_path()
    if _shared is None or _shared_path != path:
        if _shared is not None:
            _shared.close()
        _shared = Ipc(path)
        _shared_path = path
    return _shared


def reset() -> None:
    global _shared, _shared_path
    if _shared is not None:
        _shared.close()
    _shared = None
    _shared_path = None

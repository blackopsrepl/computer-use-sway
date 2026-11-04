"""Stdlib Sway IPC client (spike, reference implementation).

Wire format: b"i3-ipc" + uint32 length + uint32 type (native endian) + JSON.
Types: 0 RUN_COMMAND, 1 GET_WORKSPACES, 2 SUBSCRIBE, 3 GET_OUTPUTS, 4 GET_TREE,
5 GET_MARKS, 7 GET_VERSION, 8 GET_BINDING_MODES, 9 GET_CONFIG, 10 SEND_TICK,
100 GET_INPUTS, 101 GET_SEATS. Events have the high bit set:
0 workspace, 1 output, 2 mode, 3 window, 5 binding, 6 shutdown, 7 tick, 0x14 input.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

MAGIC = b"i3-ipc"
RUN_COMMAND, GET_WORKSPACES, SUBSCRIBE, GET_OUTPUTS, GET_TREE = 0, 1, 2, 3, 4
GET_SEATS, GET_INPUTS = 101, 100
EVENT_NAMES = {0: "workspace", 1: "output", 2: "mode", 3: "window", 5: "binding",
               6: "shutdown", 7: "tick", 0x14: "input"}


class Ipc:
    def __init__(self, path: str):
        self.path = path
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.lock = threading.Lock()

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("sway closed the IPC socket")
            out += chunk
        return out

    def _read(self):
        head = self._recv_exact(14)
        if head[:6] != MAGIC:
            raise ConnectionError("bad IPC magic")
        length, kind = struct.unpack("=II", head[6:])
        return kind, json.loads(self._recv_exact(length) or b"null")

    def call(self, kind: int, payload: str = ""):
        raw = payload.encode()
        with self.lock:
            self.s.sendall(MAGIC + struct.pack("=II", len(raw), kind) + raw)
            _, reply = self._read()
        return reply

    def command(self, cmd: str):
        return self.call(RUN_COMMAND, cmd)


class Subscription(threading.Thread):
    """Event stream on its own socket; events kept as (monotonic, name, body)."""

    def __init__(self, path: str, kinds: list[str]):
        super().__init__(daemon=True)
        self.ipc = Ipc(path)
        ok = self.ipc.call(SUBSCRIBE, json.dumps(kinds))
        if not ok.get("success"):
            raise ConnectionError(f"subscribe failed: {ok}")
        self.events: list[tuple[float, str, dict]] = []
        self.cond = threading.Condition()

    def run(self):
        while True:
            try:
                kind, body = self.ipc._read()
            except (OSError, ConnectionError):
                return
            with self.cond:
                self.events.append((time.monotonic(), EVENT_NAMES.get(kind & 0x7FFFFFFF, str(kind)), body))
                self.cond.notify_all()

    def wait_for(self, predicate, since: float, timeout: float):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for ev in self.events:
                    if ev[0] >= since and predicate(ev):
                        return ev
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(left)

"""The user's side: focus snapshots and idle detection on seat0.

`ext_idle_notifier_v1` notifications are per seat, and input on the agent
seat does not resume seat0's notification (FINDINGS S11). That lets the
foreground step wait until the user is idle without our own agent-seat input
counting as user activity.
"""

from __future__ import annotations

import struct
import time
from typing import Any

from . import core

NOTIFIER_GET_IDLE_NOTIFICATION = 1
NOTIFICATION_DESTROY = 0
EV_IDLED, EV_RESUMED = 0, 1


class Sentinel:
    def __init__(self, ipc: Any, conn: Any, user_seat: str = "seat0") -> None:
        self.ipc = ipc
        self.conn = conn
        self.user_seat = user_seat
        self.supported: bool | None = None
        self._notifier: int | None = None
        self._notification: int | None = None
        self._threshold: int | None = None
        self.idle = False
        self.idle_since: float | None = None

    # -- focus ---------------------------------------------------------------
    def focus(self) -> int | None:
        for seat in self.ipc.seats():
            if seat.get("name") == self.user_seat:
                focus = seat.get("focus")
                return int(focus) if isinstance(focus, int) and focus else None
        return None

    def snapshot(self) -> dict[str, Any]:
        return {"focus": self.focus(), "t": time.monotonic()}

    def restore(self, snap: dict[str, Any]) -> bool:
        """Put seat0 focus back if it moved. True when a restore was needed."""
        want = snap.get("focus")
        if not want or self.focus() == want:
            return False
        try:
            self.ipc.command(f"[con_id={int(want)}] focus")
        except core.ToolError:
            return True
        return True

    # -- idle ----------------------------------------------------------------
    def _ensure_notifier(self) -> bool:
        if self.supported is False:
            return False
        if self._notifier is None:
            if not self.conn.has_global("ext_idle_notifier_v1"):
                self.supported = False
                return False
            self._notifier, _ = self.conn.bind("ext_idle_notifier_v1", 1)
            self.supported = True
        return True

    def _on_notification(self, opcode: int, _body: bytes) -> None:
        if opcode == EV_IDLED:
            self.idle = True
            self.idle_since = time.monotonic()
        elif opcode == EV_RESUMED:
            self.idle = False
            self.idle_since = None

    def watch(self, idle_ms: int) -> bool:
        if not self._ensure_notifier():
            return False
        if self._threshold == idle_ms and self._notification is not None:
            return True
        if self._notification is not None:
            self.conn.send(self._notification, NOTIFICATION_DESTROY)
        seat_obj = self.conn.seats().get(self.user_seat)
        if seat_obj is None:
            self.supported = False
            return False
        nid = self.conn.new_id()
        assert self._notifier is not None
        self.conn.send(self._notifier, NOTIFIER_GET_IDLE_NOTIFICATION, struct.pack("=III", nid, idle_ms, seat_obj))
        self.conn.set_handler(nid, self._on_notification)
        self._notification = nid
        self._threshold = idle_ms
        self.idle = False
        self.idle_since = None
        self.conn.roundtrip()
        return True

    def wait_user_idle(self, idle_ms: int, max_wait_ms: int) -> bool:
        """True when seat0 has been idle for idle_ms (or idle is unsupported)."""
        if not self.watch(idle_ms):
            return True
        deadline = time.monotonic() + max_wait_ms / 1000.0
        while True:
            self.conn.dispatch(0.0)
            if self.idle:
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.conn.dispatch(min(0.05, left))

    def status(self) -> dict[str, Any]:
        return {"idle_gate": "unsupported" if self.supported is False else "ext_idle_notifier_v1",
                "user_idle": self.idle}

"""Keep every window of an agent-launched app on the agent workspace.

Why: sway places a launched app's FIRST window through the launch context
(pid ancestry / activation token), and then consumes that context. Every later
toplevel of the same app — a confirm dialog, a second window, a file chooser —
falls back to seat0's focused workspace, so it opens on the user's screen and,
being on the user's active workspace, takes seat0's keyboard focus (measured:
the user's typing went into the agent's dialog). Placement adds an `assign`
criterion for each agent window's (pid, app_id) as soon as it is known, so
sway's own `select_workspace` puts later windows on the agent workspace before
they map; `should_focus` then refuses them seat0 focus because they map off
the user's workspace.

Order of defences:
1. at launch, the reaper reports the app's pid and a pid-only criterion is
   added before the app maps anything (an app that opens two windows at
   startup would otherwise lose the race for the second one);
2. each agent window adds a (pid, app_id) criterion for any other process;
3. a window that still escapes (a new process that maps before 2) is caught
   by the `window::new` listener and moved to the agent workspace.

Criteria cannot be removed at runtime and vanish on reload. A stale one only
matters if a later process receives a recorded pid (pid_max is 4194304), so
the listener also guards the reverse: a window that maps on the agent
workspace but does not descend from an agent launch is handed back to the
user's focused workspace. After a reload the criteria of live agent processes
are re-added.
"""

from __future__ import annotations

import os
import string
import threading
from typing import Any

from . import core, desktop

SAFE = set(string.ascii_letters + string.digits + "_-")
REAPER_MARK = "reaper.py"


def app_id_pattern(app_id: str) -> str | None:
    """An anchored regex for an exact app_id, using only bracket classes so it
    survives sway's quoting; None for characters that cannot be expressed so."""
    out = []
    for ch in app_id:
        if ch in SAFE:
            out.append(ch)
        elif ch in ".:/+@ ":
            out.append(f"[{ch}]")
        else:
            return None
    return "^" + "".join(out) + "$"


def criterion(pid: int, app_id: str | None) -> str:
    parts = [f"pid={int(pid)}"]
    pattern = app_id_pattern(app_id) if app_id else None
    if pattern is not None:
        parts.append(f'app_id="{pattern}"')
    return "[" + " ".join(parts) + "]"


def is_reaper(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return any(arg.endswith(REAPER_MARK.encode()) for arg in handle.read().split(b"\0"))
    except OSError:
        return False


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def alive(pid: int) -> bool:
    return os.path.exists(f"/proc/{pid}")


class Placement:
    def __init__(self, ipc: Any, log: Any, workspace: str, user_seat: str = "seat0") -> None:
        self.ipc, self.log, self.workspace, self.user_seat = ipc, log, workspace, user_seat
        self.returned: list[int] = []  # user windows handed back from the agent workspace
        self.keys: set[tuple[int, str | None]] = set()
        self.roots: set[int] = set()
        self.escaped: list[int] = []  # con_ids moved back after mapping elsewhere
        self._lock = threading.Lock()
        log.add_listener(self._on_event)

    def close(self) -> None:
        self.log.remove_listener(self._on_event)

    def adopt(self, win: dict[str, Any]) -> None:
        """Record an agent window: its launch root, and an assign for its key."""
        pid = win.get("pid")
        if not pid:
            return
        for ancestor in desktop.ancestors(int(pid)):
            if is_reaper(ancestor):
                with self._lock:
                    self.roots.add(ancestor)
                break
        self._assign(int(pid), win.get("app_id"))

    def expect(self, pid: int) -> None:
        """The app's process, reported by the reaper before it maps a window."""
        parent = desktop.parent_pid(pid)
        if parent is not None and is_reaper(parent):
            with self._lock:
                self.roots.add(parent)
        self._assign(pid, None)

    def _assign(self, pid: int, app_id: str | None) -> None:
        key = (pid, app_id)
        with self._lock:
            if key in self.keys:
                return
            self.keys.add(key)
        try:
            self.ipc.command(f"assign {criterion(pid, app_id)} workspace {self.workspace}")
        except core.ToolError as exc:
            core.eprint(f"placement: assign failed: {exc}")

    def reapply(self) -> None:
        """After a reload: criteria are gone; re-add those of live processes."""
        with self._lock:
            keys = {k for k in self.keys if alive(k[0])}
            self.keys = set()
            self.roots = {r for r in self.roots if alive(r)}
        for pid, app_id in keys:
            self._assign(pid, app_id)

    def belongs(self, pid: int) -> bool:
        """Descends from an agent launch (a reaper this or any earlier session started)."""
        with self._lock:
            roots = set(self.roots)
        return any(a in roots or is_reaper(a) for a in desktop.ancestors(pid))

    def _on_event(self, name: str, body: dict) -> None:
        if name != "window" or body.get("change") != "new":
            return
        container = body.get("container") or {}
        pid, con_id = container.get("pid"), container.get("id")
        if not pid or not con_id:
            return
        ours = self.belongs(int(pid))
        if ours:
            self._assign(int(pid), container.get("app_id"))
        win = desktop.window_by_id(int(con_id))
        if win is None or win.get("workspace") is None:
            return
        if ours and win["workspace"] != self.workspace:
            self.ipc.command(f"[con_id={int(con_id)}] move container to workspace {self.workspace}")
            self.escaped.append(int(con_id))
        elif not ours and win["workspace"] == self.workspace:
            # A user process that inherited a recorded pid: give it back.
            target = desktop.seat_workspace(self.user_seat)
            if target and target != self.workspace:
                self.ipc.command(f"[con_id={int(con_id)}] move container to workspace {_quote(target)}")
                self.returned.append(int(con_id))

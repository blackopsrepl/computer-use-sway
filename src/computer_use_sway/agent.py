"""The agent seat, its virtual devices and its off-screen output.

One owner for everything the agent uses beside the user. Every rule here is
a measured trap (docs/plan-1.0/FINDINGS.md):

- the seat is config-declared, never created at runtime (S7);
- after `reload`, and after any runtime `seat ...` command (even one for
  seat0 or `seat *`), sway re-sorts devices by attach rules and the agent's
  virtual devices fall back to seat0, with no IPC event (S8, S12). Input is
  therefore gated on the agent seat holding its devices, checked before every
  message, and they are recreated on the agent seat before any further input;
- every agent binding is followed by an IPC `nop`, or a view mapping from it
  focuses *all* seats (O6);
- user outputs are pinned at their current position before `create_output`,
  or sway re-arranges them and moves the user's focus (O2, O8);
- the agent workspace is named by IPC rename, never by a `workspace <name>`
  binding, which leaks the name onto user outputs (O9);
- the agent seat has a pointer only while the agent output exists, and its
  cursor is parked on that output before the pointer appears: a seat with a
  pointer draws its own cursor, and with no agent output sway puts it on the
  user's screen (I8). Start creates the output first; stop removes the
  devices first. A pointer action on a user output (a click on a visible
  window) shows the agent's cursor there for that call only: the dispatch
  layer parks it back after every tool call (I9).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import core, install, placement, swayipc, vinput, wire

AGENT_X = 100000
AGENT_MODE = "1920x1080"
WORKSPACE = "cus-agent"


class StateFile:
    """$XDG_RUNTIME_DIR/computer-use-sway/session.json, 0600 in a 0700 dir."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or install.runtime_root() / "session.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, json.dumps(data).encode("utf-8"))
        finally:
            os.close(fd)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Agent:
    def __init__(self, ipc: swayipc.Ipc, conn: wire.Connection, log: swayipc.EventLog,
                 state: StateFile | None = None, user_seat: str = "seat0", devices_factory: Any = None) -> None:
        self.ipc = ipc
        self.conn = conn
        self.log = log
        self.state = state or StateFile()
        self.user_seat = user_seat
        self._factory = devices_factory or (lambda: vinput.SeatDevices(conn, install.SEAT, gate=self.gate))
        self.devices: Any = None
        self.output: str | None = None
        self.saved_user: dict[str, list[int]] = {}
        self.stale: str | None = None
        self.layout_dirty = False
        self.started = False
        self.created = False
        self.placement = placement.Placement(ipc, log, install.SEAT, user_seat)

    # -- events --------------------------------------------------------------
    def _on_event(self, name: str, body: dict) -> None:
        if name == "workspace" and body.get("change") == "reload":
            self.stale = "reload"
        elif name == "output":
            self.layout_dirty = True
        elif name == "shutdown":
            self.stale = "shutdown"
        elif name == "ipc_lost":
            self.stale = "shutdown"

    def gate(self) -> None:
        """Called before every input message. Never let agent input reach the
        user's seat: a `seat` command moves the devices there silently [S12]."""
        if not self.stale and self.devices is not None and not self._holds_devices():
            self.stale = "moved"
        if self.stale:
            cause = "a sway reload" if self.stale == "reload" else "a sway seat change"
            raise vinput.AgentSeatLost(
                f"agent seat lost its devices to {cause}; retrying after recovery", code="agent_seat_lost"
            )

    def _holds_devices(self) -> bool:
        return len((self._seat(install.SEAT) or {}).get("devices", [])) >= 2

    # -- helpers -------------------------------------------------------------
    def _seat(self, name: str) -> dict[str, Any] | None:
        for seat in self.ipc.seats():
            if seat.get("name") == name:
                return seat
        return None

    def _outputs(self) -> list[dict[str, Any]]:
        return [o for o in self.ipc.outputs() if o.get("active")]

    def _pin_user(self) -> None:
        for name, (x, y) in self.saved_user.items():
            self.ipc.command(f"output {_quote(name)} position {x} {y}")

    def _park_cursor(self) -> None:
        """Put the agent seat's cursor on the agent output. A seat without a
        pointer draws no cursor, but keeps a position; the pointer created
        next would show its cursor there, on a user output, until it moved."""
        rect = next(o["rect"] for o in self._outputs() if o["name"] == self.output)
        self.ipc.command(f"seat {install.SEAT} cursor set "
                         f"{rect['x'] + rect['width'] // 2} {rect['y'] + rect['height'] // 2}")

    def park_pointer(self) -> bool:
        """Bring the agent's cursor home after a tool call that left it on a
        user output. Returns whether it moved. Moving the virtual pointer, not
        warping the seat, keeps its recorded position true."""
        if not self.started or self.stale or self.devices is None or self.output is None:
            return False
        pointer = self.devices.pointer
        if pointer.position is None:
            return False  # never moved: still where start parked it
        rect = next((o["rect"] for o in self._outputs() if o["name"] == self.output), None)
        if rect is None:
            return False
        x, y = pointer.position
        if rect["x"] <= x < rect["x"] + rect["width"] and rect["y"] <= y < rect["y"] + rect["height"]:
            return False
        pointer.move_to(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
        return True

    def _place_agent(self) -> None:
        assert self.output is not None
        self.ipc.command(f"output {_quote(self.output)} position {AGENT_X} 0 resolution {AGENT_MODE}")
        self.ipc.command(f"workspace {WORKSPACE} output {_quote(self.output)}")
        self._rename_workspace()

    def _rename_workspace(self) -> None:
        names = [w["name"] for w in self.ipc.workspaces() if w.get("output") == self.output]
        if WORKSPACE in names or not names:
            return
        self.ipc.command(f"rename workspace {_quote(names[0])} to {WORKSPACE}")

    def _has_agent_workspace(self, output: str) -> bool:
        return any(w.get("name") == WORKSPACE and w.get("output") == output for w in self.ipc.workspaces())

    def _user_focus(self) -> int | None:
        seat = self._seat(self.user_seat)
        focus = seat.get("focus") if seat else None
        return int(focus) if isinstance(focus, int) and focus else None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> dict[str, Any]:
        if self.started:
            self.ensure_ready()
            return self.status()
        if self._seat(install.SEAT) is None:
            raise core.ToolError(
                "agent seat 'cus-agent' is not configured; run: computer-use-sway --install-sway-config",
                code="agent_seat_missing",
            )
        self.log.add_listener(self._on_event)
        focus_before = self._user_focus()
        self._adopt_or_create_output()
        self._park_cursor()
        self.devices = self._factory()
        disturbed = False
        if focus_before is not None and self._user_focus() != focus_before:
            self.ipc.command(f"[con_id={focus_before}] focus")
            disturbed = True
        self.started = True
        result = self.status()
        result["disturbed_user"] = disturbed
        return result

    def _adopt_or_create_output(self) -> None:
        saved = self.state.load()
        names = {o["name"] for o in self._outputs()}
        if saved.get("output") in names and self._has_agent_workspace(saved["output"]):
            self.output = saved["output"]
            self.saved_user = {k: list(v) for k, v in (saved.get("saved_user") or {}).items() if k in names}
            self.created = False
            self._pin_user()
            self._place_agent()
            return
        before = self._outputs()
        self.saved_user = {o["name"]: [o["rect"]["x"], o["rect"]["y"]] for o in before}
        self._pin_user()
        self.ipc.command("create_output")
        fresh = [o["name"] for o in self._outputs() if o["name"] not in self.saved_user]
        if len(fresh) != 1:
            raise core.ToolError(f"create_output produced {len(fresh)} new outputs; expected one")
        self.output = fresh[0]
        self.created = True
        self._place_agent()
        self.state.save({"output": self.output, "saved_user": self.saved_user, "created_at": time.time()})

    def ensure_ready(self) -> None:
        if not self.started:
            self.start()
            return
        if self.stale == "shutdown":
            raise core.ToolError("sway is shutting down or the IPC socket was lost", code="sway_gone")
        if not self.stale and self.devices is not None and not self._holds_devices():
            self.stale = "moved"
        if self.stale in ("reload", "moved"):
            # After a reload or a seat change the agent's devices sit on seat0
            # [S8, S12]; recreating
            # them on the agent seat must leave seat0 with no more devices than
            # it has right now.
            user_before = len((self._seat(self.user_seat) or {}).get("devices", []))
            self._pin_user()
            self._place_agent()
            self._park_cursor()  # the reloaded seat's cursor may sit on a user output
            self.devices.recreate()
            agent_seat = self._seat(install.SEAT) or {}
            user_seat = self._seat(self.user_seat) or {}
            agent_devices = len(agent_seat.get("devices", []))
            user_devices = len(user_seat.get("devices", []))
            if agent_devices < 2 or user_devices > user_before:
                raise core.ToolError(
                    f"agent seat recovery failed: cus-agent has {agent_devices} devices, "
                    f"{self.user_seat} has {user_devices}",
                    code="agent_recovery_failed",
                )
            self.placement.reapply()
            self.stale = None
        if self.layout_dirty:
            self.layout_dirty = False
            for output in self._outputs():
                if output["name"] != self.output:
                    self.saved_user[output["name"]] = [output["rect"]["x"], output["rect"]["y"]]
            live = {o["name"] for o in self._outputs()}
            self.saved_user = {k: v for k, v in self.saved_user.items() if k in live}
            if self.output not in live:
                self.started = False
                self.devices.destroy()
                self.devices = None
                self.start()
                return
            self._pin_user()
            self._place_agent()

    def stop(self) -> None:
        self.placement.close()
        if not self.started:
            return
        self.started = False
        if self.devices is not None:  # before the output, so the cursor goes with it
            self.devices.destroy()
            self.devices = None
        if self.output:
            try:
                self.ipc.command(f"output {_quote(self.output)} unplug")
            except core.ToolError:
                pass
        self.state.clear()
        self.log.remove_listener(self._on_event)

    # -- seat operations -----------------------------------------------------
    def tap(self, key: str) -> None:
        try:
            self.devices.keyboard.chord(key)
        finally:
            self.ipc.command("nop")  # [O6]

    def focus(self, con_id: int) -> None:
        self.ipc.command(f"[con_id={int(con_id)}] mark --add {install.FOCUS_MARK}")
        try:
            self.tap(install.FOCUS_KEY)
            deadline = time.monotonic() + 0.3
            while True:
                seat = self._seat(install.SEAT) or {}
                if seat.get("focus") == con_id:
                    return
                if time.monotonic() > deadline:
                    raise core.ToolError(f"the agent seat could not focus window {con_id}", code="agent_focus_failed")
                time.sleep(0.02)
        finally:
            try:
                self.ipc.command(f"[con_id={int(con_id)}] unmark {install.FOCUS_MARK}")
            except core.ToolError:
                pass

    def agent_windows(self) -> list[dict[str, Any]]:
        from . import desktop

        return [w for w in desktop.all_windows(self.ipc.tree()) if w.get("workspace") == WORKSPACE]

    def to_workspace(self) -> None:
        windows = self.agent_windows()
        if windows:
            self.focus(int(windows[0]["id"]))
            return
        for output in self._outputs():
            if output["name"] == self.output:
                r = output["rect"]
                self.devices.pointer.click(r["x"] + r["width"] // 2, r["y"] + r["height"] // 2)
                self.ipc.command("nop")
                return
        raise core.ToolError("the agent output is missing", code="agent_output_missing")

    def status(self) -> dict[str, Any]:
        return {
            "seat": install.SEAT,
            "output": self.output,
            "workspace": WORKSPACE,
            "devices_ok": self.devices is not None and not self.stale,
            "stale": self.stale,
            "created_by_this_process": self.created,
            "started": self.started,
        }

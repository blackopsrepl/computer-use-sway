"""AT-SPI client over the stdlib D-Bus client.

Coordinates always come from `GetExtents(WINDOW)`: SCREEN coordinates are
meaningless on Wayland (FINDINGS A5). Gecko reports `NActions = 0` while
`GetName(0)` is "press" (A6), and its `SetTextContents` returns true without
changing anything (A7): callers verify by reading back.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from . import core, dbus

REGISTRY = "org.a11y.atspi.Registry"
ROOT = "/org/a11y/atspi/accessible/root"
IFACE = {
    "acc": "org.a11y.atspi.Accessible",
    "comp": "org.a11y.atspi.Component",
    "action": "org.a11y.atspi.Action",
    "text": "org.a11y.atspi.Text",
    "etext": "org.a11y.atspi.EditableText",
    "value": "org.a11y.atspi.Value",
    "app": "org.a11y.atspi.Application",
    "sel": "org.a11y.atspi.Selection",
}
COORD_WINDOW = 1
STATES = ["invalid", "active", "armed", "busy", "checked", "collapsed", "defunct", "editable",
          "enabled", "expandable", "expanded", "focusable", "focused", "has_tooltip", "horizontal",
          "iconified", "modal", "multi_line", "multiselectable", "opaque", "pressed", "resizable",
          "selectable", "selected", "sensitive", "showing", "single_line", "stale", "transient",
          "vertical", "visible", "manages_descendants", "indeterminate", "required", "truncated",
          "animated", "invalid_entry", "supports_autocompletion", "selectable_text", "is_default",
          "visited", "checkable", "has_popup", "read_only"]
ACTION_SYNONYMS = ("press", "click", "activate", "Click", "Press")
EVENTS = ("object:text-changed", "object:state-changed", "object:property-change",
          "object:children-changed", "window:activate")
CALL_TIMEOUT = 2.0
WALK_BUDGET_S = 3.0
INVALID = -(2 ** 31)

Ref = tuple[str, str]


def states_from(bits: list[int]) -> set[str]:
    out = set()
    for word, value in enumerate(bits):
        for bit in range(32):
            if value & (1 << bit):
                index = word * 32 + bit
                out.add(STATES[index] if index < len(STATES) else f"state{index}")
    return out


def valid_extents(ext: Any) -> bool:
    if not ext or len(ext) != 4:
        return False
    if any(v <= INVALID or v == INVALID for v in ext):
        return False
    return tuple(ext) != (-1, -1, -1, -1)


@dataclass
class Node:
    ref: Ref
    role: str
    name: str
    states: set[str]
    ifaces: set[str]
    extents: tuple[int, int, int, int] | None
    depth: int = 0
    parent: Ref | None = None
    actions: list[str] = field(default_factory=list)


class Client:
    def __init__(self, session_bus_address: str | None = None, session_bus: Any = None, a11y_bus: Any = None) -> None:
        self._restore: dict[str, bool] | None = None
        self.truncated = False
        if a11y_bus is not None:
            self.session, self.bus = session_bus, a11y_bus
            return
        core.ensure_session_environment()
        address = session_bus_address or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not address:
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if runtime and os.path.exists(os.path.join(runtime, "bus")):
                address = f"unix:path={runtime}/bus"
        if not address:
            raise core.ToolError("accessibility bus unavailable: no session D-Bus", code="a11y_unavailable")
        try:
            self.session = dbus.Bus(address)
            a11y_address = self.session.call("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress")[0]
            self.bus = dbus.Bus(os.environ.get("AT_SPI_BUS_ADDRESS") or a11y_address)
        except core.ToolError as exc:
            raise core.ToolError(f"accessibility bus unavailable: {exc}", code="a11y_unavailable") from exc
        self._events_registered = False

    # -- status ----------------------------------------------------------------
    def enabled(self) -> dict[str, bool]:
        return {p: bool(self.session.get_property("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", p))
                for p in ("IsEnabled", "ScreenReaderEnabled")}

    def ensure_enabled(self) -> dict[str, bool]:
        """Qt, Gecko and others gate their bridge on org.a11y.Status [A9]."""
        if self.session is None:
            return {}
        before = self.enabled()
        for prop, value in before.items():
            if not value:
                self.session.set_property("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", prop, "b", True)
        if self._restore is None:
            self._restore = before
        return before

    def restore_enabled(self) -> None:
        if self._restore is None or self.session is None:
            return
        for prop, value in self._restore.items():
            if not value:
                try:
                    self.session.set_property("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", prop, "b", False)
                except core.ToolError:
                    pass
        self._restore = None

    def status(self) -> dict[str, Any]:
        try:
            return {"bus": True, "enabled": self.enabled() if self.session is not None else {}}
        except core.ToolError as exc:
            return {"bus": True, "error": str(exc)}

    def close(self) -> None:
        self.restore_enabled()
        for bus in (self.bus, self.session):
            if bus is not None:
                bus.close()

    # -- primitives ------------------------------------------------------------
    def call(self, ref: Ref, iface: str, member: str, sig: str = "", *args: Any) -> tuple:
        return self.bus.call(ref[0], ref[1], IFACE.get(iface, iface), member, sig, *args, timeout=CALL_TIMEOUT)

    def prop(self, ref: Ref, iface: str, name: str) -> Any:
        return self.bus.get_property(ref[0], ref[1], IFACE.get(iface, iface), name)

    def apps(self) -> list[Ref]:
        return [tuple(x) for x in self.call((REGISTRY, ROOT), "acc", "GetChildren")[0]]  # type: ignore[misc]

    def app_pid(self, app: Ref) -> int | None:
        try:
            return int(self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                     "GetConnectionUnixProcessID", "s", app[0], timeout=CALL_TIMEOUT)[0])
        except core.ToolError:
            return None

    def app_for_window(self, pid: int) -> Ref | None:
        """Exact pid first, else a pid related by ancestry (wrappers, zygotes) [A2]."""
        from . import desktop

        related = None
        for app in self.apps():
            app_pid = self.app_pid(app)
            if app_pid is None:
                continue
            if app_pid == pid:
                return app
            if related is None and desktop.pids_related(app_pid, pid):
                related = app
        return related

    def toolkit(self, app: Ref) -> tuple[str | None, str | None]:
        try:
            name = str(self.prop(app, "app", "ToolkitName"))
        except core.ToolError:
            return None, None
        try:
            version = str(self.prop(app, "app", "Version"))
        except core.ToolError:
            version = None
        return name, version

    def children(self, ref: Ref) -> list[Ref]:
        return [tuple(x) for x in self.call(ref, "acc", "GetChildren")[0]]  # type: ignore[misc]

    def extents(self, ref: Ref) -> tuple[int, int, int, int] | None:
        try:
            ext = tuple(self.call(ref, "comp", "GetExtents", "u", COORD_WINDOW)[0])
        except core.ToolError:
            return None
        return ext if valid_extents(ext) else None  # type: ignore[return-value]

    def node(self, ref: Ref, depth: int = 0, parent: Ref | None = None) -> Node:
        role = str(self.call(ref, "acc", "GetRoleName")[0])
        name = str(self.prop(ref, "acc", "Name") or "")
        states = states_from(self.call(ref, "acc", "GetState")[0])
        ifaces = set(self.call(ref, "acc", "GetInterfaces")[0])
        extents = self.extents(ref) if IFACE["comp"] in ifaces else None
        return Node(ref, role, name, states, ifaces, extents, depth, parent)

    def frame_for_window(self, app: Ref, title: str | None) -> Ref | None:
        best: tuple[int, Ref] | None = None
        for child in self.children(app):
            try:
                node = self.node(child)
            except core.ToolError:
                continue
            score = 0
            if title and node.name == title:
                score += 4
            elif title and node.name and (node.name in title or title in node.name):
                score += 2
            if node.role in ("frame", "window", "dialog", "alert", "file chooser"):
                score += 1
            elif score == 0:
                # A Qt widgets top-level without a window role is a "filler":
                # accepted only when its name matches the window title.
                continue
            if "active" in node.states:
                score += 2
            if "showing" in node.states:
                score += 1
            if best is None or score > best[0]:
                best = (score, child)
        return best[1] if best else None

    def walk(self, root: Ref, max_nodes: int = 4000, prune_hidden: bool = True) -> list[Node]:
        out: list[Node] = []
        stack: list[tuple[Ref, int, Ref | None]] = [(root, 0, None)]
        deadline = time.monotonic() + WALK_BUDGET_S
        self.truncated = False
        while stack:
            if len(out) >= max_nodes or time.monotonic() > deadline:
                self.truncated = True
                break
            ref, depth, parent = stack.pop()
            try:
                node = self.node(ref, depth, parent)
            except core.ToolError:
                continue  # stale objects disappear mid-walk
            out.append(node)
            # Scrolled-out list rows are "visible" but not "showing"; only
            # subtrees that are neither (hidden widgets) are skipped.
            if prune_hidden and depth > 1 and not ({"showing", "visible"} & node.states):
                continue
            try:
                kids = self.children(ref)
            except core.ToolError:
                continue
            for kid in reversed(kids):
                stack.append((kid, depth + 1, ref))
        return out

    # -- actions -----------------------------------------------------------------
    def actions(self, ref: Ref) -> list[str]:
        try:
            count = int(self.prop(ref, "action", "NActions"))
        except core.ToolError:
            return []
        names = []
        for index in range(max(count, 1)):
            try:
                name = str(self.call(ref, "action", "GetName", "i", index)[0])
            except core.ToolError:
                break
            if name:
                names.append(name)
            elif count == 0:
                break
        return names

    def resolve_action(self, ref: Ref, name_or_index: str | int) -> int | None:
        names = self.actions(ref)
        if isinstance(name_or_index, int):
            return name_or_index if 0 <= name_or_index < max(len(names), 1) else None
        wanted = [name_or_index]
        if name_or_index.lower() in {s.lower() for s in ACTION_SYNONYMS}:
            wanted += list(ACTION_SYNONYMS)
        lowered = [n.lower() for n in names]
        for want in wanted:
            if want.lower() in lowered:
                return lowered.index(want.lower())
        return None

    def do_action(self, ref: Ref, name_or_index: str | int) -> bool:
        index = self.resolve_action(ref, name_or_index)
        if index is None:
            raise core.ToolError(f"element has no {name_or_index!r} action", code="no_action")
        return bool(self.call(ref, "action", "DoAction", "i", index)[0])

    def select_in_parent(self, ref: Ref) -> bool:
        """Selection.SelectChild on the parent: the standard way to select a
        list row or tab, scrolled out of view or not. False when the parent
        has no Selection interface."""
        parent = self.prop(ref, "acc", "Parent")
        if not parent or not isinstance(parent, (tuple, list)) or len(parent) != 2:
            return False
        parent = (str(parent[0]), str(parent[1]))
        if IFACE["sel"] not in set(self.call(parent, "acc", "GetInterfaces")[0]):
            return False
        index = int(self.call(ref, "acc", "GetIndexInParent")[0])
        return index >= 0 and bool(self.call(parent, "sel", "SelectChild", "i", index)[0])

    def grab_focus(self, ref: Ref) -> bool:
        return bool(self.call(ref, "comp", "GrabFocus")[0])

    def text(self, ref: Ref, start: int = 0, end: int = -1) -> str:
        count = int(self.prop(ref, "text", "CharacterCount"))
        stop = count if end < 0 else min(end, count)
        return str(self.call(ref, "text", "GetText", "ii", max(0, start), stop)[0])

    def set_text(self, ref: Ref, value: str) -> bool:
        try:
            return bool(self.call(ref, "etext", "SetTextContents", "s", value)[0])
        except dbus.DBusError as exc:
            if "UnknownMethod" in exc.name or "UnknownInterface" in exc.name:
                raise core.ToolError("element has no editable text", code="no_editable_text") from exc
            raise

    def value(self, ref: Ref) -> float | None:
        try:
            return float(self.prop(ref, "value", "CurrentValue"))
        except (core.ToolError, TypeError, ValueError):
            return None

    def set_value(self, ref: Ref, value: float) -> None:
        self.bus.set_property(ref[0], ref[1], IFACE["value"], "CurrentValue", "d", float(value))

    # -- events --------------------------------------------------------------------
    def register_events(self) -> None:
        if getattr(self, "_events_registered", False):
            return
        for event in EVENTS:
            try:
                self.call((REGISTRY, "/org/a11y/atspi/registry"), "org.a11y.atspi.Registry",
                          "RegisterEvent", "s", event)
            except core.ToolError:
                pass
            iface = "org.a11y.atspi.Event." + event.split(":")[0].capitalize()
            self.bus.add_match(f"type='signal',interface='{iface}'")
        self._events_registered = True

    def pump(self, seconds: float) -> None:
        self.bus.pump(seconds)

    def events_since(self, t: float, sender: str | None = None) -> list[tuple[float, str, str]]:
        out = []
        for at, header, values in self.bus.signals_since(t, sender):
            detail = str(values[0]) if values else ""
            out.append((at, str(header.get(3, "")), detail))
        return out

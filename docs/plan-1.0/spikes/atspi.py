"""AT-SPI client on top of dbus.py (spike, reference implementation)."""
from __future__ import annotations

import os
import time
from typing import Any

import dbus

REGISTRY = "org.a11y.atspi.Registry"
ROOT = "/org/a11y/atspi/accessible/root"
ACC = "org.a11y.atspi.Accessible"
COMP = "org.a11y.atspi.Component"
ACTION = "org.a11y.atspi.Action"
TEXT = "org.a11y.atspi.Text"
ETEXT = "org.a11y.atspi.EditableText"
VALUE = "org.a11y.atspi.Value"
APP = "org.a11y.atspi.Application"
CACHE = "org.a11y.atspi.Cache"
COORD_SCREEN, COORD_WINDOW, COORD_PARENT = 0, 1, 2

# AtspiStateType bit positions (at-spi2-core atspi-constants.h)
STATES = ["invalid", "active", "armed", "busy", "checked", "collapsed", "defunct", "editable",
          "enabled", "expandable", "expanded", "focusable", "focused", "has_tooltip", "horizontal",
          "iconified", "modal", "multi_line", "multiselectable", "opaque", "pressed", "resizable",
          "selectable", "selected", "sensitive", "showing", "single_line", "stale", "transient",
          "vertical", "visible", "manages_descendants", "indeterminate", "required", "truncated",
          "animated", "invalid_entry", "supports_autocompletion", "selectable_text", "is_default",
          "visited", "checkable", "has_popup", "read_only"]


def states_from(bits: list[int]) -> set[str]:
    out = set()
    for word, value in enumerate(bits):
        for bit in range(32):
            if value & (1 << bit):
                idx = word * 32 + bit
                out.add(STATES[idx] if idx < len(STATES) else f"state{idx}")
    return out


def a11y_address(session: dbus.Bus) -> str:
    return session.call("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress")[0]


class Atspi:
    def __init__(self, session_address: str | None = None):
        self.session = dbus.Bus(session_address or os.environ["DBUS_SESSION_BUS_ADDRESS"])
        self.bus = dbus.Bus(a11y_address(self.session))

    def set_enabled(self, enabled: bool = True) -> None:
        """org.a11y.Status IsEnabled / ScreenReaderEnabled: toolkits that gate
        their bridge on it (Qt, Chromium, Electron, Firefox) turn it on."""
        for prop in ("IsEnabled", "ScreenReaderEnabled"):
            self.session.set_property("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", prop, "b", enabled)

    def get_enabled(self) -> dict:
        return {p: self.session.get_property("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", p)
                for p in ("IsEnabled", "ScreenReaderEnabled")}

    # -- primitives ----------------------------------------------------
    def call(self, ref, iface, member, sig="", *args, timeout=3.0):
        return self.bus.call(ref[0], ref[1], iface, member, sig, *args, timeout=timeout)

    def prop(self, ref, iface, name):
        return self.bus.get_property(ref[0], ref[1], iface, name)

    def apps(self) -> list[tuple[str, str]]:
        return [tuple(x) for x in self.call((REGISTRY, ROOT), ACC, "GetChildren")[0]]

    def pid(self, bus_name: str) -> int:
        return self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                             "GetConnectionUnixProcessID", "s", bus_name)[0]

    def children(self, ref):
        return [tuple(x) for x in self.call(ref, ACC, "GetChildren")[0]]

    def role(self, ref) -> str:
        return self.call(ref, ACC, "GetRoleName")[0]

    def name(self, ref) -> str:
        return self.prop(ref, ACC, "Name")

    def states(self, ref) -> set[str]:
        return states_from(self.call(ref, ACC, "GetState")[0])

    def interfaces(self, ref) -> list[str]:
        return self.call(ref, ACC, "GetInterfaces")[0]

    def extents(self, ref, coord=COORD_WINDOW):
        return self.call(ref, COMP, "GetExtents", "u", coord)[0]

    def actions(self, ref) -> list[str]:
        return [a[0] for a in self.call(ref, ACTION, "GetActions")[0]]

    def do_action(self, ref, index: int) -> bool:
        return self.call(ref, ACTION, "DoAction", "i", index)[0]

    def text(self, ref) -> str:
        n = self.prop(ref, TEXT, "CharacterCount")
        return self.call(ref, TEXT, "GetText", "ii", 0, n)[0]

    def set_text(self, ref, value: str) -> bool:
        return self.call(ref, ETEXT, "SetTextContents", "s", value)[0]

    def grab_focus(self, ref) -> bool:
        return self.call(ref, COMP, "GrabFocus")[0]

    def cache_items(self, bus_name: str):
        """Whole-tree snapshot in one call, where the toolkit implements it."""
        return self.bus.call(bus_name, "/org/a11y/atspi/cache", CACHE, "GetItems")[0]

    # -- walking ---------------------------------------------------------
    def walk(self, ref, max_nodes=20000, prune_hidden=True):
        """Depth-first walk yielding (ref, depth, role, name, states, interfaces)."""
        stack, seen = [(ref, 0)], 0
        while stack and seen < max_nodes:
            node, depth = stack.pop()
            seen += 1
            try:
                role, name = self.role(node), self.name(node)
                st = self.states(node)
                ifaces = self.interfaces(node)
            except dbus.DBusError:
                continue
            yield node, depth, role, name, st, ifaces
            if prune_hidden and depth > 1 and "showing" not in st and role not in ("application",):
                continue
            try:
                kids = self.children(node)
            except dbus.DBusError:
                continue
            for k in reversed(kids):
                stack.append((k, depth + 1))

    def find(self, root, role=None, name=None, max_nodes=20000):
        for node, depth, r, n, st, ifaces in self.walk(root, max_nodes=max_nodes):
            if (role is None or r == role) and (name is None or n == name):
                return node, r, n, st, ifaces
        return None

    def register_events(self, events: list[str]) -> None:
        """Toolkits only emit what some listener registered with the registry."""
        for ev in events:
            self.call((REGISTRY, "/org/a11y/atspi/registry"), "org.a11y.atspi.Registry", "RegisterEvent", "s", ev)
            iface = "org.a11y.atspi.Event." + ev.split(":")[0].capitalize()
            self.bus.add_match(f"type='signal',interface='{iface}'")


def timed(fn, *a, **k):
    t = time.monotonic()
    out = fn(*a, **k)
    return out, round((time.monotonic() - t) * 1000, 1)

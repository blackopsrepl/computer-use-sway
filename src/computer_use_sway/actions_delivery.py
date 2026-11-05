"""The three delivery forms used by `act`: semantic, device, foreground."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import core, desktop, verify

POINTER_ACTIONS = ("press", "activate", "toggle", "focus", "click", "double_click", "right_click", "select",
                   "scroll", "drag_to")
KEYBOARD_ACTIONS = ("set_text", "type", "key")


@dataclass
class Delivery:
    text: str | None = None
    key: str | None = None
    modifiers: list[str] | None = None
    value: float | None = None
    direction: str | None = None
    clicks: int = 3
    to: Any = None
    to_point: tuple[int, int] | None = None


def needs_keyboard(action: str) -> bool:
    return action in KEYBOARD_ACTIONS


def semantic(client: Any, action: str, element: Any, d: Delivery) -> None:
    if client is None or element is None or element.a11y is None:
        raise core.ToolError("no accessibility element for a semantic action", code="no_semantic")
    ref = element.a11y
    if action in ("press", "activate", "click"):
        client.do_action(ref, "press")
    elif action == "toggle":
        index = client.resolve_action(ref, "toggle")
        client.do_action(ref, index if index is not None else "click")
    elif action == "focus":
        if not client.grab_focus(ref):
            raise core.ToolError("the toolkit refused GrabFocus", code="grab_focus_refused")
    elif action == "set_text":
        assert d.text is not None
        client.set_text(ref, d.text)
    elif action == "set_value":
        assert d.value is not None
        client.set_value(ref, d.value)
    elif action == "select":
        try:
            if client.select_in_parent(ref):
                return
        except core.ToolError:
            pass
        index = client.resolve_action(ref, "select")
        if index is None:
            index = client.resolve_action(ref, "activate")
        client.do_action(ref, index if index is not None else "click")
    else:
        raise core.ToolError(f"{action} has no semantic form", code="no_semantic")


def _point(point: Any) -> tuple[int, int]:
    if point is None:
        raise core.ToolError("this action needs a target with a position", code="no_point")
    return int(point[0]), int(point[1])


def pointer_start(action: str, element: Any, point: Any) -> tuple[int, int] | None:
    """Where the device form puts the pointer first, or None if it does not.
    The verdict baseline is taken with the pointer already there, so a hover
    highlight is not mistaken for the action's effect."""
    if point is None:
        return None
    if action in ("press", "activate", "toggle", "focus", "click", "select", "double_click", "right_click",
                  "scroll", "drag_to"):
        return _point(point)
    if action == "set_text" and element is not None:
        return _point(point)
    if action == "type" and element is not None and "focused" not in (element.states or []):
        return _point(point)
    return None


def device_form(devices: Any, action: str, element: Any, point: Any, d: Delivery) -> None:
    if element is not None and "offscreen" in (element.states or []) and action in POINTER_ACTIONS + ("set_text",):
        raise core.ToolError(f"{element.ref} is scrolled out of view; scroll it into view, then observe again",
                             code="offscreen")
    pointer, keyboard = devices.pointer, devices.keyboard
    if action in ("press", "activate", "toggle", "focus", "click", "select"):
        pointer.click(*_point(point))
    elif action == "double_click":
        pointer.click(*_point(point), count=2)
    elif action == "right_click":
        pointer.click(*_point(point), button="right")
    elif action == "scroll":
        pointer.move_to(*_point(point))
        pointer.scroll(d.direction or "down", d.clicks)
    elif action == "drag_to":
        assert d.to_point is not None
        pointer.drag(_point(point), d.to_point)
    elif action == "set_text":
        if element is not None and point is not None:
            pointer.click(*_point(point))
        keyboard.chord("a", ["ctrl"])
        keyboard.chord("BackSpace")
        _type(keyboard, d.text or "")
    elif action == "type":
        if element is not None and point is not None and "focused" not in (element.states or []):
            pointer.click(*_point(point))
        _type(keyboard, d.text or "")
    elif action == "key":
        keyboard.chord(d.key or "", d.modifiers or [])
    else:
        raise core.ToolError(f"{action} has no device form", code="no_device_form")


def _type(keyboard: Any, text: str) -> None:
    from .vinput import AgentSeatLost

    sent = 0
    try:
        for ch in text:
            keyboard.type_text(ch)
            sent += 1
    except AgentSeatLost as exc:
        if sent:
            raise core.ToolError(
                f"typing interrupted after {sent} of {len(text)} characters: the agent seat lost its "
                f"devices to a sway reload or seat change; not retyped",
                code="partial_input",
            ) from exc
        raise


def foreground(session: Any, action: str, element: Any, con_id: int | None, point: Any, d: Delivery,
               expect: Any, do_verify: bool) -> dict[str, Any]:
    """Borrow seat0 briefly: wait for user idle, focus, deliver, restore focus."""
    opts = session.options
    if con_id is None:
        raise core.ToolError("foreground delivery needs a target window", code="no_window")
    if not needs_keyboard(action) and not opts.get("foreground_pointer", False):
        return {"effect": "no_change", "evidence": [], "blocked": "foreground_pointer_disabled",
                "hint": "start the session with options.foreground_pointer=true to allow seat0 pointer input",
                "disturbed_user": False, "elapsed_ms": 0}
    sentinel = session.sentinel()
    if opts.get("foreground", "idle") == "idle":
        if not sentinel.wait_user_idle(int(opts.get("idle_ms", 1500)), int(opts.get("idle_max_wait_ms", 10000))):
            return {"effect": "no_change", "evidence": [], "blocked": "user_active", "disturbed_user": False,
                    "elapsed_ms": 0}
    snap = sentinel.snapshot()
    devices = session.user_devices()
    client = session.a11y_or_none()
    try:
        desktop.ipc().command(f"[con_id={int(con_id)}] focus")
        if element is not None and element.a11y is not None and client is not None:
            try:
                client.grab_focus(element.a11y)  # caret into the field [A8]
            except core.ToolError:
                pass
        time.sleep(0.05)
        from .actions import fresh_point

        point = fresh_point(session, element, point)
        base = verify.take_baseline(session, element, con_id, want_pixels=do_verify)
        if needs_keyboard(action) and action != "key" and element is not None:
            if action == "set_text":
                devices.keyboard.chord("a", ["ctrl"])
                devices.keyboard.chord("BackSpace")
            devices.keyboard.type_text(d.text or "")
        else:
            device_form(devices, action, element, point, d)
        out = verify.verdict(session, base, expect) if do_verify else verify.disabled()
    finally:
        sentinel.restore(snap)  # [F1]; by workspace name when it was empty
    out["disturbed_user"] = True
    return out

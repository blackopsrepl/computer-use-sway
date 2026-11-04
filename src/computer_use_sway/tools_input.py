"""Low-level input tools: pointer, keyboard, focus, clipboard writes.

Every input goes through virtual devices bound to a seat. `seat: "agent"`
(the default in seat/sandbox mode) uses the agent's own pointer and keyboard;
`seat: "user"` uses seat0, the user's. Agent-seat input snapshots seat0's
focus before and restores it after, and reports `disturbed_user`.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import core, desktop, install, keymap, session, verify

INPUT_SEATS = ("agent", "user")


def _int(arguments: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        raise core.ToolError(f"{key} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise core.ToolError(f"{key} must be an integer") from exc
    if number < low or number > high:
        raise core.ToolError(f"{key} must be between {low} and {high}")
    return number


def resolve_seat(arguments: dict[str, Any]) -> str:
    seat = arguments.get("seat")
    if seat is None:
        return session.current().default_seat()
    if seat not in INPUT_SEATS:
        raise core.ToolError("seat must be agent or user")
    return seat


class SeatUse:
    """What one input call did to the user: filled in by `using_seat`."""

    def __init__(self, seat: str, devices: Any) -> None:
        self.seat = seat
        self.devices = devices
        self.disturbed_user = False
        self.baseline: verify.Baseline | None = None
        self.verdict: dict[str, Any] | None = None

    def begin(self, con_id: int | None, enabled: bool) -> None:
        """Baseline the target window just before the input (low-level tools report, never escalate)."""
        if enabled:
            self.baseline = verify.take_baseline(session.current(), None, con_id, want_pixels=True)

    def finish(self) -> None:
        if self.baseline is not None:
            self.verdict = verify.verdict(session.current(), self.baseline)


def keyboard_target(seat: str) -> int | None:
    return desktop.seat_focus(install.SEAT if seat == "agent" else (session.current().user_seat or "seat0"))


def pointer_target(x: int, y: int) -> int | None:
    win = desktop.window_at(x, y)
    return int(win["id"]) if win else None


def wants_verify(arguments: dict[str, Any]) -> bool:
    flag = arguments.get("verify", True)
    if not isinstance(flag, bool):
        raise core.ToolError("verify must be a boolean")
    return flag


@contextmanager
def using_seat(arguments: dict[str, Any]) -> Iterator[SeatUse]:
    seat = resolve_seat(arguments)
    sess = session.current()
    if seat == "user":
        yield SeatUse(seat, sess.user_devices())
        return
    agent = sess.agent()
    sentinel = sess.sentinel()
    snap = sentinel.snapshot()
    use = SeatUse(seat, agent.devices)
    try:
        yield use
    finally:
        use.disturbed_user = sentinel.restore(snap)


def seat_payload(use: SeatUse) -> dict[str, Any]:
    out: dict[str, Any] = {"seat": use.seat, "disturbed_user": use.disturbed_user}
    if use.verdict is not None:
        out["effect"] = use.verdict["effect"]
        out["evidence"] = use.verdict["evidence"]
    return out


def point(arguments: dict[str, Any]) -> tuple[int, int] | None:
    if ("x" in arguments) != ("y" in arguments):
        raise core.ToolError("provide both x and y, or neither")
    if "x" not in arguments:
        return None
    return desktop.validate_coordinates(arguments.get("x"), arguments.get("y"))


def point_arg(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise core.ToolError(f"{name} must be an object with x and y")
    return desktop.validate_coordinates(value.get("x"), value.get("y"))


def tool_focus_window(arguments: dict[str, Any]) -> core.Result:
    seat = resolve_seat(arguments)
    sess = session.current()
    selected = desktop.find_window(arguments)
    con_id = int(selected["id"])
    if arguments.get("move_to_agent"):
        sess.agent()
        desktop.ipc().command(f"[con_id={con_id}] move container to workspace {desktop.AGENT_WORKSPACE}")
    if seat == "user":
        before = desktop.get_focused_window()
        desktop.ipc().command(f"[con_id={con_id}] focus")
        time.sleep(0.05)
        after = desktop.get_focused_window()
        return core.text_result({"before": before, "selected": selected, "after": after, "seat": seat},
                                {"seat": seat, "con_id": con_id})
    agent = sess.agent()
    sentinel = sess.sentinel()
    snap = sentinel.snapshot()
    try:
        agent.focus(con_id)
    finally:
        disturbed = sentinel.restore(snap)
    return core.text_result(
        {"selected": selected, "agent_focus": desktop.seat_focus(install.SEAT), "seat": seat,
         "disturbed_user": disturbed},
        {"seat": seat, "con_id": con_id, "disturbed_user": disturbed},
    )


def tool_move_pointer(arguments: dict[str, Any]) -> core.Result:
    mode = arguments.get("mode", "set")
    if mode not in {"set", "move"}:
        raise core.ToolError("mode must be set or move")
    with using_seat(arguments) as use:
        pointer = use.devices.pointer
        if mode == "move":
            if pointer.position is None:
                raise core.ToolError("mode=move needs a known pointer position; use mode=set first")
            x, y = desktop.validate_coordinates(
                pointer.position[0] + int(arguments.get("x", 0)), pointer.position[1] + int(arguments.get("y", 0))
            )
        else:
            x, y = desktop.validate_coordinates(arguments.get("x"), arguments.get("y"))
        pointer.move_to(x, y)
    return core.text_result({"moved": True, "mode": mode, "x": x, "y": y, **seat_payload(use)},
                            seat_payload(use))


def tool_click(arguments: dict[str, Any]) -> core.Result:
    target = point(arguments)
    button = arguments.get("button", "left")
    if button not in {"left", "middle", "right"}:
        raise core.ToolError("button must be left, middle, or right")
    count = _int(arguments, "count", 1, 1, 3)
    interval_ms = _int(arguments, "interval_ms", 80, 0, 5000)
    with using_seat(arguments) as use:
        pointer = use.devices.pointer
        if target is None:
            if pointer.position is None:
                raise core.ToolError("click without x,y needs a known pointer position; pass x and y")
            target = pointer.position
        use.begin(pointer_target(*target), wants_verify(arguments))
        pointer.click(target[0], target[1], button=button, count=count, interval_ms=interval_ms)
        use.finish()
    x, y = target
    return core.text_result(
        {"clicked": True, "x": x, "y": y, "button": button, "count": count, **seat_payload(use)},
        seat_payload(use),
    )


def tool_drag(arguments: dict[str, Any]) -> core.Result:
    start = point_arg(arguments.get("from"), "from")
    end = point_arg(arguments.get("to"), "to")
    button = arguments.get("button", "left")
    if button not in {"left", "middle", "right"}:
        raise core.ToolError("button must be left, middle, or right")
    steps = _int(arguments, "steps", 12, 1, 100)
    duration_ms = _int(arguments, "duration_ms", 350, 0, 10_000)
    with using_seat(arguments) as use:
        use.begin(pointer_target(*start), wants_verify(arguments))
        use.devices.pointer.drag(start, end, button=button, steps=steps, duration_ms=duration_ms)
        use.finish()
    return core.text_result(
        {
            "dragged": True,
            "from": {"x": start[0], "y": start[1]},
            "to": {"x": end[0], "y": end[1]},
            "button": button,
            "steps": steps,
            "duration_ms": duration_ms,
            **seat_payload(use),
        },
        seat_payload(use),
    )


def tool_scroll(arguments: dict[str, Any]) -> core.Result:
    target = point(arguments)
    direction = arguments.get("direction")
    if direction not in {"up", "down", "left", "right"}:
        raise core.ToolError("direction must be up, down, left, or right")
    clicks = _int(arguments, "clicks", 1, 1, 100)
    with using_seat(arguments) as use:
        if target is not None:
            use.devices.pointer.move_to(*target)
        at = target or use.devices.pointer.position
        use.begin(pointer_target(*at) if at else None, wants_verify(arguments))
        use.devices.pointer.scroll(direction, clicks)
        use.finish()
    x, y = target if target is not None else (None, None)
    return core.text_result(
        {"scrolled": True, "direction": direction, "clicks": clicks, "x": x, "y": y, **seat_payload(use)},
        seat_payload(use),
    )


def tool_type_text(arguments: dict[str, Any]) -> core.Result:
    text = arguments.get("text")
    if not isinstance(text, str):
        raise core.ToolError("text must be a string")
    if "\x00" in text:
        raise core.ToolError("text must not contain NUL bytes")
    if len(text) > core.TEXT_LIMIT:
        raise core.ToolError(f"text exceeds limit of {core.TEXT_LIMIT} characters")
    delay_ms = _int(arguments, "delay_ms", 0, 0, 5000)
    with using_seat(arguments) as use:
        use.begin(keyboard_target(use.seat), wants_verify(arguments))
        typed = use.devices.keyboard.type_text(text, delay_ms=delay_ms)
        use.finish()
    return core.text_result(
        {"typed": True, "characters": typed, "delay_ms": delay_ms, **seat_payload(use)},
        dict(seat_payload(use), characters=typed),
    )


def parse_modifiers(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise core.ToolError("modifiers must be a list")
    mods = []
    for modifier in value:
        name = str(modifier).lower()
        if name not in keymap.MOD_KEYS:
            raise core.ToolError("modifiers may only contain ctrl, shift, alt, or logo")
        if name not in mods:
            mods.append(name)
    return mods


def tool_key(arguments: dict[str, Any]) -> core.Result:
    key = arguments.get("key")
    if not isinstance(key, str) or not key:
        raise core.ToolError("key must be a non-empty string")
    mods = parse_modifiers(arguments.get("modifiers", []))
    with using_seat(arguments) as use:
        use.begin(keyboard_target(use.seat), wants_verify(arguments))
        use.devices.keyboard.chord(key, mods)
        use.finish()
    return core.text_result({"sent": True, "key": key, "modifiers": mods, **seat_payload(use)},
                            dict(seat_payload(use), key=key, modifiers=mods))


def clipboard_seat_args(arguments: dict[str, Any]) -> list[str]:
    seat = resolve_seat(arguments)
    if seat == "agent":
        session.current().agent()
        return ["--seat", install.SEAT]
    user = session.current().user_seat
    return ["--seat", user] if user else []


def tool_clipboard_set(arguments: dict[str, Any]) -> core.Result:
    core.require_binaries(["wl-copy"])
    text = arguments.get("text")
    if not isinstance(text, str):
        raise core.ToolError("text must be a string")
    if "\x00" in text:
        raise core.ToolError("text must not contain NUL bytes")
    size = len(text.encode("utf-8"))
    if size > core.CLIPBOARD_LIMIT:
        raise core.ToolError(f"text exceeds clipboard limit of {core.CLIPBOARD_LIMIT} bytes")
    seat_args = clipboard_seat_args(arguments)
    core.run_command(["wl-copy", *seat_args, "--", text], timeout=5.0, capture=False)
    seat = resolve_seat(arguments)
    return core.text_result({"clipboard_set": True, "bytes": size, "seat": seat}, {"bytes": size, "seat": seat})


TOOLS = {
    "focus_window": tool_focus_window,
    "move_pointer": tool_move_pointer,
    "click": tool_click,
    "drag": tool_drag,
    "scroll": tool_scroll,
    "type_text": tool_type_text,
    "key": tool_key,
    "clipboard_set": tool_clipboard_set,
}

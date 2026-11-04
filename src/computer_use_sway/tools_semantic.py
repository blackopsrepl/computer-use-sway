"""Semantic tools: session, act, launch, wait_for, run."""

from __future__ import annotations

import math
from typing import Any

from . import actions, core, launch, plans, session, waits
from .actions_delivery import Delivery
from .tools_input import parse_modifiers


def tool_session(arguments: dict[str, Any]) -> core.Result:
    action = arguments.get("action")
    sess = session.current()
    if action == "status":
        return core.text_result(sess.status(), {"action": action, "mode": sess.mode})
    if action == "start":
        mode = arguments.get("mode", "seat")
        result = sess.start(mode, arguments.get("options"))
        return core.text_result(result, {"action": action, "mode": mode})
    if action == "stop":
        return core.text_result(sess.stop(), {"action": action})
    raise core.ToolError("action must be status, start, or stop")


def _text(arguments: dict[str, Any], required: bool) -> str | None:
    text = arguments.get("text")
    if text is None:
        if required:
            raise core.ToolError("this action needs text")
        return None
    if not isinstance(text, str):
        raise core.ToolError("text must be a string")
    if "\x00" in text:
        raise core.ToolError("text must not contain NUL bytes")
    if len(text) > core.TEXT_LIMIT:
        raise core.ToolError(f"text exceeds limit of {core.TEXT_LIMIT} characters")
    return text


def tool_act(arguments: dict[str, Any]) -> core.Result:
    action = arguments.get("action")
    if action not in actions.ACTIONS:
        raise core.ToolError(f"action must be one of {', '.join(actions.ACTIONS)}")
    d = Delivery()
    d.text = _text(arguments, action in ("set_text", "type"))
    if action == "key":
        key = arguments.get("key")
        if not isinstance(key, str) or not key:
            raise core.ToolError("action key needs key")
        d.key = key
        d.modifiers = parse_modifiers(arguments.get("modifiers", []))
    if action == "set_value":
        value = arguments.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise core.ToolError("set_value needs a finite number value")
        d.value = float(value)
    if action == "scroll":
        direction = arguments.get("direction", "down")
        if direction not in ("up", "down", "left", "right"):
            raise core.ToolError("direction must be up, down, left, or right")
        d.direction = direction
        clicks = arguments.get("clicks", 3)
        if isinstance(clicks, bool) or not isinstance(clicks, int) or not 1 <= clicks <= 50:
            raise core.ToolError("clicks must be between 1 and 50")
        d.clicks = clicks
    if action == "drag_to":
        if arguments.get("to") is None:
            raise core.ToolError("drag_to needs to")
        d.to = arguments["to"]
    verify_flag = arguments.get("verify", True)
    if not isinstance(verify_flag, bool):
        raise core.ToolError("verify must be a boolean")
    return actions.act(session.current(), arguments.get("target"), action, d,
                       arguments.get("delivery", "auto"), verify_flag)


def tool_launch(arguments: dict[str, Any]) -> core.Result:
    a11y = arguments.get("a11y", True)
    if not isinstance(a11y, bool):
        raise core.ToolError("a11y must be a boolean")
    return launch.launch(session.current(), arguments.get("argv"), arguments.get("cwd"), arguments.get("env"),
                         arguments.get("timeout_ms", 20000), a11y)


def tool_wait_for(arguments: dict[str, Any]) -> core.Result:
    return waits.wait_for(session.current(), arguments.get("condition"), arguments.get("timeout_ms", 10000))


def tool_run(arguments: dict[str, Any]) -> core.Result:
    from .timeline import append_event
    from .tools import TOOLS

    return plans.run(TOOLS, arguments.get("steps"), arguments.get("stop_on", "failure"),
                     arguments.get("timeout_ms", 120000), arguments.get("observe_after"), publish=append_event)


TOOLS = {
    "session": tool_session,
    "act": tool_act,
    "launch": tool_launch,
    "wait_for": tool_wait_for,
    "run": tool_run,
}

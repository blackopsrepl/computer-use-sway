"""The MCP tool list, in the order agents should reach for them."""

from __future__ import annotations

from typing import Any

from . import specs_input, specs_observe, specs_semantic

ORDER = ("session", "screen_info", "observe", "read", "act", "run", "wait_for", "launch", "screenshot",
         "window_tree", "focus_window", "move_pointer", "click", "drag", "scroll", "type_text", "key",
         "clipboard_set", "clipboard_get")


def tool_specs() -> list[dict[str, Any]]:
    by_name = {spec["name"]: spec for spec in specs_semantic.specs() + specs_observe.specs() + specs_input.specs()}
    return [by_name[name] for name in ORDER]

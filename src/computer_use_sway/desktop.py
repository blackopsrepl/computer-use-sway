from __future__ import annotations

import re
from typing import Any

from . import core


def get_outputs() -> list[dict[str, Any]]:
    core.require_session()
    outputs = core.read_json_command(["swaymsg", "-t", "get_outputs"])
    return [output for output in outputs if output.get("active")]


def get_seats() -> list[dict[str, Any]]:
    core.require_session()
    return core.read_json_command(["swaymsg", "-t", "get_seats"])


def detect_seat() -> str:
    seats = get_seats()
    for seat in seats:
        if seat.get("name") == "seat0":
            return "seat0"
    for seat in seats:
        if int(seat.get("capabilities", 0)) & 1:
            name = seat.get("name")
            if isinstance(name, str) and name:
                return name
    raise core.ToolError("no Sway seat with pointer capability found")


def get_tree() -> dict[str, Any]:
    core.require_session()
    return core.read_json_command(["swaymsg", "-t", "get_tree"], timeout=8.0)


def walk_tree(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child_key in ("nodes", "floating_nodes"):
        for child in node.get(child_key, []) or []:
            nodes.extend(walk_tree(child))
    return nodes


def get_focused_window(tree: dict[str, Any] | None = None) -> dict[str, Any] | None:
    root = tree or get_tree()
    for node in walk_tree(root):
        if node.get("focused"):
            return simplify_window(node)
    return None


def active_output_rects() -> list[dict[str, int]]:
    rects = []
    for output in get_outputs():
        rect = output.get("rect") or {}
        try:
            rects.append(
                {
                    "x": int(rect["x"]),
                    "y": int(rect["y"]),
                    "width": int(rect["width"]),
                    "height": int(rect["height"]),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise core.ToolError(f"invalid output geometry for {output.get('name')}") from exc
    if not rects:
        raise core.ToolError("no active Sway outputs found")
    return rects


def validate_coordinates(x: Any, y: Any) -> tuple[int, int]:
    try:
        ix = int(x)
        iy = int(y)
    except (TypeError, ValueError) as exc:
        raise core.ToolError("coordinates must be integers") from exc

    for rect in active_output_rects():
        if (
            rect["x"] <= ix < rect["x"] + rect["width"]
            and rect["y"] <= iy < rect["y"] + rect["height"]
        ):
            return ix, iy

    raise core.ToolError(f"coordinates outside active outputs: x={ix}, y={iy}")


def validate_region(region: Any) -> dict[str, int] | None:
    if region is None:
        return None
    if not isinstance(region, dict):
        raise core.ToolError("region must be an object")
    x, y = validate_coordinates(region.get("x"), region.get("y"))
    try:
        width = int(region.get("width"))
        height = int(region.get("height"))
    except (TypeError, ValueError) as exc:
        raise core.ToolError("region width and height must be integers") from exc
    if width <= 0 or height <= 0:
        raise core.ToolError("region width and height must be positive")
    validate_coordinates(x + width - 1, y + height - 1)
    return {"x": x, "y": y, "width": width, "height": height}


def png_dimensions(data: bytes) -> dict[str, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return {"width": width, "height": height}


def simplify_window(node: dict[str, Any], workspace: str | None = None) -> dict[str, Any]:
    props = node.get("window_properties") or {}
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "app_id": node.get("app_id"),
        "class": props.get("class"),
        "instance": props.get("instance"),
        "focused": bool(node.get("focused")),
        "visible": bool(node.get("visible")),
        "rect": node.get("rect"),
        "workspace": workspace,
        "type": node.get("type"),
    }


def collect_windows(
    node: dict[str, Any],
    include_scratchpad: bool,
    max_depth: int,
    depth: int = 0,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    if depth > max_depth:
        return []
    current_workspace = workspace
    if node.get("type") == "workspace":
        current_workspace = node.get("name")
    if node.get("name") == "__i3_scratch" and not include_scratchpad:
        return []

    windows = []
    if node.get("type") in {"con", "floating_con"} and (
        node.get("app_id") or (node.get("window_properties") or {}).get("class")
    ):
        windows.append(simplify_window(node, current_workspace))

    for child_key in ("nodes", "floating_nodes"):
        for child in node.get(child_key, []) or []:
            windows.extend(
                collect_windows(
                    child,
                    include_scratchpad=include_scratchpad,
                    max_depth=max_depth,
                    depth=depth + 1,
                    workspace=current_workspace,
                )
            )
    return windows


def string_match(value: Any, pattern: str, mode: str) -> bool:
    if not isinstance(value, str):
        return False
    if mode == "exact":
        return value == pattern
    if mode == "contains":
        return pattern.lower() in value.lower()
    if mode == "regex":
        try:
            return re.search(pattern, value) is not None
        except re.error as exc:
            raise core.ToolError(f"invalid regex: {exc}") from exc
    raise core.ToolError("match must be exact, contains, or regex")


def find_window(arguments: dict[str, Any]) -> dict[str, Any]:
    keys = [key for key in ("con_id", "app_id", "class", "title") if arguments.get(key) is not None]
    if len(keys) != 1:
        raise core.ToolError("provide exactly one of con_id, app_id, class, or title")

    mode = arguments.get("match", "contains")
    if mode not in {"exact", "contains", "regex"}:
        raise core.ToolError("match must be exact, contains, or regex")

    windows = collect_windows(get_tree(), include_scratchpad=True, max_depth=50)
    key = keys[0]
    pattern = str(arguments[key])

    for window in windows:
        if key == "con_id":
            try:
                if int(window.get("id")) == int(pattern):
                    return window
            except (TypeError, ValueError):
                pass
        elif key == "title":
            if string_match(window.get("name"), pattern, mode):
                return window
        elif string_match(window.get(key), pattern, mode):
            return window

    raise core.ToolError(f"no matching window found for {key}={pattern!r}")


def sway_cursor(*parts: str) -> None:
    seat = detect_seat()
    core.run_command(["swaymsg", "-t", "command", "seat", seat, "cursor", *parts])

from __future__ import annotations

import base64
import json
import shutil
import time
from typing import Any

from . import core, desktop, manager, tts
from .version import SERVER_NAME, SERVER_VERSION


def tool_recording_start(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.start(arguments))


def tool_recording_status(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.status(arguments))


def tool_recording_stop(_: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.stop())


def tool_recording_timeline(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.timeline(arguments))


def tool_recording_voiceover(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.voiceover(arguments))


def tool_recording_scenes(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return core.json_text(manager.RECORDINGS.scenes(arguments))


def tool_screen_info(_: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["swaymsg", "grim", "wtype", "wl-copy", "wl-paste"])
    outputs = desktop.get_outputs()
    rects = desktop.active_output_rects()
    min_x = min(rect["x"] for rect in rects)
    min_y = min(rect["y"] for rect in rects)
    max_x = max(rect["x"] + rect["width"] for rect in rects)
    max_y = max(rect["y"] + rect["height"] for rect in rects)
    info = {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "seat": desktop.detect_seat(),
        "bounds": {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y},
        "outputs": [
            {
                "name": output.get("name"),
                "active": output.get("active"),
                "rect": output.get("rect"),
                "scale": output.get("scale"),
                "transform": output.get("transform"),
                "current_mode": output.get("current_mode"),
            }
            for output in outputs
        ],
        "focused_window": desktop.get_focused_window(),
        "binaries": {
            name: shutil.which(name)
            for name in (
                "swaymsg",
                "grim",
                "wtype",
                "wl-copy",
                "wl-paste",
                "wf-recorder",
                "ffmpeg",
                "ffprobe",
                tts.NARRATION_EDGE_COMMAND,
                tts.NARRATION_PIPER_COMMAND,
                "tesseract",
            )
        },
    }
    return core.json_text(info)


def tool_screenshot(arguments: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["grim"])
    include_cursor = bool(arguments.get("include_cursor", True))
    output_mode = arguments.get("output")
    if output_mode is None:
        output_mode = "image"
    if output_mode not in {"image", "data_url", "both"}:
        raise core.ToolError("output must be null, image, data_url, or both")
    region = desktop.validate_region(arguments.get("region"))

    args = ["grim"]
    if include_cursor:
        args.append("-c")
    if region:
        args.extend(["-g", f"{region['x']},{region['y']} {region['width']}x{region['height']}"])
    args.extend(["-t", "png", "-"])

    data = core.run_command(args, timeout=10.0, binary=True).stdout
    if not data:
        raise core.ToolError("grim returned an empty screenshot")
    encoded = base64.b64encode(data).decode("ascii")
    meta = {
        "mimeType": "image/png",
        "bytes": len(data),
        "dimensions": desktop.png_dimensions(data),
        "include_cursor": include_cursor,
        "region": region,
    }
    content = [{"type": "text", "text": json.dumps(meta, indent=2)}]
    if output_mode in {"image", "both"}:
        content.append({"type": "image", "mimeType": "image/png", "data": encoded})
    if output_mode in {"data_url", "both"}:
        content.append(
            {"type": "text", "text": f"data:image/png;base64,{encoded}"}
        )
    return content


def tool_window_tree(arguments: dict[str, Any]) -> list[dict[str, str]]:
    include_scratchpad = bool(arguments.get("include_scratchpad", False))
    max_depth = int(arguments.get("max_depth", 12))
    if max_depth < 1 or max_depth > 50:
        raise core.ToolError("max_depth must be between 1 and 50")
    windows = desktop.collect_windows(desktop.get_tree(), include_scratchpad, max_depth)
    return core.json_text({"windows": windows, "count": len(windows)})


def tool_focus_window(arguments: dict[str, Any]) -> list[dict[str, str]]:
    before = desktop.get_focused_window()
    selected = desktop.find_window(arguments)
    core.run_command(["swaymsg", "-t", "command", f"[con_id={int(selected['id'])}]", "focus"])
    time.sleep(0.05)
    after = desktop.get_focused_window()
    return core.json_text({"before": before, "selected": selected, "after": after})


def tool_move_pointer(arguments: dict[str, Any]) -> list[dict[str, str]]:
    mode = arguments.get("mode", "set")
    if mode not in {"set", "move"}:
        raise core.ToolError("mode must be set or move")
    x, y = desktop.validate_coordinates(arguments.get("x"), arguments.get("y"))
    desktop.sway_cursor(mode, str(x), str(y))
    return core.json_text({"moved": True, "mode": mode, "x": x, "y": y, "seat": desktop.detect_seat()})


def press_release(button: str, interval_ms: int = 40) -> None:
    desktop.sway_cursor("press", button)
    time.sleep(max(interval_ms, 0) / 1000.0)
    desktop.sway_cursor("release", button)


def tool_click(arguments: dict[str, Any]) -> list[dict[str, str]]:
    if ("x" in arguments) != ("y" in arguments):
        raise core.ToolError("provide both x and y, or neither")
    if "x" in arguments:
        x, y = desktop.validate_coordinates(arguments.get("x"), arguments.get("y"))
        desktop.sway_cursor("set", str(x), str(y))
    else:
        x = y = None

    button_name = arguments.get("button", "left")
    button = core.BUTTONS.get(button_name)
    if button is None:
        raise core.ToolError("button must be left, middle, or right")
    count = int(arguments.get("count", 1))
    interval_ms = int(arguments.get("interval_ms", 80))
    if count < 1 or count > 3:
        raise core.ToolError("count must be between 1 and 3")
    if interval_ms < 0 or interval_ms > 5000:
        raise core.ToolError("interval_ms must be between 0 and 5000")

    for index in range(count):
        press_release(button)
        if index + 1 < count:
            time.sleep(interval_ms / 1000.0)
    return core.json_text({"clicked": True, "x": x, "y": y, "button": button_name, "count": count})


def point_arg(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise core.ToolError(f"{name} must be an object with x and y")
    return desktop.validate_coordinates(value.get("x"), value.get("y"))


def tool_drag(arguments: dict[str, Any]) -> list[dict[str, str]]:
    start_x, start_y = point_arg(arguments.get("from"), "from")
    end_x, end_y = point_arg(arguments.get("to"), "to")
    button_name = arguments.get("button", "left")
    button = core.BUTTONS.get(button_name)
    if button is None:
        raise core.ToolError("button must be left, middle, or right")
    steps = int(arguments.get("steps", 12))
    duration_ms = int(arguments.get("duration_ms", 350))
    if steps < 1 or steps > 100:
        raise core.ToolError("steps must be between 1 and 100")
    if duration_ms < 0 or duration_ms > 10_000:
        raise core.ToolError("duration_ms must be between 0 and 10000")

    sleep_s = duration_ms / 1000.0 / max(steps, 1)
    desktop.sway_cursor("set", str(start_x), str(start_y))
    try:
        desktop.sway_cursor("press", button)
        for step in range(1, steps + 1):
            x = round(start_x + (end_x - start_x) * step / steps)
            y = round(start_y + (end_y - start_y) * step / steps)
            desktop.sway_cursor("set", str(x), str(y))
            if sleep_s:
                time.sleep(sleep_s)
    finally:
        try:
            desktop.sway_cursor("release", button)
        except core.ToolError as exc:
            core.eprint(f"drag release failed: {exc}")
    return core.json_text(
        {
            "dragged": True,
            "from": {"x": start_x, "y": start_y},
            "to": {"x": end_x, "y": end_y},
            "button": button_name,
            "steps": steps,
            "duration_ms": duration_ms,
        }
    )


def tool_scroll(arguments: dict[str, Any]) -> list[dict[str, str]]:
    if ("x" in arguments) != ("y" in arguments):
        raise core.ToolError("provide both x and y, or neither")
    if "x" in arguments:
        x, y = desktop.validate_coordinates(arguments.get("x"), arguments.get("y"))
        desktop.sway_cursor("set", str(x), str(y))
    else:
        x = y = None

    direction = arguments.get("direction")
    button = core.SCROLL_BUTTONS.get(direction)
    if button is None:
        raise core.ToolError("direction must be up, down, left, or right")
    clicks = int(arguments.get("clicks", 1))
    if clicks < 1 or clicks > 100:
        raise core.ToolError("clicks must be between 1 and 100")
    for _ in range(clicks):
        press_release(button, interval_ms=10)
    return core.json_text({"scrolled": True, "direction": direction, "clicks": clicks, "x": x, "y": y})


def tool_type_text(arguments: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["wtype"])
    text = arguments.get("text")
    if not isinstance(text, str):
        raise core.ToolError("text must be a string")
    if "\x00" in text:
        raise core.ToolError("text must not contain NUL bytes")
    if len(text) > core.TEXT_LIMIT:
        raise core.ToolError(f"text exceeds limit of {core.TEXT_LIMIT} characters")
    delay_ms = int(arguments.get("delay_ms", 0))
    if delay_ms < 0 or delay_ms > 5000:
        raise core.ToolError("delay_ms must be between 0 and 5000")
    if delay_ms == 0:
        core.run_command(["wtype", text], timeout=max(5.0, len(text) / 500.0))
    else:
        for char in text:
            core.run_command(["wtype", char], timeout=2.0)
            time.sleep(delay_ms / 1000.0)
    return core.json_text({"typed": True, "characters": len(text), "delay_ms": delay_ms})


def tool_key(arguments: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["wtype"])
    key = arguments.get("key")
    if not isinstance(key, str) or not key:
        raise core.ToolError("key must be a non-empty string")
    modifiers = arguments.get("modifiers", [])
    if not isinstance(modifiers, list):
        raise core.ToolError("modifiers must be a list")
    canonical_modifiers = []
    for modifier in modifiers:
        mapped = core.MODIFIERS.get(str(modifier).lower())
        if mapped is None:
            raise core.ToolError("modifiers may only contain ctrl, shift, alt, or logo")
        canonical_modifiers.append(mapped)

    args = ["wtype"]
    for modifier in canonical_modifiers:
        args.extend(["-M", modifier])
    args.extend(["-k", key])
    for modifier in reversed(canonical_modifiers):
        args.extend(["-m", modifier])
    core.run_command(args)
    return core.json_text({"sent": True, "key": key, "modifiers": canonical_modifiers})


def tool_clipboard_set(arguments: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["wl-copy"])
    text = arguments.get("text")
    if not isinstance(text, str):
        raise core.ToolError("text must be a string")
    if "\x00" in text:
        raise core.ToolError("text must not contain NUL bytes")
    if len(text.encode("utf-8")) > core.CLIPBOARD_LIMIT:
        raise core.ToolError(f"text exceeds clipboard limit of {core.CLIPBOARD_LIMIT} bytes")
    core.run_command(["wl-copy", text], timeout=5.0, capture=False)
    return core.json_text({"clipboard_set": True, "bytes": len(text.encode("utf-8"))})


def tool_clipboard_get(arguments: dict[str, Any]) -> list[dict[str, str]]:
    core.require_binaries(["wl-paste"])
    max_bytes = int(arguments.get("max_bytes", core.CLIPBOARD_LIMIT))
    if max_bytes < 1 or max_bytes > core.CLIPBOARD_LIMIT:
        raise core.ToolError(f"max_bytes must be between 1 and {core.CLIPBOARD_LIMIT}")
    result = core.run_command(["wl-paste", "--no-newline"], timeout=5.0, binary=True).stdout
    if b"\x00" in result:
        raise core.ToolError("clipboard appears to contain binary data")
    if len(result) > max_bytes:
        raise core.ToolError(f"clipboard content exceeds max_bytes ({max_bytes})")
    return core.json_text({"text": result.decode("utf-8", errors="strict"), "bytes": len(result)})


TOOLS = {
    "screen_info": tool_screen_info,
    "screenshot": tool_screenshot,
    "window_tree": tool_window_tree,
    "focus_window": tool_focus_window,
    "move_pointer": tool_move_pointer,
    "click": tool_click,
    "drag": tool_drag,
    "scroll": tool_scroll,
    "type_text": tool_type_text,
    "key": tool_key,
    "clipboard_set": tool_clipboard_set,
    "clipboard_get": tool_clipboard_get,
    "recording_start": tool_recording_start,
    "recording_status": tool_recording_status,
    "recording_stop": tool_recording_stop,
    "recording_timeline": tool_recording_timeline,
    "recording_voiceover": tool_recording_voiceover,
    "recording_scenes": tool_recording_scenes,
}

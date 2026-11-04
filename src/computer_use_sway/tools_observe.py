"""Observation tools: screen info, screenshots, window tree, clipboard reads."""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any

from . import core, desktop, session
from .version import SERVER_NAME, SERVER_VERSION


def tool_screen_info(_: dict[str, Any]) -> core.Result:
    sess = session.current()
    sess.ensure_started()
    outputs = desktop.get_outputs()
    agent_output = sess._agent.output if sess._agent is not None else None
    user_rects = [o["rect"] for o in outputs if o.get("name") != agent_output]
    rects = user_rects or [o["rect"] for o in outputs]
    min_x = min(r["x"] for r in rects)
    min_y = min(r["y"] for r in rects)
    max_x = max(r["x"] + r["width"] for r in rects)
    max_y = max(r["y"] + r["height"] for r in rects)
    info = {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "mode": sess.mode,
        "seat": sess.user_seat,
        "default_input_seat": sess.default_seat(),
        "agent": sess._agent.status() if sess._agent is not None else None,
        "bounds": {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y},
        "outputs": [
            {
                "name": output.get("name"),
                "agent": output.get("name") == agent_output,
                "active": output.get("active"),
                "rect": output.get("rect"),
                "scale": output.get("scale"),
                "transform": output.get("transform"),
                "current_mode": output.get("current_mode"),
            }
            for output in outputs
        ],
        "focused_window": desktop.get_focused_window(),
        "binaries": {name: shutil.which(name) for name in ("grim", "tesseract", "wl-copy", "wl-paste")},
    }
    return core.text_result(info)


def write_private_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def screenshot_region(arguments: dict[str, Any]) -> tuple[dict[str, int] | None, str | None]:
    """Resolve region / window / target_output to (region, output_name)."""
    chosen = [k for k in ("region", "window", "target_output") if arguments.get(k) is not None]
    if len(chosen) > 1:
        raise core.ToolError("pass at most one of region, window, target_output")
    if arguments.get("window") is not None:
        selector = arguments["window"]
        if not isinstance(selector, dict):
            raise core.ToolError("window must be an object selector")
        win = desktop.find_window(selector)
        c = win["content"]
        return desktop.validate_region(c), None
    target = arguments.get("target_output")
    if target is not None:
        if not isinstance(target, str) or not target:
            raise core.ToolError("target_output must be an output name or 'agent'")
        if target == "agent":
            agent = session.current().agent()
            target = agent.output
        names = {o.get("name") for o in desktop.get_outputs()}
        if target not in names:
            raise core.ToolError(f"no active output named {target!r}")
        return None, target
    if arguments.get("region") is None:
        return user_area(), None
    return desktop.validate_region(arguments.get("region")), None


def user_area() -> dict[str, int] | None:
    """The bounding box of the user's outputs. The agent output sits 100000 px
    away; a whole-layout capture would span the gap (a 110-megapixel image)
    and show the user nothing they can see. None when no agent output exists."""
    sess = session.current()
    agent_output = sess._agent.output if sess._agent is not None else None
    if agent_output is None:
        return None
    rects = [o["rect"] for o in desktop.get_outputs() if o.get("name") != agent_output]
    if not rects:
        return None
    x0, y0 = min(r["x"] for r in rects), min(r["y"] for r in rects)
    x1 = max(r["x"] + r["width"] for r in rects)
    y1 = max(r["y"] + r["height"] for r in rects)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def tool_screenshot(arguments: dict[str, Any]) -> core.Result:
    from . import capture

    include_cursor = bool(arguments.get("include_cursor", True))
    save_path = arguments.get("save_path")
    output_mode = arguments.get("output")
    if save_path is not None:
        if not isinstance(save_path, str) or not save_path:
            raise core.ToolError("save_path must be a non-empty string")
        if output_mode is not None:
            raise core.ToolError("save_path cannot be combined with output; save_path returns text-only metadata")
    else:
        if output_mode is None:
            output_mode = "image"
        if output_mode not in {"image", "data_url", "both"}:
            raise core.ToolError("output must be null, image, data_url, or both")
    region, output_name = screenshot_region(arguments)
    data = capture.png(region=region, output=output_name, cursor=include_cursor)
    meta = {
        "mimeType": "image/png",
        "bytes": len(data),
        "dimensions": desktop.png_dimensions(data),
        "include_cursor": include_cursor,
        "region": region,
        "target_output": output_name,
    }
    timeline_meta = {"bytes": len(data), "region": region is not None, "target_output": output_name}
    if save_path is not None:
        target = Path(save_path)
        if target.is_dir():
            raise core.ToolError(f"save_path is a directory: {target}")
        if not target.parent.is_dir():
            raise core.ToolError(f"save_path parent directory does not exist: {target.parent}")
        try:
            write_private_bytes(target, data)
        except OSError as exc:
            raise core.ToolError(f"could not write screenshot to {target}: {exc}") from exc
        meta["saved_to"] = str(target)
        return core.Result([{"type": "text", "text": json.dumps(meta, indent=2)}], timeline_meta)

    encoded = base64.b64encode(data).decode("ascii")
    content = [{"type": "text", "text": json.dumps(meta, indent=2)}]
    if output_mode in {"image", "both"}:
        content.append({"type": "image", "mimeType": "image/png", "data": encoded})
    if output_mode in {"data_url", "both"}:
        content.append({"type": "text", "text": f"data:image/png;base64,{encoded}"})
    return core.Result(content, timeline_meta)


def tool_window_tree(arguments: dict[str, Any]) -> core.Result:
    include_scratchpad = bool(arguments.get("include_scratchpad", False))
    max_depth = arguments.get("max_depth", 12)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1 or max_depth > 50:
        raise core.ToolError("max_depth must be between 1 and 50")
    windows = desktop.collect_windows(desktop.get_tree(), include_scratchpad, max_depth)
    sess = session.current()
    seats = {"user": sess.user_seat}
    return core.text_result({"windows": windows, "count": len(windows),
                             "agent_focus": desktop.seat_focus("cus-agent"), "seats": seats},
                            {"count": len(windows)})


def tool_clipboard_get(arguments: dict[str, Any]) -> core.Result:
    from .tools_input import clipboard_seat_args, resolve_seat

    core.require_binaries(["wl-paste"])
    max_bytes = arguments.get("max_bytes", core.CLIPBOARD_LIMIT)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= core.CLIPBOARD_LIMIT:
        raise core.ToolError(f"max_bytes must be between 1 and {core.CLIPBOARD_LIMIT}")
    seat_args = clipboard_seat_args(arguments)
    result = core.run_command(["wl-paste", *seat_args, "--no-newline"], timeout=5.0, binary=True).stdout
    if b"\x00" in result:
        raise core.ToolError("clipboard appears to contain binary data")
    if len(result) > max_bytes:
        raise core.ToolError(f"clipboard content exceeds max_bytes ({max_bytes})")
    return core.text_result(
        {"text": result.decode("utf-8", errors="strict"), "bytes": len(result), "seat": resolve_seat(arguments)},
        {"bytes": len(result)},
    )


def _bounded_int(arguments: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise core.ToolError(f"{key} must be an integer between {low} and {high}")
    return value


def tool_observe(arguments: dict[str, Any]) -> core.Result:
    from . import scene

    scope = arguments.get("scope")
    if scope is not None and scope not in scene.SCOPES:
        raise core.ToolError("scope must be agent, focused, visible, or window")
    window = arguments.get("window")
    if window is not None and not isinstance(window, dict):
        raise core.ToolError("window must be a selector object")
    ocr_mode = arguments.get("ocr")
    if ocr_mode is not None and ocr_mode not in ("auto", "always", "never"):
        raise core.ToolError("ocr must be auto, always, or never")
    image = arguments.get("image", False)
    if not isinstance(image, bool):
        raise core.ToolError("image must be a boolean")
    return scene.observe(
        session.current(), scope=scope, window=window, query_arg=arguments.get("query"),
        max_elements=_bounded_int(arguments, "max_elements", 150, 1, 500), ocr_mode=ocr_mode, image=image,
    )


def tool_read(arguments: dict[str, Any]) -> core.Result:
    from . import scene

    ref = arguments.get("ref")
    if not isinstance(ref, str):
        raise core.ToolError("ref must be a string like s3:12")
    start = _bounded_int(arguments, "start", 0, 0, 10_000_000)
    length = _bounded_int(arguments, "length", 4000, 1, 100_000)
    sess = session.current()
    element = scene.validate(sess, ref)
    out: dict[str, Any] = {"ref": ref, "role": element.role, "name": element.name, "states": element.states,
                           "bounds": list(element.bounds), "source": element.source}
    client = sess.a11y_or_none()
    if element.source == "a11y" and client is not None and element.a11y is not None:
        if "org.a11y.atspi.Text" in element.ifaces:
            out["text"] = client.text(element.a11y, start, start + length)
        if "org.a11y.atspi.Value" in element.ifaces:
            out["value"] = client.value(element.a11y)
        out["actions"] = element.actions
    else:
        out["text"] = element.name[start:start + length]
    return core.text_result(out, {"ref": ref, "source": element.source})


TOOLS = {
    "observe": tool_observe,
    "read": tool_read,
    "screen_info": tool_screen_info,
    "screenshot": tool_screenshot,
    "window_tree": tool_window_tree,
    "clipboard_get": tool_clipboard_get,
}

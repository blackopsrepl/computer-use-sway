"""Publish this server's actions to the take timeline.

Recording and narration live in a separate server (`seshat`). It cannot
timestamp actions it does not perform, so this server publishes them: one JSON
object per line, in the contract seshat's README documents.

    {"at_monotonic": 12345.678, "tool": "click", "ok": true,
     "payload": {"x": 640, "y": 360}, "source": "computer-use-sway"}

``at_monotonic`` is CLOCK_MONOTONIC seconds, which is host-wide, so these
timestamps are directly comparable with the recorder's own epoch: no handshake,
no session id, no shared process.

The emitter decides what is safe to publish. Only curated keys leave this
server: typed text becomes a character count, clipboard content becomes a byte
count, and neither ever reaches the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


STREAM_SUFFIX = ".jsonl"
STREAM_ROOT_DIRECTORY = "seshat"
STREAM_DIRECTORY_NAME = "streams"
STREAM_SOURCE = "computer-use-sway"
STREAM_DIR_MODE = 0o700
STREAM_FILE_MODE = 0o600

TIMELINE_ACTION_TOOLS = (
    "screen_info",
    "screenshot",
    "window_tree",
    "focus_window",
    "move_pointer",
    "click",
    "drag",
    "scroll",
    "type_text",
    "key",
    "clipboard_set",
    "clipboard_get",
)
TIMELINE_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "screen_info": (),
    "screenshot": ("output", "include_cursor", "region"),
    "window_tree": ("include_scratchpad", "max_depth"),
    "focus_window": ("con_id", "app_id", "class", "title", "match"),
    "move_pointer": ("x", "y", "mode"),
    "click": ("x", "y", "button", "count", "interval_ms"),
    "drag": ("from", "to", "button", "steps", "duration_ms"),
    "scroll": ("x", "y", "direction", "clicks"),
    "type_text": ("delay_ms",),
    "key": ("key", "modifiers"),
    "clipboard_set": (),
    "clipboard_get": ("max_bytes",),
}


def timeline_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Curated, non-sensitive summary of one tool call for the take timeline."""
    payload: dict[str, Any] = {}
    for key in TIMELINE_PAYLOAD_KEYS.get(name, ()):
        value = arguments.get(key)
        if value is not None:
            payload[key] = value
    if name == "type_text":
        text = arguments.get("text")
        payload["characters"] = len(text) if isinstance(text, str) else 0
    elif name in {"clipboard_set", "clipboard_get"}:
        text = arguments.get("text")
        if isinstance(text, str):
            payload["bytes"] = len(text.encode("utf-8"))
    return payload


def stream_path() -> Path | None:
    """Where this server publishes its stream, or None without a runtime dir."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return None
    return (
        Path(runtime_dir)
        / STREAM_ROOT_DIRECTORY
        / STREAM_DIRECTORY_NAME
        / f"{STREAM_SOURCE}{STREAM_SUFFIX}"
    )


def reset_stream() -> None:
    """Start a fresh stream for this session.

    The stream is append-only while the server runs, so it is truncated once at
    startup instead of growing across sessions. Failures are ignored: publishing
    is a side channel and must never affect startup.
    """
    path = stream_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=STREAM_DIR_MODE)
        os.chmod(path.parent, STREAM_DIR_MODE)
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, STREAM_FILE_MODE)
        os.close(fd)
    except OSError:
        pass


def append_event(name: str, arguments: dict[str, Any], at_monotonic: float, ok: bool) -> None:
    """Publish one dispatched action to the timeline stream.

    Silent by design. A take that cannot see these actions has no anchors, which
    is a narration problem; it is never a reason for a click to fail, so every
    failure here is swallowed.
    """
    if name not in TIMELINE_ACTION_TOOLS:
        return
    path = stream_path()
    if path is None:
        return
    record = {
        "at_monotonic": round(float(at_monotonic), 6),
        "tool": name,
        "ok": bool(ok),
        "payload": timeline_payload(name, arguments),
        "source": STREAM_SOURCE,
    }
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=STREAM_DIR_MODE)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, STREAM_FILE_MODE)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass

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

TIMELINE_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "session": ("action", "mode"),
    "screen_info": (),
    "observe": ("scope", "max_elements", "ocr", "image"),
    "read": ("ref", "start", "length"),
    "act": ("action", "delivery", "verify", "direction", "clicks", "key", "modifiers"),
    "run": ("stop_on",),
    "wait_for": ("timeout_ms",),
    "launch": ("a11y",),
    "screenshot": ("output", "include_cursor", "region", "target_output"),
    "window_tree": ("include_scratchpad", "max_depth"),
    "focus_window": ("con_id", "app_id", "class", "title", "match", "seat"),
    "move_pointer": ("x", "y", "mode", "seat"),
    "click": ("x", "y", "button", "count", "interval_ms", "seat"),
    "drag": ("from", "to", "button", "steps", "duration_ms", "seat"),
    "scroll": ("x", "y", "direction", "clicks", "seat"),
    "type_text": ("delay_ms", "seat"),
    "key": ("key", "modifiers", "seat"),
    "clipboard_set": ("seat",),
    "clipboard_get": ("max_bytes", "seat"),
}
TIMELINE_ACTION_TOOLS = tuple(TIMELINE_PAYLOAD_KEYS)
# Never published, at any depth, whatever a tool puts in its meta.
SECRET_KEYS = frozenset({"text", "value", "content", "clipboard", "password", "env", "argv"})


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in SECRET_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _curate_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    if isinstance(target.get("ref"), str):
        return {"ref": target["ref"]}
    if "x" in target and "y" in target:
        return {"point": {"x": target.get("x"), "y": target.get("y")}}
    query = target.get("query")
    if isinstance(query, dict):
        name = query.get("name")
        return {"query_role": query.get("role"), "name_chars": len(name) if isinstance(name, str) else 0}
    return {}


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
    elif name == "observe":
        query = arguments.get("query")
        if isinstance(query, dict):
            qname, qtext = query.get("name"), query.get("text")
            payload["query"] = {
                "role": query.get("role"),
                "match": query.get("match"),
                "name_chars": len(qname) if isinstance(qname, str) else 0,
                "text_chars": len(qtext) if isinstance(qtext, str) else 0,
            }
    elif name == "act":
        payload["target"] = _curate_target(arguments.get("target"))
        if arguments.get("to") is not None:
            payload["to"] = _curate_target(arguments.get("to"))
        text = arguments.get("text")
        if isinstance(text, str):
            payload["characters"] = len(text)
    elif name == "launch":
        argv = arguments.get("argv")
        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            payload["program"] = os.path.basename(argv[0])
    elif name == "run":
        steps = arguments.get("steps")
        if isinstance(steps, list):
            payload["steps"] = len(steps)
            payload["tools"] = [s.get("tool") for s in steps if isinstance(s, dict)]
    return payload


_pinned_runtime: str | None = None


def pin_runtime(runtime_dir: str | None) -> None:
    """Keep publishing to the live session's stream while sandbox mode
    re-points XDG_RUNTIME_DIR; None unpins."""
    global _pinned_runtime
    _pinned_runtime = runtime_dir


def stream_path() -> Path | None:
    """Where this server publishes its stream, or None without a runtime dir."""
    # Pinned at startup: sandbox mode re-points XDG_RUNTIME_DIR for its
    # children, but the recorder keeps reading the live session's stream.
    runtime_dir = _pinned_runtime or os.environ.get("XDG_RUNTIME_DIR")
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


def append_event(
    name: str,
    arguments: dict[str, Any],
    at_monotonic: float,
    ok: bool,
    meta: dict[str, Any] | None = None,
) -> None:
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
        "payload": _scrub({**timeline_payload(name, arguments), **(meta or {})}),
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

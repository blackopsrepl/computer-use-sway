from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import recording


TIMELINE_SUFFIX = ".timeline.json"
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


def recording_relative_ms(epoch_monotonic: float, at_monotonic: float) -> float:
    """Milliseconds from the recording epoch to ``at_monotonic``, never negative."""
    return round(max(at_monotonic - epoch_monotonic, 0.0) * 1000.0, 3)


def timeline_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Curated, non-sensitive summary of one tool call for the recording timeline."""
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


def timeline_document(job: RecordingJob) -> dict[str, Any]:
    end = job.ended_monotonic if job.ended_monotonic is not None else time.monotonic()
    return {
        "id": job.id,
        "format": job.fmt,
        "output": job.output,
        "region": job.region,
        "started_utc": job.started_utc,
        "capture_seconds": round(max(end - job.started_monotonic, 0.0), 3),
        "event_count": len(job.events),
        "events": [dict(event) for event in job.events],
    }


def write_timeline_sidecar(job: RecordingJob) -> Path | None:
    from . import recording as _recording

    if job.timeline_path is None:
        return None
    data = json.dumps(timeline_document(job), indent=2, sort_keys=True).encode("utf-8")
    fd = os.open(
        job.timeline_path,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        _recording.RECORDING_FILE_MODE,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return job.timeline_path

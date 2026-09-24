from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import core


def probe_media(path: Path) -> dict[str, Any]:
    result = core.run_command(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=30.0,
    )
    try:
        return json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise core.ToolError(f"ffprobe returned invalid JSON for {path.name}") from exc


def probe_video_stream(path: Path) -> dict[str, Any]:
    probe = probe_media(path)
    video = [stream for stream in probe.get("streams") or [] if stream.get("codec_type") == "video"]
    if not video:
        raise core.ToolError(f"{path.name} contains no video stream")
    return video[0]


def parse_frame_rate(rate: Any) -> float:
    try:
        numerator, denominator = str(rate).split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return 0.0
        return float(numerator) / denominator_value
    except (TypeError, ValueError):
        return 0.0

from __future__ import annotations

import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import core, desktop, media, timeline


RECORDING_FORMATS = ("webm", "gif")
RECORDING_MIME_TYPES = {"webm": "video/webm", "gif": "image/gif"}
RECORDING_MAX_DURATION_SECONDS = {"webm": 300.0, "gif": 15.0}
RECORDING_DEFAULT_DURATION_SECONDS = {"webm": 60.0, "gif": 15.0}
RECORDING_CAPTURE_CODEC = "libx264rgb"
RECORDING_CAPTURE_FPS = 30
RECORDING_VIDEO_FPS = 30
RECORDING_GIF_FPS = 12
RECORDING_GIF_MAX_WIDTH = 960
RECORDING_MAX_INTERMEDIATE_BYTES = 1024**3
RECORDING_DIR_MODE = 0o700
RECORDING_FILE_MODE = 0o600
RECORDING_STARTUP_PROBE_SECONDS = 1.0
RECORDING_STOP_TIMEOUT_SECONDS = 10.0
RECORDING_TERM_TIMEOUT_SECONDS = 5.0
RECORDING_KILL_TIMEOUT_SECONDS = 5.0


@dataclass
class RecordingJob:
    id: str
    fmt: str
    output: str
    region: dict[str, int] | None
    width: int
    height: int
    max_duration: float
    started_monotonic: float
    started_utc: str
    directory: Path
    intermediate: Path
    artifact: Path
    log_path: Path
    log_fd: int
    encoder: str = "libsvtav1"
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    phase: str = "recording"
    detail: str | None = None
    auto_stopped: bool = False
    forced: bool = False
    ended_monotonic: float | None = None
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    timeline_path: Path | None = None
    narration: dict[str, Any] | None = None


def parse_recording_format(value: Any) -> str:
    fmt = "webm" if value is None else value
    if not isinstance(fmt, str) or fmt not in RECORDING_FORMATS:
        raise core.ToolError("format must be one of: " + ", ".join(RECORDING_FORMATS))
    return fmt


def parse_max_duration(value: Any, fmt: str) -> float:
    if value is None:
        return RECORDING_DEFAULT_DURATION_SECONDS[fmt]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise core.ToolError("max_duration_seconds must be a number")
    duration = float(value)
    if duration <= 0:
        raise core.ToolError("max_duration_seconds must be positive")
    if duration > RECORDING_MAX_DURATION_SECONDS[fmt]:
        raise core.ToolError(
            f"max_duration_seconds must not exceed {RECORDING_MAX_DURATION_SECONDS[fmt]:g} for {fmt}"
        )
    return duration


def select_recording_output(requested: Any) -> dict[str, Any]:
    outputs = desktop.get_outputs()
    names = [str(output.get("name")) for output in outputs]
    if requested is not None:
        if not isinstance(requested, str) or requested not in names:
            raise core.ToolError(
                f"unknown output {requested!r}; active outputs: {', '.join(names) or 'none'}"
            )
        return next(output for output in outputs if str(output.get("name")) == requested)
    if len(outputs) == 1:
        return outputs[0]
    raise core.ToolError(
        "multiple active outputs; provide output as one of: " + ", ".join(names)
    )


def validate_contained_region(region: Any, output_rect: dict[str, Any]) -> dict[str, int] | None:
    if region is None:
        return None
    if not isinstance(region, dict):
        raise core.ToolError("region must be an object with integer x, y, width, and height")
    x = core.strict_int(region.get("x"), "region x")
    y = core.strict_int(region.get("y"), "region y")
    width = core.strict_int(region.get("width"), "region width")
    height = core.strict_int(region.get("height"), "region height")
    if width <= 0 or height <= 0:
        raise core.ToolError("region width and height must be positive")
    try:
        rect_x = int(output_rect["x"])
        rect_y = int(output_rect["y"])
        rect_width = int(output_rect["width"])
        rect_height = int(output_rect["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.ToolError("selected output has invalid geometry") from exc
    if (
        x < rect_x
        or y < rect_y
        or x + width > rect_x + rect_width
        or y + height > rect_y + rect_height
    ):
        raise core.ToolError(
            f"region must be fully contained in output rect {rect_x},{rect_y} {rect_width}x{rect_height}"
        )
    return {"x": x, "y": y, "width": width, "height": height}


def recording_directory() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise core.ToolError("XDG_RUNTIME_DIR is not set; cannot create recording directory")
    directory = Path(runtime_dir) / "computer-use-sway" / "recordings"
    directory.mkdir(parents=True, exist_ok=True, mode=RECORDING_DIR_MODE)
    os.chmod(directory, RECORDING_DIR_MODE)
    return directory


def create_private_file(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, RECORDING_FILE_MODE)
    os.close(fd)


def allocate_recording_paths(fmt: str) -> tuple[str, Path, Path, Path, int]:
    directory = recording_directory()
    for _ in range(5):
        recording_id = secrets.token_hex(8)
        intermediate = directory / f"{recording_id}.mkv"
        artifact = directory / f"{recording_id}.{fmt}"
        log_path = directory / f"{recording_id}.log"
        try:
            log_fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, RECORDING_FILE_MODE)
            try:
                create_private_file(intermediate)
                create_private_file(artifact)
            except FileExistsError:
                os.close(log_fd)
                log_path.unlink(missing_ok=True)
                raise
            return recording_id, intermediate, artifact, log_path, log_fd
        except FileExistsError:
            continue
    raise core.ToolError("could not allocate unique recording paths")


def new_recording_job(arguments: dict[str, Any]) -> RecordingJob:
    fmt = parse_recording_format(arguments.get("format"))
    output = select_recording_output(arguments.get("output"))
    region = validate_contained_region(arguments.get("region"), output.get("rect") or {})
    max_duration = parse_max_duration(arguments.get("max_duration_seconds"), fmt)
    output_rect = output.get("rect") or {}
    if region is not None:
        width = region["width"]
        height = region["height"]
    else:
        try:
            width = int(output_rect["width"])
            height = int(output_rect["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise core.ToolError(f"output {output.get('name')} has invalid geometry") from exc
    recording_id, intermediate, artifact, log_path, log_fd = allocate_recording_paths(fmt)
    return RecordingJob(
        id=recording_id,
        fmt=fmt,
        output=str(output.get("name")),
        region=region,
        width=width,
        height=height,
        max_duration=max_duration,
        started_monotonic=time.monotonic(),
        started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        directory=intermediate.parent,
        intermediate=intermediate,
        artifact=artifact,
        log_path=log_path,
        log_fd=log_fd,
        timeline_path=intermediate.with_suffix(timeline.TIMELINE_SUFFIX),
    )


RECORDING_ENCODER_PREFERENCE = ("libsvtav1", "libaom-av1")
RECORDING_LOG_TAIL_BYTES = 500


def recording_capture_argv(job: RecordingJob) -> list[str]:
    argv = ["wf-recorder", "-o", job.output]
    if job.region is not None:
        argv.extend(
            [
                "-g",
                f"{job.region['x']},{job.region['y']} {job.region['width']}x{job.region['height']}",
            ]
        )
    argv.extend(
        [
            "-f",
            str(job.intermediate),
            "-c",
            RECORDING_CAPTURE_CODEC,
            "-r",
            str(RECORDING_CAPTURE_FPS),
            "-p",
            "preset=ultrafast",
            "-p",
            "crf=0",
            "-y",
        ]
    )
    return argv


def choose_video_encoder() -> str:
    result = core.run_command(["ffmpeg", "-hide_banner", "-encoders"], timeout=10.0)
    for name in RECORDING_ENCODER_PREFERENCE:
        if re.search(rf"^\s*V\S*\s+{re.escape(name)}\s", result.text, re.MULTILINE):
            return name
    raise core.ToolError(
        "no AV1 encoder available in ffmpeg (need one of: "
        + ", ".join(RECORDING_ENCODER_PREFERENCE)
        + ")"
    )


def recording_webm_argv(job: RecordingJob) -> list[str]:
    filters = [
        f"fps={RECORDING_VIDEO_FPS}",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "format=yuv420p",
    ]
    argv = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(job.intermediate),
        "-an",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-vf",
        ",".join(filters),
        "-c:v",
        job.encoder,
    ]
    if job.encoder == "libsvtav1":
        argv.extend(["-crf", "28", "-preset", "8"])
    else:
        argv.extend(["-crf", "30", "-cpu-used", "6", "-row-mt", "1", "-tiles", "2x2"])
    argv.extend(
        [
            "-force_key_frames",
            "expr:gte(t,n_forced*2)",
            "-f",
            "webm",
            str(job.artifact),
        ]
    )
    return argv


def recording_gif_argv(job: RecordingJob, capture_width: int) -> list[str]:
    filters = [f"fps={RECORDING_GIF_FPS}"]
    if capture_width > RECORDING_GIF_MAX_WIDTH:
        filters.append(f"scale={RECORDING_GIF_MAX_WIDTH}:-1:flags=lanczos")
    filters.append(
        "split[a][b];[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(job.intermediate),
        "-vf",
        ",".join(filters),
        "-loop",
        "0",
        "-an",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-f",
        "gif",
        str(job.artifact),
    ]


def validate_recording_artifact(
    job: RecordingJob, expect_audio: bool = False, path: Path | None = None
) -> dict[str, Any]:
    target = path or job.artifact
    probe = media.probe_media(target)
    format_name = str((probe.get("format") or {}).get("format_name") or "")
    streams = probe.get("streams") or []
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if job.fmt == "webm":
        if "webm" not in format_name:
            raise core.ToolError(f"artifact is not WebM (format: {format_name or 'unknown'})")
        if len(video) != 1 or video[0].get("codec_name") != "av1":
            raise core.ToolError("artifact must contain exactly one AV1 video stream")
        if expect_audio:
            if len(audio) != 1 or audio[0].get("codec_name") != "opus":
                raise core.ToolError("narrated artifact must contain exactly one Opus audio stream")
        elif audio:
            raise core.ToolError("artifact must not contain audio streams")
    else:
        if audio:
            raise core.ToolError("artifact must not contain audio streams")
        if "gif" not in format_name:
            raise core.ToolError(f"artifact is not a GIF (format: {format_name or 'unknown'})")
        if len(video) != 1:
            raise core.ToolError("artifact must contain exactly one image stream")
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0.0:
        raise core.ToolError("artifact has no readable duration")
    return {
        "codec": str(video[0].get("codec_name")),
        "container": job.fmt,
        "mime_type": RECORDING_MIME_TYPES[job.fmt],
        "width": int(video[0].get("width") or 0),
        "height": int(video[0].get("height") or 0),
        "duration_seconds": round(duration, 3),
        "frame_rate": round(media.parse_frame_rate(video[0].get("avg_frame_rate")), 3),
    }


def finalize_recording(job: RecordingJob) -> dict[str, Any]:
    capture = media.probe_video_stream(job.intermediate)
    try:
        capture_width = int(capture.get("width") or 0)
    except (TypeError, ValueError):
        capture_width = 0
    if job.fmt == "webm":
        argv = recording_webm_argv(job)
    else:
        argv = recording_gif_argv(job, capture_width)
    core.run_command(argv, timeout=600.0)
    return validate_recording_artifact(job)

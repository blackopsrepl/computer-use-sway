#!/usr/bin/env python3
"""MCP server for computer-use control of the current Sway session."""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any


SERVER_NAME = "computer-use-sway"
SERVER_VERSION = "0.1.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
COMMAND_NAME = "computer-use-sway"
COMMAND_ENV = "COMPUTER_USE_SWAY_COMMAND"

DEFAULT_TIMEOUT = 5.0
TEXT_LIMIT = 10_000
CLIPBOARD_LIMIT = 100_000
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
BUTTONS = {
    "left": "button1",
    "middle": "button2",
    "right": "button3",
}
SCROLL_BUTTONS = {
    "up": "button4",
    "down": "button5",
    "left": "button6",
    "right": "button7",
}
MODIFIERS = {
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "logo": "logo",
}


class ToolError(Exception):
    """Expected tool failure reported as MCP tool content."""


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_command(
    args: list[str],
    timeout: float = DEFAULT_TIMEOUT,
    input_text: str | None = None,
    input_bytes: bytes | None = None,
    binary: bool = False,
    capture: bool = True,
) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            input=input_bytes if input_bytes is not None else input_text,
            text=(input_bytes is None and not binary),
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {timeout:.1f}s: {args[0]}") from exc

    stdout = completed.stdout if completed.stdout is not None else b""
    stderr = completed.stderr if completed.stderr is not None else b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")

    if completed.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-500:].strip()
        detail = f": {tail}" if tail else ""
        raise ToolError(f"command failed ({completed.returncode}): {args[0]}{detail}")

    return CommandResult(stdout=stdout, stderr=stderr, returncode=completed.returncode)


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def require_binaries(names: list[str]) -> None:
    missing = [name for name in names if not command_available(name)]
    if missing:
        raise ToolError(f"missing required command(s): {', '.join(missing)}")


def command_argv() -> list[str]:
    configured = os.environ.get(COMMAND_ENV)
    if configured:
        parts = shlex.split(configured)
        if not parts:
            raise ToolError(f"{COMMAND_ENV} is set but empty")
        return parts

    invoked = Path(sys.argv[0])
    if invoked.name == COMMAND_NAME:
        if invoked.exists():
            return [str(invoked.resolve())]
        found = shutil.which(sys.argv[0])
        if found:
            return [found]

    installed = shutil.which(COMMAND_NAME)
    if installed:
        return [installed]

    return [sys.executable, "-m", "computer_use_sway"]


def newest_socket(pattern: str) -> str | None:
    candidates = [Path(path) for path in glob.glob(pattern) if Path(path).is_socket()]
    if not candidates:
        return None
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def ensure_session_environment() -> None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        candidate = f"/run/user/{os.getuid()}"
        if Path(candidate).is_dir():
            runtime_dir = candidate
            os.environ["XDG_RUNTIME_DIR"] = candidate

    if runtime_dir and not os.environ.get("SWAYSOCK"):
        socket = newest_socket(f"{runtime_dir}/sway-ipc.{os.getuid()}.*.sock")
        if socket:
            os.environ["SWAYSOCK"] = socket

    if runtime_dir and not os.environ.get("WAYLAND_DISPLAY"):
        displays = sorted(Path(runtime_dir).glob("wayland-*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for display in displays:
            if display.is_socket():
                os.environ["WAYLAND_DISPLAY"] = display.name
                break


def read_json_command(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> Any:
    return json.loads(run_command(args, timeout=timeout).text)


def require_session() -> None:
    ensure_session_environment()
    missing = [
        name
        for name in ("SWAYSOCK", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
        if not os.environ.get(name)
    ]
    if missing:
        raise ToolError(
            "not running inside a usable Sway session; missing "
            + ", ".join(missing)
        )
    require_binaries(["swaymsg"])
    read_json_command(["swaymsg", "-t", "get_outputs"])


def get_outputs() -> list[dict[str, Any]]:
    require_session()
    outputs = read_json_command(["swaymsg", "-t", "get_outputs"])
    return [output for output in outputs if output.get("active")]


def get_seats() -> list[dict[str, Any]]:
    require_session()
    return read_json_command(["swaymsg", "-t", "get_seats"])


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
    raise ToolError("no Sway seat with pointer capability found")


def get_tree() -> dict[str, Any]:
    require_session()
    return read_json_command(["swaymsg", "-t", "get_tree"], timeout=8.0)


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
            raise ToolError(f"invalid output geometry for {output.get('name')}") from exc
    if not rects:
        raise ToolError("no active Sway outputs found")
    return rects


def validate_coordinates(x: Any, y: Any) -> tuple[int, int]:
    try:
        ix = int(x)
        iy = int(y)
    except (TypeError, ValueError) as exc:
        raise ToolError("coordinates must be integers") from exc

    for rect in active_output_rects():
        if (
            rect["x"] <= ix < rect["x"] + rect["width"]
            and rect["y"] <= iy < rect["y"] + rect["height"]
        ):
            return ix, iy

    raise ToolError(f"coordinates outside active outputs: x={ix}, y={iy}")


def validate_region(region: Any) -> dict[str, int] | None:
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ToolError("region must be an object")
    x, y = validate_coordinates(region.get("x"), region.get("y"))
    try:
        width = int(region.get("width"))
        height = int(region.get("height"))
    except (TypeError, ValueError) as exc:
        raise ToolError("region width and height must be integers") from exc
    if width <= 0 or height <= 0:
        raise ToolError("region width and height must be positive")
    validate_coordinates(x + width - 1, y + height - 1)
    return {"x": x, "y": y, "width": width, "height": height}


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


def strict_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be an integer")
    return value


def parse_recording_format(value: Any) -> str:
    fmt = "webm" if value is None else value
    if not isinstance(fmt, str) or fmt not in RECORDING_FORMATS:
        raise ToolError("format must be one of: " + ", ".join(RECORDING_FORMATS))
    return fmt


def parse_max_duration(value: Any, fmt: str) -> float:
    if value is None:
        return RECORDING_DEFAULT_DURATION_SECONDS[fmt]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("max_duration_seconds must be a number")
    duration = float(value)
    if duration <= 0:
        raise ToolError("max_duration_seconds must be positive")
    if duration > RECORDING_MAX_DURATION_SECONDS[fmt]:
        raise ToolError(
            f"max_duration_seconds must not exceed {RECORDING_MAX_DURATION_SECONDS[fmt]:g} for {fmt}"
        )
    return duration


def select_recording_output(requested: Any) -> dict[str, Any]:
    outputs = get_outputs()
    names = [str(output.get("name")) for output in outputs]
    if requested is not None:
        if not isinstance(requested, str) or requested not in names:
            raise ToolError(
                f"unknown output {requested!r}; active outputs: {', '.join(names) or 'none'}"
            )
        return next(output for output in outputs if str(output.get("name")) == requested)
    if len(outputs) == 1:
        return outputs[0]
    raise ToolError(
        "multiple active outputs; provide output as one of: " + ", ".join(names)
    )


def validate_contained_region(region: Any, output_rect: dict[str, Any]) -> dict[str, int] | None:
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ToolError("region must be an object with integer x, y, width, and height")
    x = strict_int(region.get("x"), "region x")
    y = strict_int(region.get("y"), "region y")
    width = strict_int(region.get("width"), "region width")
    height = strict_int(region.get("height"), "region height")
    if width <= 0 or height <= 0:
        raise ToolError("region width and height must be positive")
    try:
        rect_x = int(output_rect["x"])
        rect_y = int(output_rect["y"])
        rect_width = int(output_rect["width"])
        rect_height = int(output_rect["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("selected output has invalid geometry") from exc
    if (
        x < rect_x
        or y < rect_y
        or x + width > rect_x + rect_width
        or y + height > rect_y + rect_height
    ):
        raise ToolError(
            f"region must be fully contained in output rect {rect_x},{rect_y} {rect_width}x{rect_height}"
        )
    return {"x": x, "y": y, "width": width, "height": height}


def recording_directory() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise ToolError("XDG_RUNTIME_DIR is not set; cannot create recording directory")
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
    raise ToolError("could not allocate unique recording paths")


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
            raise ToolError(f"output {output.get('name')} has invalid geometry") from exc
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
    )


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
            raise ToolError(f"invalid regex: {exc}") from exc
    raise ToolError("match must be exact, contains, or regex")


def find_window(arguments: dict[str, Any]) -> dict[str, Any]:
    keys = [key for key in ("con_id", "app_id", "class", "title") if arguments.get(key) is not None]
    if len(keys) != 1:
        raise ToolError("provide exactly one of con_id, app_id, class, or title")

    mode = arguments.get("match", "contains")
    if mode not in {"exact", "contains", "regex"}:
        raise ToolError("match must be exact, contains, or regex")

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

    raise ToolError(f"no matching window found for {key}={pattern!r}")


def sway_cursor(*parts: str) -> None:
    seat = detect_seat()
    run_command(["swaymsg", "-t", "command", "seat", seat, "cursor", *parts])


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
    result = run_command(["ffmpeg", "-hide_banner", "-encoders"], timeout=10.0)
    for name in RECORDING_ENCODER_PREFERENCE:
        if re.search(rf"^\s*V\S*\s+{re.escape(name)}\s", result.text, re.MULTILINE):
            return name
    raise ToolError(
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


def probe_media(path: Path) -> dict[str, Any]:
    result = run_command(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=30.0,
    )
    try:
        return json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise ToolError(f"ffprobe returned invalid JSON for {path.name}") from exc


def probe_video_stream(path: Path) -> dict[str, Any]:
    probe = probe_media(path)
    video = [stream for stream in probe.get("streams") or [] if stream.get("codec_type") == "video"]
    if not video:
        raise ToolError(f"{path.name} contains no video stream")
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


def validate_recording_artifact(job: RecordingJob) -> dict[str, Any]:
    probe = probe_media(job.artifact)
    format_name = str((probe.get("format") or {}).get("format_name") or "")
    streams = probe.get("streams") or []
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if audio:
        raise ToolError("artifact must not contain audio streams")
    if job.fmt == "webm":
        if "webm" not in format_name:
            raise ToolError(f"artifact is not WebM (format: {format_name or 'unknown'})")
        if len(video) != 1 or video[0].get("codec_name") != "av1":
            raise ToolError("artifact must contain exactly one AV1 video stream")
    else:
        if "gif" not in format_name:
            raise ToolError(f"artifact is not a GIF (format: {format_name or 'unknown'})")
        if len(video) != 1:
            raise ToolError("artifact must contain exactly one image stream")
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0.0:
        raise ToolError("artifact has no readable duration")
    return {
        "codec": str(video[0].get("codec_name")),
        "container": job.fmt,
        "mime_type": RECORDING_MIME_TYPES[job.fmt],
        "width": int(video[0].get("width") or 0),
        "height": int(video[0].get("height") or 0),
        "duration_seconds": round(duration, 3),
        "frame_rate": round(parse_frame_rate(video[0].get("avg_frame_rate")), 3),
    }


def finalize_recording(job: RecordingJob) -> dict[str, Any]:
    capture = probe_video_stream(job.intermediate)
    try:
        capture_width = int(capture.get("width") or 0)
    except (TypeError, ValueError):
        capture_width = 0
    if job.fmt == "webm":
        argv = recording_webm_argv(job)
    else:
        argv = recording_gif_argv(job, capture_width)
    run_command(argv, timeout=600.0)
    return validate_recording_artifact(job)


class RecordingManager:
    """Owns the single capture/finalization lifecycle for this server process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: RecordingJob | None = None

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _intermediate_size(job: RecordingJob) -> int:
        try:
            return job.intermediate.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _close_log_fd(job: RecordingJob) -> None:
        if job.log_fd >= 0:
            try:
                os.close(job.log_fd)
            except OSError:
                pass
            job.log_fd = -1

    @staticmethod
    def _log_tail(job: RecordingJob) -> str:
        try:
            data = job.log_path.read_bytes()
        except OSError:
            return ""
        return data[-RECORDING_LOG_TAIL_BYTES:].decode("utf-8", errors="replace").strip()

    @staticmethod
    def _signal_group(process: subprocess.Popen, sig: int) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _wait_exited(process: subprocess.Popen, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.05)
        return process.poll() is not None

    def _terminate_process_group(self, process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        escalations = (
            (signal.SIGINT, RECORDING_STOP_TIMEOUT_SECONDS, True),
            (signal.SIGTERM, RECORDING_TERM_TIMEOUT_SECONDS, False),
            (signal.SIGKILL, RECORDING_KILL_TIMEOUT_SECONDS, False),
        )
        for sig, timeout, was_graceful in escalations:
            if process.poll() is not None:
                return True
            self._signal_group(process, sig)
            if self._wait_exited(process, timeout):
                return was_graceful
        if process.poll() is None:
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pass
        return False

    @staticmethod
    def _discard_intermediate(job: RecordingJob) -> None:
        job.intermediate.unlink(missing_ok=True)

    def _fail(self, job: RecordingJob, message: str) -> None:
        job.phase = "failed"
        job.detail = f"{job.detail}; {message}" if job.detail else message
        job.artifact.unlink(missing_ok=True)

    def _cleanup_failed_start(self, job: RecordingJob) -> None:
        self._close_log_fd(job)
        for path in (job.intermediate, job.artifact, job.log_path):
            path.unlink(missing_ok=True)

    # -- lifecycle -------------------------------------------------------

    def start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        require_binaries(["wf-recorder", "ffmpeg", "ffprobe"])
        with self._lock:
            job = self._job
            if job is not None and job.phase in {"recording", "stopping", "processing"}:
                raise ToolError(
                    f"a recording is already active (phase: {job.phase}); call recording_status"
                )
            new_job = new_recording_job(arguments)
            if new_job.fmt == "webm":
                new_job.encoder = choose_video_encoder()
            try:
                new_job.process = subprocess.Popen(
                    recording_capture_argv(new_job),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=new_job.log_fd,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                self._cleanup_failed_start(new_job)
                raise ToolError(f"failed to launch wf-recorder: {exc}") from exc
            time.sleep(RECORDING_STARTUP_PROBE_SECONDS)
            if new_job.process.poll() is not None:
                detail = self._log_tail(new_job)
                self._cleanup_failed_start(new_job)
                raise ToolError(
                    f"wf-recorder exited during startup (code {new_job.process.returncode})"
                    + (f": {detail}" if detail else "")
                )
            self._job = new_job
            watchdog = threading.Thread(target=self._watchdog, args=(new_job,), daemon=True)
            new_job.thread = watchdog
            watchdog.start()
            return self._summary(new_job)

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None:
                return {"phase": "idle"}
            if job.phase == "recording":
                process = job.process
                if process is not None and process.poll() is not None:
                    job.ended_monotonic = time.monotonic()
                    self._close_log_fd(job)
                    if process.returncode != 0:
                        tail = self._log_tail(job)
                        self._fail(
                            job,
                            f"wf-recorder exited unexpectedly (code {process.returncode})"
                            + (f": {tail}" if tail else ""),
                        )
                    elif self._intermediate_size(job) == 0:
                        self._fail(job, "capture produced no data")
                    else:
                        job.phase = "processing"
                        job.thread = threading.Thread(
                            target=self._finalize, args=(job,), daemon=True
                        )
                        job.thread.start()
            return self._summary(job)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or job.phase != "recording":
                phase = job.phase if job is not None else "idle"
                raise ToolError(f"no active recording to stop (phase: {phase})")
            self._end_capture(job)
            return self._summary(job)

    def shutdown(self) -> None:
        with self._lock:
            job = self._job
            if job is None or job.phase != "recording":
                return
            if job.detail is None:
                job.detail = "capture ended at server shutdown"
            self._end_capture(job)

    # -- internals -------------------------------------------------------

    def _end_capture(self, job: RecordingJob) -> None:
        """Stop the recorder and begin finalization. Caller must hold the lock."""
        job.phase = "stopping"
        process = job.process
        if process is not None and process.poll() is None:
            job.forced = not self._terminate_process_group(process)
        job.ended_monotonic = time.monotonic()
        self._close_log_fd(job)
        if self._intermediate_size(job) == 0:
            self._fail(job, "capture produced no data")
            return
        job.phase = "processing"
        job.thread = threading.Thread(target=self._finalize, args=(job,), daemon=True)
        job.thread.start()

    def _watchdog(self, job: RecordingJob) -> None:
        deadline = job.started_monotonic + job.max_duration
        while True:
            with self._lock:
                if self._job is not job or job.phase != "recording":
                    return
                exceeded_time = time.monotonic() >= deadline
                exceeded_size = self._intermediate_size(job) > RECORDING_MAX_INTERMEDIATE_BYTES
                if exceeded_time or exceeded_size:
                    job.auto_stopped = True
                    if exceeded_size and not exceeded_time:
                        job.detail = "recording stopped: intermediate size limit reached"
                    self._end_capture(job)
                    return
            time.sleep(0.5)

    def _finalize(self, job: RecordingJob) -> None:
        try:
            summary = finalize_recording(job)
            job.artifact.chmod(RECORDING_FILE_MODE)
        except ToolError as exc:
            with self._lock:
                self._fail(job, f"finalization failed: {exc}")
            return
        except Exception as exc:
            eprint(f"unexpected finalization error for {job.id}: {exc}")
            with self._lock:
                self._fail(job, "unexpected finalization failure; see server log")
            return
        capture_seconds = max(
            (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 0.0
        )
        try:
            artifact_bytes = job.artifact.stat().st_size
        except OSError:
            artifact_bytes = 0
        result = {
            "id": job.id,
            "phase": "completed",
            "format": job.fmt,
            "output": job.output,
            "region": job.region,
            "path": str(job.artifact),
            "bytes": artifact_bytes,
            "capture_seconds": round(capture_seconds, 3),
            "graceful": not job.forced,
            "audio_included": False,
            "cursor_included": True,
            "auto_stopped": job.auto_stopped,
            **summary,
        }
        with self._lock:
            job.result = result
            job.phase = "completed"
            self._discard_intermediate(job)
            job.log_path.unlink(missing_ok=True)

    def _summary(self, job: RecordingJob) -> dict[str, Any]:
        base = {
            "id": job.id,
            "format": job.fmt,
            "output": job.output,
            "region": job.region,
            "audio_included": False,
            "cursor_included": True,
        }
        if job.phase == "recording":
            return {
                **base,
                "phase": "recording",
                "width": job.width,
                "height": job.height,
                "elapsed_seconds": round(time.monotonic() - job.started_monotonic, 3),
                "max_duration_seconds": job.max_duration,
                "bytes": self._intermediate_size(job),
            }
        if job.phase == "stopping":
            return {
                **base,
                "phase": "stopping",
                "capture_seconds": round(
                    (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 3
                ),
            }
        if job.phase == "processing":
            return {
                **base,
                "phase": "processing",
                "capture_seconds": round(
                    (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 3
                ),
                "note": "finalizing artifact; poll recording_status until completed or failed",
            }
        if job.phase == "completed":
            return dict(job.result or {"id": job.id, "phase": "completed"})
        return {
            **base,
            "phase": "failed",
            "detail": job.detail or "recording failed",
            "intermediate_path": str(job.intermediate) if job.intermediate.exists() else None,
            "log_path": str(job.log_path) if job.log_path.exists() else None,
        }


RECORDINGS = RecordingManager()


def tool_recording_start(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return json_text(RECORDINGS.start(arguments))


def tool_recording_status(_: dict[str, Any]) -> list[dict[str, str]]:
    return json_text(RECORDINGS.status())


def tool_recording_stop(_: dict[str, Any]) -> list[dict[str, str]]:
    return json_text(RECORDINGS.stop())


def tool_screen_info(_: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["swaymsg", "grim", "wtype", "wl-copy", "wl-paste"])
    outputs = get_outputs()
    rects = active_output_rects()
    min_x = min(rect["x"] for rect in rects)
    min_y = min(rect["y"] for rect in rects)
    max_x = max(rect["x"] + rect["width"] for rect in rects)
    max_y = max(rect["y"] + rect["height"] for rect in rects)
    info = {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "seat": detect_seat(),
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
        "focused_window": get_focused_window(),
        "binaries": {
            name: shutil.which(name)
            for name in ("swaymsg", "grim", "wtype", "wl-copy", "wl-paste", "wf-recorder", "ffmpeg", "ffprobe")
        },
    }
    return json_text(info)


def tool_screenshot(arguments: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["grim"])
    include_cursor = bool(arguments.get("include_cursor", True))
    output_mode = arguments.get("output")
    if output_mode is None:
        output_mode = "image"
    if output_mode not in {"image", "data_url", "both"}:
        raise ToolError("output must be null, image, data_url, or both")
    region = validate_region(arguments.get("region"))

    args = ["grim"]
    if include_cursor:
        args.append("-c")
    if region:
        args.extend(["-g", f"{region['x']},{region['y']} {region['width']}x{region['height']}"])
    args.extend(["-t", "png", "-"])

    data = run_command(args, timeout=10.0, binary=True).stdout
    if not data:
        raise ToolError("grim returned an empty screenshot")
    encoded = base64.b64encode(data).decode("ascii")
    meta = {
        "mimeType": "image/png",
        "bytes": len(data),
        "dimensions": png_dimensions(data),
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
        raise ToolError("max_depth must be between 1 and 50")
    windows = collect_windows(get_tree(), include_scratchpad, max_depth)
    return json_text({"windows": windows, "count": len(windows)})


def tool_focus_window(arguments: dict[str, Any]) -> list[dict[str, str]]:
    before = get_focused_window()
    selected = find_window(arguments)
    run_command(["swaymsg", "-t", "command", f"[con_id={int(selected['id'])}]", "focus"])
    time.sleep(0.05)
    after = get_focused_window()
    return json_text({"before": before, "selected": selected, "after": after})


def tool_move_pointer(arguments: dict[str, Any]) -> list[dict[str, str]]:
    mode = arguments.get("mode", "set")
    if mode not in {"set", "move"}:
        raise ToolError("mode must be set or move")
    x, y = validate_coordinates(arguments.get("x"), arguments.get("y"))
    sway_cursor(mode, str(x), str(y))
    return json_text({"moved": True, "mode": mode, "x": x, "y": y, "seat": detect_seat()})


def press_release(button: str, interval_ms: int = 40) -> None:
    sway_cursor("press", button)
    time.sleep(max(interval_ms, 0) / 1000.0)
    sway_cursor("release", button)


def tool_click(arguments: dict[str, Any]) -> list[dict[str, str]]:
    if ("x" in arguments) != ("y" in arguments):
        raise ToolError("provide both x and y, or neither")
    if "x" in arguments:
        x, y = validate_coordinates(arguments.get("x"), arguments.get("y"))
        sway_cursor("set", str(x), str(y))
    else:
        x = y = None

    button_name = arguments.get("button", "left")
    button = BUTTONS.get(button_name)
    if button is None:
        raise ToolError("button must be left, middle, or right")
    count = int(arguments.get("count", 1))
    interval_ms = int(arguments.get("interval_ms", 80))
    if count < 1 or count > 3:
        raise ToolError("count must be between 1 and 3")
    if interval_ms < 0 or interval_ms > 5000:
        raise ToolError("interval_ms must be between 0 and 5000")

    for index in range(count):
        press_release(button)
        if index + 1 < count:
            time.sleep(interval_ms / 1000.0)
    return json_text({"clicked": True, "x": x, "y": y, "button": button_name, "count": count})


def point_arg(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise ToolError(f"{name} must be an object with x and y")
    return validate_coordinates(value.get("x"), value.get("y"))


def tool_drag(arguments: dict[str, Any]) -> list[dict[str, str]]:
    start_x, start_y = point_arg(arguments.get("from"), "from")
    end_x, end_y = point_arg(arguments.get("to"), "to")
    button_name = arguments.get("button", "left")
    button = BUTTONS.get(button_name)
    if button is None:
        raise ToolError("button must be left, middle, or right")
    steps = int(arguments.get("steps", 12))
    duration_ms = int(arguments.get("duration_ms", 350))
    if steps < 1 or steps > 100:
        raise ToolError("steps must be between 1 and 100")
    if duration_ms < 0 or duration_ms > 10_000:
        raise ToolError("duration_ms must be between 0 and 10000")

    sleep_s = duration_ms / 1000.0 / max(steps, 1)
    sway_cursor("set", str(start_x), str(start_y))
    try:
        sway_cursor("press", button)
        for step in range(1, steps + 1):
            x = round(start_x + (end_x - start_x) * step / steps)
            y = round(start_y + (end_y - start_y) * step / steps)
            sway_cursor("set", str(x), str(y))
            if sleep_s:
                time.sleep(sleep_s)
    finally:
        try:
            sway_cursor("release", button)
        except ToolError as exc:
            eprint(f"drag release failed: {exc}")
    return json_text(
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
        raise ToolError("provide both x and y, or neither")
    if "x" in arguments:
        x, y = validate_coordinates(arguments.get("x"), arguments.get("y"))
        sway_cursor("set", str(x), str(y))
    else:
        x = y = None

    direction = arguments.get("direction")
    button = SCROLL_BUTTONS.get(direction)
    if button is None:
        raise ToolError("direction must be up, down, left, or right")
    clicks = int(arguments.get("clicks", 1))
    if clicks < 1 or clicks > 100:
        raise ToolError("clicks must be between 1 and 100")
    for _ in range(clicks):
        press_release(button, interval_ms=10)
    return json_text({"scrolled": True, "direction": direction, "clicks": clicks, "x": x, "y": y})


def tool_type_text(arguments: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["wtype"])
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ToolError("text must be a string")
    if "\x00" in text:
        raise ToolError("text must not contain NUL bytes")
    if len(text) > TEXT_LIMIT:
        raise ToolError(f"text exceeds limit of {TEXT_LIMIT} characters")
    delay_ms = int(arguments.get("delay_ms", 0))
    if delay_ms < 0 or delay_ms > 5000:
        raise ToolError("delay_ms must be between 0 and 5000")
    if delay_ms == 0:
        run_command(["wtype", text], timeout=max(5.0, len(text) / 500.0))
    else:
        for char in text:
            run_command(["wtype", char], timeout=2.0)
            time.sleep(delay_ms / 1000.0)
    return json_text({"typed": True, "characters": len(text), "delay_ms": delay_ms})


def tool_key(arguments: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["wtype"])
    key = arguments.get("key")
    if not isinstance(key, str) or not key:
        raise ToolError("key must be a non-empty string")
    modifiers = arguments.get("modifiers", [])
    if not isinstance(modifiers, list):
        raise ToolError("modifiers must be a list")
    canonical_modifiers = []
    for modifier in modifiers:
        mapped = MODIFIERS.get(str(modifier).lower())
        if mapped is None:
            raise ToolError("modifiers may only contain ctrl, shift, alt, or logo")
        canonical_modifiers.append(mapped)

    args = ["wtype"]
    for modifier in canonical_modifiers:
        args.extend(["-M", modifier])
    args.extend(["-k", key])
    for modifier in reversed(canonical_modifiers):
        args.extend(["-m", modifier])
    run_command(args)
    return json_text({"sent": True, "key": key, "modifiers": canonical_modifiers})


def tool_clipboard_set(arguments: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["wl-copy"])
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ToolError("text must be a string")
    if "\x00" in text:
        raise ToolError("text must not contain NUL bytes")
    if len(text.encode("utf-8")) > CLIPBOARD_LIMIT:
        raise ToolError(f"text exceeds clipboard limit of {CLIPBOARD_LIMIT} bytes")
    run_command(["wl-copy", text], timeout=5.0, capture=False)
    return json_text({"clipboard_set": True, "bytes": len(text.encode("utf-8"))})


def tool_clipboard_get(arguments: dict[str, Any]) -> list[dict[str, str]]:
    require_binaries(["wl-paste"])
    max_bytes = int(arguments.get("max_bytes", CLIPBOARD_LIMIT))
    if max_bytes < 1 or max_bytes > CLIPBOARD_LIMIT:
        raise ToolError(f"max_bytes must be between 1 and {CLIPBOARD_LIMIT}")
    result = run_command(["wl-paste", "--no-newline"], timeout=5.0, binary=True).stdout
    if b"\x00" in result:
        raise ToolError("clipboard appears to contain binary data")
    if len(result) > max_bytes:
        raise ToolError(f"clipboard content exceeds max_bytes ({max_bytes})")
    return json_text({"text": result.decode("utf-8", errors="strict"), "bytes": len(result)})


def json_text(value: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(value, indent=2, sort_keys=True)}]


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
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "screen_info",
            "description": "Inspect the active Sway outputs, seat, focused window, and required binaries.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "screenshot",
            "description": "Capture the current Sway screen or a rectangular region as PNG.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_cursor": {"type": "boolean", "default": True},
                    "output": {"type": ["string", "null"], "enum": ["image", "data_url", "both", None]},
                    "region": {
                        "type": ["object", "null"],
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "window_tree",
            "description": "Return a simplified Sway window tree.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_scratchpad": {"type": "boolean", "default": False},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "focus_window",
            "description": "Focus a Sway window by con_id, app_id, class, or title.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "con_id": {"type": ["integer", "string"]},
                    "app_id": {"type": "string"},
                    "class": {"type": "string"},
                    "title": {"type": "string"},
                    "match": {"type": "string", "enum": ["exact", "contains", "regex"], "default": "contains"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "move_pointer",
            "description": "Move the Sway pointer in logical output coordinates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["set", "move"], "default": "set"},
                },
                "required": ["x", "y"],
                "additionalProperties": False,
            },
        },
        {
            "name": "click",
            "description": "Click the pointer, optionally moving to a coordinate first.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                    "interval_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 80},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "drag",
            "description": "Drag from one Sway coordinate to another.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from": {"type": "object"},
                    "to": {"type": "object"},
                    "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 12},
                    "duration_ms": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 350},
                },
                "required": ["from", "to"],
                "additionalProperties": False,
            },
        },
        {
            "name": "scroll",
            "description": "Scroll using pointer wheel button events.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1},
                },
                "required": ["direction"],
                "additionalProperties": False,
            },
        },
        {
            "name": "type_text",
            "description": "Type text into the focused Wayland application.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": TEXT_LIMIT},
                    "delay_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 0},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "key",
            "description": "Send one key with optional modifiers through wtype.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["ctrl", "shift", "alt", "logo"]},
                        "default": [],
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        },
        {
            "name": "clipboard_set",
            "description": "Set text clipboard contents with wl-copy.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "clipboard_get",
            "description": "Read text clipboard contents with wl-paste.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": CLIPBOARD_LIMIT,
                        "default": CLIPBOARD_LIMIT,
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_start",
            "description": (
                "Start recording an active Sway output (or a region within it) into a silent "
                "artifact: AV1 WebM video, or a constrained GIF fallback. Returns immediately; "
                "call recording_stop to finish, then poll recording_status until the phase is "
                "completed or failed. The pointer cursor is always included."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "description": "Exact Sway output name; inferred when exactly one output is active.",
                    },
                    "region": {
                        "type": ["object", "null"],
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                        "description": "Region fully contained in the selected output.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["webm", "gif"],
                        "default": "webm",
                        "description": (
                            "webm: silent AV1 WebM at 30 fps; gif: constrained fallback at 12 fps, "
                            "960 px maximum width."
                        ),
                    },
                    "max_duration_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 300,
                        "description": (
                            "Automatic stop deadline. Defaults to 60 (webm) or 15 (gif); "
                            "GIF recordings are capped at 15 seconds."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_status",
            "description": (
                "Report the recording lifecycle phase (idle, recording, stopping, processing, "
                "completed, failed) plus live progress or final artifact metadata."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "recording_stop",
            "description": (
                "Gracefully stop the active recording and begin finalizing the requested "
                "artifact. Returns the stopping/processing state; poll recording_status for "
                "the final artifact path and metadata."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tool_specs()},
        }

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                },
            }
        try:
            content = TOOLS[name](arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": content, "isError": False},
            }
        except ToolError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        except Exception as exc:
            eprint(f"unexpected tool error in {name}: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "internal tool error"}],
                    "isError": True,
                },
            }

    if request_id is None:
        return None

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_mcp_server() -> int:
    def terminate(_signum: int, _frame: Any) -> None:
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, terminate)
        except (OSError, ValueError):
            pass
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            else:
                response = handle_message(message)
            if response is not None:
                print(json.dumps(response, separators=(",", ":")), flush=True)
    finally:
        RECORDINGS.shutdown()
    return 0


def install_codex_mcp() -> int:
    require_binaries(["codex"])
    expected_command = command_argv()
    expected_args = expected_command[1:]
    existing = subprocess.run(
        ["codex", "mcp", "get", SERVER_NAME, "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        try:
            data = json.loads(existing.stdout)
        except json.JSONDecodeError:
            data = {}
        transport = data.get("transport") or {}
        command = data.get("command") or transport.get("command")
        args = data.get("args")
        if args is None:
            args = transport.get("args") or []
        if command == expected_command[0] and args == expected_args:
            print(f"{SERVER_NAME} MCP entry already installed")
            return 0
        subprocess.run(["codex", "mcp", "remove", SERVER_NAME], check=False)

    add = subprocess.run(
        ["codex", "mcp", "add", SERVER_NAME, "--", *expected_command],
        check=False,
    )
    if add.returncode != 0:
        return add.returncode
    print(f"installed {SERVER_NAME} MCP entry")
    return 0


def uninstall_codex_mcp() -> int:
    require_binaries(["codex"])
    existing = subprocess.run(
        ["codex", "mcp", "get", SERVER_NAME, "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if existing.returncode != 0:
        print(f"{SERVER_NAME} MCP entry is not installed")
        return 0
    removed = subprocess.run(["codex", "mcp", "remove", SERVER_NAME], check=False)
    if removed.returncode == 0:
        print(f"removed {SERVER_NAME} MCP entry")
    return removed.returncode


def self_test() -> int:
    checks: list[tuple[str, str]] = []
    for name in (
        "python3",
        "swaymsg",
        "grim",
        "wtype",
        "wl-copy",
        "wl-paste",
        "wf-recorder",
        "ffmpeg",
        "ffprobe",
        "codex",
    ):
        checks.append((name, shutil.which(name) or "missing"))
    require_binaries(["swaymsg", "grim", "wtype", "wl-copy", "wl-paste"])
    require_session()
    outputs = get_outputs()
    seat = detect_seat()
    focused = get_focused_window()
    summary = {
        "ok": True,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "environment": {
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
            "SWAYSOCK": os.environ.get("SWAYSOCK"),
            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR"),
        },
        "binaries": dict(checks),
        "seat": seat,
        "outputs": [
            {
                "name": output.get("name"),
                "rect": output.get("rect"),
                "scale": output.get("scale"),
                "current_mode": output.get("current_mode"),
            }
            for output in outputs
        ],
        "focused_window": focused,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def doctor() -> int:
    report: dict[str, Any] = {
        "server": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "command": command_argv(),
            "module_path": __file__,
        },
        "environment": {
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
            "SWAYSOCK": os.environ.get("SWAYSOCK"),
            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR"),
        },
        "binaries": {
            name: shutil.which(name)
            for name in (
                "python3",
                "swaymsg",
                "grim",
                "wtype",
                "wl-copy",
                "wl-paste",
                "wf-recorder",
                "ffmpeg",
                "ffprobe",
                "codex",
            )
        },
        "session": None,
        "codex_mcp": None,
    }
    try:
        require_session()
        report["session"] = {
            "ok": True,
            "seat": detect_seat(),
            "outputs": [
                {
                    "name": output.get("name"),
                    "active": output.get("active"),
                    "rect": output.get("rect"),
                    "scale": output.get("scale"),
                    "transform": output.get("transform"),
                    "current_mode": output.get("current_mode"),
                }
                for output in get_outputs()
            ],
            "focused_window": get_focused_window(),
        }
    except ToolError as exc:
        report["session"] = {"ok": False, "error": str(exc)}

    if command_available("codex"):
        proc = subprocess.run(
            ["codex", "mcp", "get", SERVER_NAME, "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            try:
                report["codex_mcp"] = json.loads(proc.stdout)
            except json.JSONDecodeError:
                report["codex_mcp"] = {"raw": proc.stdout}
        else:
            report["codex_mcp"] = {"installed": False, "stderr": proc.stderr.strip()}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Computer Use MCP server for Sway")
    parser.add_argument("--self-test", action="store_true", help="run non-mutating checks")
    parser.add_argument("--doctor", action="store_true", help="print detailed diagnostics")
    parser.add_argument("--install-codex-mcp", action="store_true", help="register with Codex")
    parser.add_argument("--uninstall-codex-mcp", action="store_true", help="remove Codex registration")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if args.doctor:
            return doctor()
        if args.install_codex_mcp:
            return install_codex_mcp()
        if args.uninstall_codex_mcp:
            return uninstall_codex_mcp()
        return run_mcp_server()
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cli() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())

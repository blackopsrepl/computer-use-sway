from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

from . import core


SCENE_DEFAULT_THRESHOLD = 0.30
SCENE_MIN_THRESHOLD = 0.05
SCENE_MAX_THRESHOLD = 0.95
SCENE_DEFAULT_MAX = 40
SCENE_MAX_CUTS = 200


def parse_scene_cuts(stderr_text: str, limit: int) -> list[dict[str, Any]]:
    cuts: list[dict[str, Any]] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr_text):
        cuts.append({"t_ms": round(float(match.group(1)) * 1000.0, 3)})
        if len(cuts) >= limit:
            break
    return cuts


def detect_scene_cuts(path: Path, threshold: float, limit: int) -> list[dict[str, Any]]:
    result = core.run_command(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{threshold:.4f})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=120.0,
    )
    return parse_scene_cuts(result.stderr.decode("utf-8", errors="replace"), limit)


def ocr_frame(path: Path, t_ms: float, workdir: Path) -> str:
    core.require_binaries(["ffmpeg", "tesseract"])
    frame_path = workdir / f"ocr-{secrets.token_hex(4)}.png"
    try:
        core.run_command(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{t_ms / 1000.0:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                str(frame_path),
            ],
            timeout=60.0,
        )
        return core.run_command(["tesseract", str(frame_path), "stdout"], timeout=60.0).text.strip()
    finally:
        frame_path.unlink(missing_ok=True)

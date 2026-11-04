"""Pixel capture through grim: PNGs, 2x PPMs for OCR, and change hashes."""

from __future__ import annotations

import hashlib
import shutil
import time
from typing import Any

from . import core, desktop

GRIM_MISSING = "grim is required for screenshots, OCR and pixel verification (install grim)"


def _grim(args: list[str]) -> bytes:
    if shutil.which("grim") is None:
        raise core.ToolError(GRIM_MISSING, code="grim_missing")
    data = core.run_command(["grim", *args], timeout=10.0, binary=True).stdout
    if not data:
        raise core.ToolError("grim returned an empty capture")
    return data


def geometry(region: dict[str, int]) -> str:
    return f"{region['x']},{region['y']} {region['width']}x{region['height']}"


def png(region: dict[str, int] | None = None, output: str | None = None, cursor: bool = False) -> bytes:
    args = ["-c"] if cursor else []
    if region is not None:
        args += ["-g", geometry(region)]
    elif output is not None:
        args += ["-o", output]
    return _grim(args + ["-t", "png", "-"])


def ppm(region: dict[str, int], scale: int = 1) -> bytes:
    return _grim(["-t", "ppm", "-s", str(scale), "-g", geometry(region), "-"])


def ppm2x(region: dict[str, int]) -> bytes:
    """2x capture for OCR: small UI text recognizes far better upscaled [P1]."""
    return ppm(region, 2)


def region_hash(region: dict[str, int]) -> str:
    return hashlib.blake2b(ppm(region, 1), digest_size=16).hexdigest()


def settle(region: dict[str, int], interval_ms: int = 60, max_ms: int = 600) -> tuple[str, bool]:
    """Hash until two consecutive captures match or max_ms passes."""
    deadline = time.monotonic() + max_ms / 1000.0
    last = region_hash(region)
    while time.monotonic() < deadline:
        time.sleep(interval_ms / 1000.0)
        current = region_hash(region)
        if current == last:
            return current, True
        last = current
    return last, False


def window_region(win: dict[str, Any]) -> dict[str, int]:
    """The window's content area clipped to the output it sits on."""
    c = win.get("content") or desktop.content_rect(win)
    output = desktop.output_at(c["x"] + max(c["width"], 1) // 2, c["y"] + max(c["height"], 1) // 2)
    if output is None:
        raise core.ToolError("the window is not on an active output", code="window_offscreen")
    r = output["rect"]
    x0, y0 = max(c["x"], r["x"]), max(c["y"], r["y"])
    x1 = min(c["x"] + c["width"], r["x"] + r["width"])
    y1 = min(c["y"] + c["height"], r["y"] + r["height"])
    if x1 <= x0 or y1 <= y0:
        raise core.ToolError("the window has no visible area", code="window_offscreen")
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

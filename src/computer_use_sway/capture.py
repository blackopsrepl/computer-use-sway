"""Pixel capture through grim: PNGs, 2x PPMs for OCR, and change detection
that ignores a blinking caret (FINDINGS P4)."""

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


# A change narrower than MIN_SPAN columns that touches at most MAX_DIFF_ROWS
# rows is noise: a blinking text caret toggles a 1-2 px wide column, and it
# made every caret-bearing window look "changed" and every OCR ref "stale".
MIN_SPAN = 6
MAX_DIFF_ROWS = 64


def parse(frame_bytes: bytes) -> tuple[int, int, bytes]:
    from .ocr import parse_ppm

    return parse_ppm(frame_bytes)


def frame(region: dict[str, int]) -> bytes:
    """A 1x PPM of the region, for change detection."""
    return ppm(region, 1)


def region_hash(region: dict[str, int]) -> str:
    return hashlib.blake2b(frame(region), digest_size=16).hexdigest()


def changed(a: bytes, b: bytes) -> bool:
    """Whether two captures of the same region differ by more than noise."""
    if a == b:
        return False
    wa, ha, pa = parse(a)
    wb, hb, pb = parse(b)
    if (wa, ha) != (wb, hb):
        return True
    row = wa * 3
    rows = [y for y in range(ha) if pa[y * row:(y + 1) * row] != pb[y * row:(y + 1) * row]]
    if len(rows) > MAX_DIFF_ROWS:
        return True
    x0, x1 = wa, -1
    for y in rows:
        ra, rb = pa[y * row:(y + 1) * row], pb[y * row:(y + 1) * row]
        for x in range(wa):
            if ra[x * 3:x * 3 + 3] != rb[x * 3:x * 3 + 3]:
                x0, x1 = min(x0, x), max(x1, x)
        if x1 - x0 + 1 >= MIN_SPAN:
            return True
    return False


def crop(frame_bytes: bytes, box: tuple[int, int, int, int]) -> bytes:
    """A PPM of `box` (x, y, w, h in image pixels) cut from a PPM, clipped."""
    width, height, pixels = parse(frame_bytes)
    x, y, w, h = box
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return b"P6 0 0 255\n"
    rows = b"".join(pixels[(r * width + x0) * 3:(r * width + x1) * 3] for r in range(y0, y1))
    return b"P6 %d %d 255\n" % (x1 - x0, y1 - y0) + rows


def settle(region: dict[str, int], interval_ms: int = 60, max_ms: int = 600) -> tuple[bytes, bool]:
    """Capture until two consecutive frames show no real change or max_ms passes."""
    deadline = time.monotonic() + max_ms / 1000.0
    last = frame(region)
    while time.monotonic() < deadline:
        time.sleep(interval_ms / 1000.0)
        current = frame(region)
        if not changed(last, current):
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

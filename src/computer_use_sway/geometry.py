"""AT-SPI WINDOW extents and OCR pixel boxes to Sway layout points [A4].

    layout = sway.rect.xy + sway.window_rect.xy
           - frame_extents.xy - csd_origin
           + element_extents  (÷ output scale for Chromium/Electron)

`csd_origin` is (0,0) unless the frame's size differs from window_rect's,
which happens with client-side decorations: the frame then includes the
shadow margin, and the visible window starts at the min x/y of the frame's
direct children.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

Box = tuple[int, int, int, int]


@dataclass
class WindowGeo:
    origin: tuple[int, int]
    size: tuple[int, int]
    output_scale: float
    frame_extents: Box
    csd_origin: tuple[int, int]


def csd_origin(frame_extents: Box, child_boxes: Iterable[Box], window_size: tuple[int, int]) -> tuple[int, int]:
    fw, fh = frame_extents[2], frame_extents[3]
    ww, wh = window_size
    if (fw, fh) == (ww, wh):
        return (0, 0)
    valid = [b for b in child_boxes if min(b[0], b[1]) > -10000 and b[2] > 1 and b[3] > 1]
    if not valid:
        return ((fw - ww) // 2, (fh - wh) // 2)
    return (min(b[0] for b in valid), min(b[1] for b in valid))


def a11y_to_layout(geo: WindowGeo, ext: Box, device_pixels: bool) -> Box:
    if device_pixels and geo.output_scale and geo.output_scale != 1:
        ext = tuple(int(round(v / geo.output_scale)) for v in ext)  # type: ignore[assignment]
    x = geo.origin[0] + ext[0] - geo.frame_extents[0] - geo.csd_origin[0]
    y = geo.origin[1] + ext[1] - geo.frame_extents[1] - geo.csd_origin[1]
    return (x, y, ext[2], ext[3])


def center(box: Box) -> tuple[int, int]:
    return (box[0] + box[2] // 2, box[1] + box[3] // 2)


def ocr_to_layout(window_origin: tuple[int, int], box_px: Box, image_scale: int = 2) -> Box:
    x, y, w, h = box_px
    return (
        window_origin[0] + x // image_scale,
        window_origin[1] + y // image_scale,
        max(1, w // image_scale),
        max(1, h // image_scale),
    )


def output_scale_at(outputs: list[dict[str, Any]], x: int, y: int) -> float:
    for output in outputs:
        r = output.get("rect") or {}
        if r and r["x"] <= x < r["x"] + r["width"] and r["y"] <= y < r["y"] + r["height"]:
            return float(output.get("scale") or 1.0)
    return 1.0


def contains(box: Box, x: int, y: int) -> bool:
    return box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]

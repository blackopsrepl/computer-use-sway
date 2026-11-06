"""OCR ensemble over tesseract [P1, P2].

Each preprocessing variant alone misses text on some toolkit or theme; the
union of four cheap variants, run as parallel tesseract processes, did not
miss in the measured matrix. Transforms are byte-level (slicing and
bytes.translate), never per-pixel Python loops.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import core

MIN_CONF = 50.0
TIMEOUT_S = 20.0
MISSING = "OCR needs tesseract (install tesseract and an English traineddata)"

Box = tuple[int, int, int, int]


@dataclass
class Word:
    text: str
    box_px: Box
    conf: float
    variant: str


@dataclass
class Line:
    text: str
    box_px: Box
    words: list[Word] = field(default_factory=list)


def available() -> bool:
    return shutil.which("tesseract") is not None


def parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    parts: list[bytes] = []
    pos = 0
    while len(parts) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        parts.append(data[start:pos])
    if parts[0] != b"P6" or parts[3] != b"255":
        raise core.ToolError("unsupported image format for OCR (expected 8-bit P6)")
    return int(parts[1]), int(parts[2]), data[pos + 1:]


def variants(ppm: bytes) -> dict[str, bytes]:
    width, height, rgb = parse_ppm(ppm)
    green = rgb[1::3]  # the green channel carries most of the luminance
    lo, hi = (min(green), max(green)) if green else (0, 255)
    mid = (lo + hi) // 2
    head = b"P5 %d %d 255\n" % (width, height)
    invert = bytes(range(255, -1, -1))
    dark_on_light = bytes(0 if v > mid else 255 for v in range(256))
    light_on_dark = bytes(255 - x for x in dark_on_light)
    dark_pixels = sum(green.count(bytes([v])) for v in range(0, mid + 1, 8))
    light_text = dark_pixels > len(green) / 16
    return {
        "raw": ppm,
        "gray": head + green,
        "gray_inv": head + green.translate(invert),
        "mid": head + green.translate(dark_on_light if light_text else light_on_dark),
    }


def _tmp_root() -> str | None:
    if os.environ.get("TMPDIR"):
        return os.environ["TMPDIR"]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        path = os.path.join(runtime, "computer-use-sway")
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path
    return None


def parse_tsv(out: str, variant: str) -> list[Word]:
    words = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) != 12 or not f[11].strip():
            continue
        try:
            conf = float(f[10])
            box = (int(f[6]), int(f[7]), int(f[8]), int(f[9]))
        except ValueError:
            continue
        if conf >= MIN_CONF:
            words.append(Word(f[11].strip(), box, conf, variant))
    return words


def recognize(ppm: bytes, psm: str = "12") -> list[Word]:
    if not available():
        raise core.ToolError(MISSING, code="ocr_missing")
    tmp = tempfile.mkdtemp(prefix="cus-ocr-", dir=_tmp_root())
    procs: dict[str, subprocess.Popen] = {}
    words: list[Word] = []
    try:
        for name, data in variants(ppm).items():
            path = os.path.join(tmp, name + (".ppm" if name == "raw" else ".pgm"))
            with open(path, "wb") as handle:
                handle.write(data)
            procs[name] = subprocess.Popen(
                ["tesseract", path, "-", "--psm", psm, "-c", "thresholding_method=2", "tsv"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        for name, proc in procs.items():
            try:
                out, _ = proc.communicate(timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                continue
            words.extend(parse_tsv(out, name))
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    return dedupe(words)


def _overlap(a: Box, b: Box) -> float:
    """Intersection over the smaller box's area."""
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    smaller = min(a[2] * a[3], b[2] * b[3])
    return (ix * iy) / smaller if smaller > 0 else 0.0


def dedupe(words: list[Word]) -> list[Word]:
    """One word per place. Variants that read the same spot are alternatives,
    not neighbours: keep the reading most variants agree on (then the most
    confident). Keeping both turned "Hit" into the line "Hi Hit" (measured)."""
    clusters: list[list[Word]] = []
    for word in sorted(words, key=lambda w: -w.conf):
        for cluster in clusters:
            if _overlap(cluster[0].box_px, word.box_px) >= 0.5:
                cluster.append(word)
                break
        else:
            clusters.append([word])
    kept: list[Word] = []
    for cluster in clusters:
        support: dict[str, set[str]] = {}
        for word in cluster:
            support.setdefault(word.text, set()).add(word.variant)
        best = max(cluster, key=lambda w: (len(support[w.text]), w.conf))
        kept.append(best)
    return sorted(kept, key=lambda w: (w.box_px[1], w.box_px[0]))


def union(boxes: list[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2]) if ordered else 0.0


def lines(words: list[Word]) -> list[Line]:
    ordered = sorted(words, key=lambda w: (w.box_px[1] + w.box_px[3] / 2, w.box_px[0]))
    groups: list[list[Word]] = []
    for word in ordered:
        cy = word.box_px[1] + word.box_px[3] / 2
        placed = False
        for group in groups:
            height = _median([w.box_px[3] for w in group]) or word.box_px[3]
            gy = sum(w.box_px[1] + w.box_px[3] / 2 for w in group) / len(group)
            right = max(w.box_px[0] + w.box_px[2] for w in group)
            left = min(w.box_px[0] for w in group)
            gap = word.box_px[0] - right if word.box_px[0] >= left else left - (word.box_px[0] + word.box_px[2])
            if abs(cy - gy) <= 0.6 * height and gap <= 1.5 * height:
                group.append(word)
                placed = True
                break
        if not placed:
            groups.append([word])
    out = []
    for group in groups:
        group.sort(key=lambda w: w.box_px[0])
        out.append(Line(" ".join(w.text for w in group), union([w.box_px for w in group]), group))
    out.sort(key=lambda line: (line.box_px[1], line.box_px[0]))
    return out


def find(found_lines: list[Line], text: str, match: str = "contains") -> list[tuple[Line, Box]]:
    """Lines matching `text`, each with the box of the matched word span."""
    results = []
    for line in found_lines:
        if match == "regex":
            try:
                m = re.search(text, line.text)
            except re.error as exc:
                raise core.ToolError(f"invalid regex: {exc}") from exc
            if not m:
                continue
            span = (m.start(), m.end())
        elif match == "exact":
            if line.text.lower() != text.lower():
                continue
            span = (0, len(line.text))
        else:
            index = line.text.lower().find(text.lower())
            if index < 0:
                continue
            span = (index, index + len(text))
        boxes, cursor = [], 0
        for word in line.words:
            start, end = cursor, cursor + len(word.text)
            if start < span[1] and end > span[0]:
                boxes.append(word.box_px)
            cursor = end + 1
        results.append((line, union(boxes) if boxes else line.box_px))
    return results

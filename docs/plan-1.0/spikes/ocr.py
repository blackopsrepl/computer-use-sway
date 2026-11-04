"""OCR ensemble (spike, reference). Stdlib + the tesseract binary.

Each variant alone misses on some toolkit/theme (proven in spike 6); the union
of four cheap variants, run as parallel tesseract processes, does not.
Byte-level transforms only (slicing + bytes.translate): no per-pixel Python.
"""
import os
import subprocess
import tempfile

from pgm import parse_ppm


def variants(ppm: bytes) -> dict[str, bytes]:
    w, h, rgb = parse_ppm(ppm)
    green = rgb[1::3]  # luminance proxy: the green channel carries ~60% of Y
    lo, hi = min(green), max(green)
    mid = (lo + hi) // 2
    inv = bytes(range(255, -1, -1))
    head = b"P5 %d %d 255\n" % (w, h)
    binar = bytes(0 if v > mid else 255 for v in range(256))  # light text -> black
    binar_inv = bytes(255 - x for x in binar)
    # pick the polarity that makes the minority black
    dark_px = sum(green.count(bytes([v])) for v in range(0, mid + 1, 8))  # coarse sample of the histogram
    light_text = dark_px > len(green) / 16  # dark background dominates
    return {
        "raw": ppm,
        "gray": head + green,
        "gray_inv": head + green.translate(inv),
        "mid": head + green.translate(binar if light_text else binar_inv),
    }


def run(ppm: bytes, psm: str = "11") -> list[dict]:
    tmp = tempfile.mkdtemp(prefix="ocr-", dir=os.environ.get("TMPDIR"))
    procs = {}
    for name, data in variants(ppm).items():
        path = os.path.join(tmp, name + (".ppm" if name == "raw" else ".pgm"))
        with open(path, "wb") as f:
            f.write(data)
        procs[name] = subprocess.Popen(["tesseract", path, "-", "--psm", psm, "-c", "thresholding_method=2", "tsv"],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    words = []
    for name, p in procs.items():
        out, _ = p.communicate(timeout=30)
        for line in out.splitlines()[1:]:
            f = line.split("\t")
            if len(f) == 12 and f[11].strip() and float(f[10]) >= 50:
                words.append({"text": f[11].strip(), "x": int(f[6]), "y": int(f[7]), "w": int(f[8]),
                              "h": int(f[9]), "conf": float(f[10]), "variant": name})
    for name in os.listdir(tmp):
        os.unlink(os.path.join(tmp, name))
    os.rmdir(tmp)
    return dedupe(words)


def dedupe(words: list[dict]) -> list[dict]:
    """Same text with overlapping boxes is one word; keep the highest confidence."""
    out: list[dict] = []
    for w in sorted(words, key=lambda x: -x["conf"]):
        cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
        if any(o["text"] == w["text"] and o["x"] <= cx <= o["x"] + o["w"] and o["y"] <= cy <= o["y"] + o["h"]
               for o in out):
            continue
        out.append(w)
    return sorted(out, key=lambda x: (x["y"], x["x"]))

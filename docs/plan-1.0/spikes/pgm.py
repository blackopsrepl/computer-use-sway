"""Stdlib OCR preprocessing (spike, reference).

grim -t ppm gives raw RGB, so nothing needs decoding. Steps: grayscale ->
Otsu threshold -> the minority class (text) becomes black on white. Works
for light and dark themes alike.
"""


def parse_ppm(data: bytes):
    # P6\n<w> <h>\n<max>\n<rgb...>; header tokens separated by whitespace
    parts, pos = [], 0
    while len(parts) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        start = pos
        while not data[pos:pos + 1].isspace():
            pos += 1
        parts.append(data[start:pos])
    assert parts[0] == b"P6" and parts[3] == b"255", parts
    return int(parts[1]), int(parts[2]), data[pos + 1:]


def binarize(ppm: bytes) -> tuple[bytes, dict]:
    w, h, rgb = parse_ppm(ppm)
    r, g, b = rgb[0::3], rgb[1::3], rgb[2::3]
    gray = bytes((x * 77 + y * 150 + z * 29) >> 8 for x, y, z in zip(r, g, b))
    hist = [0] * 256
    for v in gray:
        hist[v] += 1
    total, sum_all = len(gray), sum(i * c for i, c in enumerate(hist))
    best, thr, w0, sum0 = -1.0, 128, 0, 0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0 or w0 == total:
            continue
        sum0 += t * hist[t]
        m0, m1 = sum0 / w0, (sum_all - sum0) / (total - w0)
        between = w0 * (total - w0) * (m0 - m1) ** 2
        if between > best:
            best, thr = between, t
    dark_count = sum(hist[: thr + 1])
    text_is_dark = dark_count < total - dark_count
    table = bytes((0 if (v <= thr) == text_is_dark else 255) for v in range(256))
    return b"P5 %d %d 255\n" % (w, h) + gray.translate(table), {"threshold": thr, "text_dark": text_is_dark}

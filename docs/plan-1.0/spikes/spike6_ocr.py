"""Spike 6: OCR grounding fallback (tesseract TSV) + click on the agent seat,
and a whole-tree Cache.GetItems timing per toolkit."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import atspi  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import click  # noqa: E402
from spike4_a11y import find_app, safe  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "gtk3"
scale = int(sys.argv[2]) if len(sys.argv) > 2 else 1
r = {"target": name, "scale": scale}
extra = f"output HEADLESS-1 resolution {1280 * scale}x{800 * scale} scale {scale}\n" if scale != 1 else ""
with sbx.Sandbox(f"spike6-{name}", extra_config=extra) as s:
    root = os.path.join(s.root, "assets")
    targets.write_assets(root)
    a = atspi.Atspi(s.env["DBUS_SESSION_BUS_ADDRESS"])
    argv, env = targets.argv(name, root)
    s.spawn(argv, **env)
    win = None
    for _ in range(120):
        win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
        if win:
            break
        time.sleep(0.3)
    time.sleep(2)
    win = next(w for w in s.windows() if w["id"] == win["id"])
    app, _ = find_app(a, win["pid"], time.monotonic() + 10)
    got = safe(lambda: atspi.timed(a.cache_items, app[0]))
    r["cache_getitems"] = ({"items": len(got[0]), "ms": got[1]} if isinstance(got, tuple) else got[:90])
    # capture only the window's content rect, in layout coordinates
    rect, wr = win["rect"], win["window_rect"]
    gx, gy, gw, gh = rect["x"] + wr["x"], rect["y"] + wr["y"], wr["width"], wr["height"]
    png = os.path.join(s.root, "win.png")
    t0 = time.monotonic()
    # OCR wants ~2x UI text: ask grim for a fixed 2 image px per layout point
    # regardless of output scale, so the image->layout factor is always 2.
    ppm = subprocess.run(["grim", "-t", "ppm", "-s", "2", "-g", f"{gx},{gy} {gw}x{gh}", "-"], env=s.env,
                         check=True, capture_output=True).stdout
    open(png, "wb").write(ppm)
    import shutil; shutil.copy(png, os.path.expanduser(f"~/.hermes/cache/scratch/ocr-{name}-{scale}.png"))
    import ocr
    t1 = time.monotonic()
    found = ocr.run(ppm)
    r["ocr_ms"] = round((time.monotonic() - t1) * 1000)
    words = [(w["text"], w["x"], w["y"], w["w"], w["h"], w["conf"], w["variant"]) for w in found]
    r["ocr_words"] = [w[0] for w in words][:20]
    hit = next((w for w in words if w[0] == "Hit"), None)
    r["ocr_hit_box_px"] = hit
    if hit:
        # image pixels -> layout points: divide by the output scale
        px = gx + (hit[1] + hit[3] / 2) / 2
        py = gy + (hit[2] + hit[4] / 2) / 2
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seat = "seat0" if name in targets.SINGLE_SEAT else "agent"
        ptr = wl.VirtualPointer(c, c.seats()[seat])
        time.sleep(0.3)
        click(ptr, int(px), int(py), extent=(1280, 800))
        time.sleep(0.5)
        r["click_point"] = (int(px), int(py))
        r["title_after"] = next(w for w in s.windows() if w["id"] == win["id"]).get("name")
        r["pass"] = "T:hit" in (r["title_after"] or "")
print(json.dumps(r, default=str))

"""Spike 3: drag and scroll through zwlr_virtual_pointer_v1 on the agent seat
(replaces /dev/uinput). Pass = motion events with the button held inside the
drag area, a press and release at different points, discrete scroll events."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import sbx, wl
from spike1_seats_lib import PY, HERE, center, last_geo

r = {}
with sbx.Sandbox("spike3") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log]); s.window("oracle-A"); time.sleep(0.6)
    w, g = s.window("oracle-A"), last_geo(log, "A")
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    ap = wl.VirtualPointer(c, c.seats()["agent"])
    x0, y0 = center(w, g, "area")
    x0 -= 100
    t0 = time.monotonic()
    ap.move_abs(x0, y0, 1280, 800); ap.flush(); time.sleep(0.05)
    ap.button(wl.BTN_LEFT, True); ap.flush(); time.sleep(0.05)
    for i in range(1, 21):
        ap.move_abs(x0 + i * 10, y0 + i * 2, 1280, 800); ap.flush(); time.sleep(0.01)
    time.sleep(0.05)
    ap.button(wl.BTN_LEFT, False); ap.flush(); time.sleep(0.2)
    evs = [e for e in sbx.read_log(log) if e["t"] >= t0]
    held = [e for e in evs if e["ev"] == "motion" and e["held"]]
    pr = [(e["ev"], round(e["x"]), round(e["y"])) for e in evs if e["ev"] in ("press", "release")]
    r["drag"] = {"held_motion_events": len(held), "press_release": pr,
                 "pass": len(held) >= 15 and len(pr) == 2 and pr[0][1:] != pr[1][1:]}
    t1 = time.monotonic()
    ap.scroll(0, 3); ap.flush(); time.sleep(0.1)
    ap.scroll(0, -2); ap.flush(); time.sleep(0.3)
    sc = [(e["direction"], e["dy"]) for e in sbx.read_log(log) if e["t"] >= t1 and e["ev"] == "scroll"]
    r["scroll"] = {"events": sc}
print(json.dumps(r, indent=1))

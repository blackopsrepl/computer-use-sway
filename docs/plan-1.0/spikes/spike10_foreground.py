"""Spike 10: foreground text entry for single-seat engines (Chromium/Electron)
without touching the user's pointer, with focus restored afterwards.

  user (seat0) works in GTK3 oracle A; agent must type into Chromium's field.
  1. remember seat0 focus (con A)
  2. IPC `[con_id=C] focus` (acts on seat0)
  3. AT-SPI GrabFocus on the field
  4. seat0 virtual keyboard types the text
  5. IPC `[con_id=A] focus` restores
Pass: field text read back via title, A keeps all user text typed before and
after, seat0 focus back on A, pointer never moved (no seat0 pointer exists).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import atspi  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, text_of  # noqa: E402
from spike4_a11y import find_app, find_nodes  # noqa: E402

r = {}
name = sys.argv[1] if len(sys.argv) > 1 else "chromium"
with sbx.Sandbox(f"spike10-{name}") as s:
    root = os.path.join(s.root, "assets")
    targets.write_assets(root)
    log = os.path.join(s.root, "oracle.jsonl")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    wa = s.window("oracle-A")
    a = atspi.Atspi(s.env["DBUS_SESSION_BUS_ADDRESS"])
    argv, env = targets.argv(name, root)
    s.spawn(argv, **env)
    wc = None
    for _ in range(100):
        wc = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
        if wc:
            break
        time.sleep(0.3)
    time.sleep(2)
    app, _ = find_app(a, wc["pid"], time.monotonic() + 15)
    nodes, _ = find_nodes(a, app, time.monotonic() + 15)
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    uk = wl.VirtualKeyboard(c, c.seats()["seat0"])
    time.sleep(0.3)
    s.msg(f"[con_id={wa['id']}] focus")
    time.sleep(0.2)
    uk.type("u1")  # user typing in A (stands in for the human)
    time.sleep(0.2)
    before = s.seats()["seat0"]["focus"]
    t0 = time.monotonic()
    s.msg(f"[con_id={wc['id']}] focus")
    a.grab_focus(nodes["entry"])
    time.sleep(0.15)
    uk.type("agent7")
    time.sleep(0.1)
    s.msg(f"[con_id={before}] focus")
    r["foreground_ms"] = round((time.monotonic() - t0) * 1000)
    time.sleep(0.2)
    uk.type("u2")
    time.sleep(0.5)
    r["field_title"] = next(w.get("name") for w in s.windows() if w["id"] == wc["id"])
    r["A_text"] = text_of(log, "A")
    r["seat0_focus_restored"] = s.seats()["seat0"]["focus"] == wa["id"]
    r["pass"] = "agent7" in r["field_title"] and r["A_text"] == "u1u2" and r["seat0_focus_restored"]
print(json.dumps(r))

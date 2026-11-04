"""Spike 14b: minimal — config-file binding vs runtime binding, which seat runs it."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE  # noqa: E402

mode = sys.argv[1]
base = "xwayland disable\noutput HEADLESS-1 resolution 1280x800\nseat seat0 fallback true\nseat cus-agent fallback false\n"
bind = 'bindsym --no-repeat XF86Macro27 [con_mark="_cus_focus"] focus\n'
sbx.BASE_CONFIG = base + (bind if mode == "config" else "")
r = {"mode": mode}
with sbx.Sandbox(f"spike14b-{mode}") as s:
    log = os.path.join(s.root, "o.jsonl")
    for n in "AB":
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", n, log])
        s.window(f"oracle-{n}")
    wa, wb = s.window("oracle-A"), s.window("oracle-B")
    if mode == "runtime":
        s.msg(bind.strip())
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    k = wl.VirtualKeyboard(c, seats["cus-agent"], ["XF86Macro27"])
    time.sleep(0.3)
    s.msg(f"[con_id={wa['id']}] focus")

    def f():
        st = s.seats()
        return {n: ("A" if v["focus"] == wa["id"] else "B" if v["focus"] == wb["id"] else v["focus"]) for n, v in st.items()}

    r["start"] = f()
    s.msg(f"[con_id={wb['id']}] mark --add _cus_focus")
    k.chord("XF86Macro27")
    time.sleep(0.2)
    r["after_tap_B"] = f()
    s.msg(f"[con_id={wa['id']}] mark --add _cus_focus")
    k.chord("XF86Macro27")
    time.sleep(0.2)
    r["after_tap_A"] = f()
    r["config_bindings"] = [l for l in s.msg(kind="get_config")["config"].splitlines() if "bindsym" in l]
print(json.dumps(r))

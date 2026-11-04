"""Spike 7: everything the server must set up at RUNTIME on a live session.

  R1  `seat agent ...` created over IPC (not in config) -> new wl_seat global
  R2  `create_output` + position far outside the layout + `workspace agent output`
  R3  agent-only focus binding: private keysym + --input-device, triggered only
      by the agent keyboard; seat0's virtual keyboard cannot hit it
  R4  `reload`: what survives (seat, devices, output, bindings, workspace pin)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import ipc  # noqa: E402
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, click, text_of  # noqa: E402

KEYSYM = "XF86Macro30"
r = {}
with sbx.Sandbox("spike7") as s:
    # sandbox config DOES declare seat agent; rebuild without it to test runtime creation
    pass

cfg_no_agent = "seat seat0 fallback true\n"
import sbx as _s  # noqa: E402
_s.BASE_CONFIG = "xwayland disable\noutput HEADLESS-1 resolution 1280x800 position 0 0\n" + cfg_no_agent
with sbx.Sandbox("spike7") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    for n in "AB":
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", n, log])
        s.window(f"oracle-{n}")
    time.sleep(0.6)
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    r["R1_before"] = sorted(c.seats())
    r["R1_cmd"] = s.msg("seat agent fallback false")
    c.roundtrip()
    seats = c.seats()
    r["R1_after"] = sorted(seats)
    r["R2_create"] = s.msg("create_output")
    outs = [o["name"] for o in s.msg(kind="get_outputs")]
    new = [o for o in outs if o != "HEADLESS-1"][0]
    r["R2_place"] = s.msg(f"output {new} position 100000 0 resolution 1280x800")
    r["R2_pin"] = s.msg(f"workspace agent output {new}")
    r["R2_outputs"] = [(o["name"], o["rect"]) for o in s.msg(kind="get_outputs")]
    r["R2_workspaces"] = [(w["name"], w["output"], w["focused"]) for w in s.msg(kind="get_workspaces")]

    up, uk = wl.VirtualPointer(c, seats["seat0"]), wl.VirtualKeyboard(c, seats["seat0"], [KEYSYM])
    ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"], [KEYSYM])
    time.sleep(0.3)
    devs = {n: [(d["identifier"], d["type"]) for d in v["devices"]] for n, v in s.seats().items()}
    r["devices"] = devs
    wa, wb = s.window("oracle-A"), s.window("oracle-B")
    ext = (101280, 800)
    click(up, *center(wa, last_geo(log, "A"), "entry"), extent=ext)
    click(ap, *center(wb, last_geo(log, "B"), "entry"), extent=ext)
    r["R3_bind"] = s.msg(f"bindsym --no-repeat --input-device=0:0:wlr_virtual_keyboard_v1 {KEYSYM} "
                         f"[con_mark=\"_cus_target\"] focus")
    s.msg(f"[con_id={wa['id']}] mark --add _cus_target")
    sub = ipc.Subscription(s.swaysock, ["binding", "workspace", "output", "input"])
    sub.start()

    def focus():
        st = s.seats()
        return {k: ("A" if v["focus"] == wa["id"] else "B" if v["focus"] == wb["id"] else v["focus"])
                for k, v in st.items()}

    r["R3_start"] = focus()
    ak.chord(KEYSYM)
    time.sleep(0.3)
    r["R3_after_agent_key"] = focus()
    s.msg(f"[con_id={wb['id']}] mark --add _cus_target")
    uk.chord(KEYSYM)  # seat0 virtual keyboard has the same identifier -> does it fire?
    time.sleep(0.3)
    r["R3_after_seat0_key"] = focus()
    r["R3_binding_events"] = [e[2]["binding"]["command"] for e in sub.events if e[1] == "binding"]

    # R4 reload
    t0 = time.monotonic()
    r["R4_reload"] = s.msg("reload")
    time.sleep(1.0)
    st = s.seats()
    r["R4_seats"] = {n: [d["identifier"] for d in v["devices"]] for n, v in st.items()}
    r["R4_outputs"] = [o["name"] for o in s.msg(kind="get_outputs")]
    r["R4_workspaces"] = [(w["name"], w["output"]) for w in s.msg(kind="get_workspaces")]
    r["R4_events"] = [(e[1], e[2].get("change")) for e in sub.events if e[0] >= t0]
    s.msg(f"[con_id={wa['id']}] mark --add _cus_target")
    ak.chord(KEYSYM)
    time.sleep(0.3)
    r["R4_binding_after_reload"] = [e[2]["binding"]["command"] for e in sub.events if e[1] == "binding" and e[0] >= t0]
    # does the agent device still route to the agent seat after reload?
    click(ap, *center(wb, last_geo(log, "B"), "entry"), extent=ext)
    ak.type("zz")
    time.sleep(0.3)
    r["R4_agent_typing_lands"] = {"A": text_of(log, "A"), "B": text_of(log, "B")}
    r["R4_focus"] = focus()
print(json.dumps(r, indent=None, default=str))

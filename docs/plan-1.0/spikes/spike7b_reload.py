"""Spike 7b: recover the agent seat after `reload` without leaking input.

Protocol: on workspace::reload -> (1) mark seat generation stale (input gate
closed) -> (2) destroy agent virtual devices -> (3) `seat agent fallback false`
-> (4) wait for the new wl_seat global -> (5) recreate devices on it ->
(6) verify via get_seats that agent owns exactly our 2 devices -> (7) re-add
the focus binding -> gate open.
Also checks: the gate check itself (get_seats device count) catches the stale
state before any input is sent.
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

sbx.BASE_CONFIG = "xwayland disable\noutput HEADLESS-1 resolution 1280x800 position 0 0\nseat seat0 fallback true\n"
KEYSYM = "XF86Macro30"


def wait_seat(c, name, timeout=2.0):
    """The wl_seat global arrives asynchronously after the IPC reply."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c.roundtrip()
        seats = c.seats()
        if name in seats:
            return seats
        time.sleep(0.02)
    raise RuntimeError(f"wl_seat {name} never appeared")


def agent_ok(s):
    st = s.seats()
    return "agent" in st and len(st["agent"]["devices"]) == 2


r = {}
with sbx.Sandbox("spike7b") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    for n in "AB":
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", n, log])
        s.window(f"oracle-{n}")
    time.sleep(0.6)
    wa, wb = s.window("oracle-A"), s.window("oracle-B")
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    s.msg("seat agent fallback false")
    seats = wait_seat(c, "agent")
    up = wl.VirtualPointer(c, seats["seat0"])
    ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"], [KEYSYM])
    time.sleep(0.3)
    click(up, *center(wa, last_geo(log, "A"), "entry"))
    sub = ipc.Subscription(s.swaysock, ["workspace"])
    sub.start()
    r["gate_before_reload"] = agent_ok(s)
    t0 = time.monotonic()
    s.msg("reload")
    ev = sub.wait_for(lambda e: e[2].get("change") == "reload", t0, 3.0)
    r["reload_event_ms"] = None if not ev else round((ev[0] - t0) * 1000, 1)
    r["gate_after_reload"] = agent_ok(s)
    # recovery
    t1 = time.monotonic()
    ap.c.send(ap.id, wl.VPTR_DESTROY)
    ak.c.send(ak.id, wl.VKBD_DESTROY)
    c.roundtrip()
    r["recreate_reply"] = s.msg("seat agent fallback false")
    c.roundtrip()
    r["ipc_seats_after_recreate"] = list(s.seats())
    r["wl_seat_globals"] = [g for g, (i, v) in c.globals.items() if i == "wl_seat"]
    seats = wait_seat(c, "agent")
    r["wl_seat_names"] = sorted(seats)
    if "agent" not in seats:
        print(json.dumps(r, default=str)); raise SystemExit
    ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"], [KEYSYM])
    time.sleep(0.3)
    s.msg(f"bindsym --no-repeat {KEYSYM} [con_mark=\"_cus_target\"] focus")
    r["recovery_ms"] = round((time.monotonic() - t1) * 1000)
    r["gate_after_recovery"] = agent_ok(s)
    r["seats_after_recovery"] = {n: [d["identifier"] for d in v["devices"]] for n, v in s.seats().items()}
    click(ap, *center(wb, last_geo(log, "B"), "entry"))
    t2 = time.monotonic()
    ak.type("ok")
    time.sleep(0.3)
    r["key_events_B"] = [(e["ev"], e.get("keyval")) for e in sbx.read_log(log) if e["t"] >= t2 and e["win"] == "B"]
    r["text"] = {"A": text_of(log, "A"), "B": text_of(log, "B")}
    if r["text"]["B"] != "ok":
        # variant 1: focus bounce via the agent binding (A then B)
        s.msg(f"[con_id={wa['id']}] mark --add _cus_target"); ak.chord(KEYSYM); time.sleep(0.2)
        s.msg(f"[con_id={wb['id']}] mark --add _cus_target"); ak.chord(KEYSYM); time.sleep(0.2)
        ak.type("v1")
        time.sleep(0.3)
        r["text_after_bounce"] = {"A": text_of(log, "A"), "B": text_of(log, "B")}
        r["focus_events_B"] = [e["ev"] for e in sbx.read_log(log) if e["t"] >= t2 and e["win"] in "AB" and e["ev"].startswith(("focus", "key"))]
    st = s.seats()
    r["focus"] = {k: ("A" if v["focus"] == wa["id"] else "B" if v["focus"] == wb["id"] else v["focus"]) for k, v in st.items()}
    s.msg(f"[con_id={wa['id']}] mark --add _cus_target")
    ak.chord(KEYSYM)
    time.sleep(0.3)
    st = s.seats()
    r["binding_after_recovery"] = {k: ("A" if v["focus"] == wa["id"] else "B" if v["focus"] == wb["id"] else v["focus"]) for k, v in st.items()}
    r["pass"] = (r["text"] == {"A": "", "B": "ok"} and r["focus"] == {"agent": "B", "seat0": "A"}
                 and r["binding_after_recovery"] == {"agent": "A", "seat0": "A"} and not r["gate_after_reload"])
print(json.dumps(r, default=str))

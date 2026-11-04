"""Spike 1d: launch through an agent-seat binding (sway launcher context).

The binding runs `exec` with handler_context.seat = agent, so
launcher_ctx_create_internal() records the agent seat's focused workspace and
the new view (matched by pid ancestry) maps there. No assign rule leaks.
Also proves the forking case (sh -c '... &') maps by ancestry.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import ipc  # noqa: E402
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, click  # noqa: E402

r = {}
with sbx.Sandbox("spike1d", extra_config="workspace agent output HEADLESS-2\n") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    s.msg("create_output")
    s.msg("output HEADLESS-2 position 100000 0 resolution 1280x800")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    s.window("oracle-A")
    s.msg('assign [app_id="oracle-C"] workspace agent')  # seed one agent window
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "C", log])
    s.window("oracle-C")
    time.sleep(0.6)
    ext = (101280, 800)
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    up, ap = wl.VirtualPointer(c, seats["seat0"]), wl.VirtualPointer(c, seats["agent"])
    ak = wl.VirtualKeyboard(c, seats["agent"], ["F34"])
    click(up, *center(s.window("oracle-A"), last_geo(log, "A"), "entry"), extent=ext)
    click(ap, *center(s.window("oracle-C"), last_geo(log, "C"), "entry"), extent=ext)
    sub = ipc.Subscription(s.swaysock, ["window", "workspace", "binding"])
    sub.start()

    def focus():
        st = s.seats()
        return {k: st[k]["focus"] for k in st}

    def by_binding(name, cmd):
        f0 = focus()
        s.msg(f"bindsym --no-repeat F34 exec {cmd}")
        t0 = time.monotonic()
        ak.tap("F34")
        s.msg("unbindsym F34")
        win = s.window(f"oracle-{name}")
        time.sleep(0.4)
        win = s.window(f"oracle-{name}")
        evs = [e[2]["change"] for e in sub.events if e[0] >= t0 and e[1] == "window"
               and e[2]["container"]["id"] == win["id"]]
        return {"ws": win["_ws"], "seat0": [f0["seat0"], focus()["seat0"]],
                "agent": [f0["agent"], focus()["agent"]], "new_id": win["id"], "events": evs,
                "user_ws_events": [e[2]["change"] for e in sub.events if e[0] >= t0 and e[1] == "workspace"]}

    r["direct"] = by_binding("G", f"{PY} {HERE}/oracle_gtk3.py G {log}")
    r["forked"] = by_binding("H", f"{HERE}/forker.sh {HERE}/oracle_gtk3.py H {log}")
    r["bindings_left"] = s.msg(kind="get_binding_state")
    r["A_id"] = s.window("oracle-A")["id"]

print(json.dumps(r, indent=1))

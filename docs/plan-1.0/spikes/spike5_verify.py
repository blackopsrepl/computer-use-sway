"""Spike 5: effect verification signals, stdlib only.

  V1  AT-SPI events (text-changed, state-changed:focused, property-change:name)
      arrive on our own bus connection after RegisterEvent + AddMatch
  V2  Sway IPC window::title event arrives for the same action
  V3  latency from action to first event
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import atspi  # noqa: E402
import ipc  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
from spike4_a11y import find_app, find_nodes  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "gtk3"
r = {"target": name}
with sbx.Sandbox(f"spike5-{name}") as s:
    root = os.path.join(s.root, "assets")
    targets.write_assets(root)
    a = atspi.Atspi(s.env["DBUS_SESSION_BUS_ADDRESS"])
    a.register_events(["object:text-changed", "object:state-changed", "object:property-change"])
    sub = ipc.Subscription(s.swaysock, ["window"])
    sub.start()
    argv, env = targets.argv(name, root)
    s.spawn(argv, **env)
    win = None
    for _ in range(120):
        win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
        if win:
            break
        time.sleep(0.3)
    time.sleep(1.5)
    app, _ = find_app(a, win["pid"], time.monotonic() + 15)
    nodes, _ = find_nodes(a, app, time.monotonic() + 15)
    a.bus.pump(0.5)
    a.bus.signals.clear()
    t0 = time.monotonic()
    a.do_action(nodes["button"], 0)
    a.bus.pump(1.0)
    sigs = [(round((t - t0) * 1000, 1), h.get(2, "").rsplit(".", 1)[-1], h.get(3), v[0] if v else None)
            for t, h, v in a.bus.signals]
    r["V1_atspi_after_press"] = sigs[:12]
    ev = sub.wait_for(lambda e: e[1] == "window" and e[2]["change"] == "title"
                      and e[2]["container"]["id"] == win["id"], t0, 2.0)
    r["V2_sway_title_event"] = None if not ev else {"ms": round((ev[0] - t0) * 1000, 1),
                                                    "title": ev[2]["container"]["name"]}
    if "Editable" in "".join(a.interfaces(nodes["entry"])):
        a.bus.signals.clear()
        t1 = time.monotonic()
        a.set_text(nodes["entry"], "abc")
        a.bus.pump(1.0)
        r["V1_atspi_after_set_text"] = [(round((t - t1) * 1000, 1), h.get(3), v[0] if v else None)
                                        for t, h, v in a.bus.signals][:8]
print(json.dumps(r, indent=1, default=str))

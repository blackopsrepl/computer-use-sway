"""Spike 4b: semantic (device-free) operations on web content, per engine."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import atspi  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
from spike4_a11y import find_app, find_nodes, safe, title  # noqa: E402

name = sys.argv[1]
r = {"target": name}
with sbx.Sandbox(f"spike4b-{name}") as s:
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
    app, _ = find_app(a, win["pid"], time.monotonic() + 15)
    nodes, _ = find_nodes(a, app, time.monotonic() + 15)
    b, e = nodes["button"], nodes["entry"]
    r["button_ifaces"] = a.interfaces(b)
    r["entry_ifaces"] = a.interfaces(e)
    r["nactions"] = safe(lambda: a.prop(b, atspi.ACTION, "NActions"))
    r["action_names"] = [safe(lambda i=i: a.call(b, atspi.ACTION, "GetName", "i", i)[0]) for i in range(3)]
    r["action_desc"] = [safe(lambda i=i: a.call(b, atspi.ACTION, "GetDescription", "i", i)[0]) for i in range(2)]
    r["entry_actions"] = safe(lambda: [a.call(e, atspi.ACTION, "GetName", "i", i)[0]
                                        for i in range(a.prop(e, atspi.ACTION, "NActions"))])
    r["grab_focus_entry"] = safe(lambda: a.grab_focus(e))
    time.sleep(0.3)
    r["entry_states_after_grab"] = sorted(safe(lambda: a.states(e)) or [])
    r["insert_text"] = safe(lambda: a.call(e, atspi.ETEXT, "InsertText", "isi", 0, "ins", 3)[0])
    time.sleep(0.4)
    r["after_insert"] = {"readback": safe(lambda: a.text(e)), "title": title(s, win["id"])}
    r["set_contents"] = safe(lambda: a.set_text(e, "setc"))
    time.sleep(0.4)
    r["after_set"] = {"readback": safe(lambda: a.text(e)), "title": title(s, win["id"])}
    r["do_action_0"] = safe(lambda: a.do_action(b, 0))
    time.sleep(0.4)
    r["after_action"] = title(s, win["id"])
print(json.dumps(r, indent=1, default=str))

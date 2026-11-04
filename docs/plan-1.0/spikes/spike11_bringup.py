"""Spike 11: full agent-workspace bring-up from nothing (runtime output),
agent seat takes the workspace via an agent-keyboard binding, launches an app
there via a binding exec, works in it; seat0 never moves."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, click, text_of  # noqa: E402

KEY_WS, KEY_EXEC = "XF86Macro29", "XF86Macro28"
r = {}
with sbx.Sandbox("spike11") as s:
    root = os.path.join(s.root, "assets")
    targets.write_assets(root)
    log = os.path.join(s.root, "oracle.jsonl")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    wa = s.window("oracle-A")
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    uk = wl.VirtualKeyboard(c, seats["seat0"])
    ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"], [KEY_WS, KEY_EXEC])
    time.sleep(0.3)
    s.msg(f"[con_id={wa['id']}] focus")
    uk.type("u1")
    for o in s.msg(kind="get_outputs"):
        s.msg(f"output {o['name']} position {o['rect']['x']} {o['rect']['y']}")
    s.msg("create_output")
    new = [o["name"] for o in s.msg(kind="get_outputs") if o["name"] != "HEADLESS-1"][0]
    s.msg(f"output {new} position 100000 0 resolution 1280x800")
    s.msg(f"workspace cus-agent output {new}")
    s.msg(f"bindsym --no-repeat {KEY_WS} workspace cus-agent")
    s.msg('bindsym --no-repeat XF86Macro27 [con_mark="_cus_target"] focus')
    ak.ensure(["XF86Macro27"])
    s.msg(f"bindsym --no-repeat {KEY_EXEC} exec env -u XDG_ACTIVATION_TOKEN -u DESKTOP_STARTUP_ID {PY} {root}/gtk3.py")
    ak.chord(KEY_WS)
    s.msg("nop")
    time.sleep(0.3)
    ws = {w["name"]: (w["output"], w["visible"], w["focused"]) for w in s.msg(kind="get_workspaces")}
    r["workspaces_after_agent_ws"] = ws
    ak.chord(KEY_EXEC)
    s.msg("nop")  # reset sway's handler-context seat to the default seat (seat0)
    win = None
    for _ in range(60):
        win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
        if win:
            break
        time.sleep(0.2)
    time.sleep(1.0)
    win = next(w for w in s.windows() if w["id"] == win["id"])
    def fz():
        st = s.seats()
        return {k: ("A" if v["focus"] == wa["id"] else "app" if v["focus"] == win["id"] else v["focus"]) for k, v in st.items()}
    r["focus_after_map"] = fz()
    s.msg(f"[con_id={win['id']}] mark --add _cus_target")
    ak.chord("XF86Macro27")
    s.msg("nop")
    time.sleep(0.2)
    r["focus_after_agent_focus_binding"] = fz()
    r["env_token"] = open(f"/proc/{win['pid']}/environ", "rb").read().split(b"\0")
    r["env_token"] = [e.decode() for e in r["env_token"] if b"ACTIVATION" in e or b"STARTUP" in e]
    r["app_workspace"] = win["_ws"]
    r["app_rect"] = win["rect"]
    outs = s.msg(kind="get_outputs")
    ext = (max(o["rect"]["x"] + o["rect"]["width"] for o in outs), max(o["rect"]["y"] + o["rect"]["height"] for o in outs))
    rc = win["rect"]
    click(ap, rc["x"] + rc["width"] // 2, rc["y"] + 140, extent=ext)
    r["focus_after_click"] = fz()
    ak.type("agent")
    uk.type("u2")
    time.sleep(0.5)
    st = s.seats()
    r["app_title"] = next(w.get("name") for w in s.windows() if w["id"] == win["id"])
    r["A_text"] = text_of(log, "A")
    r["seat_focus"] = {k: ("A" if v["focus"] == wa["id"] else "app" if v["focus"] == win["id"] else v["focus"]) for k, v in st.items()}
    r["pass"] = (r["app_workspace"] == "cus-agent" and r["app_title"] == "T:agent" and r["A_text"] == "u1u2"
                 and r["seat_focus"] == {"agent": "app", "seat0": "A"})
print(json.dumps(r))

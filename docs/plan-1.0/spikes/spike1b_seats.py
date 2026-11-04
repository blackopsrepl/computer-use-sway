"""Spike 1b: disambiguate which seat IPC commands drive, and prove an
agent workspace on its own headless output.

  P3  IPC `[con_id=C] focus` with seat0=A, agent=B: who moves?
  P4  agent-keyboard binding `[con_mark=_agent_target] focus`: only agent moves?
  P5  agent output: `create_output` + workspace pinned to it; agent focuses and
      types there while seat0's visible workspace and focus stay put
  P6  where a newly launched window maps: IPC exec vs agent-seat binding exec
      vs runtime `assign`/`for_window`
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, text_of, type_text, click  # noqa: E402

CFG = (
    'bindsym --no-repeat F35 [con_mark="_agent_target"] focus\n'
    "workspace agent output HEADLESS-2\n"
)

r = {}
with sbx.Sandbox("spike1b", extra_config=CFG) as s:
    log = os.path.join(s.root, "oracle.jsonl")
    for n in "ABC":
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", n, log])
        s.window(f"oracle-{n}")
    time.sleep(0.8)
    w = {n: s.window(f"oracle-{n}") for n in "ABC"}
    g = {n: last_geo(log, n) for n in "ABC"}
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    syms = sorted({wl.keysym_for(ch) for ch in "abcdefghijklmnopqrstuvwxyz0123456789"} | {"F35", "F34"})
    up, uk = wl.VirtualPointer(c, seats["seat0"]), wl.VirtualKeyboard(c, seats["seat0"], syms)
    ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"], syms)
    time.sleep(0.3)

    def focus():
        st = s.seats()
        return {k: st[k]["focus"] for k in st}

    def by_id(i):
        return next((n for n in "ABC" if w[n]["id"] == i), i)

    click(up, *center(w["A"], g["A"], "entry"))
    click(ap, *center(w["B"], g["B"], "entry"))
    r["start"] = {k: by_id(v) for k, v in focus().items()}
    s.msg(f"[con_id={w['C']['id']}] focus")
    time.sleep(0.2)
    r["P3_ipc_focus_C"] = {k: by_id(v) for k, v in focus().items()}

    # reset: seat0 A, agent B
    click(up, *center(w["A"], g["A"], "entry"))
    click(ap, *center(w["B"], g["B"], "entry"))
    s.msg(f"[con_id={w['C']['id']}] mark --add _agent_target")
    ak.tap("F35")
    time.sleep(0.3)
    r["P4_agent_binding_C"] = {k: by_id(v) for k, v in focus().items()}
    type_text(ak, "agentc")
    type_text(uk, "usera")
    time.sleep(0.3)
    r["P4_text"] = {n: text_of(log, n) for n in "ABC"}

    # P5: agent output
    s.msg("create_output")
    time.sleep(0.3)
    outs = s.msg(kind="get_outputs")
    r["P5_outputs"] = [(o["name"], o["rect"]) for o in outs]
    s.msg("output HEADLESS-2 position 100000 0 resolution 1280x800")
    time.sleep(0.2)
    # move C onto the agent workspace without disturbing seat0 (marks + IPC move: no focus change?)
    before = s.msg(kind="get_workspaces")
    s.msg(f"[con_id={w['C']['id']}] move container to workspace agent")
    time.sleep(0.3)
    after = s.msg(kind="get_workspaces")
    r["P5_workspaces_before"] = [(x["name"], x["output"], x["visible"], x["focused"]) for x in before]
    r["P5_workspaces_after_move"] = [(x["name"], x["output"], x["visible"], x["focused"]) for x in after]
    r["P5_focus_after_move"] = {k: by_id(v) for k, v in focus().items()}
    wc = s.window("oracle-C")
    r["P5_C_rect"] = wc["rect"]
    gc = last_geo(log, "C")
    # agent clicks C on the agent output: absolute motion extent must cover the layout
    lay_w = max(o["rect"]["x"] + o["rect"]["width"] for o in s.msg(kind="get_outputs"))
    lay_h = max(o["rect"]["y"] + o["rect"]["height"] for o in s.msg(kind="get_outputs"))
    cx, cy = center(wc, gc, "entry")
    ap.move_abs(cx, cy, lay_w, lay_h)
    ap.flush()
    time.sleep(0.05)
    ap.button(wl.BTN_LEFT, True); ap.flush(); time.sleep(0.03)
    ap.button(wl.BTN_LEFT, False); ap.flush(); time.sleep(0.2)
    type_text(ak, "offscreen")
    type_text(uk, "still")
    time.sleep(0.3)
    r["P5_text"] = {n: text_of(log, n) for n in "ABC"}
    r["P5_focus"] = {k: by_id(v) for k, v in focus().items()}
    r["P5_workspaces_final"] = [(x["name"], x["output"], x["visible"], x["focused"]) for x in s.msg(kind="get_workspaces")]
    shot = os.path.join(s.root, "agent.png")
    import subprocess
    r["P5_grim_agent_output"] = subprocess.run(["grim", "-o", "HEADLESS-2", shot], env=s.env).returncode == 0 and os.path.getsize(shot)

    # P6: where new windows map
    def spawn_and_ws(name, how):
        p = s.spawn([PY, f"{HERE}/oracle_gtk3.py", name, log])
        if how == "assign":
            pass
        win = s.window(f"oracle-{name}")
        time.sleep(0.2)
        return win["_ws"], p.pid

    r["P6_plain_spawn"] = spawn_and_ws("D", None)[0]
    r["P6_runtime_assign_reply"] = s.msg('assign [app_id="oracle-E"] workspace agent')
    r["P6_assign_spawn"] = spawn_and_ws("E", "assign")[0]
    r["P6_runtime_for_window_reply"] = s.msg('for_window [app_id="oracle-F"] move container to workspace agent')
    r["P6_for_window_spawn"] = spawn_and_ws("F", None)[0]
    # exec through an agent-seat binding: sway's launcher context is the
    # executing seat's focused workspace
    s.msg(f"bindsym --no-repeat F34 exec {PY} {HERE}/oracle_gtk3.py G {log}")
    ak.tap("F34")
    wg = s.window("oracle-G")
    r["P6_agent_binding_exec"] = wg["_ws"]
    r["P6_focus_end"] = {k: v for k, v in focus().items()}
    r["P6_visible_ws_end"] = [(x["name"], x["output"], x["visible"]) for x in s.msg(kind="get_workspaces")]

print(json.dumps(r, indent=None))

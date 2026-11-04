"""Spike 1: per-seat input on Sway. Proves (or refutes):

  P1  a virtual keyboard/pointer created on wl_seat "agent" is attached to the
      Sway seat "agent", not seat0
  P2  agent-seat focus and seat0 focus are independent: the agent types into
      window B while the user keeps typing into window A
  P3  which seat Sway IPC commands act on
  P4  a binding fired by the agent keyboard runs in the agent seat's context
      (the IPC-free way to move only the agent's focus)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "/usr/bin/python3"


def center(win, geo, widget):
    r, wr = win["rect"], win["window_rect"]
    g = geo["widgets"][widget]
    return r["x"] + wr["x"] + g["x"] + g["w"] // 2, r["y"] + wr["y"] + g["y"] + g["h"] // 2


def last_geo(log, name):
    gs = [e for e in sbx.read_log(log) if e["win"] == name and e["ev"] == "geometry"]
    return gs[-1] if gs else None


def text_of(log, name):
    ts = [e["text"] for e in sbx.read_log(log) if e["win"] == name and e["ev"] == "text"]
    return ts[-1] if ts else ""


def type_text(kbd, text):
    for ch in text:
        kbd.tap(wl.keysym_for(ch))


def click(ptr, x, y):
    ptr.move_abs(x, y, 1280, 800)
    ptr.flush()
    time.sleep(0.05)
    ptr.button(wl.BTN_LEFT, True)
    ptr.flush()
    time.sleep(0.03)
    ptr.button(wl.BTN_LEFT, False)
    ptr.flush()
    time.sleep(0.15)


results = {}
with sbx.Sandbox("spike1", extra_config="bindsym --no-repeat F35 [con_mark=\"_agent_target\"] focus\n") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    wa = s.window("oracle-A")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "B", log])
    wb = s.window("oracle-B")
    time.sleep(0.8)
    wa, wb = s.window("oracle-A"), s.window("oracle-B")
    ga, gb = last_geo(log, "A"), last_geo(log, "B")

    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    results["wl_seats"] = sorted(seats)
    syms = sorted({wl.keysym_for(ch) for ch in "u1u2agentxyz"} | {"F35"})
    user_ptr = wl.VirtualPointer(c, seats["seat0"])
    user_kbd = wl.VirtualKeyboard(c, seats["seat0"], syms)
    agent_ptr = wl.VirtualPointer(c, seats["agent"])
    agent_kbd = wl.VirtualKeyboard(c, seats["agent"], syms)
    time.sleep(0.3)
    st = s.seats()
    results["P1_devices"] = {n: [d["identifier"] for d in st[n]["devices"]] for n in st}

    # user focuses A's entry and types
    click(user_ptr, *center(wa, ga, "entry"))
    type_text(user_kbd, "u1")
    # agent focuses B's entry and types
    click(agent_ptr, *center(wb, gb, "entry"))
    type_text(agent_kbd, "agent")
    # user keeps typing: must still land in A
    type_text(user_kbd, "u2")
    time.sleep(0.3)
    st = s.seats()
    results["P2_text"] = {"A": text_of(log, "A"), "B": text_of(log, "B")}
    results["P2_seat_focus"] = {n: st[n]["focus"] for n in st}
    results["con_ids"] = {"A": wa["id"], "B": wb["id"]}
    results["tree_focused"] = [w["app_id"] for w in s.windows() if w.get("focused")]

    # P3: an IPC focus command -- which seat moves?
    s.msg(f"[con_id={wb['id']}] focus")
    time.sleep(0.2)
    st = s.seats()
    results["P3_after_ipc_focus_B"] = {n: st[n]["focus"] for n in st}
    s.msg(f"[con_id={wa['id']}] focus")  # restore seat0 to A
    time.sleep(0.2)

    # P4: agent moves only its own focus to A via a mark + agent-keyboard binding
    s.msg(f"[con_id={wa['id']}] mark --add _agent_target")
    click(agent_ptr, *center(wb, gb, "entry"))  # agent back on B first
    agent_kbd.tap("F35")
    time.sleep(0.3)
    st = s.seats()
    results["P4_after_agent_binding"] = {n: st[n]["focus"] for n in st}
    type_text(agent_kbd, "xyz")
    time.sleep(0.3)
    results["P4_text"] = {"A": text_of(log, "A"), "B": text_of(log, "B")}
    results["focus_events"] = [(e["win"], e["ev"]) for e in sbx.read_log(log) if e["ev"].startswith("focus")]

print(json.dumps(results, indent=1))

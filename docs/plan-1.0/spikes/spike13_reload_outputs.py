"""Spike 13: `reload` with the agent output present, when the user's outputs
have no configured position (like the live DP-1).

Measures where outputs land after reload, whether seat0 focus/pointer moves,
and proves the recovery: re-pin user outputs at their saved positions, move
the agent output back to x=100000, re-pin the agent workspace.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import ipc  # noqa: E402
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, text_of  # noqa: E402

# user output WITHOUT a position (auto-arranged), config-declared agent seat
sbx.BASE_CONFIG = ("xwayland disable\noutput HEADLESS-1 resolution 1280x800\n"
                   "seat seat0 fallback true\nseat cus-agent fallback false\n")


def outs(s):
    return {o["name"]: (o["rect"]["x"], o["rect"]["y"]) for o in s.msg(kind="get_outputs")}


r = {}
with sbx.Sandbox("spike13") as s:
    log = os.path.join(s.root, "oracle.jsonl")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    wa = s.window("oracle-A")
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    up, uk = wl.VirtualPointer(c, seats["seat0"]), wl.VirtualKeyboard(c, seats["seat0"])
    time.sleep(0.3)
    up.move_abs(400, 300, 1280, 800)
    up.flush()
    s.msg(f"[con_id={wa['id']}] focus")
    saved = outs(s)
    for n, (x, y) in saved.items():
        s.msg(f"output {n} position {x} {y}")
    s.msg("create_output")
    agent_out = [n for n in outs(s) if n not in saved][0]
    s.msg(f"output {agent_out} position 100000 0 resolution 1280x800")
    s.msg(f"workspace cus-agent output {agent_out}")
    r["before_reload"] = {"outputs": outs(s), "seat0": s.seats()["seat0"]["focus"]}
    sub = ipc.Subscription(s.swaysock, ["workspace", "output"])
    sub.start()
    t0 = time.monotonic()
    s.msg("reload")
    sub.wait_for(lambda e: e[1] == "workspace" and e[2].get("change") == "reload", t0, 2)
    time.sleep(0.5)
    r["after_reload"] = {"outputs": outs(s), "seat0": s.seats()["seat0"]["focus"],
                         "workspaces": [(w["name"], w["output"]) for w in s.msg(kind="get_workspaces")]}
    # recovery
    t1 = time.monotonic()
    for n, (x, y) in saved.items():
        s.msg(f"output {n} position {x} {y}")
    s.msg(f"output {agent_out} position 100000 0 resolution 1280x800")
    s.msg(f"workspace cus-agent output {agent_out}")
    if s.seats()["seat0"]["focus"] != wa["id"]:
        s.msg(f"[con_id={wa['id']}] focus")
    r["recovery_ms"] = round((time.monotonic() - t1) * 1000)
    time.sleep(0.3)
    r["after_recovery"] = {"outputs": outs(s), "seat0": s.seats()["seat0"]["focus"]}
    uk.type("ok")
    time.sleep(0.3)
    r["user_typing_after"] = text_of(log, "A")
    r["A_id"] = wa["id"]
    r["pass"] = (r["after_recovery"]["outputs"] == r["before_reload"]["outputs"]
                 and r["after_recovery"]["seat0"] == wa["id"] and r["user_typing_after"] == "ok")
print(json.dumps(r))

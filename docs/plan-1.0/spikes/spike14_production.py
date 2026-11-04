"""Spike 14: the exact production design, end to end.

Config file declares the seat and the three bindings (focus-by-mark, agent
workspace, exec of a fixed absolute launcher path). No runtime bindsym.
Flow: bring-up -> launch app 1 via next.sh -> agent types -> `reload` ->
recover (devices, pins, agent output) -> launch app 2 -> agent types into
app 1 AND app 2 -> user typing stays in A throughout.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import ipc  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, click, text_of  # noqa: E402

ROOT = os.path.join(sbx.SCRATCH, "spike14")
LAUNCH = os.path.join(ROOT, "launch")
sbx.BASE_CONFIG = (
    "xwayland disable\noutput HEADLESS-1 resolution 1280x800\nseat seat0 fallback true\n"
    "seat cus-agent fallback false\n"
    'bindsym --no-repeat XF86Macro27 [con_mark="_cus_focus"] focus\n'
    f"bindsym --no-repeat XF86Macro29 exec {LAUNCH}/next.sh\n"
)
KEYS = ["XF86Macro27", "XF86Macro29"]
BINDINGS = [l for l in sbx.BASE_CONFIG.splitlines() if l.startswith("bindsym")]
if os.environ.get("RUNTIME_BIND"):
    sbx.BASE_CONFIG = "\n".join(l for l in sbx.BASE_CONFIG.splitlines() if not l.startswith("bindsym")) + "\n"


def main():
    r = {}
    with sbx.Sandbox("spike14") as s:
        os.makedirs(LAUNCH, mode=0o700, exist_ok=True)
        assets = os.path.join(s.root, "assets")
        targets.write_assets(assets)
        log = os.path.join(s.root, "oracle.jsonl")
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
        wa = s.window("oracle-A")
        sub = ipc.Subscription(s.swaysock, ["workspace", "window", "binding"])
        sub.start()
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = c.seats()
        uk = wl.VirtualKeyboard(c, seats["seat0"])
        dev = {}

        def make_devices():
            dev["p"] = wl.VirtualPointer(c, seats["cus-agent"])
            dev["k"] = wl.VirtualKeyboard(c, seats["cus-agent"], KEYS)
            time.sleep(0.3)

        trace = r.setdefault("trace", [])

        def seat0():
            st = s.seats()
            return (st["seat0"]["focus"], st["cus-agent"]["focus"])

        def tap(key):
            dev["k"].chord(key)
            s.msg("nop")  # [O6]
            time.sleep(0.05)
            fired = [e[2]["binding"]["command"] for e in sub.events if e[1] == "binding"]
            trace.append((key, seat0(), fired[-1] if fired else None, len(fired)))

        def agent_focus(con_id):
            s.msg(f"[con_id={con_id}] mark --add _cus_focus")
            tap("XF86Macro27")

        saved = {}

        def bring_up():
            for o in s.msg(kind="get_outputs"):
                if o["name"] not in saved.values():
                    pass
            for n, xy in saved.get("user", {}).items():
                s.msg(f"output {n} position {xy[0]} {xy[1]}")
            s.msg(f"output {saved['agent']} position 100000 0 resolution 1280x800")
            s.msg(f"workspace cus-agent output {saved['agent']}")
            # name the agent output's workspace by IPC rename (no seat involved);
            # a `workspace <name>` binding would donate the name to user outputs
            ws_on = [w["name"] for w in s.msg(kind="get_workspaces") if w["output"] == saved["agent"]]
            if ws_on and ws_on[0] != "cus-agent":
                s.msg(f'rename workspace "{ws_on[0]}" to cus-agent')

        if os.environ.get("RUNTIME_BIND"):
            for b in BINDINGS:
                s.msg(b)
        make_devices()
        r["devices_at_start"] = {n: [d["identifier"] for d in v["devices"]] for n, v in s.seats().items()}
        r["wl_seats"] = sorted(seats)
        s.msg(f"[con_id={wa['id']}] focus")
        uk.type("u1")
        saved["user"] = {o["name"]: (o["rect"]["x"], o["rect"]["y"]) for o in s.msg(kind="get_outputs")}
        for n, xy in saved["user"].items():
            s.msg(f"output {n} position {xy[0]} {xy[1]}")
        s.msg("create_output")
        saved["agent"] = [o["name"] for o in s.msg(kind="get_outputs") if o["name"] not in saved["user"]][0]
        bring_up()
        def extent():
            outs = s.msg(kind="get_outputs")
            return (max(o["rect"]["x"] + o["rect"]["width"] for o in outs),
                    max(o["rect"]["y"] + o["rect"]["height"] for o in outs))

        def agent_to_workspace():
            """Put the agent seat on cus-agent: mark-focus a window there, or
            click the empty workspace background with the agent pointer."""
            on_ws = [w for w in s.windows() if w["_ws"] == "cus-agent"]
            if on_ws:
                agent_focus(on_ws[0]["id"])
                return "mark"
            o = next(o for o in s.msg(kind="get_outputs") if o["name"] == saved["agent"])
            cx, cy = o["rect"]["x"] + o["rect"]["width"] // 2, o["rect"]["y"] + o["rect"]["height"] // 2
            click(dev["p"], cx, cy, extent=extent())
            s.msg("nop")
            return "click"

        def launch(tag):
            script = os.path.join(LAUNCH, "next.sh")
            with open(script, "w") as f:
                f.write(f"#!/bin/sh\nexec env -u XDG_ACTIVATION_TOKEN -u DESKTOP_STARTUP_ID "
                        f"{PY} {assets}/gtk3.py\n")
            os.chmod(script, 0o700)
            before = {w["id"] for w in s.windows()}
            how = agent_to_workspace()
            trace.append(("agent_to_ws", how, seat0()))
            tap("XF86Macro29")
            for _ in range(100):
                new = [w for w in s.windows() if w["id"] not in before and "T:" in (w.get("name") or "")]
                if new:
                    break
                time.sleep(0.1)
            time.sleep(0.8)
            win = s.window(new[0]["app_id"]) if False else next(w for w in s.windows() if w["id"] == new[0]["id"])
            agent_focus(win["id"])
            return win

        def extent():
            outs = s.msg(kind="get_outputs")
            return (max(o["rect"]["x"] + o["rect"]["width"] for o in outs),
                    max(o["rect"]["y"] + o["rect"]["height"] for o in outs))

        def agent_type(win, text):
            win = next(w for w in s.windows() if w["id"] == win["id"])
            agent_focus(win["id"])
            rc = win["rect"]
            click(dev["p"], rc["x"] + rc["width"] // 2, rc["y"] + 140, extent=extent())
            dev["k"].type(text)
            time.sleep(0.3)
            return next(w.get("name") for w in s.windows() if w["id"] == win["id"])

        trace.append(("A", wa["id"], seat0()))
        w1 = launch("1")
        trace.append(("after_launch1", w1["id"], seat0()))
        r["app1_ws"] = w1["_ws"]
        r["workspaces"] = [(w["name"], w["output"]) for w in s.msg(kind="get_workspaces")]
        r["app1_title"] = agent_type(w1, "one")
        trace.append(("after_type1", seat0()))
        uk.type("u2")
        if os.environ.get("RUNTIME_BIND"):
            time.sleep(0.3)
            r["A_text"] = text_of(log, "A")
            print(json.dumps(r))
            return
        # reload in the middle
        t0 = time.monotonic()
        s.msg("reload")
        ev = sub.wait_for(lambda e: e[1] == "workspace" and e[2].get("change") == "reload", t0, 2)
        r["reload_seen"] = ev is not None
        st = s.seats()
        r["devices_after_reload"] = {n: len(v["devices"]) for n, v in st.items()}
        # recovery
        c.send(dev["p"].id, wl.VPTR_DESTROY)
        c.send(dev["k"].id, wl.VKBD_DESTROY)
        c.roundtrip()
        make_devices()
        bring_up()
        st = s.seats()
        r["devices_after_recovery"] = {n: len(v["devices"]) for n, v in st.items()}
        r["outputs_after_recovery"] = {o["name"]: o["rect"]["x"] for o in s.msg(kind="get_outputs")}
        w2 = launch("2")
        r["app2_ws"] = w2["_ws"]
        r["app2_title"] = agent_type(w2, "two")
        r["app1_title_after"] = agent_type(w1, "more")
        uk.type("u3")
        time.sleep(0.3)
        r["A_text"] = text_of(log, "A")
        r["seat0_focus_is_A"] = s.seats()["seat0"]["focus"] == wa["id"]
        r["pass"] = (r["app1_ws"] == r["app2_ws"] == "cus-agent" and r["app1_title"] == "T:one"
                     and r["app2_title"] == "T:two" and r["app1_title_after"] == "T:onemore"
                     and r["A_text"] == "u1u2u3" and r["seat0_focus_is_A"]
                     and r["outputs_after_recovery"][saved["agent"]] == 100000)
    print(json.dumps(r))


if __name__ == "__main__":
    main()

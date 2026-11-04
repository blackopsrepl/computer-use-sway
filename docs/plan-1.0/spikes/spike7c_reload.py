"""Spike 7c: reload survival.

  (a) runtime seat, reload, recover, then a window spawned AFTER recovery:
      does it accept agent typing? (separates client-side seat handling from
      compositor-side state)
  (b) config-declared agent seat: reload -> are devices still on agent, and do
      PRE-EXISTING gtk3 / gtk4 / qt6 windows still accept agent typing?
Typing is verified through window titles (targets.py apps) or the oracle log.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, click, text_of  # noqa: E402

RUNTIME_BASE = "xwayland disable\noutput HEADLESS-1 resolution 1280x800 position 0 0\nseat seat0 fallback true\n"
CONFIG_BASE = RUNTIME_BASE + "seat agent fallback false\n"


def wait_seat(c, name, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c.roundtrip()
        seats = c.seats()
        if name in seats:
            return seats
        time.sleep(0.02)
    raise RuntimeError(f"wl_seat {name} never appeared")


def title_of(s, con_id):
    return next((w.get("name") for w in s.windows() if w["id"] == con_id), None)


def agent_types_into(s, ap, ak, win, token):
    r = win["rect"]
    click(ap, r["x"] + r["width"] // 2, r["y"] + 140)  # the entry sits 120px from the top
    time.sleep(0.2)
    ak.type(token)
    time.sleep(0.5)
    return token in (title_of(s, win["id"]) or "")


def part_a():
    sbx.BASE_CONFIG = RUNTIME_BASE
    out = {}
    with sbx.Sandbox("spike7c-a") as s:
        root = os.path.join(s.root, "assets")
        targets.write_assets(root)
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        s.msg("seat agent fallback false")
        seats = wait_seat(c, "agent")
        ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"])
        argv, env = targets.argv("gtk3", root)
        s.spawn(argv, **env)
        time.sleep(1.5)
        old = next(w for w in s.windows() if "T:" in (w.get("name") or ""))
        out["old_before_reload"] = agent_types_into(s, ap, ak, old, "pre")
        s.msg("reload")
        time.sleep(0.5)
        c.send(ap.id, wl.VPTR_DESTROY)
        c.send(ak.id, wl.VKBD_DESTROY)
        c.roundtrip()
        s.msg("seat agent fallback false")
        seats = wait_seat(c, "agent")
        ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"])
        time.sleep(0.3)
        s.spawn(argv, **env)
        time.sleep(1.5)
        new = next(w for w in s.windows() if "T:" in (w.get("name") or "") and w["id"] != old["id"])
        out["new_after_recovery"] = agent_types_into(s, ap, ak, new, "new")
        out["old_after_recovery"] = agent_types_into(s, ap, ak, old, "old")
    return out


def part_b(name):
    sbx.BASE_CONFIG = CONFIG_BASE
    out = {"target": name}
    with sbx.Sandbox(f"spike7c-b-{name}") as s:
        root = os.path.join(s.root, "assets")
        targets.write_assets(root)
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = wait_seat(c, "agent")
        ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"])
        argv, env = targets.argv(name, root)
        s.spawn(argv, **env)
        deadline = time.monotonic() + 20
        win = None
        while not win and time.monotonic() < deadline:
            win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
            time.sleep(0.3)
        time.sleep(1.0)
        out["before_reload"] = agent_types_into(s, ap, ak, win, "pre")
        s.msg("reload")
        time.sleep(0.8)
        st = s.seats()
        out["agent_devices_after_reload"] = [d["identifier"] for d in st.get("agent", {}).get("devices", [])]
        out["seat0_devices_after_reload"] = [d["identifier"] for d in st.get("seat0", {}).get("devices", [])]
        out["agent_seat_exists_after_reload"] = "agent" in st
        out["wl_seat_names_after_reload"] = sorted(wait_seat(c, "agent"))
        # recovery: recreate devices on the SAME (persistent) wl_seat object
        c.send(ap.id, wl.VPTR_DESTROY)
        c.send(ak.id, wl.VKBD_DESTROY)
        c.roundtrip()
        ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"])
        time.sleep(0.3)
        st = s.seats()
        out["agent_devices_after_recovery"] = [d["identifier"] for d in st["agent"]["devices"]]
        out["seat0_devices_after_recovery"] = [d["identifier"] for d in st["seat0"]["devices"]]
        out["preexisting_window_after_recovery"] = agent_types_into(s, ap, ak, win, "post")
        out["title"] = title_of(s, win["id"])
    return out


if __name__ == "__main__":
    for n in ("gtk3", "gtk4", "qt6"):
        print(json.dumps(part_b(n)))

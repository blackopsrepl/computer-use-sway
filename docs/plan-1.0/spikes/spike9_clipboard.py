"""Spike 9: per-seat clipboard. wl-copy --seat agent + ctrl+v on the agent seat
pastes the agent's text; seat0's clipboard is untouched; wl-paste --seat reads back."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import click  # noqa: E402

r = {}
for name in sys.argv[1:] or ["gtk3", "qt6"]:
    with sbx.Sandbox(f"spike9-{name}") as s:
        root = os.path.join(s.root, "assets")
        targets.write_assets(root)
        argv, env = targets.argv(name, root)
        s.spawn(argv, **env)
        time.sleep(6 if name == "firefox" else 2)
        win = next(w for w in s.windows() if "T:" in (w.get("name") or ""))
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = c.seats()
        ap, ak = wl.VirtualPointer(c, seats["agent"]), wl.VirtualKeyboard(c, seats["agent"])
        time.sleep(0.3)
        subprocess.run(["wl-copy", "user-clip"], env=s.env, check=True)
        subprocess.run(["wl-copy", "--seat", "agent", "agent-clip"], env=s.env, check=True)
        time.sleep(0.3)
        rc = win["rect"]
        if name == "firefox":
            click(ap, rc["x"] + 300, rc["y"] + 290)
        else:
            click(ap, rc["x"] + rc["width"] // 2, rc["y"] + 140)
        ak.chord("v", ["ctrl"])
        time.sleep(0.5)
        paste = lambda *a: subprocess.run(["wl-paste", "--no-newline", *a], env=s.env,  # noqa: E731
                                          capture_output=True, text=True, timeout=5).stdout
        r[name] = {"title": next(w.get("name") for w in s.windows() if w["id"] == win["id"]),
                   "seat0_clipboard": paste(), "agent_clipboard": paste("--seat", "agent")}
        r[name]["pass"] = (r[name]["title"].startswith("T:agent-clip") and r[name]["seat0_clipboard"] == "user-clip"
                           and r[name]["agent_clipboard"] == "agent-clip")
print(json.dumps(r, indent=1))

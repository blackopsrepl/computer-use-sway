"""Spike 8: Unicode + shifted ASCII + modifier chords via the generated keymap,
on the agent seat. Read back through the window title."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import click  # noqa: E402

TEXT = "Ab9 !@#$%^&*()_+{}|:\"<>? é ü ß 日本 €"
r = {}
for name in sys.argv[1:] or ["gtk3", "gtk4", "qt6", "firefox"]:
    with sbx.Sandbox(f"spike8-{name}") as s:
        root = os.path.join(s.root, "assets")
        targets.write_assets(root)
        argv, env = targets.argv(name, root)
        s.spawn(argv, **env)
        win = None
        for _ in range(100):
            win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
            if win:
                break
            time.sleep(0.3)
        time.sleep(1.5)
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = c.seats()
        seat = "seat0" if name in targets.SINGLE_SEAT else "agent"
        ap, ak = wl.VirtualPointer(c, seats[seat]), wl.VirtualKeyboard(c, seats[seat])
        time.sleep(0.3)
        rc = win["rect"]
        if name == "firefox":
            click(ap, rc["x"] + 300, rc["y"] + 290); time.sleep(0.3)
        else:
            click(ap, rc["x"] + rc["width"] // 2, rc["y"] + 140)
        time.sleep(0.2)
        if name == "firefox":
            steps = {}
            for chunk in ("abc", "A", "!@", " é", " 日本"):
                ak.type(chunk)
                time.sleep(0.4)
                steps[chunk] = next(w.get("name") for w in s.windows() if w["id"] == win["id"])
            r["firefox_steps"] = steps
            import subprocess
            subprocess.run(["grim", os.path.expanduser("~/.hermes/cache/scratch/ff8.png")], env=s.env)
            ak.chord("a", ["ctrl"]); ak.chord("BackSpace"); time.sleep(0.3)
        ak.type(TEXT)
        time.sleep(0.6)
        t1 = next(w.get("name") for w in s.windows() if w["id"] == win["id"])
        # select-all + delete through modifier chords, then one char
        ak.chord("a", ["ctrl"])
        ak.chord("BackSpace")
        ak.type("z")
        time.sleep(0.5)
        t2 = next(w.get("name") for w in s.windows() if w["id"] == win["id"])
        r[name] = {"typed_ok": TEXT in (t1 or ""), "title": t1, "after_ctrl_a_bs_z": t2,
                   "chord_ok": (t2 or "").startswith("T:z")}
print(json.dumps(r, ensure_ascii=False, indent=1))

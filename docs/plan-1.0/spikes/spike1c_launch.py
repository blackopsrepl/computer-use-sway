"""Spike 1c: launch an app onto the agent workspace without touching seat0.

For each placement strategy, record: workspace the window mapped on, seat0
focus before/after, agent focus after, and whether the window was ever seen
on the user's workspace (window::new event workspace vs final).
"""
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, click  # noqa: E402

CFG = "workspace agent output HEADLESS-2\n"


class IpcEvents(threading.Thread):
    """Raw Sway IPC subscription (stdlib): i3-ipc magic + len + type."""

    def __init__(self, path, kinds):
        super().__init__(daemon=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        payload = json.dumps(kinds).encode()
        self.s.sendall(b"i3-ipc" + struct.pack("=II", len(payload), 2) + payload)
        self.events = []

    def _read(self):
        head = b""
        while len(head) < 14:
            head += self.s.recv(14 - len(head))
        length, kind = struct.unpack("=II", head[6:])
        body = b""
        while len(body) < length:
            body += self.s.recv(length - len(body))
        return kind, json.loads(body)

    def run(self):
        self._read()  # subscribe reply
        while True:
            try:
                kind, ev = self._read()
            except OSError:
                return
            self.events.append((time.monotonic(), kind & 0x7FFFFFFF, ev))


r = {}
with sbx.Sandbox("spike1c", extra_config=CFG) as s:
    log = os.path.join(s.root, "oracle.jsonl")
    s.msg("create_output")
    s.msg("output HEADLESS-2 position 100000 0 resolution 1280x800")
    s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
    wa = s.window("oracle-A")
    time.sleep(0.6)
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    up = wl.VirtualPointer(c, seats["seat0"])
    ap = wl.VirtualPointer(c, seats["agent"])
    click(up, *center(s.window("oracle-A"), last_geo(log, "A"), "entry"), extent=(101280, 800))
    ev = IpcEvents(s.swaysock, ["window", "workspace"])
    ev.start()

    def focus():
        st = s.seats()
        return {k: st[k]["focus"] for k in st}

    def trial(name, pre_cmds):
        f0 = focus()
        p = s.spawn([PY, f"{HERE}/oracle_gtk3.py", name, log])
        for cmd in pre_cmds:
            s.msg(cmd.replace("PID", str(p.pid)))
        t0 = time.monotonic()
        win = s.window(f"oracle-{name}")
        time.sleep(0.4)
        win = s.window(f"oracle-{name}")
        news = [e for t, k, e in ev.events if t >= t0 - 1 and e.get("container", {}).get("id") == win["id"]]
        return {
            "final_ws": win["_ws"],
            "seat0_before": f0["seat0"], "seat0_after": focus()["seat0"],
            "agent_after": focus()["agent"], "win_id": win["id"],
            "events": [(e["change"]) for e in news],
            "user_ws_changes": [e["change"] for t, k, e in ev.events if t >= t0 and k == 0 and "current" in e],
        }

    r["plain"] = trial("P", [])
    click(up, *center(s.window("oracle-A"), last_geo(log, "A"), "entry"), extent=(101280, 800))
    r["assign_pid"] = trial("Q", ["assign [pid=PID] workspace agent"])
    r["assign_pid_nofocus"] = trial("R", ["no_focus [pid=PID]", "assign [pid=PID] workspace agent"])
    r["for_window_pid"] = trial("S", ["for_window [pid=PID] move container to workspace agent"])
    r["seat0_is_A"] = wa["id"]
    r["workspaces"] = [(x["name"], x["output"], x["visible"], x["focused"]) for x in s.msg(kind="get_workspaces")]

print(json.dumps(r, indent=1))

"""Spike 12: per-seat user-idle detection via ext_idle_notifier_v1.

  I1  seat0 notification fires `idled` after the timeout with no input
  I2  seat0 virtual key -> `resumed`
  I3  agent-seat input does NOT resume seat0's notification
Opcodes: notifier requests destroy=0 get_idle_notification(id,timeout,seat)=1;
notification events idled=0 resumed=1.
"""
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402

r = {}
with sbx.Sandbox("spike12") as s:
    c = wl.Connection(runtime_dir=s.root, display="wayland-1")
    seats = c.seats()
    notifier, _ = c.bind("ext_idle_notifier_v1", 1)
    events = []

    def watch(seat_name, timeout_ms):
        nid = c.new_id()
        c.send(notifier, 1, struct.pack("=III", nid, timeout_ms, seats[seat_name]))
        c.handlers[nid] = lambda op, body, n=seat_name: events.append((time.monotonic(), n, "idled" if op == 0 else "resumed"))
        return nid

    t0 = time.monotonic()
    watch("seat0", 400)
    watch("agent", 400)
    uk, ak = wl.VirtualKeyboard(c, seats["seat0"]), wl.VirtualKeyboard(c, seats["agent"])

    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            c.dispatch(0.05)

    pump(0.8)
    r["I1_after_quiet"] = [(round(t - t0, 2), n, e) for t, n, e in events]
    events.clear()
    t1 = time.monotonic()
    ak.type("x")  # agent activity only
    pump(0.2)
    r["I3_after_agent_input"] = [(round(t - t1, 2), n, e) for t, n, e in events]
    events.clear()
    t2 = time.monotonic()
    uk.type("y")  # user activity
    pump(0.2)
    r["I2_after_user_input"] = [(round(t - t2, 2), n, e) for t, n, e in events]
    pump(0.6)
    r["I1b_idle_again"] = [(round(t - t2, 2), n, e) for t, n, e in events]
    r["pass"] = (("seat0", "idled") in [(n, e) for _, n, e in r["I1_after_quiet"]]
                 and ("seat0", "resumed") not in [(n, e) for _, n, e in r["I3_after_agent_input"]]
                 and ("seat0", "resumed") in [(n, e) for _, n, e in r["I2_after_user_input"]])
print(json.dumps(r))

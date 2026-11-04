#!/usr/bin/python3
"""GTK3 oracle window: logs every input it receives and its own geometry.

Run with the *system* python (needs gi). Usage:
  oracle_gtk3.py NAME LOGFILE [--csd]
Each line in LOGFILE is JSON: {"t": monotonic, "win": NAME, "ev": ..., ...}.
"""
import json
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

name, log_path = sys.argv[1], sys.argv[2]
csd = "--csd" in sys.argv
GLib.set_prgname(f"oracle-{name}")
log = open(log_path, "a", buffering=1)


def emit(ev, **kw):
    log.write(json.dumps({"t": time.monotonic(), "win": name, "ev": ev, **kw}) + "\n")


win = Gtk.Window(title=f"Oracle {name}")
win.set_default_size(600, 400)
if csd:
    bar = Gtk.HeaderBar(title=f"Oracle {name}", show_close_button=True)
    win.set_titlebar(bar)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24)
entry = Gtk.Entry()
entry.set_name("entry")
entry.get_accessible().set_name(f"entry-{name}")
button = Gtk.Button(label=f"Press {name}")
button.get_accessible().set_name(f"button-{name}")
check = Gtk.CheckButton(label=f"Check {name}")
area = Gtk.EventBox()
area.set_size_request(300, 150)
area.add(Gtk.Label(label="drag area"))
for w in (entry, button, check, area):
    box.pack_start(w, False, False, 0)
win.add(box)

win.connect("focus-in-event", lambda *_: emit("focus-in") or False)
win.connect("focus-out-event", lambda *_: emit("focus-out") or False)
win.connect("key-press-event", lambda w, e: emit("key", keyval=e.keyval, string=e.string) or False)
win.connect("button-press-event", lambda w, e: emit("press", x=e.x, y=e.y, button=e.button) or False)
win.connect("button-release-event", lambda w, e: emit("release", x=e.x, y=e.y, button=e.button) or False)
area.add_events(0x4 | 0x100 | 0x200)  # POINTER_MOTION | BUTTON_PRESS | BUTTON_RELEASE
area.connect("motion-notify-event", lambda w, e: emit("motion", x=e.x, y=e.y, held=bool(e.state & 0x100)) or False)
win.add_events(0x200000 | 0x800000)  # SCROLL_MASK | SMOOTH_SCROLL_MASK
win.connect("scroll-event", lambda w, e: emit("scroll", direction=int(e.direction), dx=e.delta_x, dy=e.delta_y) or False)
button.connect("clicked", lambda *_: emit("clicked"))
check.connect("toggled", lambda c: emit("toggled", active=c.get_active()))
entry.connect("changed", lambda e: emit("text", text=e.get_text()))


def report_geometry(*_):
    out = {}
    for label, w in (("entry", entry), ("button", button), ("check", check), ("area", area)):
        a = w.get_allocation()
        x, y = w.translate_coordinates(win, 0, 0)
        out[label] = {"x": x, "y": y, "w": a.width, "h": a.height}
    gdkwin = win.get_window()
    emit("geometry", widgets=out, csd=csd,
         window_size=list(win.get_size()),
         surface_size=[gdkwin.get_width(), gdkwin.get_height()] if gdkwin else None)
    return False


win.connect("size-allocate", lambda *_: GLib.timeout_add(150, report_geometry))
win.connect("destroy", Gtk.main_quit)
win.show_all()
emit("ready")
Gtk.main()

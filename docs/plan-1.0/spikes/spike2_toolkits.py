"""Spike 2: which toolkits accept input from a second seat.

Setup per app: the user (seat0) sits in a GTK3 oracle window A and keeps
typing; the agent seat clicks into the app under test (tiled next to A) and
types a token. Pass = the app received the agent's token (read back through
its window title) AND A received all user text AND seat0 focus never moved.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import sbx  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import PY, HERE, center, last_geo, text_of, type_text, click  # noqa: E402

PAGE = ("data:text/html,<title>T:</title><body style='margin:0'>"
        "<input id=i autofocus style='width:100%;height:200px;font-size:40px' "
        "oninput=\"document.title='T:'+this.value\"></body>")

QML = """import QtQuick
import QtQuick.Controls
ApplicationWindow { id: w; visible: true; width: 600; height: 400; title: "T:" + f.text
  TextField { id: f; anchors.fill: parent; font.pixelSize: 40 } }
"""

GTK4 = """import gi, sys
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
GLib.set_prgname('oracle-gtk4')
def act(app):
    w = Gtk.ApplicationWindow(application=app, title='T:')
    e = Gtk.Entry(); e.set_vexpand(True); w.set_child(e)
    e.connect('changed', lambda x: w.set_title('T:' + x.get_text()))
    w.present()
a = Gtk.Application(application_id='org.test.oracle4'); a.connect('activate', act); a.run([])
"""

KITTY_SH = "printf '\\033]2;T:\\007'; read x; printf '\\033]2;T:%s\\007' \"$x\"; sleep 30"


def page_file(root):
    path = f"{root}/page.html"
    with open(path, "w") as f:
        f.write(PAGE[len("data:text/html,"):])
    return "file://" + path


def apps(root):
    open(f"{root}/o.qml", "w").write(QML)
    open(f"{root}/gtk4.py", "w").write(GTK4)
    prof = f"{root}/ff"
    os.makedirs(prof, exist_ok=True)
    open(f"{prof}/user.js", "w").write(
        'user_pref("browser.aboutwelcome.enabled", false);\n'
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
        'user_pref("trailhead.firstrun.didSeeAboutWelcome", true);\n')
    return {
        "gtk4": [PY, f"{root}/gtk4.py"],
        "qt6": ["qml6", f"{root}/o.qml"],
        "chromium": ["chromium", "--ozone-platform=wayland", f"--user-data-dir={root}/chr",
                     "--no-first-run", "--no-default-browser-check", f"--app={PAGE}"],
        "firefox": ["firefox", "--no-remote", "--new-instance", "--profile", prof, page_file(root)],
        "electron": ["/home/pvd/.hermes/cache/scratch/electron-oracle/electron-oracle", "--ozone-platform=wayland",
                     f"--user-data-dir={root}/el"],
        "kitty": ["kitty", "--class", "kitty-oracle", "-o", "linux_display_server=wayland", "sh", "-c", KITTY_SH],
    }


def run(name, control=False):
    out = {}
    with sbx.Sandbox(f"spike2-{name}") as s:
        log = os.path.join(s.root, "oracle.jsonl")
        s.spawn([PY, f"{HERE}/oracle_gtk3.py", "A", log])
        s.window("oracle-A")
        argv = apps(s.root)[name]
        s.spawn(argv, **({"TARGET_HTML": page_file(s.root)[7:]} if name == "electron" else {}))
        deadline = time.monotonic() + 30
        app = None
        while time.monotonic() < deadline and app is None:
            for w in s.windows():
                if w.get("app_id") != "oracle-A" and "T:" in (w.get("name") or ""):
                    app = w
            time.sleep(0.3)
        if app is None:
            out["error"] = "app window with T: title never mapped: " + str(
                [(w.get("app_id"), w.get("name")) for w in s.windows()])
            return out
        time.sleep(2.0)
        wa, app = s.window("oracle-A"), next(w for w in s.windows() if w["id"] == app["id"])
        out["app_id"] = app.get("app_id")
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = c.seats()
        syms = sorted({wl.keysym_for(ch) for ch in "abcdefghijklmnopqrstuvwxyz0123456789"} | {"Return"})
        up, uk = wl.VirtualPointer(c, seats["seat0"]), wl.VirtualKeyboard(c, seats["seat0"], syms)
        aseat = seats["seat0"] if control else seats["agent"]
        ap, ak = wl.VirtualPointer(c, aseat), wl.VirtualKeyboard(c, aseat, syms)
        time.sleep(0.3)
        click(up, *center(wa, last_geo(log, "A"), "entry"))
        type_text(uk, "user1")
        r = app["rect"]
        click(ap, r["x"] + r["width"] // 2, r["y"] + (160 if name == "firefox" else 60))
        time.sleep(0.3)
        type_text(ak, "agent7")
        type_text(uk, "user2")
        if name == "kitty":
            ak.tap("Return")
        time.sleep(1.0)
        st = s.seats()
        app_now = next(w for w in s.windows() if w["id"] == app["id"])
        out["app_title"] = app_now.get("name")
        out["A_text"] = text_of(log, "A")
        out["seat_focus"] = {k: ("A" if st[k]["focus"] == wa["id"] else "app" if st[k]["focus"] == app["id"] else st[k]["focus"]) for k in st}
        out["agent_text_arrived"] = "agent7" in (out["app_title"] or "")
        out["pass"] = out["agent_text_arrived"] and out["A_text"] == "user1user2" and out["seat_focus"]["seat0"] == "A"
    return out


if __name__ == "__main__":
    control = "--control" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or ["gtk4", "qt6", "chromium", "firefox", "kitty"]
    print(json.dumps({n: run(n, control) for n in names}, indent=1))

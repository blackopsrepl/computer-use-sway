"""Target apps for the a11y/geometry spikes. Every target shows:
  - a text entry named "Field"     (typing sets window title "T:<text>")
  - a push button named "Go"       (activating sets window title "T:clicked")
so effects are read back through the Sway window title, toolkit-independent.
"""
import json
import os

PY = "/usr/bin/python3"
ELECTRON = os.path.expanduser("~/.hermes/cache/scratch/electron-oracle/electron-oracle")

HTML = """<!doctype html><title>T:</title>
<body style="margin:0;font-size:32px">
<div style="height:120px;background:#ddd">spacer</div>
<input aria-label="Field" style="width:400px;height:60px;font-size:32px;margin-left:150px"
 oninput="document.title='T:'+this.value">
<button style="width:200px;height:80px;margin-left:300px;margin-top:40px;font-size:32px"
 onclick="document.title='T:clicked'">Go</button>
<button style="width:200px;height:80px;margin-left:40px;font-size:32px"
 onclick="event.stopPropagation();document.title='T:hit'">Hit</button>
<script>document.addEventListener('click', e => { if (!e.target.closest('button'))
 document.title = 'T:miss ' + e.clientX + ',' + e.clientY; });</script>
</body>"""

GTK3 = """import gi, sys
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
GLib.set_prgname('t-gtk3' + ('-csd' if '--csd' in sys.argv else ''))
w = Gtk.Window(title='T:'); w.set_default_size(700, 500)
if '--csd' in sys.argv:
    w.set_titlebar(Gtk.HeaderBar(title='T:', show_close_button=True))
b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=30, margin=120)
e = Gtk.Entry(); e.get_accessible().set_name('Field')
g = Gtk.Button(label='Go'); h = Gtk.Button(label='Hit')
e.connect('changed', lambda x: w.set_title('T:' + x.get_text()))
g.connect('clicked', lambda x: w.set_title('T:clicked'))
h.connect('clicked', lambda x: w.set_title('T:hit'))
w.connect('button-press-event', lambda x, ev: w.set_title('T:miss %d,%d' % (ev.x, ev.y)) or False)
b.pack_start(e, False, False, 0); b.pack_start(g, False, False, 0); b.pack_start(h, False, False, 0)
w.add(b); w.connect('destroy', Gtk.main_quit); w.show_all(); Gtk.main()
"""

GTK4 = """import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
GLib.set_prgname('t-gtk4')
def act(app):
    w = Gtk.ApplicationWindow(application=app, title='T:'); w.set_default_size(700, 500)
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=30)
    for m in ('top', 'bottom', 'start', 'end'):
        getattr(b, 'set_margin_' + m)(120)
    e = Gtk.Entry(); e.update_property([Gtk.AccessibleProperty.LABEL], ['Field'])
    g = Gtk.Button(label='Go'); h = Gtk.Button(label='Hit')
    e.connect('changed', lambda x: w.set_title('T:' + x.get_text()))
    g.connect('clicked', lambda x: w.set_title('T:clicked'))
    h.connect('clicked', lambda x: w.set_title('T:hit'))
    ck = Gtk.GestureClick(); ck.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    ck.connect('pressed', lambda c, n, x, y: w.set_title('T:miss %d,%d' % (x, y)))
    w.add_controller(ck)
    b.append(e); b.append(g); b.append(h); w.set_child(b); w.present()
a = Gtk.Application(application_id='org.test.tgtk4'); a.connect('activate', act); a.run([])
"""

QML = """import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ApplicationWindow { id: w; visible: true; width: 700; height: 500; title: "T:"
  MouseArea { anchors.fill: parent; z: -1; onPressed: (m) => w.title = "T:miss " + Math.round(m.x) + "," + Math.round(m.y) }
  ColumnLayout { anchors.fill: parent; anchors.margins: 120; spacing: 30
    TextField { id: f; Accessible.name: "Field"; Layout.fillWidth: true
      onTextChanged: w.title = "T:" + text }
    Button { text: "Go"; Accessible.name: "Go"; onClicked: w.title = "T:clicked" }
    Button { text: "Hit"; Accessible.name: "Hit"; onClicked: w.title = "T:hit" }
    Item { Layout.fillHeight: true } } }
"""

ELECTRON_MAIN = """const { app, BrowserWindow } = require('electron');
if (process.env.TARGET_A11Y_API === '1') app.setAccessibilitySupportEnabled(true);
app.whenReady().then(() => {
  const w = new BrowserWindow({ width: 700, height: 500 });
  w.loadFile(process.env.TARGET_HTML);
});
"""


def write_assets(root):
    os.makedirs(root, exist_ok=True)
    open(f"{root}/page.html", "w").write(HTML)
    open(f"{root}/gtk3.py", "w").write(GTK3)
    open(f"{root}/gtk4.py", "w").write(GTK4)
    open(f"{root}/t.qml", "w").write(QML)
    app_dir = os.path.join(os.path.dirname(ELECTRON), "resources", "app")
    os.makedirs(app_dir, exist_ok=True)
    open(f"{app_dir}/package.json", "w").write(json.dumps({"name": "t-electron", "main": "main.js"}))
    open(f"{app_dir}/main.js", "w").write(ELECTRON_MAIN)
    prof = f"{root}/ff"
    os.makedirs(prof, exist_ok=True)
    open(f"{prof}/user.js", "w").write(
        'user_pref("browser.aboutwelcome.enabled", false);\n'
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n')


def argv(name, root):
    page = f"file://{root}/page.html"
    return {
        "gtk3": ([PY, f"{root}/gtk3.py"], {}),
        "gtk3-csd": ([PY, f"{root}/gtk3.py", "--csd"], {}),
        "gtk4": ([PY, f"{root}/gtk4.py"], {}),
        "qt6": (["qml6", f"{root}/t.qml"], {"QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1"}),
        "qt6-noforce": (["qml6", f"{root}/t.qml"], {}),
        "firefox": (["firefox", "--no-remote", "--new-instance", "--profile", f"{root}/ff", page], {}),
        "chromium": (["chromium", "--ozone-platform=wayland", f"--user-data-dir={root}/chr", "--no-first-run",
                      "--no-default-browser-check", "--force-renderer-accessibility", f"--app={page}"], {}),
        "chromium-noforce": (["chromium", "--ozone-platform=wayland", f"--user-data-dir={root}/chr2",
                              "--no-first-run", "--no-default-browser-check", f"--app={page}"], {}),
        "electron": ([ELECTRON, "--ozone-platform=wayland", "--force-renderer-accessibility",
                      f"--user-data-dir={root}/el"], {"TARGET_HTML": f"{root}/page.html"}),
        "electron-api": ([ELECTRON, "--ozone-platform=wayland", f"--user-data-dir={root}/el2"],
                         {"TARGET_HTML": f"{root}/page.html", "TARGET_A11Y_API": "1"}),
        "chromium-env": (["chromium", "--ozone-platform=wayland", f"--user-data-dir={root}/chr3",
                          "--no-first-run", "--no-default-browser-check", f"--app={page}"],
                         {"ACCESSIBILITY_ENABLED": "1"}),
        "electron-env": ([ELECTRON, "--ozone-platform=wayland", f"--user-data-dir={root}/el4"],
                         {"TARGET_HTML": f"{root}/page.html", "ACCESSIBILITY_ENABLED": "1"}),
        "electron-noforce": ([ELECTRON, "--ozone-platform=wayland", f"--user-data-dir={root}/el3"],
                             {"TARGET_HTML": f"{root}/page.html"}),
    }[name]


# Which seat can drive the toolkit's raw input (spike 2 result).
SINGLE_SEAT = {"chromium", "chromium-noforce", "chromium-env", "electron", "electron-api", "electron-noforce", "electron-env"}

#!/usr/bin/python3
"""Benchmark target app (GTK3, system python with gi).

Usage: app.py FAMILY PARAM LOGFILE
Families: forms, dialogs, menus, lists, multiwindow, text. The window title
starts at "B:" and becomes "B:done <result>" when the task is completed, which
is what the oracle checks. Every named widget's box (layout of its surface,
in surface coordinates) is logged as JSON lines, so the legacy arm can click
with perfect, oracle-given grounding.
"""

import json
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

family, param, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
GLib.set_prgname(f"bench-{family}")
log = open(log_path, "a", buffering=1)
named: dict[str, Gtk.Widget] = {}


def emit(ev, **kw):
    log.write(json.dumps({"t": time.monotonic(), "ev": ev, **kw}) + "\n")


def name(widget, label):
    widget.get_accessible().set_name(label)
    named[label] = widget
    return widget


def geometry(*_):
    boxes = {}
    for label, w in named.items():
        if not w.get_mapped():
            continue
        top = w.get_toplevel()
        coords = w.translate_coordinates(top, 0, 0)
        if coords is None:
            continue
        a = w.get_allocation()
        popup = isinstance(w, Gtk.MenuItem) and top is not main
        box = {"x": coords[0], "y": coords[1], "w": a.width, "h": a.height, "surface": top.get_title(),
               "popup": popup}
        if popup:
            # On Wayland a popup's GDK origin is relative to the menu item it
            # hangs from (measured against a screenshot); report that anchor
            # in main-window coordinates.
            gdk = top.get_window()
            menu = w.get_parent()
            anchor = menu.get_attach_widget() if isinstance(menu, Gtk.Menu) else None
            at = anchor.translate_coordinates(main, 0, 0) if anchor is not None else None
            if gdk is None or at is None:
                continue
            origin = gdk.get_origin()[1:]
            box["window_xy"] = [at[0] + origin[0] + coords[0], at[1] + origin[1] + coords[1]]
        boxes[label] = box
    emit("geometry", widgets=boxes)
    return False


def window(title, w=700, h=500):
    win = Gtk.Window(title=title)
    win.set_default_size(w, h)
    win.connect("destroy", Gtk.main_quit)
    win.connect("size-allocate", lambda *_: GLib.timeout_add(200, geometry))
    return win


def done(win, result):
    win.set_title(f"B:done {result}")


main = window("B:")
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=40)
main.add(box)

if family == "forms":
    entries = {}
    for label in ("Name", "Email"):
        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Label(label=label), False, False, 0)
        entries[label] = name(Gtk.Entry(), label)
        row.pack_start(entries[label], True, True, 0)
        box.pack_start(row, False, False, 0)
    sub = name(Gtk.CheckButton(label="Subscribe"), "Subscribe")
    box.pack_start(sub, False, False, 0)
    btn = name(Gtk.Button(label="Submit"), "Submit")
    btn.connect("clicked", lambda *_: done(main, f"{entries['Name'].get_text()}|{entries['Email'].get_text()}|"
                                                 f"{int(sub.get_active())}"))
    box.pack_start(btn, False, False, 0)

elif family == "dialogs":
    def ask(*_):
        dialog = Gtk.Dialog(title="Confirm", transient_for=main, modal=True)
        keep = name(dialog.add_button("Keep", 1), "Keep")
        drop = name(dialog.add_button("Delete", 2), "Delete")
        dialog.get_content_area().add(Gtk.Label(label=f"Delete {param}?"))
        dialog.connect("size-allocate", lambda *_: GLib.timeout_add(200, geometry))
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        done(main, "deleted" if response == 2 else "kept")
        del keep, drop
    btn = name(Gtk.Button(label=f"Delete {param}"), f"Delete {param}")
    btn.connect("clicked", ask)
    box.pack_start(btn, False, False, 0)

elif family == "menus":
    bar = Gtk.MenuBar()
    for top_label, items in (("File", ("New", "Open", "Export PDF", "Export PNG")),
                             ("Edit", ("Undo", "Redo", "Preferences"))):
        top = name(Gtk.MenuItem(label=top_label), top_label)
        menu = Gtk.Menu()
        for item_label in items:
            item = name(Gtk.MenuItem(label=item_label), item_label)
            item.connect("activate", lambda _w, lbl=item_label: done(main, lbl))
            menu.append(item)
        menu.connect("show", lambda *_: GLib.timeout_add(250, geometry))
        top.set_submenu(menu)
        bar.append(top)
    box.pack_start(bar, False, False, 0)

elif family == "lists":
    scroll = name(Gtk.ScrolledWindow(), "List")
    scroll.set_size_request(400, 260)
    listbox = Gtk.ListBox()
    for i in range(1, 41):
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=f"Item {i}", xalign=0)
        row.add(label)
        name(row, f"Item {i}")
        listbox.add(row)
    scroll.add(listbox)
    scroll.get_vadjustment().connect("value-changed", lambda *_: GLib.timeout_add(150, geometry))
    box.pack_start(scroll, False, False, 0)
    btn = name(Gtk.Button(label="Open"), "Open")
    btn.connect("clicked", lambda *_: done(main, (listbox.get_selected_row().get_child().get_text()
                                                  if listbox.get_selected_row() else "none")))
    box.pack_start(btn, False, False, 0)

elif family == "multiwindow":
    source = window("Source", 400, 200)
    source.add(name(Gtk.Label(label=f"Transfer code: {param}"), "Transfer code"))
    source.show_all()
    code = name(Gtk.Entry(), "Code")
    box.pack_start(code, False, False, 0)
    btn = name(Gtk.Button(label="Apply"), "Apply")
    btn.connect("clicked", lambda *_: done(main, code.get_text()))
    box.pack_start(btn, False, False, 0)

elif family == "text":
    # Words painted on a canvas: no accessible text, only pixels.
    words = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL", "INDIA"]
    area = Gtk.DrawingArea()
    area.set_size_request(600, 300)
    boxes = {}

    def draw(widget, cr):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        cr.select_font_face("Sans")
        cr.set_font_size(26)
        for i, word in enumerate(words):
            x, y = 30 + (i % 3) * 190, 60 + (i // 3) * 90
            ext = cr.text_extents(word)
            boxes[word] = (x + ext.x_bearing, y + ext.y_bearing, ext.width, ext.height)
            cr.move_to(x, y)
            cr.show_text(word)
        return False

    def press(widget, event):
        for word, (x, y, w, h) in boxes.items():
            if x - 4 <= event.x <= x + w + 4 and y - 4 <= event.y <= y + h + 4:
                done(main, word) if word == param else main.set_title(f"B:wrong {word}")
                return True
        main.set_title("B:miss")
        return True

    area.add_events(0x100)  # BUTTON_PRESS_MASK
    area.connect("draw", draw)
    area.connect("button-press-event", press)
    named["canvas"] = area
    box.pack_start(area, False, False, 0)

    def word_geometry():
        origin = area.translate_coordinates(main, 0, 0)
        if origin and boxes:
            emit("words", words={w: {"x": origin[0] + b[0], "y": origin[1] + b[1], "w": b[2], "h": b[3]}
                                 for w, b in boxes.items()})
        return False
    main.connect("size-allocate", lambda *_: GLib.timeout_add(300, word_geometry))

main.show_all()
emit("ready")
Gtk.main()

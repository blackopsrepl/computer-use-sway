"""Shared helpers for the seat spikes."""
import os
import time

import sbx
import wl

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "/usr/bin/python3"


def center(win, geo, widget):
    r, wr = win["rect"], win["window_rect"]
    g = geo["widgets"][widget]
    return r["x"] + wr["x"] + g["x"] + g["w"] // 2, r["y"] + wr["y"] + g["y"] + g["h"] // 2


def last_geo(log, name):
    gs = [e for e in sbx.read_log(log) if e["win"] == name and e["ev"] == "geometry"]
    return gs[-1] if gs else None


def text_of(log, name):
    ts = [e["text"] for e in sbx.read_log(log) if e["win"] == name and e["ev"] == "text"]
    return ts[-1] if ts else ""


def type_text(kbd, text):
    for ch in text:
        kbd.tap(wl.keysym_for(ch))


def click(ptr, x, y, extent=(1280, 800)):
    ptr.move_abs(x, y, *extent)
    ptr.flush()
    time.sleep(0.05)
    ptr.button(wl.BTN_LEFT, True)
    ptr.flush()
    time.sleep(0.03)
    ptr.button(wl.BTN_LEFT, False)
    ptr.flush()
    time.sleep(0.15)

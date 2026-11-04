"""Spike 4: AT-SPI over the stdlib D-Bus client, per toolkit.

Proves, per target app:
  A1  the app's AT-SPI tree is reachable and maps to its Sway window by pid
  A2  which coordinate formula turns AT-SPI extents into Sway layout points
      (verified by clicking the computed point and reading the app's effect)
  A3  semantic actions work with no input device: EditableText.SetTextContents
      and Action.DoAction
  A4  how a11y gets switched on for toolkits that gate it
Usage: spike4_a11y.py TARGET [--scale 2] [--floating] [--status-before]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import atspi  # noqa: E402
import sbx  # noqa: E402
import targets  # noqa: E402
import wl  # noqa: E402
from spike1_seats_lib import click  # noqa: E402

BUTTON_ROLES = {"push button", "button"}
ENTRY_ROLES = {"entry", "text", "password text", "editbar", "text box"}


def ancestors(pid):
    out = []
    while pid > 1:
        out.append(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError):
            break
    return out


def find_app(a, pid, deadline):
    while time.monotonic() < deadline:
        for app in a.apps():
            try:
                apid = a.pid(app[0])
            except Exception:
                continue
            if apid == pid or pid in ancestors(apid):
                return app, apid
        time.sleep(0.3)
    return None, None


def find_nodes(a, app, deadline):
    while time.monotonic() < deadline:
        found = {}
        count = 0
        for ref, depth, role, name, st, ifaces in a.walk(app, max_nodes=20000, prune_hidden=False):
            count += 1
            if role in ("frame", "window") and "frame" not in found:
                found["frame"] = ref
            if "showing" not in st and role not in ("frame", "window"):
                continue
            if (role in BUTTON_ROLES and name in ("Go", "Hit")) or (role in ENTRY_ROLES and name == "Field"):
                try:
                    e = a.extents(ref, 1)
                except Exception:
                    continue
                if e[2] <= 0 or e[3] <= 0:
                    continue
                found["entry" if role in ENTRY_ROLES else ("button" if name == "Go" else "hit")] = ref
        if {"frame", "button", "entry", "hit"} <= set(found):
            return found, count
        time.sleep(0.5)
    return found, count


def title(s, con_id):
    for w in s.windows():
        if w["id"] == con_id:
            return w.get("name") or ""
    return None


def wait_title(s, con_id, pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    t = None
    while time.monotonic() < deadline:
        t = title(s, con_id)
        if t is not None and pred(t):
            return t, True
        time.sleep(0.1)
    return t, False


def run(name, scale=1, floating=False, status_before=False):
    r = {"target": name, "scale": scale, "floating": floating}
    extra = ""
    if scale != 1:
        extra += f"output HEADLESS-1 resolution {1280 * scale}x{800 * scale} scale {scale}\n"
    if floating:
        extra += "for_window [title=\"^T:\"] floating enable, resize set 900 700, move position 150 50\n"
    with sbx.Sandbox(f"spike4-{name}", extra_config=extra) as s:
        root = os.path.join(s.root, "assets")
        targets.write_assets(root)
        a = atspi.Atspi(s.env["DBUS_SESSION_BUS_ADDRESS"])
        if status_before:
            a.set_enabled(True)
        r["status"] = a.get_enabled()
        argv, env = targets.argv(name, root)
        p = s.spawn(argv, **env)
        deadline = time.monotonic() + 40
        win = None
        while time.monotonic() < deadline and not win:
            win = next((w for w in s.windows() if "T:" in (w.get("name") or "")), None)
            time.sleep(0.3)
        if not win:
            r["error"] = f"no window: {[(w.get('app_id'), w.get('name')) for w in s.windows()]}"
            return r
        time.sleep(1.5)
        win = next(w for w in s.windows() if w["id"] == win["id"])
        r["sway"] = {k: win.get(k) for k in ("app_id", "rect", "window_rect", "geometry", "pid", "type")}
        app, apid = find_app(a, win["pid"], time.monotonic() + 15)
        r["A1_app"] = None if not app else {"bus": app[0], "pid": apid, "window_pid": win["pid"],
                                            "toolkit": safe(lambda: a.prop(app, atspi.APP, "ToolkitName"))}
        if not app:
            r["A1_apps_seen"] = [(safe(lambda x=x: a.name(x)), safe(lambda x=x: a.pid(x[0]))) for x in a.apps()]
            return r
        nodes, count = find_nodes(a, app, time.monotonic() + 15)
        r["A1_nodes_walked"] = count
        r["A1_found"] = sorted(nodes)
        if not {"frame", "button", "entry", "hit"} <= set(nodes):
            return r
        fr_w, fr_s = a.extents(nodes["frame"], 1), a.extents(nodes["frame"], 0)
        bt_w, bt_s = a.extents(nodes["button"], 1), a.extents(nodes["button"], 0)
        en_w = a.extents(nodes["entry"], 1)
        hit_w = a.extents(nodes["hit"], 1)
        r["toolkit"] = safe(lambda: a.prop(app, atspi.APP, "ToolkitName"))
        r["A2_extents"] = {"frame_window": fr_w, "frame_screen": fr_s, "button_window": bt_w,
                           "button_screen": bt_s, "entry_window": en_w}
        rect, wr, geo = win["rect"], win["window_rect"], win.get("geometry") or {"x": 0, "y": 0}
        cx, cy = rect["x"] + wr["x"], rect["y"] + wr["y"]

        def f1(ext):  # window coords are relative to the xdg surface origin
            return cx - geo["x"] + ext[0] + ext[2] // 2, cy - geo["y"] + ext[1] + ext[3] // 2

        # F3: the frame's surface may carry CSD shadow margins. The bounding box
        # of the frame's direct children is the visible window geometry; its
        # origin is the margin to subtract. Without margins it is (0, 0).
        origin = [0, 0]
        if (fr_w[2], fr_w[3]) != (wr["width"], wr["height"]):
            boxes = []
            for kid in a.children(nodes["frame"]):
                try:
                    e = a.extents(kid, 1)
                except Exception:
                    continue
                if e[0] > -10000 and e[2] > 1 and e[3] > 1:
                    boxes.append(e)
            if boxes:
                origin = [min(b[0] for b in boxes), min(b[1] for b in boxes)]
            else:
                origin = [(fr_w[2] - wr["width"]) // 2, (fr_w[3] - wr["height"]) // 2]
        r["A2_margin_origin"] = origin

        # Chromium/Electron report descendant extents in device pixels while the
        # frame stays logical: detect by a child box exceeding the frame, then
        # divide by the output scale of the window's output.
        out_scale = next((o["scale"] for o in s.msg(kind="get_outputs")
                          if o["rect"]["x"] <= rect["x"] < o["rect"]["x"] + o["rect"]["width"]), 1.0)
        dev = r.get("toolkit") == "Chromium" and out_scale != 1.0
        r["A2_device_pixel_correction"] = out_scale if dev else None

        def f3(ext):
            if dev:
                ext = tuple(int(round(v / out_scale)) for v in ext)
            return cx + ext[0] - fr_w[0] - origin[0] + ext[2] // 2, cy + ext[1] - fr_w[1] - origin[1] + ext[3] // 2

        def f2(ext):  # window coords relative to the frame's own extents
            return cx + ext[0] - fr_w[0] + ext[2] // 2, cy + ext[1] - fr_w[1] + ext[3] // 2

        r["A2_points"] = {"button_f1": f1(bt_w), "button_f2": f2(bt_w), "button_f3": f3(bt_w)}
        # A3 semantic first (no pointer, no keyboard)
        ok = safe(lambda: a.set_text(nodes["entry"], "sem"))
        r["A3_set_text"] = {"returned": ok, "title": wait_title(s, win["id"], lambda t: "T:sem" in t),
                            "readback": safe(lambda: a.text(nodes["entry"]))}
        acts = safe(lambda: a.actions(nodes["button"]))
        r["A3_button_actions"] = acts
        if isinstance(acts, list) and acts:
            idx = next((i for i, n in enumerate(acts) if n in ("click", "press", "activate", "Press")), 0)
            ok = safe(lambda: a.do_action(nodes["button"], idx))
            r["A3_do_action"] = {"action": acts[idx], "returned": ok,
                                 "title": wait_title(s, win["id"], lambda t: "T:clicked" in t)}
        safe(lambda: a.set_text(nodes["entry"], "reset"))
        wait_title(s, win["id"], lambda t: "reset" in t)
        # A2 pointer click at f1 on the seat that can drive this toolkit
        c = wl.Connection(runtime_dir=s.root, display="wayland-1")
        seats = c.seats()
        seat = os.environ.get("FORCE_SEAT") or ("seat0" if name in targets.SINGLE_SEAT else "agent")
        ptr = wl.VirtualPointer(c, seats[seat])
        time.sleep(0.3)
        outs = s.msg(kind="get_outputs")
        lw = max(o["rect"]["x"] + o["rect"]["width"] for o in outs)
        lh = max(o["rect"]["y"] + o["rect"]["height"] for o in outs)
        r["A2_hit_extents"] = hit_w
        r["A2_hit_point"] = f3(hit_w)
        click(ptr, *f3(hit_w), extent=(lw, lh))
        r["A2_click_f3"] = {"seat": seat, "title": wait_title(s, win["id"], lambda t: "T:hit" in t or "T:miss" in t)}
        r["A2_click_f3"]["title"] = (r["A2_click_f3"]["title"][0], "T:hit" in (r["A2_click_f3"]["title"][0] or ""))
    return r


def safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - spike reporting
        return f"ERR {type(exc).__name__}: {str(exc)[:160]}"


if __name__ == "__main__":
    args = sys.argv[1:]
    scale = int(args[args.index("--scale") + 1]) if "--scale" in args else 1
    names = [x for x in args if not x.startswith("--") and not x.isdigit()]
    out = [run(n, scale, "--floating" in args, "--status-before" in args) for n in names]
    print(json.dumps(out, indent=1, default=str))

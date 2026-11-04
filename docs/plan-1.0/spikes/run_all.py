#!/usr/bin/env python3
"""Re-run every proof against the final reference code; print PASS/FAIL per claim.

Each entry: (claim id, argv, checker over the parsed JSON output).
Sandboxed: every spike starts its own private D-Bus + AT-SPI + headless Sway.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def last_json(out: str):
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        start = out.index("{")
        return json.loads(out[start:])
    except (ValueError, json.JSONDecodeError):
        return None


def s4(line_target, key):
    def check(_):
        return None  # spike4 prints a matrix line; handled by spike4_matrix parsing
    return check


CLAIMS = [
    ("seat.independent_focus_and_typing", ["spike1b_seats.py"],
     lambda d: d["P4_text"] == {"A": "usera", "B": "", "C": "agentc"}
     and d["P5_text"]["C"] == "agentcoffscreen" and d["P5_text"]["A"] == "userastill"),
    ("seat.ipc_commands_act_on_seat0", ["spike1b_seats.py"],
     lambda d: d["P3_ipc_focus_C"] == {"agent": "B", "seat0": "C"}),
    ("launch.binding_exec_places_on_agent_ws", ["spike1d_launch.py"],
     lambda d: d["direct"]["ws"] == "agent" and d["forked"]["ws"] == "agent"
     and d["direct"]["seat0"][0] == d["direct"]["seat0"][1]),
    ("launch.bringup_from_empty_output", ["spike11_bringup.py"], lambda d: d["pass"]),
    ("toolkit.multi_seat.gtk4_qt6_firefox", ["spike2_toolkits.py", "gtk4", "qt6", "firefox"],
     lambda d: all(d[k]["pass"] for k in ("gtk4", "qt6", "firefox"))),
    ("toolkit.single_seat.chromium_electron_kitty", ["spike2_toolkits.py", "chromium", "electron", "kitty"],
     lambda d: not any(d[k]["agent_text_arrived"] for k in ("chromium", "electron", "kitty"))),
    ("pointer.drag_and_scroll_virtual_pointer", ["spike3_pointer.py"],
     lambda d: d["drag"]["pass"] and len(d["scroll"]["events"]) >= 2),
    ("a11y.web_semantics", ["spike4b_web_semantic.py", "chromium"],
     lambda d: d["after_action"] == "T:clicked"),
    ("verify.signals", ["spike5_verify.py", "gtk3"],
     lambda d: d["V2_sway_title_event"] and d["V1_atspi_after_set_text"]),
    ("ocr.ensemble.gtk3_dark", ["spike6_ocr.py", "gtk3", "1"], lambda d: d.get("pass")),
    ("ocr.ensemble.chromium_scale2", ["spike6_ocr.py", "chromium", "2"], lambda d: d.get("pass")),
    ("reload.config_seat_recovery", ["spike7c_reload.py"], None),  # multi-line; checked below
    ("keyboard.unicode_shift_chords", ["spike8_unicode.py", "gtk3", "gtk4", "qt6", "firefox"],
     lambda d: all(v["typed_ok"] and v["chord_ok"] for k, v in d.items() if k != "firefox_steps")),
    ("clipboard.per_seat_matrix", ["spike9_clipboard.py", "gtk3", "firefox", "gtk4", "qt6"],
     lambda d: d["gtk3"]["pass"] and d["firefox"]["pass"] and not d["gtk4"]["pass"] and not d["qt6"]["pass"]),
    ("foreground.chromium_keyboard_no_pointer", ["spike10_foreground.py", "chromium"], lambda d: d["pass"]),
    ("foreground.electron_keyboard_no_pointer", ["spike10_foreground.py", "electron"], lambda d: d["pass"]),
    ("idle.per_seat", ["spike12_idle.py"], lambda d: d["pass"]),
    ("reload.output_repin_recovery", ["spike13_reload_outputs.py"], lambda d: d["pass"]),
    ("production.end_to_end_with_reload", ["spike14_production.py"], lambda d: d["pass"]),
    ("binding.context_config_equals_runtime", ["spike14b_binding_context.py", "config"],
     lambda d: d["after_tap_B"] == {"cus-agent": "B", "seat0": "A"} and d["after_tap_A"]["seat0"] == "A"),
]

MATRIX = ["gtk3", "gtk3-csd::f", "gtk4", "qt6", "firefox", "chromium", "electron",
          "gtk4:2", "qt6:2", "firefox:2", "chromium:2", "electron:2"]


def main() -> int:
    only = sys.argv[1:]
    failures = 0
    for claim, argv, check in CLAIMS:
        if only and not any(o in claim for o in only):
            continue
        proc = subprocess.run([sys.executable, *argv], cwd=HERE, capture_output=True, text=True, timeout=600)
        if check is None:
            lines = [json.loads(x) for x in proc.stdout.splitlines() if x.startswith("{")]
            ok = len(lines) == 3 and all(x["preexisting_window_after_recovery"]
                                         and x["seat0_devices_after_recovery"] == [] for x in lines)
        else:
            data = last_json(proc.stdout)
            try:
                ok = bool(data) and bool(check(data))
            except (KeyError, TypeError):
                ok = False
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {claim}", flush=True)
        if not ok:
            print("      " + (proc.stdout[-600:] + proc.stderr[-600:]).replace("\n", "\n      "))
    if not only or any("a11y.matrix" in o for o in only):
        proc = subprocess.run([sys.executable, "spike4_matrix.py", *MATRIX], cwd=HERE,
                              capture_output=True, text=True, timeout=1800)
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            ok = "app=Y" in line and "click_f3=Y" in line and "do_action=Y" in line or (
                line.startswith("firefox") and "app=Y" in line and "click_f3=Y" in line)
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  a11y.matrix {line.split('|')[0].strip()}")
    print(f"\n{failures} failing claim(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

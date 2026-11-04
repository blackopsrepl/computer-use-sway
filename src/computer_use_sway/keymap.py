"""Keymap and keysym tables for the virtual keyboard.

The keymap is the real evdev keycode set with a US layout, so ASCII goes out
on the physical key a real keyboard would use (with a real Shift press), and
apps that look at key codes behave normally. Any other keysym (non-ASCII
text, F13-F24, the agent's private binding keys) is bound to one of twelve
slots on the F13-F24 keys. The slots must sit below xkb keycode 256:
Chromium and Electron silently drop key events with higher codes, which lost
every non-ASCII character typed into them (measured).
"""

from __future__ import annotations

US_ROWS = [
    ("1234567890-=", "!@#$%^&*()_+", 2),
    ("qwertyuiop[]", "QWERTYUIOP{}", 16),
    ("asdfghjkl;'`", 'ASDFGHJKL:"~', 30),
    ("\\zxcvbnm,./", "|ZXCVBNM<>?", 43),
]

US: dict[str, tuple[int, bool]] = {}
for _plain, _shifted, _first in US_ROWS:
    for _i, (_a, _b) in enumerate(zip(_plain, _shifted)):
        US[_a] = (_first + _i, False)
        US[_b] = (_first + _i, True)
US[" "] = (57, False)

NAMED: dict[str, int] = {
    "Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "space": 57,
    "Control_L": 29, "Shift_L": 42, "Shift_R": 54, "Alt_L": 56, "Super_L": 125,
    "Caps_Lock": 58, "Num_Lock": 69, "Scroll_Lock": 70,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66,
    "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "KP_Enter": 96, "Print": 99, "Pause": 119,
    "Home": 102, "Up": 103, "Prior": 104, "Left": 105, "Right": 106, "End": 107,
    "Down": 108, "Next": 109, "Insert": 110, "Delete": 111, "Menu": 127,
}

MOD_KEYS = {"shift": 42, "ctrl": 29, "alt": 56, "logo": 125}
# xkb real-modifier bits for this keymap. wlroots does not derive modifier
# state from a virtual keyboard's key events, so the client sends this mask
# with zwp_virtual_keyboard_v1.modifiers around every modified key.
MOD_MASK = {"shift": 1, "ctrl": 4, "alt": 8, "logo": 64}
# (evdev code, xkb key name) of the slots; xkb code = evdev + 8.
SLOTS = [(183 + i, f"FK{13 + i}") for i in range(12)]
FOCUS_KEY = "XF86Macro27"
LAUNCH_KEY = "XF86Macro29"
AGENT_KEYSYMS = (FOCUS_KEY, LAUNCH_KEY)

_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "tab": "Tab", "space": "space", "delete": "Delete",
    "del": "Delete", "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Prior", "page_up": "Prior", "prior": "Prior",
    "pagedown": "Next", "page_down": "Next", "next": "Next",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right", "menu": "Menu",
}

KEYMAP_TEMPLATE = """xkb_keymap {
xkb_keycodes "cus" { include "evdev" %s };
xkb_types "cus" { include "complete" };
xkb_compatibility "cus" { include "complete" };
xkb_symbols "cus" { include "pc+us+inet(evdev)" %s };
};
"""


def keysym_for_char(ch: str) -> str:
    if ch == " ":
        return "space"
    if ch == "\n":
        return "Return"
    if ch == "\t":
        return "Tab"
    if len(ch) == 1 and ch.isascii() and ch.isprintable():
        return ch
    return "U%04X" % ord(ch)


def normalize_key(name: str) -> str:
    """Tool-level key names to keysyms: 'enter' -> 'Return', 'f5' -> 'F5'."""
    if len(name) == 1:
        return name
    lowered = name.lower()
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    if lowered.startswith("f") and lowered[1:].isdigit():
        return "F" + lowered[1:]
    return name


def build_keymap(slots: list[str | None]) -> bytes:
    """Keymap with slot i bound to keysym slots[i] (None leaves the slot empty)."""
    syms = " ".join(
        f"key <{SLOTS[i][1]}> {{[ {sym} ]}};" for i, sym in enumerate(slots) if sym is not None
    )
    return (KEYMAP_TEMPLATE % ("", syms)).encode("utf-8") + b"\0"

"""Toolkit capabilities, as measured data (FINDINGS I5, A4b, A7, A9, C2).

Never inferred from version strings: each value was observed in the spikes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    editable_text: str  # "reliable" | "lies" | "absent"  [A7]
    device_pixel_extents: bool  # divide extents by the output scale [A4b]
    launch_flags: tuple[str, ...]  # added by launch for a11y [A9]
    a11y_by_bus_status: bool  # honours org.a11y.Status [A9]
    # DoAction runs the widget's handler inside the D-Bus call; a handler that
    # starts a nested main loop (gtk_dialog_run) blocks the app's a11y until
    # the loop returns, so a pointer press is preferred [A12]
    blocking_actions: bool = False


PROFILES = {
    "gtk3": Profile("gtk3", "reliable", False, (), True, blocking_actions=True),
    "gtk4": Profile("gtk4", "reliable", False, (), True),
    "qt": Profile("qt", "reliable", False, (), True),
    "gecko": Profile("gecko", "lies", False, (), True),
    "chromium": Profile("chromium", "absent", True, ("--force-renderer-accessibility",), False),
    "terminal": Profile("terminal", "absent", False, (), False),
    "unknown": Profile("unknown", "absent", False, (), False),
}

TERMINALS = {"kitty"}
ELECTRON_APPS = {"md.obsidian.obsidian", "obsidian", "discord", "com.nousresearch.hermes", "code", "slack",
                 "code-oss", "vscodium", "signal", "element"}
CHROMIUM_HINTS = ("chrom", "electron", "brave", "vivaldi", "msedge")


def _major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.strip().split(".")[0]
    return int(head) if head.isdigit() else None


def _terminal(app_id: str | None, argv0: str | None) -> Profile | None:
    for candidate in (app_id, argv0):
        if candidate:
            base = candidate.lower().rsplit("/", 1)[-1]
            if base in TERMINALS:
                return PROFILES["terminal"]
    return None


def classify(toolkit_name: str | None, app_id: str | None, argv0: str | None = None,
             toolkit_version: str | None = None) -> Profile:
    # A terminal before its toolkit: its surface has no text interface.
    terminal = _terminal(app_id, argv0)
    if terminal is not None:
        return terminal
    if toolkit_name:
        lowered = toolkit_name.lower()
        if lowered == "gtk":
            major = _major(toolkit_version)
            if major == 4 or (major is None and toolkit_name == "GTK"):
                return PROFILES["gtk4"]
            return PROFILES["gtk3"]
        if lowered == "qt":
            return PROFILES["qt"]
        if lowered == "gecko":
            return PROFILES["gecko"]
        if lowered == "chromium":
            return PROFILES["chromium"]
    for candidate in (app_id, argv0):
        if not candidate:
            continue
        lowered = candidate.lower()
        base = lowered.rsplit("/", 1)[-1]
        if base in ELECTRON_APPS or any(h in base for h in CHROMIUM_HINTS):
            return PROFILES["chromium"]
        if base in {"firefox", "org.mozilla.firefox", "librewolf", "thunderbird"}:
            return PROFILES["gecko"]
    return PROFILES["unknown"]

"""Toolkit capabilities, as measured data (FINDINGS I5, A4b, A7, A9, C2).

Never inferred from version strings: each value was observed in the spikes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Profile:
    name: str
    multi_seat: bool | None  # raw input from a second seat is accepted [I5]
    editable_text: str  # "reliable" | "lies" | "absent"  [A7]
    device_pixel_extents: bool  # divide extents by the output scale [A4b]
    paste_follows_seat: bool | None  # [C2]
    launch_flags: tuple[str, ...]  # added by launch for a11y [A9]
    a11y_by_bus_status: bool  # honours org.a11y.Status [A9]
    # DoAction runs the widget's handler inside the D-Bus call; a handler that
    # starts a nested main loop (gtk_dialog_run) blocks the app's a11y until
    # the loop returns, so a pointer press is preferred [A12]
    blocking_actions: bool = False


PROFILES = {
    "gtk3": Profile("gtk3", True, "reliable", False, True, (), True, blocking_actions=True),
    "gtk4": Profile("gtk4", True, "reliable", False, False, (), True),
    "qt": Profile("qt", True, "reliable", False, False, (), True),
    "gecko": Profile("gecko", True, "lies", False, True, (), True),
    "chromium": Profile("chromium", False, "absent", True, None, ("--force-renderer-accessibility",), False),
    # kitty measured; foot/alacritty/wezterm/ghostty assumed until measured
    "terminal": Profile("terminal", False, "absent", False, None, (), False),
    "unknown": Profile("unknown", None, "absent", False, None, (), False),
}

SINGLE_SEAT_TERMINALS = {"kitty", "org.wezfurlong.wezterm"}
SEAT_TERMINALS = {"foot", "footclient", "alacritty", "com.mitchellh.ghostty"}
TERMINALS = SINGLE_SEAT_TERMINALS | SEAT_TERMINALS
ELECTRON_APPS = {"md.obsidian.obsidian", "obsidian", "discord", "com.nousresearch.hermes", "code", "slack",
                 "code-oss", "vscodium", "signal", "element"}
CHROMIUM_HINTS = ("chrom", "electron", "brave", "vivaldi", "msedge")


def _major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.strip().split(".")[0]
    return int(head) if head.isdigit() else None


def classify(toolkit_name: str | None, app_id: str | None, argv0: str | None = None,
             toolkit_version: str | None = None) -> Profile:
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
        if base in TERMINALS or lowered in TERMINALS:
            return PROFILES["terminal"]
        if base in ELECTRON_APPS or any(h in base for h in CHROMIUM_HINTS):
            return PROFILES["chromium"]
        if base in {"firefox", "org.mozilla.firefox", "librewolf", "thunderbird"}:
            return PROFILES["gecko"]
    return PROFILES["unknown"]


class Learned:
    """Per-session overrides keyed by app_id, learned from verdicts."""

    def __init__(self) -> None:
        self._single_seat: set[str] = set()

    def mark_single_seat(self, app_id: str | None) -> None:
        if app_id:
            self._single_seat.add(app_id)

    def profile(self, base: Profile, app_id: str | None) -> Profile:
        if app_id and app_id in self._single_seat and base.multi_seat is not False:
            return replace(base, multi_seat=False)
        return base

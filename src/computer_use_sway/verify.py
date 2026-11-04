"""Action verdicts: did the input have an effect, and what is the evidence?

Rules, first that holds wins:
1. readback — the element's text/states match the expectation -> confirmed
   (a failed text readback is remembered: Gecko's SetTextContents lies [A7],
   and the verdict can then be at most `changed`);
2. AT-SPI events from the element's app since the baseline -> changed;
3. Sway events on the target window (or a new window) -> changed;
4. the window's pixels differ from the baseline -> changed;
5. pixels were capturable and did not change -> no_change;
6. otherwise -> unverifiable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import capture, core, desktop

EFFECTS = ("confirmed", "changed", "no_change", "unverifiable")
# Focus changes are excluded: the input itself focuses the window (a click on
# empty background must still read as no_change).
SWAY_CHANGES = {"title", "urgent", "new", "close", "move", "floating", "fullscreen_mode"}
FOCUS_DETAILS = {"focused", "active", "selected:focus"}
FOCUS_MEMBERS = {"Activate", "Deactivate"}
SLICE_S = 0.05


@dataclass
class Expectation:
    text: str | None = None
    text_endswith: str | None = None
    state_on: set[str] = field(default_factory=set)
    state_off: set[str] = field(default_factory=set)
    value: float | None = None

    def empty(self) -> bool:
        return (self.text is None and self.text_endswith is None and not self.state_on and not self.state_off
                and self.value is None)


@dataclass
class Baseline:
    t: float
    con_id: int | None
    title: str | None
    region: dict[str, int] | None
    region_hash: str | None
    a11y: tuple[str, str] | None


def take_baseline(session: Any, element: Any, con_id: int | None, want_pixels: bool = True) -> Baseline:
    win = desktop.window_by_id(con_id) if con_id is not None else None
    region = digest = None
    if want_pixels and win is not None and win.get("visible"):
        try:
            region = capture.window_region(win)
            digest = capture.region_hash(region)
        except core.ToolError:
            region = digest = None
    client = session.a11y_or_none()
    if client is not None and element is not None and getattr(element, "a11y", None):
        try:
            client.register_events()
            client.pump(0)
        except core.ToolError:
            pass
    return Baseline(time.monotonic(), con_id, (win or {}).get("name"), region, digest,
                    getattr(element, "a11y", None) if element is not None else None)


def _readback(client: Any, ref: tuple[str, str], expect: Expectation) -> tuple[bool, bool, list[str]]:
    """(holds, text_failed, evidence)."""
    evidence: list[str] = []
    try:
        node = client.node(ref)
    except core.ToolError:
        return False, False, evidence
    text_failed = False
    if expect.text is not None or expect.text_endswith is not None:
        try:
            current = client.text(ref)
        except core.ToolError:
            current = None
        if current is None:
            text_failed = True
        elif expect.text is not None and current != expect.text:
            text_failed = True
        elif expect.text_endswith is not None and not current.endswith(expect.text_endswith):
            text_failed = True
        else:
            evidence.append("readback:text")
    for state in expect.state_on:
        if state not in node.states:
            return False, text_failed, evidence
        evidence.append(f"readback:state:{state}")
    for state in expect.state_off:
        if state in node.states:
            return False, text_failed, evidence
        evidence.append(f"readback:state:!{state}")
    if expect.value is not None:
        current_value = client.value(ref)
        if current_value is None or abs(current_value - expect.value) > 1e-6:
            return False, text_failed, evidence
        evidence.append("readback:value")
    return (not text_failed), text_failed, evidence


def _sway_evidence(session: Any, base: Baseline) -> list[str]:
    found = []
    for _t, name, body in session.log.since(base.t, "window"):
        change = body.get("change")
        container = body.get("container") or {}
        if change == "new" and container.get("id") != base.con_id:
            found.append("sway:new_window")
        elif container.get("id") == base.con_id and change in SWAY_CHANGES:
            found.append(f"sway:{change}")
    return sorted(set(found))


def _a11y_evidence(client: Any, base: Baseline) -> list[str]:
    if client is None or base.a11y is None:
        return []
    try:
        events = client.events_since(base.t, base.a11y[0])
    except core.ToolError:
        return []
    return sorted({f"atspi:{member}" for _t, member, detail in events
                   if detail not in FOCUS_DETAILS and member not in FOCUS_MEMBERS})


def verdict(session: Any, base: Baseline, expect: Expectation | None = None,
            settle_ms: int | None = None) -> dict[str, Any]:
    if settle_ms is None:
        settle_ms = int(session.options.get("settle_ms", 600))
    client = session.a11y_or_none() if base.a11y is not None else None
    deadline = time.monotonic() + settle_ms / 1000.0
    text_failed = False
    events: list[str] = []
    while True:
        if client is not None:
            try:
                client.pump(SLICE_S)
            except core.ToolError:
                client = None
        else:
            time.sleep(SLICE_S)
        if expect is not None and not expect.empty() and client is not None and base.a11y is not None:
            holds, text_failed, evidence = _readback(client, base.a11y, expect)
            if holds:
                return _result("confirmed", evidence, base)
        events = _a11y_evidence(client, base) + _sway_evidence(session, base)
        if events and (expect is None or expect.empty() or client is None):
            break
        if time.monotonic() >= deadline:
            break
    if events:
        return _result("changed", events, base, text_failed)
    if base.region is not None and base.region_hash is not None:
        try:
            digest, _stable = capture.settle(base.region, 60, max(120, settle_ms // 2))
        except core.ToolError:
            digest = None
        if digest is not None and digest != base.region_hash:
            return _result("changed", ["pixels"], base, text_failed)
        if digest is not None:
            return _result("no_change", ["pixels:unchanged"] + (["readback:text:mismatch"] if text_failed else []),
                           base, text_failed)
    return _result("unverifiable", ["no readback, events or pixels available"], base, text_failed)


def _result(effect: str, evidence: list[str], base: Baseline, text_failed: bool = False) -> dict[str, Any]:
    return {
        "effect": effect,
        "evidence": evidence,
        "elapsed_ms": round((time.monotonic() - base.t) * 1000),
        "readback_failed": text_failed,
    }


def disabled() -> dict[str, Any]:
    return {"effect": "unverifiable", "evidence": ["verification disabled"], "elapsed_ms": 0,
            "readback_failed": False}

"""`wait_for`: block until a condition holds, or report that it did not."""

from __future__ import annotations

import time
from typing import Any

from . import capture, core, desktop, ocr, query, scene

KINDS = ("element", "window", "text", "pixels_stable", "user_idle_ms")
POLL_S = 0.15


def _selector_match(win: dict[str, Any], selector: dict[str, Any]) -> bool:
    mode = selector.get("match", "contains")
    if "con_id" in selector:
        return win.get("id") == selector["con_id"]
    if "app_id" in selector:
        return desktop.string_match(win.get("app_id"), str(selector["app_id"]), mode)
    if "title" in selector:
        return desktop.string_match(win.get("name"), str(selector["title"]), mode)
    if "class" in selector:
        return desktop.string_match(win.get("class"), str(selector["class"]), mode)
    raise core.ToolError("window selector needs con_id, app_id, class, or title")


def _element(session: Any, cond: dict[str, Any], deadline: float) -> tuple[bool, str]:
    q = query.parse(cond["element"])
    state = cond.get("state", "present")
    while True:
        result = scene.collect(session, "visible", None, q, 500, "never" if state == "absent" else "auto")
        found = scene.matching(result, q)
        if state == "present" and found:
            return True, f"found {found[0].ref}"
        if state == "absent" and not found:
            return True, "absent"
        if state not in ("present", "absent") and any(state in e.states for e in found):
            return True, f"{state}"
        if time.monotonic() >= deadline:
            return False, f"{len(found)} matching element(s), condition not met"
        time.sleep(POLL_S)


def _window(session: Any, cond: dict[str, Any], started: float, deadline: float) -> tuple[bool, str]:
    selector = cond["window"]
    if not isinstance(selector, dict):
        raise core.ToolError("condition.window must be a selector object")
    event = cond.get("event")
    if event is None:
        while True:
            match = [w for w in desktop.all_windows() if _selector_match(w, selector)]
            if match:
                return True, f"window {match[0]['id']}"
            if time.monotonic() >= deadline:
                return False, "no matching window"
            session.log.wait_for(lambda e: e[1] == "window", time.monotonic(), POLL_S)
    if event not in ("new", "close", "title", "focus"):
        raise core.ToolError("condition.event must be new, close, title, or focus")

    def pred(e: Any) -> bool:
        if e[1] != "window" or e[2].get("change") != event:
            return False
        container = e[2].get("container") or {}
        win = {"id": container.get("id"), "app_id": container.get("app_id"), "name": container.get("name"),
               "class": (container.get("window_properties") or {}).get("class")}
        return _selector_match(win, selector)

    hit = session.log.wait_for(pred, started, max(0.0, deadline - time.monotonic()))
    return (hit is not None), (f"{event} on {hit[2]['container'].get('id')}" if hit else f"no {event} event")


def _text(session: Any, cond: dict[str, Any], deadline: float) -> tuple[bool, str]:
    text = cond["text"]
    if not isinstance(text, str) or not text:
        raise core.ToolError("condition.text must be a non-empty string")
    selector = cond.get("window")
    while True:
        result = scene.collect(session, "window" if selector else "visible", selector,
                               query.Query(text=text), 500, "auto")
        found = scene.matching(result, query.Query(text=text))
        if found:
            return True, f"found {found[0].ref} ({found[0].source})"
        if time.monotonic() >= deadline:
            return False, "text not found" + ("" if ocr.available() else " (OCR unavailable)")
        time.sleep(0.5)


def wait_for(session: Any, condition: Any, timeout_ms: Any = 10000) -> core.Result:
    session.ensure_started()
    if not isinstance(condition, dict):
        raise core.ToolError("condition must be an object")
    kinds = [k for k in KINDS if k in condition]
    if len(kinds) != 1:
        raise core.ToolError(f"condition needs exactly one of {', '.join(KINDS)}")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= 120000:
        raise core.ToolError("timeout_ms must be between 0 and 120000")
    kind = kinds[0]
    started = time.monotonic()
    deadline = started + timeout_ms / 1000.0
    if kind == "element":
        ok, detail = _element(session, condition, deadline)
    elif kind == "window":
        ok, detail = _window(session, condition, started, deadline)
    elif kind == "text":
        ok, detail = _text(session, condition, deadline)
    elif kind == "pixels_stable":
        selector = condition["pixels_stable"]
        win = desktop.find_window(selector) if isinstance(selector, dict) else None
        if win is None:
            raise core.ToolError("pixels_stable needs a window selector")
        for_ms = condition.get("for_ms", 300)
        if isinstance(for_ms, bool) or not isinstance(for_ms, int) or not 60 <= for_ms <= 10000:
            raise core.ToolError("for_ms must be between 60 and 10000")
        region = capture.window_region(win)
        while True:
            _frame, ok = capture.settle(region, for_ms, max(for_ms * 2, 120))
            if ok or time.monotonic() >= deadline:
                break
        detail = "stable" if ok else "still changing"
    else:
        idle_ms = condition["user_idle_ms"]
        if isinstance(idle_ms, bool) or not isinstance(idle_ms, int) or not 0 <= idle_ms <= 60000:
            raise core.ToolError("user_idle_ms must be between 0 and 60000")
        ok = session.sentinel().wait_user_idle(idle_ms, timeout_ms)
        detail = "user idle" if ok else "user active"
    waited = round((time.monotonic() - started) * 1000)
    return core.text_result({"satisfied": ok, "waited_ms": waited, "detail": detail},
                            {"kind": kind, "satisfied": ok, "waited_ms": waited})

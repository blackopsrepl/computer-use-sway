"""`act`: do one thing to one element and report whether it worked.

The delivery ladder, climbed only while the effect is not established:
1. semantic — an AT-SPI action/text/value call; no input device at all;
2. agent — the agent's own seat (pointer/keyboard), focused on the window by
   the agent binding; the user's focus is snapshotted and restored;
3. foreground — only when policy allows and the user is idle: a brief IPC
   focus swap on seat0 for toolkits that ignore other seats (Chromium,
   Electron, terminals), always restored in `finally`.
"""

from __future__ import annotations

import time
from typing import Any

from . import core, desktop, geometry, query, scene, toolkits, verify, vinput
from .actions_delivery import Delivery, device_form, foreground, needs_keyboard, semantic

ACTIONS = ("press", "activate", "toggle", "focus", "set_text", "type", "key", "click", "double_click",
           "right_click", "scroll", "drag_to", "set_value", "select")
DELIVERIES = ("auto", "semantic", "agent", "foreground")


def resolve_target(session: Any, target: Any) -> tuple[Any, int | None, tuple[int, int] | None]:
    if not isinstance(target, dict):
        raise core.ToolError("target must be {ref}, {query} or {x, y}")
    if "ref" in target:
        element = scene.validate(session, target["ref"])
        return element, element.con_id, geometry.center(element.bounds)
    if "query" in target:
        q = query.parse(target["query"])
        result = scene.collect(session, "visible", None, q, 500, session.options.get("ocr", "auto"))
        found = scene.matching(result, q)
        if len(found) <= q.nth:
            raise core.ToolError(f"no element matches the query (found {len(found)})", code="no_match")
        element = found[q.nth]
        return element, element.con_id, geometry.center(element.bounds)
    if "x" in target and "y" in target:
        x, y = desktop.validate_coordinates(target["x"], target["y"])
        win = desktop.window_at(x, y)
        return None, (int(win["id"]) if win else None), (x, y)
    raise core.ToolError("target must be {ref}, {query} or {x, y}")


def resolve_to(session: Any, to: Any) -> tuple[int, int]:
    _element, _con, point = resolve_target(session, to)
    assert point is not None
    return point


def profile_for(session: Any, element: Any, con_id: int | None) -> toolkits.Profile:
    if element is not None:
        base = next((p for p in toolkits.PROFILES.values() if p.name == element.profile), toolkits.PROFILES["unknown"])
        return scene.learned(session).profile(base, element.app_id)
    win = desktop.window_by_id(con_id) if con_id is not None else None
    app_id = (win or {}).get("app_id") or (win or {}).get("class")
    return scene.learned(session).profile(toolkits.classify(None, app_id), (win or {}).get("app_id"))


def expectation(action: str, element: Any, d: Delivery, client: Any) -> verify.Expectation | None:
    if element is None or element.source != "a11y" or client is None:
        return None
    if action == "toggle":
        try:
            checked = "checked" in client.node(element.a11y).states
        except core.ToolError:
            return None
        return verify.Expectation(state_off={"checked"}) if checked else verify.Expectation(state_on={"checked"})
    if action == "focus":
        return verify.Expectation(state_on={"focused"})
    if action == "set_text":
        return verify.Expectation(text=d.text)
    if action == "type" and d.text and "org.a11y.atspi.Text" in element.ifaces:
        return verify.Expectation(text_endswith=d.text)
    if action == "set_value":
        return verify.Expectation(value=d.value)
    if action == "select":
        return verify.Expectation(state_on={"selected"})
    return None


def run_rung(session: Any, rung: str, action: str, element: Any, con_id: int | None, point: Any,
             d: Delivery, do_verify: bool) -> dict[str, Any]:
    client = session.a11y_or_none()
    expect = expectation(action, element, d, client)
    if rung == "semantic":
        base = verify.take_baseline(session, element, con_id, want_pixels=do_verify)
        semantic(client, action, element, d)
        out = verify.verdict(session, base, expect) if do_verify else verify.disabled()
        out["disturbed_user"] = False
        return out
    if rung == "agent":
        agent = session.agent()
        sentinel = session.sentinel()
        snap = sentinel.snapshot()
        try:
            if con_id is not None:
                agent.focus(con_id)
            base = verify.take_baseline(session, element, con_id, want_pixels=do_verify)
            try:
                device_form(agent.devices, action, element, point, d)
            except vinput.AgentSeatLost:
                # A reload landed before anything was sent: recover the seat
                # (devices, pins, output) and deliver once more.
                agent.ensure_ready()
                if con_id is not None:
                    agent.focus(con_id)
                device_form(agent.devices, action, element, point, d)
            out = verify.verdict(session, base, expect) if do_verify else verify.disabled()
        finally:
            disturbed = sentinel.restore(snap)
        out["disturbed_user"] = disturbed
        return out
    if rung == "user":
        sess_devices = session.user_devices()
        if con_id is not None:
            desktop.ipc().command(f"[con_id={int(con_id)}] focus")
        base = verify.take_baseline(session, element, con_id, want_pixels=do_verify)
        device_form(sess_devices, action, element, point, d)
        out = verify.verdict(session, base, expect) if do_verify else verify.disabled()
        out["disturbed_user"] = True
        return out
    return foreground(session, action, element, con_id, point, d, expect, do_verify)


def ladder(session: Any, action: str, element: Any, profile: toolkits.Profile, delivery: str) -> list[str]:
    semantic_ok = element is not None and element.source == "a11y" and action in (
        "press", "activate", "toggle", "focus", "set_text", "set_value", "select")
    if action == "set_text" and profile.editable_text == "absent":
        semantic_ok = False
    if delivery != "auto":
        if delivery == "semantic" and not semantic_ok:
            raise core.ToolError(f"no semantic form of {action} for this target", code="no_semantic")
        if delivery == "agent" and session.mode == "user":
            return ["user"]
        return [delivery]
    rungs = ["semantic"] if semantic_ok else []
    if session.mode == "user":
        return rungs + ["user"]
    if profile.multi_seat is not False:
        rungs.append("agent")
    if session.options.get("foreground", "idle") != "never":
        rungs.append("foreground")
    # A pointer click the seats cannot deliver (single-seat toolkit, seat0
    # pointer not allowed) still has a meaning: the element's press action.
    if action == "click" and element is not None and element.source == "a11y" and element.actions:
        rungs.append("semantic")
    return rungs


def act(session: Any, target: Any, action: str, d: Delivery, delivery: str = "auto",
        do_verify: bool = True) -> core.Result:
    session.ensure_started()
    if action not in ACTIONS:
        raise core.ToolError(f"action must be one of {', '.join(ACTIONS)}")
    if delivery not in DELIVERIES:
        raise core.ToolError(f"delivery must be one of {', '.join(DELIVERIES)}")
    started = time.monotonic()
    if session.mode in ("seat", "sandbox"):
        session.agent()
    element, con_id, point = resolve_target(session, target)
    if action == "drag_to":
        d.to_point = resolve_to(session, d.to)
    profile = profile_for(session, element, con_id)
    rungs = ladder(session, action, element, profile, delivery)
    if not rungs:
        raise core.ToolError(f"no way to deliver {action} to this target", code="no_delivery")
    escalations: list[dict[str, Any]] = []
    out: dict[str, Any] | None = None
    used = None
    for rung in rungs:
        if rung == "foreground" and action in ("set_value",):
            continue
        try:
            out = run_rung(session, rung, action, element, con_id, point, d, do_verify)
        except core.ToolError as exc:
            escalations.append({"rung": rung, "error": str(exc), "code": exc.code})
            if delivery != "auto" or exc.code in ("partial_input",):
                raise
            continue
        used = rung
        if out.get("blocked") and rung != rungs[-1]:
            escalations.append({"rung": rung, "blocked": out["blocked"]})
            continue
        if action == "set_text" and out.get("readback_failed") and rung != rungs[-1]:
            # Gecko's SetTextContents reports success and changes nothing [A7];
            # set_text is idempotent, so the next rung may redo it.
            escalations.append({"rung": rung, "effect": out["effect"], "readback_failed": True})
            continue
        if out.get("blocked") or out["effect"] in ("confirmed", "changed") or not do_verify:
            break
        escalations.append({"rung": rung, "effect": out["effect"]})
        if rung == "agent" and out["effect"] == "no_change" and profile.multi_seat is None:
            win = desktop.window_by_id(con_id) if con_id is not None else None
            scene.learned(session).mark_single_seat(element.app_id if element else (win or {}).get("app_id"))
    if out is None:
        last = escalations[-1] if escalations else {"error": "no rung ran"}
        raise core.ToolError(f"{action} failed on every delivery: {last.get('error')}", code=last.get("code"))
    if escalations and escalations[-1].get("rung") == used and "effect" in escalations[-1]:
        escalations.pop()
    summary: dict[str, Any] = {
        "target": {
            "ref": element.ref if element is not None else None,
            "point": list(point) if point else None,
            "con_id": con_id,
            "role": element.role if element is not None else None,
            "name": element.name if element is not None else None,
        },
        "action": action,
        "effect": out["effect"],
        "evidence": out["evidence"],
        "delivery": used,
        "escalations": escalations,
        "disturbed_user": bool(out.get("disturbed_user")),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    for key in ("blocked", "partial", "characters_sent", "readback_failed"):
        if out.get(key):
            summary[key] = out[key]
    meta = {"action": action, "ref": summary["target"]["ref"], "delivery": used, "effect": out["effect"],
            "disturbed_user": summary["disturbed_user"]}
    if d.text is not None:
        meta["characters"] = len(d.text)
    return core.text_result(summary, meta)


def needs_text(action: str) -> bool:
    return action in ("set_text", "type")


__all__ = ["ACTIONS", "DELIVERIES", "act", "resolve_target", "needs_keyboard", "needs_text", "Delivery"]

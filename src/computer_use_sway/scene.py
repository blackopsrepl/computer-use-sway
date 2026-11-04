"""`observe`: a ref-indexed map of what is on screen, structure first.

Per window in scope: resolve the AT-SPI app and frame, walk it, keep the
actionable / editable / text-bearing / focused / landmark elements, convert
their WINDOW extents to layout points, and fall back to OCR when the tree is
thin or a text query found nothing in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import capture, core, desktop, geometry, install, ocr, query, refs, toolkits

SCOPES = ("agent", "focused", "visible", "window")
LANDMARKS = {"frame", "window", "dialog", "alert", "menu", "menu bar", "page tab list", "tool bar"}
TEXTUAL = {"label", "heading", "static", "paragraph", "text", "link", "list item", "table cell",
           "tree item", "page tab", "menu item", "status bar", "notification", "caption"}
STATE_SUBSET = ("focused", "checked", "selected", "pressed", "expanded", "editable", "indeterminate")
LINE_LIMIT = 120
OCR_MIN_ELEMENTS = 3


@dataclass
class WindowScene:
    win: dict[str, Any]
    profile: toolkits.Profile
    a11y: bool
    elements: list[refs.Element] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    toolkit: str | None = None
    ocr_used: bool = False


def learned(session: Any) -> toolkits.Learned:
    value = getattr(session, "_learned", None)
    if value is None:
        value = toolkits.Learned()
        session._learned = value
    return value


def windows_in_scope(session: Any, scope: str, selector: dict[str, Any] | None) -> list[dict[str, Any]]:
    if scope not in SCOPES:
        raise core.ToolError("scope must be agent, focused, visible, or window")
    tree = desktop.get_tree()
    windows = desktop.all_windows(tree)
    if scope == "window":
        if not isinstance(selector, dict):
            raise core.ToolError("scope window needs a window selector")
        found = desktop.find_window(selector)
        return [w for w in windows if w["id"] == found["id"]]
    if scope == "agent":
        return [w for w in windows if w.get("workspace") == install.SEAT]
    if scope == "visible":
        return [w for w in windows if w.get("visible")]
    seat = install.SEAT if session.default_seat() == "agent" else (session.user_seat or "seat0")
    focus = desktop.seat_focus(seat)
    return [w for w in windows if w["id"] == focus]


def keep(node: Any) -> bool:
    if node.extents is None or node.extents[2] <= 0 or node.extents[3] <= 0:
        return False
    if "showing" not in node.states:
        return False
    if node.role in LANDMARKS and node.depth > 0:
        return True
    if "org.a11y.atspi.Action" in node.ifaces or "editable" in node.states or "focused" in node.states:
        return True
    return node.role in TEXTUAL and bool(node.name)


def intersects(box: geometry.Box, area: dict[str, int]) -> bool:
    return (box[0] < area["x"] + area["width"] and box[0] + box[2] > area["x"]
            and box[1] < area["y"] + area["height"] and box[1] + box[3] > area["y"])


def window_geo(win: dict[str, Any], frame_ext: geometry.Box, csd: tuple[int, int],
               outputs: list[dict[str, Any]]) -> geometry.WindowGeo:
    c = win["content"]
    scale = geometry.output_scale_at(outputs, c["x"] + 1, c["y"] + 1)
    return geometry.WindowGeo((c["x"], c["y"]), (c["width"], c["height"]), scale, frame_ext, csd)


def a11y_elements(session: Any, client: Any, ws: WindowScene, outputs: list[dict[str, Any]],
                  max_elements: int) -> bool:
    """Fill ws.elements from AT-SPI. Returns whether the walk was truncated."""
    win = ws.win
    if not win.get("pid"):
        return False
    app = client.app_for_window(int(win["pid"]))
    if app is None:
        return False
    tk_name, tk_version = client.toolkit(app)
    ws.toolkit = tk_name
    profile = toolkits.classify(tk_name, win.get("app_id") or win.get("class"), toolkit_version=tk_version)
    ws.profile = learned(session).profile(profile, win.get("app_id"))
    frame = client.frame_for_window(app, win.get("name"))
    if frame is None:
        return False
    nodes = client.walk(frame, max_nodes=4000)
    if not nodes:
        return False
    ws.a11y = len(nodes) > 3
    frame_ext = nodes[0].extents or (0, 0, win["content"]["width"], win["content"]["height"])
    child_boxes = [n.extents for n in nodes if n.parent == frame and n.extents]
    csd = geometry.csd_origin(frame_ext, child_boxes, (win["content"]["width"], win["content"]["height"]))
    geo = window_geo(win, frame_ext, csd, outputs)
    kept = []
    for node in nodes[1:]:
        if not keep(node):
            continue
        bounds = geometry.a11y_to_layout(geo, node.extents, ws.profile.device_pixel_extents)
        if not intersects(bounds, win["content"]):
            continue
        kept.append((node, bounds))
    kept.sort(key=lambda item: (item[1][1], item[1][0]))
    for node, bounds in kept[:max_elements]:
        actions = client.actions(node.ref) if "org.a11y.atspi.Action" in node.ifaces else []
        preview = None
        if "org.a11y.atspi.Text" in node.ifaces and ("editable" in node.states or node.role in ("entry", "text")):
            try:
                preview = client.text(node.ref, 0, 81)
            except core.ToolError:
                preview = None
        ws.elements.append(refs.Element(
            ref="", source="a11y", con_id=int(win["id"]), role=node.role, name=node.name,
            states=sorted(s for s in node.states if s in STATE_SUBSET)
            + ([] if "enabled" in node.states or "sensitive" in node.states else ["disabled"]),
            actions=actions, bounds=bounds, preview=preview, a11y=node.ref, frame=frame, csd=csd,
            device_px=ws.profile.device_pixel_extents, app_id=win.get("app_id"), profile=ws.profile.name,
            ifaces=sorted(node.ifaces),
        ))
    return bool(client.truncated or len(kept) > max_elements)


def ocr_elements(ws: WindowScene, max_elements: int) -> None:
    if not ocr.available():
        ws.notes.append("ocr: unavailable (install tesseract)")
        return
    if not ws.win.get("visible"):
        ws.notes.append("ocr: window not visible")
        return
    try:
        region = capture.window_region(ws.win)
        image = capture.ppm2x(region)
    except core.ToolError as exc:
        ws.notes.append(f"ocr: {exc}")
        return
    ws.ocr_used = True
    for line in ocr.lines(ocr.recognize(image))[:max_elements]:
        bounds = geometry.ocr_to_layout((region["x"], region["y"]), line.box_px)
        x, y, w, h = line.box_px
        box = (x - 4, y - 4, w + 8, h + 8)
        ws.elements.append(refs.Element(
            ref="", source="ocr", con_id=int(ws.win["id"]), role="text", name=line.text, bounds=bounds,
            region=region, crop_px=box, pixels=capture.crop(image, box), app_id=ws.win.get("app_id"),
            profile=ws.profile.name,
        ))


def collect(session: Any, scope: str = "agent", window: dict[str, Any] | None = None,
            q: query.Query | None = None, max_elements: int = 150, ocr_mode: str = "auto") -> dict[str, Any]:
    if q is not None and q.window is not None:
        scope, window = "window", q.window
    wins = windows_in_scope(session, scope, window)
    client = session.a11y_or_none()
    if client is not None:
        try:
            client.ensure_enabled()
        except core.ToolError:
            client = None
    outputs = desktop.get_outputs()
    store = refs.store_for(session)
    snap = store.new_snapshot()
    scenes, truncated = [], False
    for win in wins:
        base = toolkits.classify(None, win.get("app_id") or win.get("class"))
        ws = WindowScene(win=win, profile=learned(session).profile(base, win.get("app_id")), a11y=False)
        if client is not None:
            try:
                truncated |= a11y_elements(session, client, ws, outputs, max_elements)
            except core.ToolError as exc:
                ws.notes.append(f"a11y: {exc}")
        if not ws.a11y and ws.profile.name == "chromium":
            ws.notes.append("a11y: off — relaunch with --force-renderer-accessibility (launch does this automatically)")
        a11y_hit = q is None or any(query.matches(q, e.role, e.name, e.preview) for e in ws.elements)
        want_ocr = ocr_mode == "always" or (ocr_mode == "auto" and (
            len(ws.elements) < OCR_MIN_ELEMENTS or (q is not None and q.text is not None and not a11y_hit)))
        if want_ocr and ocr_mode != "never":
            ocr_elements(ws, max_elements)
        for element in ws.elements:
            store.add(snap, element)
        scenes.append(ws)
    return {"snapshot": snap, "windows": scenes, "truncated": truncated, "scope": scope}


def matching(result: dict[str, Any], q: query.Query) -> list[refs.Element]:
    found = []
    for ws in result["windows"]:
        found.extend(e for e in ws.elements if query.matches(q, e.role, e.name, e.preview))
    return found


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def render_element(e: refs.Element) -> str:
    x, y, w, h = e.bounds
    parts = [f"  {e.ref} {e.role} \"{_clip(e.name, 48)}\""]
    if e.states:
        parts.append("[" + ",".join(e.states) + "]")
    parts.append(f"@{x},{y} {w}x{h}")
    if e.actions:
        parts.append("actions=" + ",".join(e.actions[:3]))
    if e.preview is not None:
        parts.append(f"value=\"{_clip(e.preview, 30)}\"")
    if e.source == "ocr":
        parts.append("(ocr)")
    return "  " + _clip(" ".join(parts).lstrip(), LINE_LIMIT - 2)


def seats_label(profile: toolkits.Profile) -> str:
    return {True: "agent+user", False: "user only", None: "unknown"}[profile.multi_seat]


def render(session: Any, result: dict[str, Any], q: query.Query | None) -> str:
    agent = session._agent
    head = f"snapshot s{result['snapshot']} · mode {session.mode}"
    if agent is not None and agent.output:
        head += f" · agent workspace {install.SEAT} ({agent.output})"
    lines = [head]
    if not result["windows"]:
        lines.append(f"(no windows in scope {result['scope']})")
    for ws in result["windows"]:
        w, c = ws.win, ws.win["content"]
        lines.append(
            f"window {w['id']} \"{_clip(w.get('name') or '', 40)}\" app={w.get('app_id') or w.get('class')} "
            f"toolkit={ws.profile.name} seats={seats_label(ws.profile)} ws={w.get('workspace')} "
            f"@{c['x']},{c['y']} {c['width']}x{c['height']} a11y={'on' if ws.a11y else 'off'}"
        )
        for note in ws.notes:
            lines.append(f"  note: {note}")
        shown = ws.elements if q is None else [e for e in ws.elements if query.matches(q, e.role, e.name, e.preview)]
        lines.extend(render_element(e) for e in shown)
    if result["truncated"]:
        lines.append("(truncated: raise max_elements or use a query)")
    return "\n".join(lines)


def observe(session: Any, scope: str | None = None, window: dict[str, Any] | None = None,
            query_arg: dict[str, Any] | None = None, max_elements: int = 150, ocr_mode: str | None = None,
            image: bool = False) -> core.Result:
    session.ensure_started()
    if session.mode in ("seat", "sandbox"):
        session.agent()
    scope = scope or ("window" if window else ("focused" if session.mode == "user" else "agent"))
    q = query.parse(query_arg) if query_arg is not None else None
    result = collect(session, scope, window, q, max_elements, ocr_mode or session.options.get("ocr", "auto"))
    content: list[dict[str, Any]] = [{"type": "text", "text": render(session, result, q)}]
    if image:
        import base64

        for ws in result["windows"]:
            try:
                data = capture.png(region=capture.window_region(ws.win))
            except core.ToolError:
                continue
            content.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(data).decode()})
    meta = {
        "scope": result["scope"],
        "windows": len(result["windows"]),
        "elements": sum(len(ws.elements) for ws in result["windows"]),
        "ocr": any(ws.ocr_used for ws in result["windows"]),
        "truncated": result["truncated"],
    }
    return core.Result(content, meta)


def rebound(session: Any, element: refs.Element) -> geometry.Box | None:
    """Fresh layout bounds for an a11y element (the window may have moved)."""
    client = session.a11y_or_none()
    if client is None or element.a11y is None:
        return None
    win = desktop.window_by_id(element.con_id)
    if win is None:
        raise core.ToolError(f"stale_ref {element.ref}: its window closed", code="stale_ref")
    ext = client.extents(element.a11y)
    frame_ext = client.extents(element.frame) if element.frame else None
    if ext is None:
        return None
    frame_ext = frame_ext or (0, 0, win["content"]["width"], win["content"]["height"])
    geo = window_geo(win, frame_ext, element.csd, desktop.get_outputs())
    return geometry.a11y_to_layout(geo, ext, element.device_px)


def ocr_unchanged(element: refs.Element) -> bool:
    """An OCR element is the same while its own box shows the same pixels in
    the same window region (a moved or resized window means re-observe)."""
    win = desktop.window_by_id(element.con_id)
    if win is None or element.region is None or element.crop_px is None or element.pixels is None:
        return False
    if capture.window_region(win) != element.region:
        return False
    now = capture.crop(capture.ppm2x(element.region), element.crop_px)
    return not capture.changed(element.pixels, now)


def validate(session: Any, ref: str) -> refs.Element:
    return refs.store_for(session).validate(ref, session.a11y_or_none(), lambda e: rebound(session, e), ocr_unchanged)

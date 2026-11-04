"""Element refs (`sN:k`) and their re-validation before use.

A ref names an element in one observe snapshot. Before any action uses it,
it is re-validated against the live screen: an a11y element must still exist
with the same role and name and be showing (its bounds are re-read, since
the window may have moved); an OCR element's own pixels must be unchanged.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from . import core

KEEP_SNAPSHOTS = 8
REF_RE = re.compile(r"^s(\d+):(\d+)$")

Box = tuple[int, int, int, int]


@dataclass
class Element:
    ref: str
    source: str  # "a11y" | "ocr"
    con_id: int
    role: str
    name: str
    states: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    bounds: Box = (0, 0, 0, 0)
    preview: str | None = None
    a11y: tuple[str, str] | None = None
    frame: tuple[str, str] | None = None
    csd: tuple[int, int] = (0, 0)
    device_px: bool = False
    region: dict[str, int] | None = None
    crop_px: tuple[int, int, int, int] | None = None  # OCR: the element's box in the 2x window capture
    pixels: bytes | None = None  # OCR: that crop at observe time
    app_id: str | None = None
    profile: str = "unknown"
    ifaces: list[str] = field(default_factory=list)


class RefStore:
    def __init__(self) -> None:
        self._snapshots: OrderedDict[int, dict[str, Element]] = OrderedDict()
        self._next = 1

    def new_snapshot(self) -> int:
        snap = self._next
        self._next += 1
        self._snapshots[snap] = {}
        while len(self._snapshots) > KEEP_SNAPSHOTS:
            self._snapshots.popitem(last=False)
        return snap

    def add(self, snap: int, element: Element) -> str:
        bucket = self._snapshots.setdefault(snap, {})
        ref = f"s{snap}:{len(bucket) + 1}"
        element.ref = ref
        bucket[ref] = element
        return ref

    def get(self, ref: str) -> Element:
        if not isinstance(ref, str) or not REF_RE.match(ref):
            raise core.ToolError(f"invalid ref {ref!r}; refs look like s3:12", code="unknown_ref")
        snap = int(REF_RE.match(ref).group(1))  # type: ignore[union-attr]
        bucket = self._snapshots.get(snap)
        if bucket is None or ref not in bucket:
            raise core.ToolError(f"unknown_ref {ref}: not in the last {KEEP_SNAPSHOTS} snapshots; call observe again",
                                 code="unknown_ref")
        return bucket[ref]

    def validate(self, ref: str, client: Any, rebound: Callable[[Element], Box | None],
                 still_same: Callable[[Element], bool]) -> Element:
        element = self.get(ref)
        stale = core.ToolError(f"stale_ref {ref}: element changed or disappeared; call observe again",
                               code="stale_ref")
        if element.source == "a11y":
            if client is None or element.a11y is None:
                raise stale
            try:
                node = client.node(element.a11y)
            except core.ToolError as exc:
                raise stale from exc
            if node.role != element.role or (node.name or "") != (element.name or "") or "showing" not in node.states:
                raise stale
            element.states = sorted(node.states)
            bounds = rebound(element)
            if bounds is not None:
                element.bounds = bounds
            return element
        if element.region is None or element.pixels is None:
            raise stale
        try:
            same = still_same(element)
        except core.ToolError as exc:
            raise stale from exc
        if not same:
            raise stale
        return element


def store_for(session: Any) -> RefStore:
    store = getattr(session, "_refs", None)
    if store is None:
        store = RefStore()
        session._refs = store
    return store

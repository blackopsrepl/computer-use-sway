"""Shared harness for the integration suite: a product sandbox per test.

Everything runs in a private nested sway (`session` sandbox mode). The
user is seat0, driven directly by the session virtual devices. Nothing here touches the live session.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Callable

from computer_use_sway import desktop, sandbox, session
from computer_use_sway.server import call_tool

APPS = Path(__file__).resolve().parent / "apps"
sys.path.insert(0, str(APPS))
import targets  # noqa: E402

PY = "/usr/bin/python3"
ORACLE = str(APPS / "oracle_gtk3.py")
BINARIES = {"gtk3": PY, "gtk3-csd": PY, "gtk4": PY, "qt6": "qml6", "firefox": "firefox", "chromium": "chromium",
            "chromium-noforce": "chromium", "electron": targets.ELECTRON}


def available(name: str) -> bool:
    binary = BINARIES.get(name, name)
    return bool(shutil.which(binary) or os.access(binary, os.X_OK))


def require(case: unittest.TestCase, name: str) -> None:
    """Skip, visibly, a case whose app is not installed: a matrix that passed
    over missing apps in silence could pass having checked nothing."""
    if not available(name):
        case.skipTest(f"{name} is not installed")


def needs_sway(cls: type) -> type:
    return unittest.skipUnless(shutil.which("sway") and shutil.which("dbus-daemon") and shutil.which("grim"),
                               "needs sway, dbus-daemon and grim")(cls)


class Box:
    """One sandbox session, installed as the process-wide current session."""

    def __init__(self, scale: int = 1, options: dict[str, Any] | None = None) -> None:
        self.scale = scale
        self.options = options
        self.sess = session.Session()

    def __enter__(self) -> "Box":
        self.sess.sandbox_factory = functools.partial(
            sandbox.Sandbox, width=1280 * self.scale, height=800 * self.scale, scale=float(self.scale))
        session.reset_for_tests(self.sess)
        self.sess.start("sandbox", self.options)
        self.root = self.sess.sandbox().root
        self.assets = str(self.root / "assets")
        targets.write_assets(self.assets)
        self.log = str(self.root / "oracle.jsonl")
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            self.sess.stop()
        finally:
            session.reset_for_tests(None)

    # -- tools ---------------------------------------------------------------
    def call(self, tool: str, args: dict[str, Any]) -> Any:
        content, _meta = call_tool(tool, args)
        text = content[0]["text"]
        try:
            return json.loads(text)
        except ValueError:
            return text

    def launch(self, name: str, **extra: Any) -> dict[str, Any]:
        argv, env = targets.argv(name, self.assets)
        out = self.call("launch", {"argv": argv, "env": env, "timeout_ms": 60000, **extra})
        win = out["window"]
        self.wait(lambda: (self.window(win["id"]) or {}).get("name", "").startswith("T:"), 20)
        return win

    def oracle(self, name: str) -> dict[str, Any]:
        """A user window (seat0's workspace), logging every input it gets."""
        self.sess.sandbox().spawn([PY, ORACLE, name, self.log])
        found = self.wait(lambda: next((w for w in desktop.all_windows() if w.get("app_id") == f"oracle-{name}"),
                                       None), 20)
        self.wait(lambda: self.events(name, "geometry"), 10)
        return found

    # -- observation ---------------------------------------------------------
    def window(self, con_id: int) -> dict[str, Any] | None:
        return desktop.window_by_id(con_id)

    def title(self, con_id: int) -> str:
        return (self.window(con_id) or {}).get("name") or ""

    def events(self, name: str, kind: str | None = None) -> list[dict[str, Any]]:
        try:
            with open(self.log, encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if r["win"] == name and (kind is None or r["ev"] == kind)]

    def text_of(self, name: str) -> str:
        rows = self.events(name, "text")
        return rows[-1]["text"] if rows else ""

    def seat_focus(self, seat: str = "seat0") -> int | None:
        return desktop.seat_focus(seat)

    def user_keyboard(self) -> Any:
        return self.sess.user_devices().keyboard

    def user_pointer(self) -> Any:
        return self.sess.user_devices().pointer

    @staticmethod
    def wait(predicate: Callable[[], Any], timeout: float, interval: float = 0.1) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError("condition not met in time")
            time.sleep(interval)

    def ref(self, con_id: int, name: str, role: str | None = None, ocr: str | None = None) -> str:
        args: dict[str, Any] = {"scope": "window", "window": {"con_id": con_id},
                                "query": {"name": name, "match": "exact", **({"role": role} if role else {})}}
        if ocr:
            args["ocr"] = ocr
            args["query"] = {"text": name, "match": "exact"}
        text = self.call("observe", args)
        for line in text.splitlines():
            parts = line.split()
            if not (parts and parts[0].startswith("s") and ":" in parts[0] and f'"{name}"' in line):
                continue
            if ocr and not line.endswith("(ocr)"):
                continue
            return parts[0]
        raise AssertionError(f"no element {name!r} in:\n{text}")

    def center_of(self, oracle_window: dict[str, Any], name: str, widget: str) -> tuple[int, int]:
        geo = self.events(name, "geometry")[-1]["widgets"][widget]
        win = self.window(oracle_window["id"]) or oracle_window
        c = win["content"]
        return c["x"] + geo["x"] + geo["w"] // 2, c["y"] + geo["y"] + geo["h"] // 2

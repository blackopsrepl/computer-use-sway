"""The process-wide session: mode, options, transports, agent, sentinel.

Modes:
- seat (default): the agent works on its own seat and workspace beside the user.
- user: the agent drives the user's seat0 directly (for recordings of the
  visible screen, or when the agent seat is not installed).
- sandbox: a private nested sway; nothing touches the live session.
"""

from __future__ import annotations

import atexit
import os
import shutil
from typing import Any

from . import core, desktop, swayipc, timeline

MODES = ("seat", "user", "sandbox")
DEFAULT_OPTIONS: dict[str, Any] = {
    "foreground": "idle",
    "idle_ms": 1500,
    "idle_max_wait_ms": 10000,
    "foreground_pointer": False,
    "settle_ms": 600,
    "ocr": "auto",
}
OPTION_ENUMS = {"foreground": ("idle", "always", "never"), "ocr": ("auto", "always", "never")}
OPTION_BOUNDS = {"idle_ms": (0, 60000), "idle_max_wait_ms": (0, 120000), "settle_ms": (0, 5000)}
EVENT_KINDS = ["workspace", "window", "output", "binding", "shutdown"]
ENV_KEYS = ("SWAYSOCK", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS")


def validate_options(options: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base or DEFAULT_OPTIONS)
    if options is None:
        return merged
    if not isinstance(options, dict):
        raise core.ToolError("options must be an object")
    for key, value in options.items():
        if key not in DEFAULT_OPTIONS:
            raise core.ToolError(f"unknown session option: {key}")
        if key in OPTION_ENUMS:
            if value not in OPTION_ENUMS[key]:
                raise core.ToolError(f"{key} must be one of {', '.join(OPTION_ENUMS[key])}")
        elif key in OPTION_BOUNDS:
            low, high = OPTION_BOUNDS[key]
            number = core.strict_int(value, key)
            if not low <= number <= high:
                raise core.ToolError(f"{key} must be between {low} and {high}")
        elif key == "foreground_pointer" and not isinstance(value, bool):
            raise core.ToolError("foreground_pointer must be a boolean")
        merged[key] = value
    return merged


class Session:
    def __init__(self) -> None:
        self.mode: str | None = None
        self.options: dict[str, Any] = dict(DEFAULT_OPTIONS)
        self.log = swayipc.EventLog()
        self._ipc: swayipc.Ipc | None = None
        self._sub: swayipc.Subscription | None = None
        self._conn: Any = None
        self._agent: Any = None
        self._user_devices: Any = None
        self._sentinel: Any = None
        self._a11y: Any = None
        self._sandbox: Any = None
        self._saved_env: dict[str, str | None] | None = None
        self.user_seat: str | None = None

    # -- transports ----------------------------------------------------------
    def ipc(self) -> swayipc.Ipc:
        if self._ipc is None:
            core.require_session()
            self._ipc = swayipc.connect()
        return self._ipc

    def _ensure_subscription(self) -> None:
        if self._sub is None or self._sub.dead:
            self._sub = swayipc.Subscription(self.ipc().path, EVENT_KINDS, self.log)
            self._sub.start()

    def conn(self) -> Any:
        if self._conn is None:
            from . import wire

            self._conn = wire.Connection()
        return self._conn

    # -- lifecycle -----------------------------------------------------------
    def ensure_started(self) -> None:
        if self.mode is None:
            self.start("seat")

    def start(self, mode: str = "seat", options: Any = None) -> dict[str, Any]:
        if mode not in MODES:
            raise core.ToolError("mode must be seat, user, or sandbox")
        opts = validate_options(options)
        if self.mode is not None:
            self.stop()
        self.options = opts
        if mode == "sandbox":
            self._enter_sandbox()
        self.mode = mode
        self.ipc()
        self.user_seat = desktop.detect_seat()
        self._ensure_subscription()
        result: dict[str, Any] = {"mode": mode}
        if mode in ("seat", "sandbox"):
            result["agent"] = self.agent().status()
        return dict(self.status(), started=True, **result)

    def _enter_sandbox(self) -> None:
        from . import sandbox

        box = sandbox.Sandbox()
        box.start()
        self._sandbox = box
        self._saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
        timeline.pin_runtime(self._saved_env.get("XDG_RUNTIME_DIR"))
        for key in ENV_KEYS:
            if key in box.env:
                os.environ[key] = box.env[key]
            else:
                os.environ.pop(key, None)
        os.environ["XDG_RUNTIME_DIR"] = str(box.root)
        swayipc.reset()

    def _leave_sandbox(self) -> None:
        if self._saved_env is not None:
            for key, value in self._saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            self._saved_env = None
        timeline.pin_runtime(None)
        if self._sandbox is not None:
            self._sandbox.stop()
            self._sandbox = None
        swayipc.reset()

    def stop(self) -> dict[str, Any]:
        was = self.mode
        for closer in (
            lambda: self._agent and self._agent.stop(),
            lambda: self._user_devices and self._user_devices.destroy(),
            lambda: self._a11y and self._a11y.close(),
            lambda: self._sub and self._sub.stop(),
            lambda: self._conn and self._conn.close(),
        ):
            try:
                closer()
            except Exception as exc:  # stopping must always finish
                core.eprint(f"session stop: {exc}")
        self._agent = self._user_devices = self._a11y = self._sub = self._conn = self._sentinel = None
        self._ipc = None
        if was == "sandbox":
            self._leave_sandbox()
        self.mode = None
        return {"stopped": True, "mode": was}

    # -- components ----------------------------------------------------------
    def agent(self) -> Any:
        self.ensure_started()
        if self.mode == "user":
            raise core.ToolError("the agent seat is not used in user mode; start a seat session", code="user_mode")
        if self._agent is None:
            from . import agent

            self._agent = agent.Agent(self.ipc(), self.conn(), self.log, user_seat=self.user_seat or "seat0")
        self._ensure_subscription()
        self._agent.ensure_ready()
        return self._agent

    def user_devices(self) -> Any:
        self.ensure_started()
        if self._user_devices is None:
            from . import vinput

            self._user_devices = vinput.SeatDevices(self.conn(), self.user_seat or "seat0")
        return self._user_devices

    def devices(self, seat: str) -> Any:
        if seat == "agent":
            return self.agent().devices
        if seat == "user":
            return self.user_devices()
        raise core.ToolError("seat must be agent or user")

    def default_seat(self) -> str:
        self.ensure_started()
        return "user" if self.mode == "user" else "agent"

    def sentinel(self) -> Any:
        self.ensure_started()
        if self._sentinel is None:
            from . import sentinel

            self._sentinel = sentinel.Sentinel(self.ipc(), self.conn(), self.user_seat or "seat0")
        return self._sentinel

    def a11y(self) -> Any:
        if self._a11y is None:
            from . import atspi

            self._a11y = atspi.Client()
        return self._a11y

    def a11y_or_none(self) -> Any:
        try:
            return self.a11y()
        except core.ToolError:
            return None

    def sandbox(self) -> Any:
        return self._sandbox

    def status(self) -> dict[str, Any]:
        a11y: dict[str, Any] = {"bus": False}
        if self._a11y is not None:
            a11y = self._a11y.status()
        return {
            "mode": self.mode,
            "options": self.options,
            "user_seat": self.user_seat,
            "agent": self._agent.status() if self._agent is not None else None,
            "a11y": a11y,
            "sandbox": str(self._sandbox.root) if self._sandbox is not None else None,
            "binaries": {name: shutil.which(name) for name in ("grim", "tesseract", "wl-copy", "wl-paste")},
        }


_current: Session | None = None


def _stop_at_exit() -> None:
    if _current is not None and _current.mode is not None:
        try:
            _current.stop()
        except Exception as exc:
            core.eprint(f"session stop at exit failed: {exc!r}")


def current() -> Session:
    global _current
    if _current is None:
        _current = Session()
        atexit.register(_stop_at_exit)
    return _current


def reset_for_tests(session: Session | None = None) -> None:
    global _current
    _current = session

"""`launch`: start an app on the agent workspace, beside the user.

In seat/sandbox mode the app is exec'd by sway itself through the agent
binding (`bindsym <LAUNCH_KEY> exec <launcher>`), fired from the agent
keyboard while the agent seat sits on workspace `cus-agent`: sway then maps
the new window there, for the agent seat, without touching seat0 (O5, O6,
O9, O10). The launcher script is rewritten per launch and reset to a no-op
placeholder afterwards so a stray key tap does nothing.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from typing import Any

from . import core, desktop, install, toolkits

ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
STRIP = ("XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID")


def validate(argv: Any, cwd: Any, env: Any, timeout_ms: Any) -> tuple[list[str], str | None, dict[str, str], int]:
    if not isinstance(argv, list) or not argv or len(argv) > 64 or not all(isinstance(a, str) for a in argv):
        raise core.ToolError("argv must be a non-empty list of up to 64 strings")
    if any("\x00" in a for a in argv):
        raise core.ToolError("argv must not contain NUL bytes")
    program = argv[0]
    if not (os.path.isabs(program) and os.access(program, os.X_OK)) and shutil.which(program) is None:
        raise core.ToolError(f"program not found or not executable: {program}")
    if cwd is not None and (not isinstance(cwd, str) or not os.path.isdir(cwd)):
        raise core.ToolError("cwd must be an existing directory")
    env = env or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise core.ToolError("env must be an object of string values")
    for key in env:
        if not ENV_KEY.match(key):
            raise core.ToolError(f"invalid environment variable name: {key}")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1000 <= timeout_ms <= 120000:
        raise core.ToolError("timeout_ms must be between 1000 and 120000")
    return list(argv), cwd, env, timeout_ms


def with_a11y_flags(argv: list[str], a11y: bool) -> tuple[list[str], toolkits.Profile]:
    profile = toolkits.classify(None, None, argv0=os.path.basename(argv[0]))
    if a11y:
        for flag in profile.launch_flags:
            if flag not in argv:
                argv = [argv[0], flag, *argv[1:]]
    return argv, profile


def script(argv: list[str], cwd: str | None, env: dict[str, str]) -> str:
    lines = ["#!/bin/sh"]
    if cwd:
        lines.append(f"cd {shlex.quote(cwd)} || exit 1")
    for key, value in env.items():
        lines.append(f"export {key}={shlex.quote(value)}")
    unset = " ".join(f"-u {name}" for name in STRIP)
    lines.append(f"exec env {unset} {shlex.join(argv)}")
    return "\n".join(lines) + "\n"


def write_launcher(text: str) -> None:
    path = install.launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o700)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o700)


def _wait_new_window(session: Any, before: set[int], t0: float, timeout_s: float,
                     workspace: str | None) -> dict[str, Any] | None:
    deadline = t0 + timeout_s
    while time.monotonic() < deadline:
        event = session.log.wait_for(
            lambda e: e[1] == "window" and e[2].get("change") == "new"
            and (e[2].get("container") or {}).get("id") not in before,
            t0, 0.25,
        )
        candidates = [w for w in desktop.all_windows() if w["id"] not in before]
        if workspace is not None:
            on_ws = [w for w in candidates if w.get("workspace") == workspace]
            candidates = on_ws or candidates
        if candidates:
            return candidates[0]
        if event is not None:
            continue
    return None


def _settle_title(con_id: int, limit_s: float = 1.5) -> dict[str, Any] | None:
    deadline = time.monotonic() + limit_s
    win = desktop.window_by_id(con_id)
    while win is not None and not win.get("name") and time.monotonic() < deadline:
        time.sleep(0.1)
        win = desktop.window_by_id(con_id)
    return win


def _a11y_ready(session: Any, win: dict[str, Any], limit_s: float = 3.0) -> bool:
    client = session.a11y_or_none()
    if client is None or not win.get("pid"):
        return False
    try:
        client.ensure_enabled()
    except core.ToolError:
        return False
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        try:
            if client.app_for_window(int(win["pid"])) is not None:
                return True
        except core.ToolError:
            return False
        time.sleep(0.2)
    return False


def launch(session: Any, argv: Any, cwd: Any = None, env: Any = None, timeout_ms: Any = 20000,
           a11y: bool = True) -> core.Result:
    session.ensure_started()
    argv, cwd, env, timeout_ms = validate(argv, cwd, env, timeout_ms)
    argv, profile = with_a11y_flags(argv, a11y)
    if a11y:
        client = session.a11y_or_none()
        if client is not None:
            try:
                client.ensure_enabled()
            except core.ToolError:
                pass
    before = {w["id"] for w in desktop.all_windows()}
    t0 = time.monotonic()
    meta = {"program": os.path.basename(argv[0])}
    if session.mode == "user":
        child_env = {k: v for k, v in os.environ.items() if k not in STRIP}
        child_env.update(env)
        subprocess.Popen(argv, cwd=cwd, env=child_env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        win = _wait_new_window(session, before, t0, timeout_ms / 1000.0, None)
        disturbed = True
    else:
        agent = session.agent()
        sentinel = session.sentinel()
        snap = sentinel.snapshot()
        try:
            write_launcher(script(argv, cwd, env))
            agent.to_workspace()
            agent.tap(install.LAUNCH_KEY)
            win = _wait_new_window(session, before, t0, timeout_ms / 1000.0, install.SEAT)
            if win is not None:
                win = _settle_title(int(win["id"])) or win
                agent.focus(int(win["id"]))
        finally:
            write_launcher(install.PLACEHOLDER)
            disturbed = sentinel.restore(snap)
    if win is None:
        appeared = [w["id"] for w in desktop.all_windows() if w["id"] not in before]
        raise core.ToolError(
            f"{os.path.basename(argv[0])} opened no window within {timeout_ms} ms"
            + (f" (new windows elsewhere: {appeared})" if appeared else "")
            + "; check that the program starts from a terminal",
            code="launch_timeout",
        )
    ready = _a11y_ready(session, win) if a11y else False
    profile_seen = toolkits.classify(None, win.get("app_id") or win.get("class"), argv0=os.path.basename(argv[0]))
    if profile_seen.name == "unknown":
        profile_seen = profile
    result = {
        "launched": True,
        "window": win,
        "profile": profile_seen.name,
        "seats": {True: "agent+user", False: "user only", None: "unknown"}[profile_seen.multi_seat],
        "a11y": ready,
        "disturbed_user": disturbed,
        "flags_added": [f for f in profile.launch_flags if a11y],
    }
    return core.text_result(result, dict(meta, a11y=ready, disturbed_user=disturbed))

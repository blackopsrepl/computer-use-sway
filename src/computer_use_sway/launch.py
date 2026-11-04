"""`launch`: start an app on the agent workspace, beside the user.

In seat/sandbox mode the app is exec'd by sway itself through the agent
binding (`bindsym <LAUNCH_KEY> exec <launcher>`), fired from the agent
keyboard while the agent seat sits on workspace `cus-agent`: sway then maps
the new window there, for the agent seat, without touching seat0 (O5, O6,
O9, O10). The launcher script is rewritten per launch and reset to a no-op
placeholder afterwards so a stray key tap does nothing. The activation
token sway passes to the exec is kept: it is what places Qt and Chromium
windows (O11).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import core, desktop, install, toolkits

ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Only for user mode, where the child inherits *our* environment and a stale
# token there would be wrong. In seat mode the token sway's exec creates is
# what places the window on the agent workspace: Qt and Chromium map on the
# user's workspace and take seat0 focus without it (measured, FINDINGS O11).
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


REAPER = Path(__file__).with_name("reaper.py")


def pid_file_path() -> Path:
    return install.launcher_path().with_name("child.pid")


def script(argv: list[str], cwd: str | None, env: dict[str, str], pid_file: Path | None = None) -> str:
    lines = ["#!/bin/sh"]
    if cwd:
        lines.append(f"cd {shlex.quote(cwd)} || exit 1")
    for key, value in env.items():
        lines.append(f"export {key}={shlex.quote(value)}")
    if pid_file is not None:
        lines.append(f"export COMPUTER_USE_SWAY_PIDFILE={shlex.quote(str(pid_file))}")
    # The reaper keeps sway's launch pid alive as the subreaper of everything
    # the app forks, so sway's pid-ancestry placement holds for apps whose
    # launcher process exits (Electron).
    lines.append(f"exec {shlex.join([sys.executable, str(REAPER), *argv])}")
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


def _register_child(session: Any, pid_file: Path, limit_s: float = 3.0) -> None:
    """Assign the app's process to the agent workspace as soon as the reaper
    reports it, so windows after the first (which sway places through the
    launch context, then consumes it) map there too."""
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        try:
            pid = int(pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.01)
            continue
        session.agent().placement.expect(pid)
        return


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


def _settle(con_id: int, limit_s: float = 2.0, stable_s: float = 0.4) -> dict[str, Any] | None:
    """Wait for a title and a geometry that held still for stable_s.

    Right after mapping, sway reports the window without its title bar
    offset (y=0) for up to ~200 ms; geometry read then is wrong by the title
    bar height (measured).
    """
    deadline = time.monotonic() + limit_s
    win = desktop.window_by_id(con_id)
    last, since = None, time.monotonic()
    while win is not None and time.monotonic() < deadline:
        key = (win.get("name"), tuple(sorted((win.get("rect") or {}).items())), tuple(sorted(win["content"].items())))
        if key != last:
            last, since = key, time.monotonic()
        elif win.get("name") and time.monotonic() - since >= stable_s:
            break
        time.sleep(0.03)
        win = desktop.window_by_id(con_id)
    return win


def _a11y_ready(session: Any, win: dict[str, Any], limit_s: float = 5.0) -> bool:
    """True once the window's accessible frame exists with a populated tree and
    no page still loading: an app appears on the bus before its widgets do."""
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
            app = client.app_for_window(int(win["pid"]))
            title = (desktop.window_by_id(int(win["id"])) or win).get("name")
            frame = client.frame_for_window(app, title) if app is not None else None
            if frame is not None:
                nodes = client.walk(frame, max_nodes=400)
                # A browser's frame is populated before its page: wait until
                # no document is still loading ("busy"), or observe sees chrome only.
                loading = any(n.role.startswith("document") and "busy" in n.states for n in nodes)
                if len(nodes) > 3 and not loading:
                    return True
        except core.ToolError:
            pass
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
        if win is not None:
            win = _settle(int(win["id"])) or win
        disturbed = True
    else:
        agent = session.agent()
        sentinel = session.sentinel()
        snap = sentinel.snapshot()
        try:
            pid_file = pid_file_path()
            pid_file.unlink(missing_ok=True)
            write_launcher(script(argv, cwd, env, pid_file))
            agent.to_workspace()
            agent.tap(install.LAUNCH_KEY)
            _register_child(session, pid_file)
            win = _wait_new_window(session, before, t0, timeout_ms / 1000.0, install.SEAT)
            if win is not None and win.get("workspace") != install.SEAT:
                # Placement failed (e.g. the app handed the window to an already
                # running instance): move it off the user's screen.
                moved_from = win.get("workspace")
                desktop.ipc().command(f"[con_id={int(win['id'])}] move container to workspace {install.SEAT}")
                win = desktop.window_by_id(int(win["id"])) or win
                win["moved_from"] = moved_from
            if win is not None:
                agent.placement.adopt(win)
                agent.focus(int(win["id"]))
                win = _settle(int(win["id"])) or win
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

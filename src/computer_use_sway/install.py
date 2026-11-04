"""The agent seat's sway config fragment: render, install, inspect.

The agent seat must be declared in the config. A seat created at runtime is
destroyed by `reload`, and its virtual devices then fall back to seat0 and
type into the user's window (FINDINGS S7). Bindings live here too, because a
binding fired by the agent keyboard is the only way to run a command *as the
agent seat*: IPC commands always act on seat0 (S4/S5).
"""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path
from typing import Any

from . import core, keymap, swayipc

SEAT = "cus-agent"
FOCUS_KEY = keymap.FOCUS_KEY
LAUNCH_KEY = keymap.LAUNCH_KEY
FOCUS_MARK = "_cus_focus"
PLACEHOLDER = "#!/bin/sh\nexit 0\n"


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base)


def config_path() -> Path:
    return config_home() / "sway" / "config.d" / "computer-use-sway.conf"


def runtime_root() -> Path:
    core.ensure_session_environment()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise core.ToolError("XDG_RUNTIME_DIR is not set")
    return Path(runtime) / "computer-use-sway"


def launcher_path(root: Path | None = None) -> Path:
    return (root or runtime_root()) / "launch" / "next.sh"


def render(launcher: Path | None = None) -> str:
    target = launcher or launcher_path()
    return (
        "# computer-use-sway agent seat — managed by `computer-use-sway --install-sway-config`.\n"
        "# Do not add a `workspace <name>` binding here: sway donates such names to new\n"
        "# workspaces on any output (see docs/plan-1.0/FINDINGS.md O9).\n"
        f"seat {SEAT} fallback false\n"
        f'bindsym --no-repeat {FOCUS_KEY} [con_mark="{FOCUS_MARK}"] focus\n'
        f"bindsym --no-repeat {LAUNCH_KEY} exec {target}\n"
    )


def _expand(pattern: str) -> str:
    pattern = pattern.strip().strip('"').strip("'")
    home = os.path.expanduser("~")
    pattern = pattern.replace("$HOME", home).replace("${HOME}", home)
    if "$XDG_CONFIG_HOME" in pattern or "${XDG_CONFIG_HOME}" in pattern:
        base = str(config_home())
        pattern = pattern.replace("${XDG_CONFIG_HOME}", base).replace("$XDG_CONFIG_HOME", base)
    return os.path.expanduser(pattern)


def included(config_text: str, path: Path) -> bool:
    """Whether some `include` line of the live config covers `path`."""
    target = os.path.realpath(path)
    for raw in config_text.splitlines():
        line = raw.strip()
        if not line.startswith("include "):
            continue
        pattern = _expand(line[len("include "):])
        if fnmatch.fnmatch(target, os.path.realpath(pattern)) or fnmatch.fnmatch(str(path), pattern):
            return True
        if os.path.realpath(pattern) == target:
            return True
    return False


def conflicts(config_text: str) -> list[str]:
    """Bindings of our keys outside our own fragment."""
    ours = set(render().splitlines())
    found = []
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("bindsym") and (FOCUS_KEY in line or LAUNCH_KEY in line) and line not in ours:
            found.append(line)
    return found


def _live_config() -> str:
    return swayipc.connect().config()


def seat_live(ipc: Any | None = None) -> bool:
    try:
        seats = (ipc or swayipc.connect()).seats()
    except core.ToolError:
        return False
    return any(seat.get("name") == SEAT for seat in seats)


def status(ipc: Any | None = None) -> dict[str, Any]:
    path = config_path()
    report: dict[str, Any] = {"file": str(path), "file_present": path.is_file(), "config_path": str(path)}
    try:
        text = (ipc.config() if ipc is not None else _live_config())
        report["included"] = included(text, path)
        report["conflicts"] = conflicts(text)
        report["seat_live"] = seat_live(ipc)
    except core.ToolError as exc:
        report.update({"included": False, "conflicts": [], "seat_live": False, "error": str(exc)})
    if report["file_present"]:
        try:
            report["current"] = path.read_text(encoding="utf-8") == render()
        except (OSError, core.ToolError):
            report["current"] = False
        if not report["current"]:
            report["reason"] = "launcher path mismatch or outdated fragment; re-run --install-sway-config"
    report["ok"] = bool(
        report["file_present"] and report["included"] and report["seat_live"] and not report["conflicts"]
        and report.get("current", False)
    )
    return report


def write_placeholder(launcher: Path) -> None:
    launcher.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(launcher.parent, 0o700)
    fd = os.open(launcher, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o700)
    try:
        os.write(fd, PLACEHOLDER.encode())
    finally:
        os.close(fd)
    os.chmod(launcher, 0o700)


def install(reload: bool = True, ipc: Any = None) -> dict[str, Any]:
    ipc = ipc if ipc is not None else swayipc.connect()
    path = config_path()
    text = ipc.config()
    if not included(text, path):
        raise core.ToolError(
            f"your sway config does not include {path.parent}/*; add the line "
            f"`include {path.parent}/*` to your sway config, then run --install-sway-config again"
        )
    clashes = conflicts(text)
    if clashes:
        raise core.ToolError("these bindings already use the agent keys: " + "; ".join(clashes))
    write_placeholder(launcher_path())
    content = render()
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o644)
    if reload:
        ipc.command("reload")
        deadline = time.monotonic() + 2.0
        while not seat_live(ipc):
            if time.monotonic() > deadline:
                raise core.ToolError("sway reloaded but the cus-agent seat did not appear; check `swaymsg -t get_config`")
            time.sleep(0.05)
    return status(ipc)


def uninstall(reload: bool = True, ipc: Any = None) -> dict[str, Any]:
    ipc = ipc if ipc is not None else swayipc.connect()
    path = config_path()
    if path.is_file():
        path.unlink()
    if reload:
        ipc.command("reload")
    return status(ipc)

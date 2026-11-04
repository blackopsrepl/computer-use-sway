"""Command-line facade: MCP server, install helpers, self-test, doctor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any

from . import core, desktop, install
from .core import ToolError, command_available, command_argv, require_binaries
from .timeline import stream_path
from .version import SERVER_NAME, SERVER_VERSION

WAYLAND_GLOBALS = (
    "zwlr_virtual_pointer_manager_v1",
    "zwp_virtual_keyboard_manager_v1",
    "ext_idle_notifier_v1",
    "zwlr_screencopy_manager_v1",
)


def install_codex_mcp() -> int:
    require_binaries(["codex"])
    expected_command = command_argv()
    expected_args = expected_command[1:]
    existing = subprocess.run(
        ["codex", "mcp", "get", SERVER_NAME, "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if existing.returncode == 0:
        try:
            data = json.loads(existing.stdout)
        except json.JSONDecodeError:
            data = {}
        transport = data.get("transport") or {}
        command = data.get("command") or transport.get("command")
        args = data.get("args")
        if args is None:
            args = transport.get("args") or []
        if command == expected_command[0] and args == expected_args:
            print(f"{SERVER_NAME} MCP entry already installed")
            return 0
        subprocess.run(["codex", "mcp", "remove", SERVER_NAME], check=False)
    add = subprocess.run(["codex", "mcp", "add", SERVER_NAME, "--", *expected_command], check=False)
    if add.returncode != 0:
        return add.returncode
    print(f"installed {SERVER_NAME} MCP entry")
    return 0


def uninstall_codex_mcp() -> int:
    require_binaries(["codex"])
    existing = subprocess.run(
        ["codex", "mcp", "get", SERVER_NAME, "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if existing.returncode != 0:
        print(f"{SERVER_NAME} MCP entry is not installed")
        return 0
    removed = subprocess.run(["codex", "mcp", "remove", SERVER_NAME], check=False)
    if removed.returncode == 0:
        print(f"removed {SERVER_NAME} MCP entry")
    return removed.returncode


def environment() -> dict[str, Any]:
    return {key: os.environ.get(key) for key in ("WAYLAND_DISPLAY", "SWAYSOCK", "XDG_RUNTIME_DIR")}


def wayland_report() -> dict[str, Any]:
    from . import wire

    try:
        conn = wire.Connection()
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        present = {name: conn.has_global(name) for name in WAYLAND_GLOBALS}
        seats = sorted(conn.seats())
    finally:
        conn.close()
    return {"ok": present["zwlr_virtual_pointer_manager_v1"] and present["zwp_virtual_keyboard_manager_v1"],
            "globals": present, "seats": seats}


def a11y_report() -> dict[str, Any]:
    try:
        from . import atspi

        client = atspi.Client()
    except (ToolError, ImportError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        report = client.status()
        report["apps"] = len(client.apps())
        report["ok"] = True
        return report
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.close()


def ocr_report() -> dict[str, Any]:
    path = shutil.which("tesseract")
    version = None
    if path:
        try:
            version = core.run_command([path, "--version"], timeout=5.0).text.splitlines()[0]
        except (ToolError, IndexError):
            version = None
    return {"tesseract": path, "version": version, "grim": shutil.which("grim")}


def self_test() -> int:
    core.require_session()
    seat_status = install.status()
    wayland = wayland_report()
    summary = {
        "ok": bool(seat_status["ok"] and wayland["ok"]),
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "environment": environment(),
        "user_seat": desktop.detect_seat(),
        "agent_seat": seat_status,
        "wayland": wayland,
        "ocr": ocr_report(),
        "outputs": [
            {"name": o.get("name"), "rect": o.get("rect"), "scale": o.get("scale")} for o in desktop.get_outputs()
        ],
        "focused_window": desktop.get_focused_window(),
    }
    if not seat_status["ok"]:
        summary["remedy"] = "run: computer-use-sway --install-sway-config"
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


def electron_without_a11y() -> list[dict[str, Any]]:
    from . import toolkits

    try:
        from . import atspi

        client = atspi.Client()
    except (ToolError, ImportError):
        return []
    found = []
    try:
        for win in desktop.all_windows():
            profile = toolkits.classify(None, win.get("app_id") or win.get("class"))
            if profile.name != "chromium" or not win.get("pid"):
                continue
            app = client.app_for_window(int(win["pid"]))
            nodes = len(client.walk(app, max_nodes=8)) if app else 0
            if nodes <= 3:
                found.append({"con_id": win["id"], "app_id": win.get("app_id"),
                              "hint": "relaunch with --force-renderer-accessibility (launch does this)"})
    except ToolError:
        pass
    finally:
        client.close()
    return found


def doctor() -> int:
    stream = stream_path()
    report: dict[str, Any] = {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION, "command": command_argv(),
                   "module_path": __file__},
        "timeline_stream": str(stream) if stream is not None else None,
        "environment": environment(),
        "binaries": {name: shutil.which(name) for name in ("python3", "grim", "tesseract", "wl-copy", "wl-paste",
                                                           "codex")},
        "obsolete": {"wtype": "no longer used", "/dev/uinput": "no longer used"},
        "session": None,
        "codex_mcp": None,
    }
    try:
        core.require_session()
        report["session"] = {
            "ok": True,
            "seat": desktop.detect_seat(),
            "outputs": [
                {"name": o.get("name"), "active": o.get("active"), "rect": o.get("rect"), "scale": o.get("scale"),
                 "transform": o.get("transform"), "current_mode": o.get("current_mode")}
                for o in desktop.get_outputs()
            ],
            "focused_window": desktop.get_focused_window(),
        }
        report["agent_seat"] = install.status()
        report["wayland"] = wayland_report()
        report["a11y"] = a11y_report()
        report["electron_without_a11y"] = electron_without_a11y()
    except ToolError as exc:
        report["session"] = {"ok": False, "error": str(exc)}
    report["ocr"] = ocr_report()
    if command_available("codex"):
        proc = subprocess.run(["codex", "mcp", "get", SERVER_NAME, "--json"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if proc.returncode == 0:
            try:
                report["codex_mcp"] = json.loads(proc.stdout)
            except json.JSONDecodeError:
                report["codex_mcp"] = {"raw": proc.stdout}
        else:
            report["codex_mcp"] = {"installed": False, "stderr": proc.stderr.strip()}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def sway_config(action: str) -> int:
    core.require_session()
    status = install.install() if action == "install" else install.uninstall()
    print(json.dumps(status, indent=2, sort_keys=True))
    if action == "install":
        return 0 if status["ok"] else 1
    return 0 if not status["file_present"] else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Computer Use MCP server for Sway")
    parser.add_argument("--self-test", action="store_true", help="run non-mutating checks")
    parser.add_argument("--doctor", action="store_true", help="print detailed diagnostics")
    parser.add_argument("--install-codex-mcp", action="store_true", help="register with Codex")
    parser.add_argument("--uninstall-codex-mcp", action="store_true", help="remove Codex registration")
    parser.add_argument("--install-sway-config", action="store_true",
                        help="declare the agent seat in ~/.config/sway/config.d and reload sway")
    parser.add_argument("--uninstall-sway-config", action="store_true",
                        help="remove the agent seat fragment and reload sway")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if args.doctor:
            return doctor()
        if args.install_codex_mcp:
            return install_codex_mcp()
        if args.uninstall_codex_mcp:
            return uninstall_codex_mcp()
        if args.install_sway_config:
            return sway_config("install")
        if args.uninstall_sway_config:
            return sway_config("uninstall")
        from .server import run_mcp_server

        return run_mcp_server()
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cli() -> int:
    return main(sys.argv[1:])

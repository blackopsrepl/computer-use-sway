"""Benchmark layer 2: a real agent (Claude Code, headless) drives each arm
through MCP in a sandbox sway. See bench/eval.md.

Arms (the same agent, prompt and tasks for each; only the MCP server differs):
- v1:     this server, the working tree
- v0.3.2: this server at tag v0.3.2 (a git worktree)
- cua:    cua-driver (newest stable release) `mcp`, native Wayland enabled

Every server is wrapped in bench/mcp_meter.py, so calls, images and bytes are
counted identically. Success is the bench app's window title, read from sway.

Usage: python -m bench.eval --arms v1,v0.3.2,cua [--cua PATH] [--legacy PATH] [task or family ...]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from computer_use_sway import desktop, sandbox, vinput, wire

from .run import APP, PY, ROOT, TASKS, dump

RESULTS = ROOT / "results"
SRC = str(ROOT.parent / "src")
METER = str(ROOT / "mcp_meter.py")
PROMPT = ("You are operating a Linux desktop (Sway) through the `desk` MCP tools only. "
          "The human explicitly requests desktop use in this private test sandbox. "
          "For v1, explicitly call session(action=start, mode=user) before desktop tools. "
          "A window titled 'B:' is open. Task: {goal} "
          "Work until the task is done, then reply DONE. Do not ask questions.")
RUN_TIMEOUT_S = 420


def server_argv(arm: str, args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    if arm == "v1":
        return [sys.executable, "-m", "computer_use_sway"], {"PYTHONPATH": SRC}
    if arm == "v0.3.2":
        return [sys.executable, "-m", "computer_use_sway"], {"PYTHONPATH": str(Path(args.legacy) / "src")}
    if arm == "cua":
        return [args.cua, "mcp"], {"CUA_DRIVER_RS_ENABLE_WAYLAND": "1"}
    raise SystemExit(f"unknown arm {arm}")


def tokens(usage: dict[str, Any]) -> int:
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                 "cache_read_input_tokens", "output_tokens"))


def run_one(task: dict[str, Any], arm: str, args: argparse.Namespace) -> dict[str, Any]:
    with sandbox.Sandbox(width=1280, height=800) as box, tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        box.spawn([PY, APP, task["family"], task["param"], str(work / "app.jsonl")])
        saved = {k: os.environ.get(k) for k in box.env}
        os.environ.update(box.env)
        hardware = None
        try:
            hardware = user_hardware()
            deadline = time.monotonic() + 20
            while not any((w.get("name") or "").startswith("B:") for w in desktop.all_windows()):
                if time.monotonic() > deadline:
                    return {"success": False, "error": "app did not start"}
                time.sleep(0.2)
            time.sleep(1.0)
            argv, extra = server_argv(arm, args)
            meter_log = work / "meter.jsonl"
            config = {"mcpServers": {"desk": {"command": sys.executable,
                                              "args": [METER, str(meter_log), "--", *argv],
                                              "env": dict(box.env, **extra)}}}
            (work / "mcp.json").write_text(json.dumps(config), encoding="utf-8")
            cmd = ["claude", "-p", PROMPT.format(goal=task["goal"]), "--mcp-config", str(work / "mcp.json"),
                   "--strict-mcp-config", "--tools", "", "--allowedTools", "mcp__desk",
                   "--output-format", "json", "--no-session-persistence"]
            if args.model:
                cmd += ["--model", args.model]
            started = time.monotonic()
            try:
                proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
                                      env={k: v for k, v in os.environ.items() if k not in box.env} | saved_env(saved))
                report = json.loads(proc.stdout or "{}")
            except (subprocess.TimeoutExpired, ValueError) as exc:
                report = {"is_error": True, "result": f"{type(exc).__name__}"}
            wall = time.monotonic() - started
            win = next((w for w in desktop.all_windows() if (w.get("name") or "").startswith("B:")), None)
            calls = [json.loads(line) for line in meter_log.read_text(encoding="utf-8").splitlines()] \
                if meter_log.exists() else []
            return {
                "success": bool(win and win.get("name") == task["expect"]),
                "final_title": win.get("name") if win else None,
                "turns": report.get("num_turns"), "tokens": tokens(report.get("usage") or {}),
                "cost_usd": report.get("total_cost_usd"), "wall_s": round(wall, 1),
                "calls": len(calls), "tool_errors": sum(c["error"] for c in calls),
                "images": sum(c["images"] for c in calls), "image_bytes": sum(c["image_bytes"] for c in calls),
                "response_bytes": sum(c["bytes"] for c in calls),
                "agent_error": bool(report.get("is_error")),
                # what the agent said it did, against what the oracle shows
                "agent_said": str(report.get("result") or "")[-400:],
                "tools_used": sorted({c.get("tool") for c in calls if c.get("tool")}),
            }
        finally:
            if hardware is not None:
                hardware[1].destroy()
                hardware[0].close()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def user_hardware() -> tuple[Any, Any]:
    """An idle pointer and keyboard on seat0, as a real session has. A
    headless sway starts with none; 0.3.2's input (sway cursor commands and
    wtype) then lost clicks and keystrokes, which no user's desktop would."""
    conn = wire.Connection()
    devices = vinput.SeatDevices(conn, "seat0", extent=desktop.layout_extent)
    devices.keyboard.chord("Shift_L")
    devices.pointer.move_to(5, 5)
    return conn, devices


def saved_env(saved: dict[str, str | None]) -> dict[str, str]:
    """The agent itself runs in the caller's environment, not the sandbox's."""
    return {k: v for k, v in saved.items() if v is not None}


def summary(rows: list[dict[str, Any]], arms: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in arms:
        ms = [r[arm] for r in rows if arm in r]
        out[arm] = {"success": sum(m["success"] for m in ms), "tasks": len(ms),
                    **{k: round(sum(m.get(k) or 0 for m in ms), 2)
                       for k in ("turns", "tokens", "cost_usd", "wall_s", "calls", "images", "image_bytes")}}
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bench.eval")
    parser.add_argument("--arms", default="v1,v0.3.2,cua")
    parser.add_argument("--cua", default=os.environ.get("CUA_DRIVER", "cua-driver"))
    parser.add_argument("--legacy", default=os.environ.get("CUS_LEGACY_TREE", ""))
    parser.add_argument("--model", default=None)
    parser.add_argument("select", nargs="*")
    args = parser.parse_args(argv)
    arms = args.arms.split(",")
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS.glob("*.json"))]
    tasks = [t for t in tasks if not args.select or t["id"] in args.select or t["family"] in args.select]
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    path = RESULTS / f"eval-{stamp}.json"
    rows: list[dict[str, Any]] = []
    for task in tasks:
        row: dict[str, Any] = {"id": task["id"], "family": task["family"]}
        for arm in arms:
            row[arm] = run_one(task, arm, args)
            print(json.dumps({"id": task["id"], "arm": arm, **row[arm]}), flush=True)
        rows.append(row)
        path.write_text(dump({"date": stamp, "arms": arms, "model": args.model, "rows": rows,
                              "summary": summary(rows, arms)}, "rows"), encoding="utf-8")
    print(json.dumps(summary(rows, arms), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

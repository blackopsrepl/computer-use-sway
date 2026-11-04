"""Benchmark layer 1: scripted arms, measured, in sandbox sways.

For every task in bench/tasks: run the legacy arm (screenshot -> click/type at
oracle-given coordinates -> screenshot, on the user's seat: the pixel loop of
0.3 and the screenshot harnesses at a perfect-grounding best case) and the v1
arm (observe/act/run on the agent seat). A user types into their own window
every 200 ms throughout, with their own keyboard. Measured per arm: success,
MCP calls, image bytes, response bytes, wall time, user disturbances.
Gate: v1 succeeds wherever legacy does, and v1 never disturbs the user.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from computer_use_sway import core, desktop, launch, sandbox, session
from computer_use_sway.server import call_tool

from .user import UserSimulation

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"
RESULTS = ROOT / "results"
APP = str(ROOT / "app.py")
ORACLE = str(ROOT.parent / "tests" / "integration" / "apps" / "oracle_gtk3.py")
PY = "/usr/bin/python3"


class Meter:
    def __init__(self) -> None:
        self.calls = 0
        self.image_bytes = 0
        self.response_bytes = 0

    def call(self, tool: str, args: dict[str, Any]) -> str:
        self.calls += 1
        content, _meta = call_tool(tool, args)
        for item in content:
            if item.get("type") == "image":
                self.image_bytes += len(base64.b64decode(item["data"]))
                self.response_bytes += len(item["data"])
            else:
                self.response_bytes += len(item.get("text", ""))
        text = next((c["text"] for c in content if c.get("type") == "text"), "")
        if os.environ.get("BENCH_DEBUG"):
            print(f"  [{tool}] {json.dumps(args)[:160]}\n    -> {text[:1200]}", file=sys.stderr, flush=True)
        return text


class Arm:
    def __init__(self, sess: session.Session, task: dict[str, Any], log: Path) -> None:
        self.sess, self.task, self.log = sess, task, log
        self.meter = Meter()

    def app_argv(self) -> list[str]:
        return [PY, APP, self.task["family"], self.task["param"], str(self.log)]

    def main_window(self) -> dict[str, Any] | None:
        return next((w for w in desktop.all_windows() if (w.get("name") or "").startswith("B:")), None)

    def succeeded(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            win = self.main_window()
            if win is not None and win.get("name") == self.task["expect"]:
                return True
            time.sleep(0.1)
        return False

    def setup(self) -> None:
        raise NotImplementedError

    def run(self) -> None:
        raise NotImplementedError

    def events(self, kind: str) -> list[dict[str, Any]]:
        try:
            rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if r["ev"] == kind]


class V1(Arm):
    def setup(self) -> None:
        launch.launch(self.sess, self.app_argv(), timeout_ms=30000)
        if self.task["family"] == "multiwindow":
            deadline = time.monotonic() + 10
            while not any(w.get("name") == "Source" for w in desktop.all_windows()):
                if time.monotonic() > deadline:
                    raise core.ToolError("Source window did not open")
                time.sleep(0.1)

    def run(self) -> None:
        variables: dict[str, str] = {}
        for call in self.task["v1"]:
            args = json.loads(re.sub(r"\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)),
                                     json.dumps(call["args"])))
            text = self.meter.call(call["tool"], args)
            for var, pattern in call.get("capture", {}).items():
                found = re.search(pattern, text)
                if found:
                    variables[var] = found.group(1)


class Legacy(Arm):
    def setup(self) -> None:
        self.sess.sandbox().spawn(self.app_argv())
        deadline = time.monotonic() + 15
        while self.main_window() is None or not self.events("geometry"):
            if time.monotonic() > deadline:
                raise core.ToolError("bench app did not start")
            time.sleep(0.1)
        time.sleep(0.5)

    def surface_origin(self, box: dict[str, Any]) -> tuple[int, int]:
        title = box.get("surface")
        win = next((w for w in desktop.all_windows() if w.get("name") == title), None) or self.main_window()
        assert win is not None
        return win["content"]["x"], win["content"]["y"]

    def box(self, target: str) -> dict[str, Any]:
        # Perfect grounding: wait for the app to report the widget (a menu
        # reports its items 250 ms after it shows) instead of failing on timing.
        deadline = time.monotonic() + 3
        while True:
            for row in reversed(self.events("geometry")):
                if target in row["widgets"]:
                    return row["widgets"][target]
            if time.monotonic() > deadline:
                raise core.ToolError(f"no geometry for {target}")
            time.sleep(0.05)

    def center(self, target: str) -> tuple[int, int]:
        box = self.box(target)
        if box.get("popup"):
            main = self.main_window()
            assert main is not None
            x, y = box["window_xy"]
            return main["content"]["x"] + x + box["w"] // 2, main["content"]["y"] + y + box["h"] // 2
        ox, oy = self.surface_origin(box)
        return ox + box["x"] + box["w"] // 2, oy + box["y"] + box["h"] // 2

    def look(self) -> None:
        self.meter.call("screenshot", {"output": "image", "include_cursor": False})

    def click(self, x: int, y: int) -> None:
        self.meter.call("click", {"x": x, "y": y, "seat": "user", "verify": False})
        # A model's screenshot round-trip takes seconds; 0.6 s also clears
        # GtkMenuShell's 500 ms guard against a release right after popup.
        time.sleep(0.6)

    def run(self) -> None:
        self.look()
        for op in self.task["legacy"]:
            kind = op["op"]
            if kind == "click":
                self.click(*self.center(op["target"]))
            elif kind == "type":
                self.meter.call("type_text", {"text": op["text"], "seat": "user", "verify": False})
            elif kind == "click_word":
                words = self.events("words")[-1]["words"][op["target"]]
                win = self.main_window()
                assert win is not None
                self.click(win["content"]["x"] + words["x"] + words["w"] // 2,
                           win["content"]["y"] + words["y"] + words["h"] // 2)
            elif kind == "scroll_to":
                view = self.box(op["viewport"])
                vx, vy = self.center(op["viewport"])
                for _ in range(40):
                    row = self.box(op["target"])
                    if view["y"] <= row["y"] and row["y"] + row["h"] <= view["y"] + view["h"]:
                        break
                    self.meter.call("scroll", {"x": vx, "y": vy, "direction": "down", "clicks": 1, "seat": "user",
                                               "verify": False})
                    time.sleep(0.25)
                    self.look()
            self.look()


CONDITIONS = (("legacy", "idle"), ("legacy", "active"), ("v1", "idle"), ("v1", "active"))
ARMS: dict[str, type[Arm]] = {}


def run_arm(kind: type[Arm], task: dict[str, Any], typing: bool) -> dict[str, Any]:
    sess = session.Session()
    session.reset_for_tests(sess)
    sess.sandbox_factory = lambda: sandbox.Sandbox(width=1280, height=800)
    sess.start("sandbox")
    try:
        box = sess.sandbox()
        log = box.root / "bench.jsonl"
        user = UserSimulation(sess, ORACLE, box.root / "oracle.jsonl", typing=typing)
        user.start()
        arm = kind(sess, task, log)
        started = time.monotonic()
        error = None
        try:
            arm.setup()
            started = time.monotonic()
            arm.run()
            ok = arm.succeeded()
        except core.ToolError as exc:
            ok, error = False, str(exc)
        wall = time.monotonic() - started
        disturbances = user.stop()
        result = {"success": ok, "calls": arm.meter.calls, "image_bytes": arm.meter.image_bytes,
                  "response_bytes": arm.meter.response_bytes, "wall_s": round(wall, 2), **disturbances}
        if error:
            result["error"] = error[:200]
        return result
    finally:
        sess.stop()
        session.reset_for_tests(None)


def table(results: list[dict[str, Any]]) -> str:
    rows = ["| task | arm | user | ok | calls | image KiB | response KiB | wall s | focus moves | typing lost |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        for arm, cond in CONDITIONS:
            m = r[f"{arm}_{cond}"]
            rows.append(f"| {r['id']} | {arm} | {cond} | {'yes' if m['success'] else 'no'} | {m['calls']} | "
                        f"{m['image_bytes'] / 1024:.0f} | {m['response_bytes'] / 1024:.1f} | {m['wall_s']} | "
                        f"{m['focus_moves']} | {m['typing_lost']} |")
    return "\n".join(rows)


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm, cond in CONDITIONS:
        ms = [r[f"{arm}_{cond}"] for r in results]
        out[f"{arm}_{cond}"] = {
            "success": sum(m["success"] for m in ms), "tasks": len(ms), "calls": sum(m["calls"] for m in ms),
            "image_bytes": sum(m["image_bytes"] for m in ms), "response_bytes": sum(m["response_bytes"] for m in ms),
            "wall_s": round(sum(m["wall_s"] for m in ms), 1),
            "focus_moves": sum(m["focus_moves"] for m in ms), "typing_lost": sum(m["typing_lost"] for m in ms)}
    return out


def gate(results: list[dict[str, Any]]) -> list[str]:
    failures = []
    for r in results:
        for cond in ("idle", "active"):
            legacy, v1 = r[f"legacy_{cond}"], r[f"v1_{cond}"]
            if legacy["success"] and not v1["success"]:
                failures.append(f"{r['id']} ({cond} user): v1 failed where legacy succeeded")
            if not v1["success"]:
                failures.append(f"{r['id']} ({cond} user): v1 failed")
            if v1["focus_moves"] or v1["typing_lost"]:
                failures.append(f"{r['id']} ({cond} user): v1 disturbed the user")
    return failures


def dump(report: dict[str, Any], rows_key: str = "results") -> str:
    """JSON with one result row per line: diffable, and far under the
    repository's 500-line ceiling."""
    head = {k: v for k, v in report.items() if k != rows_key}
    rows = ",\n ".join(json.dumps(row, sort_keys=True) for row in report[rows_key])
    return json.dumps(head, sort_keys=True)[:-1] + f', "{rows_key}": [\n {rows}\n]}}\n'


def main(argv: list[str]) -> int:
    wanted = set(argv)
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS.glob("*.json"))]
    tasks = [t for t in tasks if not wanted or t["id"] in wanted or t["family"] in wanted]
    results = []
    for task in tasks:
        row: dict[str, Any] = {"id": task["id"], "family": task["family"]}
        for arm, cond in CONDITIONS:
            row[f"{arm}_{cond}"] = run_arm(Legacy if arm == "legacy" else V1, task, cond == "active")
        results.append(row)
        print(json.dumps(row), flush=True)
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    report = {"date": stamp, "summary": summary(results), "results": results, "gate_failures": gate(results)}
    (RESULTS / f"{stamp}.json").write_text(dump(report), encoding="utf-8")
    (RESULTS / f"{stamp}.md").write_text(table(results) + "\n\n```json\n" + json.dumps(report["summary"], indent=1)
                                         + "\n```\n", encoding="utf-8")
    print(table(results))
    print(json.dumps(report["summary"], indent=1))
    if report["gate_failures"]:
        print("GATE FAILED:\n  " + "\n  ".join(report["gate_failures"]))
        return 1
    print("gate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

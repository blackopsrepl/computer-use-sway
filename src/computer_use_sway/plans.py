"""`run`: several steps server-side in one call, stopping at the first failure."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from . import core

STEP_TOOLS = ("act", "wait_for", "launch", "observe", "key", "type_text", "focus_window", "assert")
MAX_STEPS = 50
SUMMARY_CHARS = 200

Publisher = Callable[[str, dict[str, Any], float, bool, dict[str, Any] | None], None]


def _summary(content: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    compact = json.dumps(data, separators=(",", ":")) if isinstance(data, dict) else text
    if len(compact) > SUMMARY_CHARS:
        compact = compact[: SUMMARY_CHARS - 1] + "…"
    return compact, data if isinstance(data, dict) else None


def step_failed(tool: str, data: dict[str, Any] | None, require: str) -> str | None:
    if data is None:
        return None
    if data.get("blocked"):
        return f"blocked: {data['blocked']}"
    if tool == "act" and require == "changed" and data.get("effect") not in ("confirmed", "changed"):
        return f"effect {data.get('effect')}"
    if tool in ("wait_for", "assert") and not data.get("satisfied", False):
        return f"condition not satisfied: {data.get('detail')}"
    return None


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise core.ToolError("steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise core.ToolError(f"at most {MAX_STEPS} steps")
    out = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise core.ToolError(f"step {index} must be an object")
        tool = step.get("tool")
        if tool == "run":
            raise core.ToolError("run steps cannot nest run")
        if tool not in STEP_TOOLS:
            raise core.ToolError(f"step {index}: tool must be one of {', '.join(STEP_TOOLS)}")
        args = step.get("args", {})
        if not isinstance(args, dict):
            raise core.ToolError(f"step {index}: args must be an object")
        require = step.get("require", "changed" if tool == "act" else "any")
        if require not in ("changed", "any"):
            raise core.ToolError(f"step {index}: require must be changed or any")
        out.append({"tool": tool, "args": args, "require": require})
    return out


def run(tools: dict[str, Callable[[dict[str, Any]], Any]], steps: Any, stop_on: Any = "failure",
        timeout_ms: Any = 120000, observe_after: Any = None, publish: Publisher | None = None) -> core.Result:
    plan = validate_steps(steps)
    if stop_on not in ("failure", "never"):
        raise core.ToolError("stop_on must be failure or never")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1000 <= timeout_ms <= 600000:
        raise core.ToolError("timeout_ms must be between 1000 and 600000")
    if observe_after is not None and not isinstance(observe_after, dict):
        raise core.ToolError("observe_after must be an object of observe arguments")
    deadline = time.monotonic() + timeout_ms / 1000.0
    results: list[dict[str, Any]] = []
    failed_step: int | None = None
    for index, step in enumerate(plan):
        tool, args = step["tool"], dict(step["args"])
        if time.monotonic() > deadline:
            results.append({"tool": tool, "ok": False, "summary": "run timeout before this step"})
            failed_step = failed_step if failed_step is not None else index
            break
        name = "wait_for" if tool == "assert" else tool
        if tool == "assert":
            args["timeout_ms"] = 0
        started = time.monotonic()
        meta: dict[str, Any] | None = None
        try:
            out = tools[name](args)
            content, meta = (out.content, out.meta) if isinstance(out, core.Result) else (out, {})
            summary, data = _summary(content)
            reason = step_failed(tool, data, step["require"])
            ok = reason is None
            entry: dict[str, Any] = {"tool": tool, "ok": ok, "summary": summary}
            if data and "effect" in data:
                entry["effect"] = data["effect"]
            if reason:
                entry["reason"] = reason
        except core.ToolError as exc:
            ok = False
            entry = {"tool": tool, "ok": False, "summary": str(exc)[:SUMMARY_CHARS]}
            meta = {"error": exc.code} if exc.code else None
        if publish is not None:
            publish(name, args, started, ok, meta)
        results.append(entry)
        if not ok and failed_step is None:
            failed_step = index
        if not ok and stop_on == "failure":
            break
    report: dict[str, Any] = {"ok": failed_step is None, "failed_step": failed_step, "steps": results}
    content = core.json_text(report)
    if observe_after is not None:
        try:
            obs = tools["observe"](observe_after)
            content += obs.content if isinstance(obs, core.Result) else obs
        except core.ToolError as exc:
            report["observe_error"] = str(exc)
            content = core.json_text(report)
    meta_out = {"steps": len(plan), "tools": [s["tool"] for s in plan], "failed_step": failed_step}
    return core.Result(content, meta_out)

"""Schemas for the semantic tools: session, observe, read, act, run, wait_for, launch."""

from __future__ import annotations

from typing import Any

from . import core

ACTIONS = ["press", "activate", "toggle", "focus", "set_text", "type", "key", "click", "double_click",
           "right_click", "scroll", "drag_to", "set_value", "select"]
MATCH = {"type": "string", "enum": ["exact", "contains", "regex"], "default": "contains"}
SELECTOR = {
    "type": "object",
    "description": "Window selector: exactly one of con_id, app_id, class, title (plus optional match).",
    "properties": {
        "con_id": {"type": "integer"},
        "app_id": {"type": "string"},
        "class": {"type": "string"},
        "title": {"type": "string"},
        "match": MATCH,
    },
    "additionalProperties": False,
}
QUERY = {
    "type": "object",
    "description": "Find elements by role, accessible name, or visible text (OCR included).",
    "properties": {
        "role": {"type": "string", "description": "e.g. button, entry, checkbox, link, menuitem, tab, item"},
        "name": {"type": "string"},
        "text": {"type": "string"},
        "match": MATCH,
        "window": SELECTOR,
        "nth": {"type": "integer", "minimum": 0, "default": 0},
    },
    "additionalProperties": False,
}
TARGET = {
    "type": "object",
    "description": "{ref: 's3:12'} from observe, {query: {...}}, or a layout point {x, y}.",
    "properties": {
        "ref": {"type": "string"},
        "query": QUERY,
        "x": {"type": "integer"},
        "y": {"type": "integer"},
    },
    "additionalProperties": False,
}
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
ACTING = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


def specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "session",
            "description": (
                "Status, start or stop the computer-use session. Modes: seat (default; the agent works on "
                "its own seat and workspace beside the user), user (drive the user's seat0), sandbox "
                "(a private nested sway)."
            ),
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "start", "stop"]},
                    "mode": {"type": "string", "enum": ["seat", "user", "sandbox"], "default": "seat"},
                    "options": {
                        "type": "object",
                        "properties": {
                            "foreground": {"type": "string", "enum": ["idle", "always", "never"]},
                            "idle_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
                            "idle_max_wait_ms": {"type": "integer", "minimum": 0, "maximum": 120000},
                            "foreground_pointer": {"type": "boolean"},
                            "settle_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
                            "ocr": {"type": "string", "enum": ["auto", "always", "never"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        {
            "name": "observe",
            "description": (
                "Map what is on screen as numbered elements (role, name, state, bounds) from the "
                "accessibility tree, with an OCR fallback. Use the refs with act/read. Prefer this to screenshots."
            ),
            "annotations": READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["agent", "focused", "visible", "window"],
                              "description": "agent (default in seat mode): windows on the agent workspace."},
                    "window": SELECTOR,
                    "query": QUERY,
                    "max_elements": {"type": "integer", "minimum": 1, "maximum": 500, "default": 150},
                    "ocr": {"type": "string", "enum": ["auto", "always", "never"]},
                    "image": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "read",
            "description": "Read an element's full text or value and its states by ref.",
            "annotations": READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "start": {"type": "integer", "minimum": 0, "default": 0},
                    "length": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 4000},
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        },
        {
            "name": "act",
            "description": (
                "Do one thing to one element and report whether it worked. Tries a semantic accessibility "
                "action first, then the agent's own seat, then (only if the user is idle and policy allows) a "
                "brief foreground step with the user's focus restored. The result's effect is "
                "confirmed/changed/no_change/unverifiable with evidence."
            ),
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": TARGET,
                    "action": {"type": "string", "enum": ACTIONS},
                    "text": {"type": "string", "maxLength": core.TEXT_LIMIT},
                    "key": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string",
                                                             "enum": ["ctrl", "shift", "alt", "logo"]}},
                    "value": {"type": "number"},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3},
                    "to": TARGET,
                    "delivery": {"type": "string", "enum": ["auto", "semantic", "agent", "foreground"],
                                 "default": "auto"},
                    "verify": {"type": "boolean", "default": True},
                },
                "required": ["target", "action"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run",
            "description": (
                "Execute up to 50 steps (act, wait_for, launch, observe, key, type_text, focus_window, assert) "
                "server-side in one call; stops at the first failure."
            ),
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50,
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string", "enum": ["act", "wait_for", "launch", "observe", "key",
                                                                    "type_text", "focus_window", "assert"]},
                                "args": {"type": "object"},
                                "require": {"type": "string", "enum": ["changed", "any"]},
                            },
                            "required": ["tool", "args"],
                            "additionalProperties": False,
                        },
                    },
                    "stop_on": {"type": "string", "enum": ["failure", "never"], "default": "failure"},
                    "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 600000, "default": 120000},
                    "observe_after": {"type": "object"},
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        },
        {
            "name": "wait_for",
            "description": (
                "Wait until a condition holds: an element present/absent/in a state, a window event, visible "
                "text, stable pixels, or the user being idle. Not satisfied is reported, not an error."
            ),
            "annotations": READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "object",
                        "properties": {
                            "element": QUERY,
                            "state": {"type": "string"},
                            "window": SELECTOR,
                            "event": {"type": "string", "enum": ["new", "close", "title", "focus"]},
                            "text": {"type": "string"},
                            "pixels_stable": SELECTOR,
                            "for_ms": {"type": "integer", "minimum": 60, "maximum": 10000},
                            "user_idle_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
                        },
                        "additionalProperties": False,
                    },
                    "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 120000, "default": 10000},
                },
                "required": ["condition"],
                "additionalProperties": False,
            },
        },
        {
            "name": "launch",
            "description": (
                "Start a program on the agent workspace (seat mode) without touching the user's focus. Adds "
                "the accessibility flag Chromium/Electron need. Returns the new window."
            ),
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64},
                    "cwd": {"type": "string"},
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                    "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 120000, "default": 20000},
                    "a11y": {"type": "boolean", "default": True},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        },
    ]

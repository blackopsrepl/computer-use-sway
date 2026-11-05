"""Schemas for the low-level input tools."""

from __future__ import annotations

from typing import Any

from . import core
from .specs_semantic import ACTING, READ_ONLY


def specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "focus_window",
            "description": "Focus a window by con_id, app_id, class, or title, on the agent seat (default in seat mode) or the user's seat.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "move_to_agent": {"type": "boolean", "default": False,
                                      "description": "First move the window to the agent workspace (hides it from the user's screen)."},
                    "con_id": {"type": ["integer", "string"]},
                    "app_id": {"type": "string"},
                    "class": {"type": "string"},
                    "title": {"type": "string"},
                    "match": {"type": "string", "enum": ["exact", "contains", "regex"], "default": "contains"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "move_pointer",
            "description": "Move a seat's pointer in layout coordinates. The agent seat's cursor never stays on the user's screen: when the call ends it returns to the agent output, so hover over a window the user sees lasts only for the call.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["set", "move"], "default": "set"},
                },
                "required": ["x", "y"],
                "additionalProperties": False,
            },
        },
        {
            "name": "click",
            "description": "Click a seat's pointer, optionally moving to a layout coordinate first. Returns the effect.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "verify": {"type": "boolean", "default": True,
                               "description": "Report the effect (confirmed/changed/no_change/unverifiable)."},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                    "interval_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 80},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "drag",
            "description": "Drag from one layout coordinate to another with a seat's pointer. Returns the effect.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "verify": {"type": "boolean", "default": True,
                               "description": "Report the effect (confirmed/changed/no_change/unverifiable)."},
                    "from": {"type": "object"},
                    "to": {"type": "object"},
                    "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 12},
                    "duration_ms": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 350},
                },
                "required": ["from", "to"],
                "additionalProperties": False,
            },
        },
        {
            "name": "scroll",
            "description": "Scroll with a seat's pointer wheel. Returns the effect.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "verify": {"type": "boolean", "default": True,
                               "description": "Report the effect (confirmed/changed/no_change/unverifiable)."},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1},
                },
                "required": ["direction"],
                "additionalProperties": False,
            },
        },
        {
            "name": "type_text",
            "description": "Type text into the window focused on a seat. Returns the effect.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "verify": {"type": "boolean", "default": True,
                               "description": "Report the effect (confirmed/changed/no_change/unverifiable)."},
                    "text": {"type": "string", "maxLength": core.TEXT_LIMIT},
                    "delay_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 0},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "key",
            "description": "Send one key with optional modifiers on a seat's keyboard. Returns the effect.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
                    "verify": {"type": "boolean", "default": True,
                               "description": "Report the effect (confirmed/changed/no_change/unverifiable)."},
                    "key": {"type": "string"},
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["ctrl", "shift", "alt", "logo"]},
                        "default": [],
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        },
        {
            "name": "clipboard_set",
            "description": "Set a seat's text clipboard. Pasting from the agent clipboard works in GTK3 and Firefox; GTK4 and Qt6 paste the user's clipboard regardless. To enter text use act/type_text, not paste.",
            "annotations": ACTING,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    ]

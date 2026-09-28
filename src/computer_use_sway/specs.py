from __future__ import annotations

from typing import Any

from . import core


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "screen_info",
            "description": "Inspect the active Sway outputs, seat, focused window, and required binaries.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "screenshot",
            "description": (
                "Capture the current Sway screen or a rectangular region as PNG. By default "
                "the PNG is returned as MCP image content; pass save_path to write it to disk "
                "and return text-only metadata instead."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_cursor": {"type": "boolean", "default": True},
                    "output": {"type": ["string", "null"], "enum": ["image", "data_url", "both", None]},
                    "save_path": {
                        "type": ["string", "null"],
                        "description": (
                            "Absolute path to write the PNG (mode 0600; the parent directory "
                            "must exist). When set, returns text-only metadata and no image "
                            "block; cannot be combined with output."
                        ),
                    },
                    "region": {
                        "type": ["object", "null"],
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "window_tree",
            "description": "Return a simplified Sway window tree.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_scratchpad": {"type": "boolean", "default": False},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "focus_window",
            "description": "Focus a Sway window by con_id, app_id, class, or title.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Move the Sway pointer in logical output coordinates.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Click the pointer, optionally moving to a coordinate first.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Drag from one Sway coordinate to another.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Scroll using pointer wheel button events.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Type text into the focused Wayland application.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": core.TEXT_LIMIT},
                    "delay_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 0},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "key",
            "description": "Send one key with optional modifiers through wtype.",
            "inputSchema": {
                "type": "object",
                "properties": {
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
            "description": "Set text clipboard contents with wl-copy.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "clipboard_get",
            "description": "Read text clipboard contents with wl-paste.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": core.CLIPBOARD_LIMIT,
                        "default": core.CLIPBOARD_LIMIT,
                    }
                },
                "additionalProperties": False,
            },
        },
    ]

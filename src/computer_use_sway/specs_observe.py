"""Schemas for the observation tools."""

from __future__ import annotations

from typing import Any

from . import core
from .specs_semantic import ACTING, READ_ONLY


def specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "screen_info",
            "description": "Inspect outputs (including the agent output), seats, mode, focused window, and required binaries.",
            "annotations": READ_ONLY,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "screenshot",
            "description": "Capture the user's screen (default), a region, one window, or one output (target_output 'agent' for the agent output) as PNG. Use only when you need pixels; observe is cheaper. Pass save_path to write it to disk and return text-only metadata instead.",
            "annotations": READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "window": {"type": "object", "description": "Crop to one window: {con_id|app_id|class|title, match}."},
                    "target_output": {"type": "string", "description": "Capture one output by name, or 'agent' for the agent output."},
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
            "description": "Return a simplified Sway window tree with workspace, output and content rects.",
            "annotations": READ_ONLY,
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
            "name": "clipboard_get",
            "description": "Read a seat's text clipboard.",
            "annotations": READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seat": {"type": "string", "enum": ["agent", "user"],
                             "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."},
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

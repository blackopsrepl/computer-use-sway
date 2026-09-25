from __future__ import annotations

from typing import Any

from . import core, recording, scenes, tts


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "screen_info",
            "description": "Inspect the active Sway outputs, seat, focused window, and required binaries.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "screenshot",
            "description": "Capture the current Sway screen or a rectangular region as PNG.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_cursor": {"type": "boolean", "default": True},
                    "output": {"type": ["string", "null"], "enum": ["image", "data_url", "both", None]},
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
        {
            "name": "recording_start",
            "description": (
                "Start recording an active Sway output (or a region within it) into a silent "
                "artifact: H.264 MP4 by default, or AV1 WebM, or a constrained GIF fallback. "
                "Returns immediately; "
                "call recording_stop to finish, then poll recording_status until the phase is "
                "completed or failed. The pointer cursor is always included."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "description": "Exact Sway output name; inferred when exactly one output is active.",
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
                        "description": "Region fully contained in the selected output.",
                    },
                    "format": {
                        "type": "string",
                        "enum": list(recording.RECORDING_FORMATS),
                        "default": "mp4",
                        "description": (
                            "mp4: silent H.264 MP4 at 30 fps (default, widest compatibility); "
                            "webm: silent AV1 WebM at 30 fps; gif: constrained fallback at 12 fps, "
                            "960 px maximum width."
                        ),
                    },
                    "max_duration_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 300,
                        "description": (
                            "Automatic stop deadline. Defaults to 60 (webm) or 15 (gif); "
                            "GIF recordings are capped at 15 seconds."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_status",
            "description": (
                "Report the recording lifecycle phase (idle, recording, stopping, processing, "
                "completed, failed) plus live progress or final artifact metadata."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Recording id from recording_start; defaults to the latest job.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_stop",
            "description": (
                "Gracefully stop the active recording and begin finalizing the requested "
                "artifact. Returns the stopping/processing state; poll recording_status for "
                "the final artifact path and metadata."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "recording_timeline",
            "description": (
                "Return a recording's monotonic event timeline: every "
                "action/observation tool call recorded while capture ran, with a "
                "recording-relative t_ms and a compact payload. Use event ids as narration "
                "anchors. A sidecar JSON copy is written next to the artifact on completion."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Recording id from recording_start; defaults to the latest job.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_voiceover",
            "description": (
                "Attach a scripted narration track to a completed webm: the caller supplies "
                "the prose, the server synthesizes speech, aligns segments to timeline "
                "anchors, and muxes Opus audio over a copied AV1 video stream. Starts an "
                "async 'narrating' phase; poll recording_status. GIF cannot carry audio."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Recording id from recording_start; defaults to the latest job.",
                    },
                    "segments": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "anchor": {
                                    "type": "object",
                                    "properties": {
                                        "event_id": {"type": "integer", "minimum": 1},
                                        "at_ms": {"type": "number", "minimum": 0},
                                    },
                                    "additionalProperties": False,
                                },
                                "text": {"type": "string", "maxLength": tts.NARRATION_MAX_SEGMENT_CHARS},
                            },
                            "required": ["anchor", "text"],
                            "additionalProperties": False,
                        },
                    },
                    "engine": {"type": "string", "enum": list(tts.NARRATION_ENGINES), "default": "auto"},
                    "voice": {"type": ["string", "null"]},
                    "offset_ms": {
                        "type": "number",
                        "minimum": -tts.NARRATION_MAX_OFFSET_MS,
                        "maximum": tts.NARRATION_MAX_OFFSET_MS,
                        "default": 0,
                    },
                    "fit": {"type": "string", "enum": list(tts.NARRATION_FITS), "default": "natural"},
                    "tail_ms": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": tts.NARRATION_MAX_TAIL_MS,
                        "default": tts.NARRATION_DEFAULT_TAIL_MS,
                    },
                    "subtitles": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Burn styled captions from the narration into the video "
                            "(re-encodes the video). Set false to copy the video without captions."
                        ),
                    },
                },
                "required": ["segments"],
                "additionalProperties": False,
            },
        },
        {
            "name": "recording_scenes",
            "description": (
                "Optional, approximate fallback anchors for a completed recording: ffmpeg "
                "scene-cut timestamps, with optional keyframe OCR via tesseract. Scene cuts "
                "are secondary evidence for when no timeline exists, never the sync source."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Recording id from recording_start; defaults to the latest job.",
                    },
                    "threshold": {
                        "type": "number",
                        "minimum": scenes.SCENE_MIN_THRESHOLD,
                        "maximum": scenes.SCENE_MAX_THRESHOLD,
                        "default": scenes.SCENE_DEFAULT_THRESHOLD,
                    },
                    "max_scenes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": scenes.SCENE_MAX_CUTS,
                        "default": scenes.SCENE_DEFAULT_MAX,
                    },
                    "ocr": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
    ]

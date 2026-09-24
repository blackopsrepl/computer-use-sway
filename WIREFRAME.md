# WIREFRAME.md

This document is the current-state contract for the shipped `computer-use-sway`
MCP server: its tool surface, recording and narration lifecycle, artifacts, and
runtime files. It describes what exists now, not a roadmap.

## Scope

`computer-use-sway` is a zero-runtime-dependency Python MCP stdio server that
lets an MCP client inspect and operate the current Sway/Wayland desktop.

It does:

- expose screen, window, pointer, keyboard, clipboard, and recording tools over
  MCP `tools/call`
- reconstruct a usable Sway session environment from `/run/user/<uid>`
- record one output or region to a silent AV1 WebM or a constrained GIF
- timestamp action/observation calls into a recording-relative timeline
- synthesize caller-authored narration, align it to timeline anchors, and mux it
  over a copied AV1 video stream with a pluggable, runtime-detected TTS engine
- optionally derive approximate scene-cut/OCR fallback anchors

It does not:

- embed or call an LLM, or generate narration prose
- add a network listener; the transport is stdio
- hard-import any TTS, OCR, or scene dependency (`dependencies = []`)
- let callers pass paths, PIDs, codec names, or extra recorder arguments
- carry audio in GIF

## Protocol Surface

Transport is newline-delimited JSON-RPC over stdin/stdout. Methods:

- `initialize` returns `protocolVersion` `2024-11-05`, `capabilities.tools`,
  `serverInfo`, and `instructions` (the operating procedure every client gets).
- `notifications/initialized` returns no response.
- `tools/list` returns the schemas from `specs.py`.
- `tools/call` dispatches by name; unknown tools and expected failures return
  `isError: true` with a text message; unexpected exceptions are logged to
  stderr and returned as a generic internal error.

The `tools/call` handler also captures a monotonic timestamp before each
non-recording tool call and records it into the active timeline when a recording
is running.

## Tool Catalog

Observation and input tools:

| Tool | Arguments | Returns |
|---|---|---|
| `screen_info` | none | server, seat, bounds, active outputs, focused window, binary locations |
| `screenshot` | `include_cursor` (bool, true), `output` (`image`\|`data_url`\|`both`\|null), `region` | text metadata plus PNG image and/or data URL |
| `window_tree` | `include_scratchpad` (false), `max_depth` (1–50, 12) | simplified windows and count |
| `focus_window` | one of `con_id`/`app_id`/`class`/`title`, `match` (`contains`\|`exact`\|`regex`) | before/selected/after |
| `move_pointer` | `x`, `y`, `mode` (`set`\|`move`) | moved, mode, x, y, seat |
| `click` | optional `x`,`y`, `button` (`left`\|`middle`\|`right`), `count` (1–3), `interval_ms` (0–5000) | clicked, coordinates, button, count |
| `drag` | `from` {x,y}, `to` {x,y}, `button`, `steps` (1–100), `duration_ms` (0–10000) | dragged path summary |
| `scroll` | optional `x`,`y`, `direction` (`up`\|`down`\|`left`\|`right`), `clicks` (1–100) | scrolled, direction, clicks |
| `type_text` | `text` (≤10000, no NUL), `delay_ms` (0–5000) | typed, characters, delay_ms |
| `key` | `key`, `modifiers` ⊆ {ctrl, shift, alt, logo} | sent, key, modifiers |
| `clipboard_set` | `text` (≤100000 bytes, no NUL) | clipboard_set, bytes |
| `clipboard_get` | `max_bytes` (1–100000) | text, bytes |

Recording tools:

| Tool | Arguments | Returns |
|---|---|---|
| `recording_start` | `output`, `region`, `format` (`webm`\|`gif`), `max_duration_seconds` | recording summary |
| `recording_status` | none | current phase and metadata |
| `recording_stop` | none | stopping/processing summary |
| `recording_timeline` | none | timeline document |
| `recording_voiceover` | `segments`, `engine`, `voice`, `offset_ms`, `fit`, `tail_ms`, `subtitles` | narrating summary |
| `recording_scenes` | `threshold`, `max_scenes`, `ocr` | approximate scene anchors |

Input maps to Sway seat cursor commands and `wtype`; clipboard uses `wl-copy` and
`wl-paste`. Every schema sets `additionalProperties: false`.

## Recording Lifecycle

State machine: `idle` → `recording` → `stopping` → `processing` →
`completed` | `failed`. A `completed` webm may enter `narrating` and return to
`completed`. Only one job exists per process; `recording_start` rejects a second
concurrent job, and `RECORDINGS` (`manager.RecordingManager`) is the sole owner.

- `recording_start` requires `wf-recorder`, `ffmpeg`, and `ffprobe`; validates
  the output name, region containment, format, and duration cap; allocates
  `0700`/`0600` paths; launches `wf-recorder` in its own session with stdio on
  devnull and a bounded stderr log; probes for startup failure; and starts a
  watchdog.
- Capture writes a constant-frame-rate, lossless `libx264rgb` Matroska
  intermediate. Nothing touches MCP stdio.
- The watchdog stops capture at the duration deadline or above 1 GiB.
- `recording_stop` and the watchdog escalate `SIGINT` → `SIGTERM` → `SIGKILL`
  with bounded waits and report whether the stop was graceful.
- Finalization runs in a daemon thread: `ffmpeg` converts to AV1 WebM (30 fps,
  `yuv420p`, even-dimension padding, stripped metadata, keyframes every 2 s,
  `libsvtav1` then `libaom-av1`) or GIF (12 fps, ≤960 px, palettegen/paletteuse,
  loop). `ffprobe` then gates completion.
- On success the intermediate and log are removed; on failure they are kept and
  reported with the artifact removed.
- On MCP EOF, SIGINT, or SIGTERM, any active capture is stopped before exit.

Formats and limits:

- `webm` (default): silent AV1, 30 fps; default/cap 60/300 s.
- `gif`: silent, max 15 s, 12 fps, ≤960 px, infinite loop.
- `validate_recording_artifact` requires exactly one AV1 stream and zero audio
  for webm, exactly one image stream for gif, and a readable duration.

## Timeline Contract

While phase is `recording`, the dispatch layer appends one event per
action/observation call:

```json
{"id": 1, "t_ms": 1479.319, "tool": "move_pointer", "ok": true, "payload": {"x": 320, "y": 180, "mode": "set"}}
```

- `id` increments from 1; `t_ms` is `event_monotonic - started_monotonic` in
  milliseconds, clamped at zero.
- Payloads are curated and non-sensitive: coordinates, buttons, counts, key
  names, window identifiers, and byte/character counts. Typed text, clipboard
  contents, and screenshot bytes are never stored.
- `recording_timeline` serves the live document; on completion the same document
  is written as a `0600` sidecar `<id>.timeline.json` and reported as
  `timeline_path`. `capture_seconds` reflects the recording window; the first
  captured frame trails `started_monotonic` by a small, constant launch latency
  that the narration `offset_ms` option can compensate.

## Narrated Recording Contract

`recording_voiceover` accepts:

```json
{
  "segments": [{"anchor": {"event_id": 1}, "text": "First I open Settings."}],
  "engine": "auto",
  "voice": null,
  "offset_ms": 0,
  "fit": "natural",
  "tail_ms": 300,
  "subtitles": true
}
```

- Each segment has exactly one anchor: `event_id` (a timeline id) or `at_ms`
  (absolute milliseconds, within the capture window plus slack). The anchor
  positions the first spoken word.
- Text is caller-authored, non-empty, ≤2000 chars per segment and ≤10000 total.
- `engine`: `auto` (prefers `edge`, then `piper`), `edge`, or `piper`; a missing
  engine is a clear `ToolError`, never a silent substitution. `edge-tts`
  transmits the prose to Microsoft; `piper` is offline and needs `voice` (a model
  path) or `COMPUTER_USE_SWAY_PIPER_MODEL`.
- `fit`: `natural` pushes overlapping segments forward (`start = max(anchor,
  previous_end + 60 ms)`); `compress` time-compresses a segment that would
  overrun the next anchor using chained `atempo`. `tail_ms` (≤2000) extends the
  track past the last word.
- Measured leading silence is trimmed so the first word lands on the anchor.
- The track is an `anullsrc` bed plus per-segment `trim`/`delay`/`amix`,
  resampled to 48 kHz stereo PCM; all times are integer milliseconds.
- Muxing copies the video and adds one Opus track:
  `-map 0:v:0 -map 1:a:0 -c:v copy -c:a libopus -b:a 96k -ac 2 -ar 48000`.
- Captions are on by default: the narration text is burned in as styled ASS
  captions (white, bold, bottom-centred, boxed) synced to each segment's
  scheduled window. Burn-in re-encodes the video with the same AV1 encoder;
  `subtitles: false` keeps the stream-copy path and produces a caption-free
  video.
- GIF is refused. A failed narration returns the job to `completed` with
  `narration.error`; the silent artifact is intact and re-narration is safe
  because the video is copied and prior audio dropped.

Completed result additions: `audio_included`, `timeline_path`,
`timeline_event_count`, and `narration` (`engine`, `voice`, `offset_ms`, `fit`,
`subtitles`, `segment_count`, `total_duration_ms`, and per-segment `start_ms`,
`duration_ms`, `lead_silence_ms`, `shift_ms`, `tempo`, `compressed`,
`word_count`).

## Fallback Anchors

`recording_scenes` runs on a completed recording and returns approximate
anchors: `ffmpeg select='gt(scene,T)',showinfo` timestamps (threshold 0.05–0.95,
default 0.30) and, with `ocr: true` and `tesseract` present, keyframe text. These
are secondary evidence only, never the sync mechanism.

## Artifacts And Files

Directory: `$XDG_RUNTIME_DIR/computer-use-sway/recordings`, `0700` directories
and `0600` files. Runtime storage does not survive logout; move or upload
finished artifacts promptly.

- `<id>.mkv`: lossless intermediate (deleted on success, kept on failure)
- `<id>.webm` or `<id>.gif`: final artifact
- `<id>.timeline.json`: timeline sidecar
- `<id>.log`: recorder stderr (kept on failure)
- `<id>.narration/`: transient TTS/track work directory (`captions.ass` included)
- `<id>.narrated.webm.part`: transient mux output, atomically renamed in place

## Diagnostics And CLI

```text
computer-use-sway                 # run MCP server over stdio
computer-use-sway --self-test     # non-mutating session and binary checks
computer-use-sway --doctor        # detailed environment and session report
computer-use-sway --install-codex-mcp
computer-use-sway --uninstall-codex-mcp
```

The server reconstructs `XDG_RUNTIME_DIR`, `SWAYSOCK`, and `WAYLAND_DISPLAY` from
`/run/user/<uid>` when launched with a sanitized environment. `screen_info`,
`--self-test`, and `--doctor` report binary availability, including optional
`edge-tts`, `piper`, and `tesseract`.

## Error Model

Expected desktop, validation, and missing-capability failures are MCP tool
results with `isError: true` and a clear text message. Unexpected Python
exceptions are logged to stderr and returned as a generic internal tool error.

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, and workflows.
- `WIREFRAME.md`: this shipped tool and runtime contract.
- `docs/architecture.md`: module layout, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `AGENTS.md`: repository rules and validation.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.

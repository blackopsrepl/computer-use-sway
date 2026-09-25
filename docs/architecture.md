# Architecture

`computer-use-sway` is a single-process MCP stdio server. It keeps no state between sessions, but while a recording is active it owns a capture child process, a watchdog thread, a finalization thread, and transient files under the user's runtime directory.

## Boundaries

- MCP transport: JSON-RPC messages over stdin/stdout.
- Desktop backend: Sway IPC through `swaymsg`.
- Screenshots: `grim` returns PNG bytes; by default they become MCP image
  content, or `save_path` writes them to a `0600` file and returns text-only
  metadata so long runs do not exhaust a host's per-request image budget.
- Input: pointer actions use Sway seat cursor commands; text and key events use `wtype`.
- Clipboard: text-only set/get through `wl-copy` and `wl-paste`.
- Recording: `wf-recorder` captures a lossless Matroska intermediate; `ffmpeg` converts it to the requested artifact and `ffprobe` validates it.
- Timeline and narration: action/observation tool calls are logged against a monotonic clock; an optional TTS engine (`edge-tts` or `piper`) synthesizes caller-authored prose, which is aligned to those anchors and muxed by `ffmpeg` over the recording's video stream (copied, or re-encoded when captions are burned in). The server never generates prose, and narration is only produced when the caller explicitly requests it.

## Environment Recovery

MCP hosts often launch stdio servers with a stripped environment. Before talking to Sway, the server reconstructs:

- `XDG_RUNTIME_DIR` as `/run/user/<uid>` when available.
- `SWAYSOCK` from the newest Sway IPC socket in that runtime dir.
- `WAYLAND_DISPLAY` from the newest Wayland socket in that runtime dir.

This lets a fresh MCP host reach the current desktop session without shell-specific launch wrappers.

## Recording Lifecycle

State machine: `recording` → `stopping` → `processing` → `completed` | `failed`; `idle` when no job exists. A `completed` video may transition to `narrating` and back to `completed` when a voiceover is requested. Only one recording may be active at a time, but `RecordingManager` retains every job it created so `recording_status`, `recording_timeline`, `recording_voiceover`, and `recording_scenes` can address a specific take by its `id` (defaulting to the latest job); an unknown id is a clear `ToolError`.

- `recording_start` validates the format, duration cap, exact output name, and region containment (the region must lie fully inside the selected output; corner-touching across monitor gaps is rejected), allocates private paths, launches `wf-recorder` in its own process group with stdin/stdout on devnull and stderr in a bounded log, probes for immediate startup failure, and starts a watchdog thread.
- Capture writes a constant-frame-rate, lossless `libx264rgb` Matroska intermediate. Nothing touches MCP stdio.
- The watchdog stops the recording at the duration deadline or when the intermediate exceeds 1 GiB.
- `recording_stop` and the watchdog escalate `SIGINT` → `SIGTERM` → `SIGKILL` with bounded waits and record whether the stop was graceful. Recorder processes that exit on their own are reaped by `recording_status`.
- Finalization runs in a daemon thread: `ffmpeg` converts the intermediate to H.264/MP4 (30 fps, `yuv420p`, even-dimension padding, stripped metadata, `+faststart`) by default, AV1/WebM (30 fps, keyframes every 2 seconds, `libsvtav1` with a `libaom-av1` fallback), or a constrained GIF (12 fps, ≤ 960 px, palettegen/paletteuse, infinite loop). `ffprobe` then gates completion on container, codec, exactly one video stream, zero audio streams, and a readable duration.
- On success the intermediate and log are deleted; on failure they are preserved and reported with the artifact path removed.
- On MCP stdin EOF, SIGINT, or SIGTERM, the server stops any active capture before exiting; the recorder can never outlive the server. If finalization cannot finish, the intermediate survives for recovery.
- Recordings live in `$XDG_RUNTIME_DIR/computer-use-sway/recordings` with `0700` directories and `0600` files. Callers receive server-generated paths and can never pass paths, PIDs, codec names, or extra recorder arguments through the MCP API.

## Timeline And Narration

The recording lifecycle is deterministic and offline; narration is the only
optional, capability-gated extension.

- **Timeline.** The `tools/call` dispatch layer timestamps each non-recording
  tool call before invoking it and appends `{id, t_ms, tool, ok, payload}` to the
  active `RecordingJob` while its phase is `recording`. `t_ms` is
  `event_monotonic - started_monotonic` in milliseconds, clamped at zero. The
  payload is a curated, non-sensitive projection of the arguments (coordinates,
  buttons, counts, key names, window identifiers, byte/character counts); typed
  text and clipboard contents are never stored. On successful finalization the
  document is written as a `0600` sidecar (`<id>.timeline.json`) and returned as
  `timeline_path`; `recording_timeline` also serves the live document.
- **Boundary.** The caller supplies a narration script of segments, each with
  exactly one anchor (`event_id` or `at_ms`) and prose. `parse_narration_arguments`
  validates structure, anchors, and limits; the server never authors text.
- **TTS.** `TtsEngine` is a small pluggable interface with two runtime-detected
  implementations: `EdgeTtsEngine` (`edge-tts`, keyless but unofficial and
  network-dependent; its VTT WordBoundary cues give exact word offsets) and
  `PiperTtsEngine` (offline; leading silence measured with `ffmpeg
  silencedetect`). `auto` prefers `edge`, then `piper`, and otherwise raises a
  clear `ToolError`; it never substitutes silently. Neither engine is an import
  dependency.
- **Alignment.** Segment audio is trimmed of its measured leading silence so the
  first spoken word lands on the anchor. Segments are sorted by resolved anchor
  and scheduled sequentially: `start = max(anchor, previous_end + min_gap)`, so
  overlaps become forward shifts rather than re-timed audio. The optional
  `compress` fit time-compresses a segment that would overrun the next anchor
  using chained `atempo`. The track is an `anullsrc` bed plus per-segment
  `adelay`/`amix` in a fixed `filter_complex`, resampled to 48 kHz stereo and
  written as PCM WAV. All times are integer milliseconds; there is no
  randomness.
- **Muxing.** The published artifact at `job.artifact` is the single source of
  truth: anchors are validated and the schedule is clamped against its measured
  `ffprobe` duration, and `ffmpeg` reads that same file. A trimmed or replaced
  artifact is therefore honored; an unreadable one raises a clear `ToolError`
  instead of falling back to the capture wall-clock or the discarded
  intermediate. The output inherits the recording's container. With captions
  disabled, `ffmpeg` copies the video and adds one audio track (`-c:v copy`, AAC
  for MP4 or Opus for WebM); `os.replace` swaps it into place.
  `validate_recording_artifact(expect_audio=True)` re-probes the result to
  require exactly one video and exactly one audio stream with the container's
  codecs (H.264/AAC for MP4, AV1/Opus for WebM). GIF artifacts reject audio
  unconditionally, so voiceover is refused for `format=gif`.
- **Revert on failure.** A narration failure returns the job to `completed` (the
  silent recording is still valid) and attaches `narration.error` to the result
  rather than discarding a good recording.
- **Enrichment.** `recording_scenes` parses `ffmpeg select='gt(scene,T)'`
  `showinfo` timestamps and, optionally, OCRs each cut frame with `tesseract`.
  These are approximate fallback anchors, never the sync mechanism.

## Module Layout

The server is one Python package split into focused modules, each under the
500-line ceiling enforced by `make check`:

- `core.py`: `ToolError`, `run_command`, binary/session helpers, and strict
  parsing (`strict_int`, `strict_number`, `json_text`). Leaf module.
- `desktop.py`: Sway outputs, seats, tree, window matching, coordinates, and
  cursor commands. Depends on `core`.
- `media.py`: `ffprobe`-backed probing (`probe_media`, `probe_video_stream`,
  `parse_frame_rate`). Depends on `core`.
- `recording.py`: `RecordingJob`, path allocation, capture/finalize argv,
  artifact validation, and finalization. Depends on `core`, `desktop`, `media`,
  `timeline`.
- `timeline.py`: recording-relative event document and sidecar writer.
- `tts.py`: `TtsEngine` interface, `edge-tts`/`piper` engines, VTT and silence
  parsing, and the narration constants. Depends on `core`, `media`.
- `narration.py`: narration contract, anchor resolution, deterministic
  scheduling, track build, mux argv, and `perform_narration`. Depends on `core`,
  `recording`, `subtitles`, `tts`.
- `subtitles.py`: styled ASS caption document, timestamp/escaping helpers, and
  the burn-in mux argv (re-encodes the video). Depends on `recording`, `tts`.
- `scenes.py`: approximate scene-cut detection and OCR fallback anchors.
- `manager.py`: `RecordingManager` and the module-level `RECORDINGS` singleton;
  the only owner of recording state, including the per-id job registry that
  addresses earlier takes. Depends on every other runtime module.
- `tools.py`: MCP tool implementations and the `TOOLS` dispatch table.
- `specs.py`: JSON schemas for `tools/list`.
- `server.py`: the MCP protocol loop, `initialize` instructions, diagnostics,
  and the CLI facade; re-exports the lower layers for callers and tests.

Dependency direction is one-way (`core` → `desktop`/`media` → `recording`/`tts`
→ `narration` → `manager` → `tools`/`specs` → `server`), so a missing optional
binary is always a runtime `ToolError`, never an import failure.

## Tool Error Model

Expected desktop and validation failures are reported as MCP tool results with `isError: true`. Unexpected Python exceptions are logged to stderr and returned as a generic internal tool error.
